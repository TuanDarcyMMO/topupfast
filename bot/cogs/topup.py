"""
TopUp Cog - tất cả các lệnh và UI liên quan đến nạp tiền.

Slash commands:
  /deposit - Mở menu nạp tiền (Bank VN hoặc Crypto)
  /balance - Xem số dư hiện tại
  /history - Xem lịch sử nạp tiền hoặc mua hàng

Flow nạp Bank VN (SePay):
  /create-payment VND -> Bot gửi QR + mã TFA -> User chuyển khoản
  -> Bot polling SePay API mỗi 5s -> balance cộng -> message cập nhật ✅

Flow nạp Crypto (NowPayments):
  /create-payment USD -> Chọn coin -> Bot tạo payment
  -> Giao diện hiển thị địa chỉ ví + số coin chính xác
  -> Bot polling NowPayments API mỗi 5s -> status: pending/confirming/completed
  -> balance cộng -> message cập nhật ✅
"""

import asyncio
import logging
from datetime import datetime, timedelta

import discord
from discord import app_commands
from discord.ext import commands

import services.database as db
from bot.i18n import t, tl, get_locale
from bot.client import require_main_guild
from config import (
    EXCHANGE_RATE,
    MIN_DEPOSIT_USD,
    MIN_DEPOSIT_VND,
    PAYMENT_EXPIRY_MINUTES,
    WEBHOOK_BASE_URL,
)
from services.nowpayments import (
    create_payment as np_create_payment,
    get_payment_status as np_get_status,
    get_available_coins,
    round2 as np_round2,
    NP_STATUS_MAP,
    STATUS_DISPLAY,
)
from services.sepay import generate_qr_url, find_tfa_in_transactions

logger = logging.getLogger(__name__)


# ================================================================= Modals ==

class BankAmountModal(discord.ui.Modal, title="💳 Bank VN Deposit"):
    amount_input = discord.ui.TextInput(
        label="Amount (VND)",
        placeholder=f"Example: 100000 (minimum {50_000:,} VND)",
        min_length=4,
        max_length=12,
    )

    def __init__(self, cog: "TopUpCog") -> None:
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction) -> None:
        locale = get_locale(interaction.user)
        raw = self.amount_input.value.replace(",", "").replace(".", "").strip()
        try:
            amount_vnd = int(raw)
        except ValueError:
            await interaction.response.send_message(
                tl(locale, 'bank_amount_invalid'), ephemeral=True
            )
            return

        if amount_vnd < MIN_DEPOSIT_VND:
            await interaction.response.send_message(
                tl(locale, 'vnd_min_error', min=MIN_DEPOSIT_VND, amt=amount_vnd), ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        user = await db.get_or_create_user(
            str(interaction.user.id),
            _avatar(interaction.user),
        )

        # Tạo mã TFA duy nhất
        tfa_code = await _unique_tfa()
        amount_usd = round(amount_vnd / EXCHANGE_RATE, 4)
        qr_url = generate_qr_url(amount_vnd, tfa_code)

        expires_at = datetime.utcnow() + timedelta(minutes=PAYMENT_EXPIRY_MINUTES)
        expire_ts = int(expires_at.timestamp())

        tx = await db.create_transaction(
            discord_id=str(interaction.user.id),
            user_id=user["id"],
            type="bank",
            provider="sepay",
            amount_usd=amount_usd,
            amount_vnd=amount_vnd,
            currency="VND",
            tfa_code=tfa_code,
            qr_url=qr_url,
            discord_channel_id=str(interaction.channel_id),
        )

        embed = _bank_embed(amount_vnd, amount_usd, tfa_code, expire_ts, qr_url, locale)
        view = CancelPaymentView(tx["id"])
        msg = await interaction.followup.send(embed=embed, view=view, ephemeral=True)

        # Save message ID so webhook can edit it later
        await db.update_transaction(tx["id"], discord_message_id=str(msg.id))

        # Background polling
        asyncio.create_task(
            self.cog.poll_payment(tx["id"], msg, kind="bank")
        )


class CryptoAmountModal(discord.ui.Modal, title="🪙 Crypto Deposit"):
    amount_input = discord.ui.TextInput(
        label="Amount (USD)",
        placeholder="Example: 10 (minimum $1.00)",
        min_length=1,
        max_length=10,
    )

    def __init__(self, cog: "TopUpCog") -> None:
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction) -> None:
        raw = self.amount_input.value.replace(",", ".").strip()
        try:
            amount_usd = float(raw)
        except ValueError:
            await interaction.response.send_message(
                "❌ Invalid amount.", ephemeral=True
            )
            return

        if amount_usd < MIN_DEPOSIT_USD:
            await interaction.response.send_message(
                f"❌ Minimum deposit is **${MIN_DEPOSIT_USD:.2f} USD**. "
                f"You entered `${amount_usd:.2f}`. Please create a new order.",
                ephemeral=True,
            )
            return

        # Show coin selection with the amount stored in the view
        coins = get_available_coins()
        view = CoinConfirmView(self.cog, amount_usd=amount_usd, coins=coins)
        embed = discord.Embed(
            title="🪙 Select Coin",
            description=(
                f"**Amount:** `${amount_usd:.2f} USD`\n\n"
                "Select the coin you want to pay with, then press **Deposit**."
            ),
            color=discord.Color.orange(),
        )
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


# ================================================================== Views ==

class PaymentTypeView(discord.ui.View):
    def __init__(self, cog: "TopUpCog") -> None:
        super().__init__(timeout=120)
        self.cog = cog

    @discord.ui.button(label="💳 Bank VN", style=discord.ButtonStyle.primary)
    async def bank_btn(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.send_modal(BankAmountModal(self.cog))

    @discord.ui.button(label="🪙 Crypto", style=discord.ButtonStyle.secondary)
    async def crypto_btn(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.send_modal(CryptoAmountModal(self.cog))


class CoinConfirmView(discord.ui.View):
    """Coin selection + Deposit button. Shown after user enters USD amount."""

    def __init__(self, cog: "TopUpCog", amount_usd: float, coins: dict[str, str]) -> None:
        super().__init__(timeout=120)
        self.cog = cog
        self.amount_usd = amount_usd
        self.selected_coin = next(iter(coins), "LTC")  # default = first coin (LTC)

        options = [
            discord.SelectOption(
                label=label,
                value=coin,
                default=(coin == self.selected_coin),
            )
            for coin, label in coins.items()
        ]
        select = discord.ui.Select(
            placeholder="Choose coin...",
            options=options,
            min_values=1,
            max_values=1,
        )
        select.callback = self._on_coin_select
        self.add_item(select)

    async def _on_coin_select(self, interaction: discord.Interaction) -> None:
        self.selected_coin = interaction.data["values"][0]
        await interaction.response.defer()  # just update selection, no new message

    @discord.ui.button(label="🚀 Deposit", style=discord.ButtonStyle.success, row=1)
    async def confirm_btn(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)

        try:
            user = await db.get_or_create_user(
                str(interaction.user.id),
                _avatar(interaction.user),
            )

            callback_url = f"{WEBHOOK_BASE_URL}/webhook/nowpayments"
            np_data = await np_create_payment(
                self.amount_usd,
                self.selected_coin,
                str(interaction.user.id),
                callback_url,
            )

            pay_amount = np_data.get("pay_amount") or np_data.get("amount")
            pay_address = np_data.get("pay_address") or np_data.get("address", "")
            np_payment_id = str(np_data.get("payment_id", ""))

            expires_at = datetime.utcnow() + timedelta(minutes=PAYMENT_EXPIRY_MINUTES)
            expire_ts = int(expires_at.timestamp())

            tx = await db.create_transaction(
                discord_id=str(interaction.user.id),
                user_id=user["id"],
                type="crypto",
                provider="nowpayments",
                amount_usd=np_round2(self.amount_usd),
                currency="USD",
                coin=self.selected_coin,
                provider_ref=np_payment_id,
                discord_channel_id=str(interaction.channel_id),
            )

            embed = _crypto_embed_np(
                coin=self.selected_coin,
                amount_usd=self.amount_usd,
                pay_amount=pay_amount,
                pay_address=pay_address,
                expire_ts=expire_ts,
                np_status="waiting",
            )
            view = CancelPaymentView(tx["id"])
            msg = await interaction.followup.send(embed=embed, view=view, ephemeral=True)
            await db.update_transaction(tx["id"], discord_message_id=str(msg.id))

            asyncio.create_task(
                self.cog.poll_payment(tx["id"], msg, kind="crypto",
                                      np_payment_id=np_payment_id,
                                      pay_amount=pay_amount,
                                      pay_address=pay_address,
                                      expire_ts=expire_ts)
            )

        except Exception as exc:
            logger.error(f"CoinConfirmView.confirm_btn error: {exc}", exc_info=True)
            await interaction.followup.send(
                "❌ An error occurred. Please try again or contact admin.",
                ephemeral=True,
            )


class CancelPaymentView(discord.ui.View):
    def __init__(self, tx_id: int) -> None:
        super().__init__(timeout=PAYMENT_EXPIRY_MINUTES * 60)
        self.tx_id = tx_id

    @discord.ui.button(label="❌ Cancel", style=discord.ButtonStyle.danger)
    async def cancel_btn(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await db.update_transaction(self.tx_id, status="cancelled")
        embed = discord.Embed(
            title="❌ Cancelled",
            description=t(interaction.user, 'cancel_done'),
            color=discord.Color.red(),
        )
        await interaction.response.edit_message(embed=embed, view=None)


# ================================================================ Helpers ==

def _avatar(user: discord.User | discord.Member) -> str | None:
    return str(user.display_avatar.url) if user.display_avatar else None


async def _unique_tfa() -> str:
    """Generate a TFA code not used by any pending transaction."""
    for _ in range(10):
        code = db.generate_tfa_code()
        if not await db.get_transaction_by_tfa(code):
            return code
    import random, string
    return "TFA" + "".join(random.choices(string.digits, k=6))


def _bank_embed(
    amount_vnd: int, amount_usd: float, tfa_code: str, expire_ts: int, qr_url: str,
    locale: str = "en",
) -> discord.Embed:
    embed = discord.Embed(
        title=tl(locale, "bank_embed_title"),
        description=tl(locale, "bank_embed_desc", vnd=amount_vnd, usd=amount_usd, tfa=tfa_code, exp=expire_ts),
        color=discord.Color.blue(),
    )
    embed.set_image(url=qr_url)
    embed.set_footer(text=tl(locale, "bank_embed_footer"))
    return embed


def _crypto_embed_np(
    coin: str,
    amount_usd: float,
    pay_amount,
    pay_address: str,
    expire_ts: int,
    np_status: str,
) -> discord.Embed:
    """
    Embed hiển thị thông tin thanh toán crypto qua NowPayments.
    ⚠️ Cảnh báo rõ ràng: gửi CHÍNH XÁC 100% số tiền hiển thị.
    """
    mapped = NP_STATUS_MAP.get(np_status, "pending")
    status_str = STATUS_DISPLAY.get(mapped, f"⏳ {np_status}")
    coin_upper = coin.upper()

    desc = (
        f"💵 **USD Value:** `${np_round2(amount_usd):.2f} USD`\n"
        f"🪙 **Send EXACTLY:**\n"
        f"```\n{pay_amount} {coin_upper}\n```\n"
        f"📬 **To this address:**\n"
        f"```\n{pay_address}\n```\n"
        f"⏱️ **Expires:** <t:{expire_ts}:R>\n"
        f"🟡 **Status:** {status_str}\n\n"
        f"⚠️ **You MUST send EXACTLY `{pay_amount} {coin_upper}`.**\n"
        f"Any difference (even 1 satoshi) may cause the payment to not be credited automatically.\n"
        f"The address and amount above are unique to this order."
    )
    embed = discord.Embed(
        title=f"🪙 Crypto Deposit — {coin_upper}",
        description=desc,
        color=discord.Color.orange(),
    )
    embed.set_footer(text="Bot auto-credits your balance once payment is confirmed.")
    return embed


STATUS_ICONS = {"pending": "⏳", "completed": "✅", "failed": "❌", "cancelled": "🚫", "expired": "⏰"}

# ------------------------------------------------------------------ i18n --


# ================================================================== Cog ==

class TopUpCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ------------------------------------------------------- /balance ----

    @app_commands.command(name="balance", description="Check your account balance")
    @require_main_guild()
    async def balance(self, interaction: discord.Interaction) -> None:
        locale = get_locale(interaction.user)
        user = await db.get_user(str(interaction.user.id))
        if not user:
            await interaction.response.send_message(
                tl(locale, 'balance_no_account'), ephemeral=True
            )
            return
        embed = discord.Embed(
            title=tl(locale, 'balance_title'),
            description=f"💵 **${user['balance']:.4f} USD**",
            color=discord.Color.green(),
        )
        embed.set_thumbnail(
            url=user.get("avatar_url") or str(interaction.user.display_avatar.url)
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # -------------------------------------------------- /create-payment ----

    @app_commands.command(name="create-payment", description="Deposit money into your account.")
    @require_main_guild()
    @app_commands.describe(
        currency="Currency: VND or USD",
        amount="Amount to deposit",
    )
    @app_commands.choices(currency=[
        app_commands.Choice(name="VND - Việt Nam Đồng", value="VND"),
        app_commands.Choice(name="USD - US Dollar", value="USD"),
    ])
    async def create_payment(
        self,
        interaction: discord.Interaction,
        currency: app_commands.Choice[str],
        amount: float,
    ) -> None:
        locale = get_locale(interaction.user)
        # ---- Validate amount ----
        if currency.value == "VND":
            amount_vnd = int(amount)
            amount_usd = round(amount_vnd / EXCHANGE_RATE, 4)
            if amount_vnd < MIN_DEPOSIT_VND:
                await interaction.response.send_message(
                    tl(locale, 'vnd_min_error', min=MIN_DEPOSIT_VND, amt=amount_vnd),
                    ephemeral=True,
                )
                return
        else:  # USD
            amount_usd = amount
            amount_vnd = int(amount_usd * EXCHANGE_RATE)
            if amount_usd < MIN_DEPOSIT_USD:
                await interaction.response.send_message(
                    tl(locale, 'usd_min_error', min=MIN_DEPOSIT_USD, amt=amount_usd),
                    ephemeral=True,
                )
                return

        # ---- USD → Crypto flow ----
        if currency.value == "USD":
            await interaction.response.defer(ephemeral=True, thinking=True)
            coins = get_available_coins()
            view = CoinConfirmView(self, amount_usd=amount_usd, coins=coins)
            embed = discord.Embed(
                title="🪙 Select Coin",
                description=(
                    f"**Amount:** `${amount_usd:.2f} USD`\n\n"
                    "Select the coin you want to pay with, then press **Deposit**."
                ),
                color=discord.Color.orange(),
            )
            await interaction.followup.send(embed=embed, view=view, ephemeral=True)
            return

        # ---- VND → Bank VN flow ----
        await interaction.response.defer(ephemeral=True, thinking=True)

        user = await db.get_or_create_user(
            str(interaction.user.id),
            _avatar(interaction.user),
        )

        tfa_code = await _unique_tfa()
        qr_url = generate_qr_url(amount_vnd, tfa_code)

        expires_at = datetime.utcnow() + timedelta(minutes=PAYMENT_EXPIRY_MINUTES)
        expire_ts = int(expires_at.timestamp())

        tx = await db.create_transaction(
            discord_id=str(interaction.user.id),
            user_id=user["id"],
            type="bank",
            provider="sepay",
            amount_usd=amount_usd,
            amount_vnd=amount_vnd,
            currency="VND",
            tfa_code=tfa_code,
            qr_url=qr_url,
            discord_channel_id=str(interaction.channel_id),
        )

        embed = _bank_embed(amount_vnd, amount_usd, tfa_code, expire_ts, qr_url, locale)
        view = CancelPaymentView(tx["id"])
        msg = await interaction.followup.send(embed=embed, view=view, ephemeral=True)

        await db.update_transaction(tx["id"], discord_message_id=str(msg.id))

        asyncio.create_task(
            self.poll_payment(tx["id"], msg, kind="bank")
        )

    # ------------------------------------------------------- /history ----

    @app_commands.command(name="history", description="View transaction history")
    @require_main_guild()
    @app_commands.describe(type="History type: deposits or purchases")
    @app_commands.choices(type=[
        app_commands.Choice(name="💳 Deposit history", value="deposit"),
        app_commands.Choice(name="🛒 Purchase history", value="purchase"),
    ])
    async def history(
        self,
        interaction: discord.Interaction,
        type: app_commands.Choice[str],
    ) -> None:
        user = await db.get_user(str(interaction.user.id))

        if type.value == "purchase":
            await interaction.response.send_message(
                t(interaction.user, 'history_purchase_na'), ephemeral=True
            )
            return

        txs = await db.get_user_transactions(str(interaction.user.id))
        if not txs:
            await interaction.response.send_message(
                t(interaction.user, 'history_no_tx'), ephemeral=True
            )
            return

        lines: list[str] = []
        for tx in txs:
            icon = STATUS_ICONS.get(tx["status"], "❓")
            # HH:MM:SS in date
            raw_dt = str(tx.get("created_at", ""))
            date = raw_dt[:19].replace("T", " ")  # YYYY-MM-DD HH:MM:SS
            if tx["type"] == "bank":
                amt = f"{tx.get('amount_vnd', 0):,} VND"
            else:
                amt = f"${tx.get('amount_usd', 0):.4f} ({tx.get('coin', '')})"
            lines.append(f"{icon} `{date}` — {amt}")

        embed = discord.Embed(
            title=t(interaction.user, 'history_deposit_title'),
            description="\n".join(lines),
            color=discord.Color.blue(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ------------------------------------------------- background polling --

    async def poll_payment(
        self,
        tx_id: int,
        message: discord.WebhookMessage,
        *,
        kind: str,
        **kwargs,
    ) -> None:
        """
        Polling background task.
        - Bank: gọi SePay API mỗi 5 giây để kiểm tra TFA code.
        - Crypto: poll NowPayments API mỗi 5 giây.
          kwargs: np_payment_id, pay_amount, pay_address, expire_ts
        """
        tx = await db.get_transaction(tx_id)
        if not tx:
            return

        if kind == "bank":
            tfa_code: str = tx.get("tfa_code", "")
            amount_vnd: int = tx.get("amount_vnd", 0)
            amount_usd: float = tx.get("amount_usd", 0)
            # Parse thời gian tạo lệnh để lọc SePay tx cũ
            created_at_str = str(tx.get("created_at", ""))[:19].replace("T", " ")
            try:
                created_after = datetime.strptime(created_at_str, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                created_after = datetime.utcnow()

            max_checks = PAYMENT_EXPIRY_MINUTES * 12  # check mỗi 5s trong X phút

            for _ in range(max_checks):
                await asyncio.sleep(5)

                # Guard: kiểm tra user đã hủy / hết hạn chưa
                tx = await db.get_transaction(tx_id)
                if not tx or tx["status"] in ("failed", "cancelled", "expired", "completed"):
                    return

                # Gọi SePay API để tìm giao dịch khớp TFA + sau thời điểm tạo lệnh
                matched = await find_tfa_in_transactions(tfa_code, created_after)
                if not matched:
                    continue

                sepay_tx_id = str(matched.get("id", ""))

                # Guard: idempotency - SePay tx này đã được xử lý bởi lệnh khác chưa?
                if await db.is_sepay_tx_processed(sepay_tx_id):
                    logger.warning(
                        f"SePay tx {sepay_tx_id} đã được xử lý trước đó, bỏ qua."
                    )
                    continue

                # Guard: re-read status ngay trước khi credit (tránh race condition)
                tx_fresh = await db.get_transaction(tx_id)
                if not tx_fresh or tx_fresh["status"] != "pending":
                    return

                logger.info(
                    f"SePay: xác nhận TFA={tfa_code}, tx_id={sepay_tx_id}, "
                    f"amount_in={matched.get('amount_in')}"
                )

                # Cập nhật DB trước, cộng tiền sau để giảm window race condition
                await db.update_transaction(
                    tx_id,
                    status="completed",
                    provider_ref=sepay_tx_id,
                )
                await db.add_balance(tx_fresh["discord_id"], amount_usd)

                user_row = await db.get_user(tx_fresh["discord_id"])
                locale = get_locale_from_bot(self.bot, tx_fresh["discord_id"])
                desc = tl(locale, "deposit_success_desc", vnd=amount_vnd, usd=amount_usd)
                embed = discord.Embed(
                    title="✅ Deposit successful!",
                    description=desc,
                    color=discord.Color.green(),
                )
                try:
                    await message.edit(embed=embed, view=None)
                except Exception:
                    pass
                return

            # Hết thời gian
            await db.update_transaction(tx_id, status="expired")

        else:  # crypto - polling NowPayments API mỗi 5 giây
            max_checks = PAYMENT_EXPIRY_MINUTES * 12  # 5s * 12 = 1 phút, * 30 = 30 phút
            for _ in range(max_checks):
                await asyncio.sleep(5)

                # Guard: check DB status (user có thể đã hủy)
                tx = await db.get_transaction(tx_id)
                if not tx or tx["status"] in ("failed", "cancelled", "expired", "completed"):
                    return

                np_id = kwargs.get("np_payment_id", tx.get("provider_ref", ""))
                pay_amount = kwargs.get("pay_amount")
                pay_address = kwargs.get("pay_address", "")
                expire_ts_val = kwargs.get("expire_ts", 0)
                coin = tx.get("coin", "LTC")
                amount_usd = tx.get("amount_usd", 0)

                np_data = await np_get_status(np_id)
                if not np_data:
                    continue  # NP API tạm thời không dáp - giữ polling

                np_status = np_data.get("payment_status", "waiting")
                mapped_status = NP_STATUS_MAP.get(np_status, "pending")

                # Cập nhật embed khi status thay đổi
                try:
                    new_embed = _crypto_embed_np(
                        coin=coin,
                        amount_usd=amount_usd,
                        pay_amount=pay_amount,
                        pay_address=pay_address,
                        expire_ts=expire_ts_val,
                        np_status=np_status,
                    )
                    await message.edit(embed=new_embed)
                except Exception:
                    pass

                if mapped_status == "completed":
                    # Guard: idempotency - NP payment_id này đã được xử lý chưa?
                    if await db.is_sepay_tx_processed(np_id):
                        logger.warning(f"NP payment {np_id} đã được xử lý trước, bỏ qua.")
                        return

                    # Guard: re-read, đảm bảo vẫn là pending
                    tx_fresh = await db.get_transaction(tx_id)
                    if not tx_fresh or tx_fresh["status"] != "pending":
                        return

                    actual_usd = np_round2(
                        float(np_data.get("price_amount") or amount_usd)
                    )

                    # Cập nhật DB trước, cộng tiền sau
                    await db.update_transaction(
                        tx_id,
                        status="completed",
                        provider_ref=np_id,
                        amount_usd=actual_usd,
                    )
                    await db.add_balance(tx_fresh["discord_id"], actual_usd)

                    logger.info(
                        f"NowPayments: credited ${actual_usd} → user={tx_fresh['discord_id']}, np_id={np_id}"
                    )
                    user_row = await db.get_user(tx_fresh["discord_id"])
                    embed = discord.Embed(
                        title="✅ Crypto Deposit Successful!",
                        description=(
                            f"🪙 `{pay_amount} {coin}` = `${actual_usd:.2f} USD`\n"
                            f"has been added to your account.\n"
                            f"📊 Check balance: `/balance`"
                        ),
                        color=discord.Color.green(),
                    )
                    try:
                        await message.edit(embed=embed, view=None)
                    except Exception:
                        pass
                    return

                if mapped_status in ("failed", "expired"):
                    await db.update_transaction(tx_id, status=mapped_status)
                    return

            # Polling hết 30 phút
            await db.update_transaction(tx_id, status="expired")

        # Thông báo hết hạn
        tx_final = await db.get_transaction(tx_id)
        locale_final = get_locale_from_bot(self.bot, tx_final["discord_id"]) if tx_final else "en"
        embed = discord.Embed(
            title="⏰ Expired",
            description=tl(locale_final, "expired_desc"),
            color=discord.Color.orange(),
        )
        try:
            await message.edit(embed=embed, view=None)
        except Exception:
            pass


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(TopUpCog(bot))
