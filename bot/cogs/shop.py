"""
Shop cog - mua game package tu balance.
3-tier flow: /buy -> game -> category -> package -> account -> confirm -> order
"""

import asyncio
import logging

import discord
from discord import app_commands
from discord.ext import commands

import services.database as db
from bot.i18n import t, tl, get_locale
from bot.client import require_main_guild
from products import get_active_games, get_game, get_categories, get_packages, get_package
from services.delivery import deliver

logger = logging.getLogger(__name__)

ORDER_STATUS_ICON = {"pending": "⏳", "delivering": "🚚", "completed": "✅", "failed": "❌", "refunded": "↩️"}


def _game_list_embed(games, balance, locale="en"):
    desc = f"{tl(locale, 'shop_balance_label')} `${balance:.2f} USD`\n\n"
    desc += "\n".join(f"{g['emoji']}  **{g['name']}**" for g in games)
    return discord.Embed(title=tl(locale, 'shop_select_game_title'), description=desc, color=discord.Color.blurple())


def _package_embed(game, category, packages, balance, locale="en"):
    lines = []
    for p in packages:
        can = "✅" if balance >= p["price_usd"] else "❌"
        extra = f"\n> {p['description']}" if p.get("description") else ""
        lines.append(f"{can} `${p['price_usd']:.2f}` — **{p['name']}**{extra}")
    embed = discord.Embed(
        title=f"{game['emoji']} {game['name']} › {category.capitalize()}",
        description="\n".join(lines),
        color=discord.Color.blurple(),
    )
    if game.get("icon_url"):
        embed.set_thumbnail(url=game["icon_url"])
    embed.set_footer(text=tl(locale, 'shop_balance_footer', bal=balance))
    return embed


def _platform_label(platform: str) -> str:
    return {"ios": "🍎 iOS", "android": "🤖 Android"}.get(platform.lower(), platform.capitalize())


def _confirm_embed(game, package, game_account, platform, balance, locale="en"):
    """platform: 'ios' | 'android'."""
    price = package["price_usd"]
    r2 = lambda n: round(float(n) * 100) / 100
    remaining = r2(balance) - r2(price)
    color = discord.Color.green() if remaining >= 0 else discord.Color.red()
    parts = package["id"].split(".")
    pkg_display = " › ".join(p.capitalize() for p in parts)
    desc = (
        f"📦 **{pkg_display}**\n"
        f"{tl(locale, 'shop_price_label')} `${price:.2f} USD`\n"
        f"📱 **Platform:** `{_platform_label(platform)}`\n"
        f"{tl(locale, 'shop_account_label')} `{game_account}`\n"
    )
    desc += f"\n{tl(locale, 'shop_your_balance')} `${r2(balance):.2f} USD`\n"
    desc += f"{'✅' if remaining >= 0 else '❌'} **{tl(locale, 'shop_after_purchase')}** `${remaining:.2f} USD`"
    if remaining < 0:
        desc += f"\n\n{tl(locale, 'shop_insufficient_inline')}"
    embed = discord.Embed(title=tl(locale, 'shop_confirm_title'), description=desc, color=color)
    if game.get("icon_url"):
        embed.set_thumbnail(url=game["icon_url"])
    embed.set_footer(text=f"Package ID: {package['id']}")
    return embed


class GameSelectView(discord.ui.View):
    def __init__(self, cog, games, user_balance, locale="en"):
        super().__init__(timeout=120)
        self.cog = cog
        self.user_balance = user_balance
        self.locale = locale
        options = [discord.SelectOption(label=f"{g['emoji']}  {g['name']}", value=g["id"]) for g in games]
        select = discord.ui.Select(placeholder=tl(locale, 'shop_choose_game'), options=options)
        select.callback = self._on_select
        self.add_item(select)

    async def _on_select(self, interaction):
        game_id = interaction.data["values"][0]
        game = await get_game(game_id)
        categories = await get_categories(game_id)
        if len(categories) == 1:
            pkgs = await get_packages(game_id, categories[0])
            view = PackageSelectView(self.cog, game, categories[0], pkgs, self.user_balance, self.locale)
            embed = _package_embed(game, categories[0], pkgs, self.user_balance, self.locale)
        else:
            view = CategorySelectView(self.cog, game, categories, self.user_balance, self.locale)
            embed = discord.Embed(
                title=f"{game['emoji']} {game['name']} — {tl(self.locale, 'shop_select_type_title')}",
                description="\n".join(f"• **{c.capitalize()}**" for c in categories),
                color=discord.Color.blurple(),
            )
            if game.get("icon_url"):
                embed.set_thumbnail(url=game["icon_url"])
        await interaction.response.edit_message(embed=embed, view=view)


class CategorySelectView(discord.ui.View):
    def __init__(self, cog, game, categories, user_balance, locale="en"):
        super().__init__(timeout=120)
        self.cog = cog
        self.game = game
        self.user_balance = user_balance
        self.locale = locale
        options = [discord.SelectOption(label=c.capitalize(), value=c) for c in categories]
        select = discord.ui.Select(placeholder=tl(locale, 'shop_choose_type'), options=options)
        select.callback = self._on_select
        self.add_item(select)

    @discord.ui.button(label="◀ Back", style=discord.ButtonStyle.secondary, row=1)
    async def back_btn(self, interaction, _):
        games = await get_active_games()
        view = GameSelectView(self.cog, games, self.user_balance, self.locale)
        await interaction.response.edit_message(embed=_game_list_embed(games, self.user_balance, self.locale), view=view)

    async def _on_select(self, interaction):
        category = interaction.data["values"][0]
        pkgs = await get_packages(self.game["id"], category)
        view = PackageSelectView(self.cog, self.game, category, pkgs, self.user_balance, self.locale)
        await interaction.response.edit_message(embed=_package_embed(self.game, category, pkgs, self.user_balance, self.locale), view=view)


class PackageSelectView(discord.ui.View):
    def __init__(self, cog, game, category, packages, user_balance, locale="en"):
        super().__init__(timeout=120)
        self.cog = cog
        self.game = game
        self.category = category
        self.user_balance = user_balance
        self.locale = locale
        options = [
            discord.SelectOption(label=f"{p['name']}  —  ${p['price_usd']:.2f}", value=p["id"])
            for p in packages
        ]
        select = discord.ui.Select(placeholder=tl(locale, 'shop_choose_package'), options=options)
        select.callback = self._on_select
        self.add_item(select)

    @discord.ui.button(label="◀ Back", style=discord.ButtonStyle.secondary, row=1)
    async def back_btn(self, interaction, _):
        categories = await get_categories(self.game["id"])
        if len(categories) == 1:
            games = await get_active_games()
            view = GameSelectView(self.cog, games, self.user_balance, self.locale)
            embed = _game_list_embed(games, self.user_balance, self.locale)
        else:
            view = CategorySelectView(self.cog, self.game, categories, self.user_balance, self.locale)
            embed = discord.Embed(
                title=f"{self.game['emoji']} {self.game['name']} — {tl(self.locale, 'shop_select_type_title')}",
                description="\n".join(f"• **{c.capitalize()}**" for c in categories),
                color=discord.Color.blurple(),
            )
            if self.game.get("icon_url"):
                embed.set_thumbnail(url=self.game["icon_url"])
        await interaction.response.edit_message(embed=embed, view=view)

    async def _on_select(self, interaction):
        package_id = interaction.data["values"][0]
        package = await get_package(package_id)
        embed = discord.Embed(
            title=f"{self.game['emoji']} {self.game['name']} — 📱 Chọn nền tảng",
            description=(
                f"📦 **{package['name']}** — `${package['price_usd']:.2f}`\n\n"
                f"Chọn nền tảng bạn muốn nạp:"
            ),
            color=discord.Color.blurple(),
        )
        if self.game.get("icon_url"):
            embed.set_thumbnail(url=self.game["icon_url"])
        view = PlatformSelectView(self.cog, self.game, self.category, package, self.user_balance, self.locale)
        await interaction.response.edit_message(embed=embed, view=view)


class PlatformSelectView(discord.ui.View):
    """Chọn iOS hoặc Android sau khi chọn gói."""

    def __init__(self, cog, game, category, package, user_balance, locale="en"):
        super().__init__(timeout=120)
        self.cog = cog
        self.game = game
        self.category = category
        self.package = package
        self.user_balance = user_balance
        self.locale = locale

        pkg_platform = (package.get("platform") or "all").lower()
        ios_ok = pkg_platform in ("all", "ios")
        android_ok = pkg_platform in ("all", "android")

        ios_btn = discord.ui.Button(
            label="🍎 iOS (iCloud)" if ios_ok else "🍎 iOS — Không hỗ trợ",
            style=discord.ButtonStyle.primary if ios_ok else discord.ButtonStyle.secondary,
            disabled=not ios_ok,
            row=0,
        )
        android_btn = discord.ui.Button(
            label="🤖 Android (Google Play)" if android_ok else "🤖 Android — Không hỗ trợ",
            style=discord.ButtonStyle.success if android_ok else discord.ButtonStyle.secondary,
            disabled=not android_ok,
            row=0,
        )
        back_btn = discord.ui.Button(label="◀ Back", style=discord.ButtonStyle.secondary, row=1)

        ios_btn.callback = self._on_ios
        android_btn.callback = self._on_android
        back_btn.callback = self._on_back

        self.add_item(ios_btn)
        self.add_item(android_btn)
        self.add_item(back_btn)

    async def _on_ios(self, interaction):
        await interaction.response.send_modal(
            CredentialModal(self.cog, self.game, self.package, "ios", self.user_balance, self.locale)
        )

    async def _on_android(self, interaction):
        await interaction.response.send_modal(
            CredentialModal(self.cog, self.game, self.package, "android", self.user_balance, self.locale)
        )

    async def _on_back(self, interaction):
        pkgs = await get_packages(self.game["id"], self.category)
        view = PackageSelectView(self.cog, self.game, self.category, pkgs, self.user_balance, self.locale)
        await interaction.response.edit_message(
            embed=_package_embed(self.game, self.category, pkgs, self.user_balance, self.locale),
            view=view,
        )


class CredentialModal(discord.ui.Modal):
    """Modal nhập thông tin tài khoản iOS (iCloud) hoặc Android (Google Play)."""

    def __init__(self, cog, game, package, platform: str, user_balance: float, locale: str = "en"):
        is_ios = platform == "ios"
        title = f"{'🍎 iOS' if is_ios else '🤖 Android'} — {game['name']}"
        super().__init__(title=title)

        account_label = "iCloud Account Email" if is_ios else "Google Play Account Email"
        account_placeholder = "example@icloud.com" if is_ios else "example@gmail.com"
        password_label = "iCloud Password" if is_ios else "Google Play Password"

        self.account_input = discord.ui.TextInput(
            label=account_label, placeholder=account_placeholder, min_length=3, max_length=100
        )
        self.password_input = discord.ui.TextInput(
            label=password_label, placeholder="Nhập mật khẩu của bạn", min_length=1, max_length=100
        )
        self.add_item(self.account_input)
        self.add_item(self.password_input)

        self.cog = cog
        self.game = game
        self.package = package
        self.platform = platform
        self.user_balance = user_balance
        self.locale = locale

    async def on_submit(self, interaction: discord.Interaction):
        email = self.account_input.value.strip()
        password = self.password_input.value
        # note format parsed by _ticket_embed: "PLATFORM:ios\nPASSWORD:<pw>"
        note = f"PLATFORM:{self.platform}\nPASSWORD:{password}"
        locale = self.locale
        balance = self.user_balance
        embed = _confirm_embed(self.game, self.package, email, self.platform, balance, locale)
        view = ConfirmOrderView(self.cog, self.game, self.package, email, note, balance, locale)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


class ConfirmOrderView(discord.ui.View):
    def __init__(self, cog, game, package, game_account, note, user_balance, locale="en"):
        super().__init__(timeout=120)
        self.cog = cog
        self.game = game
        self.package = package
        self.game_account = game_account
        self.note = note
        self.user_balance = user_balance
        self.locale = locale

    @discord.ui.button(label="✅ Confirm Order", style=discord.ButtonStyle.success)
    async def confirm_btn(self, interaction, _):
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            price = self.package["price_usd"]
            ok = await db.deduct_balance(str(interaction.user.id), price)
            if not ok:
                user = await db.get_user(str(interaction.user.id))
                bal = user.get("balance", 0.0) if user else 0.0
                await interaction.followup.send(
                    tl(self.locale, 'shop_insufficient_error', bal=bal, price=price),
                    ephemeral=True,
                )
                return
            user = await db.get_or_create_user(
                str(interaction.user.id),
                str(interaction.user.display_avatar.url) if interaction.user.display_avatar else None,
            )
            order = await db.create_order(
                discord_id=str(interaction.user.id),
                user_id=user["id"],
                game_id=self.game["id"],
                package_id=self.package["id"],
                package_name=self.package["name"],
                price_usd=price,
                game_account=self.game_account,
                game_account_note=self.note,
            )

            # Tạo hoặc tái sử dụng ticket channel (1 user 1 ticket)
            ticket_channel = None
            if interaction.guild:
                from bot.cogs.admin import create_order_ticket, post_order_to_ticket
                member = interaction.guild.get_member(interaction.user.id)
                if member:
                    try:
                        # Check if user already has an open ticket channel
                        existing_ch_id = await db.get_open_ticket_channel(str(interaction.user.id))
                        if existing_ch_id:
                            existing_ch = interaction.guild.get_channel(int(existing_ch_id))
                        else:
                            existing_ch = None

                        if existing_ch:
                            # Reuse existing ticket — post new order embed there
                            await post_order_to_ticket(
                                channel=existing_ch,
                                member=member,
                                game=self.game,
                                package=self.package,
                                order=order,
                                game_account=self.game_account,
                                note=self.note,
                            )
                            ticket_channel = existing_ch
                            await db.update_order(order["id"], ticket_channel_id=str(existing_ch.id))
                        else:
                            # No open ticket — create a new one
                            ticket_channel = await create_order_ticket(
                                guild=interaction.guild,
                                member=member,
                                game=self.game,
                                package=self.package,
                                order=order,
                                game_account=self.game_account,
                                note=self.note,
                            )
                            if ticket_channel:
                                await db.update_order(order["id"], ticket_channel_id=str(ticket_channel.id))
                    except Exception as e:
                        logger.error(f"Ticket creation failed: {e}", exc_info=True)

            asyncio.create_task(self.cog._process_delivery(order, interaction))

            ticket_info = f"\n📋 Ticket: {ticket_channel.mention}" if ticket_channel else ""
            loc = self.locale
            # Parse platform from note (format: "PLATFORM:ios\nPASSWORD:xxx")
            platform_display = ""
            if self.note and self.note.startswith("PLATFORM:"):
                plat = self.note.split("\n")[0].replace("PLATFORM:", "").strip()
                platform_display = f"\n📱 **Platform:** `{_platform_label(plat)}`"
            embed = discord.Embed(
                title=tl(loc, 'shop_order_success_title'),
                description=(
                    f"{self.game['emoji']} **{self.game['name']}** — {self.package['name']}\n"
                    f"{tl(loc, 'shop_paid_label')} `${price:.2f} USD`"
                    + platform_display
                    + f"\n{tl(loc, 'shop_account_label')} `{self.game_account}`"
                    + f"\n\n🔖 Order ID: `{self.package['id']}#{order['id']}`"
                    + ticket_info
                ),
                color=discord.Color.green(),
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as exc:
            logger.error(f"ConfirmOrderView error: {exc}", exc_info=True)
            await interaction.followup.send(tl(self.locale, 'shop_error'), ephemeral=True)

    @discord.ui.button(label="✖ Cancel", style=discord.ButtonStyle.secondary)
    async def cancel_btn(self, interaction, _):
        loc = get_locale(interaction.user)
        await interaction.response.edit_message(
            embed=discord.Embed(
                title=tl(loc, 'shop_cancelled_title'),
                description=tl(loc, 'shop_cancelled_desc'),
                color=discord.Color.greyple(),
            ),
            view=None,
        )


class ShopCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def _process_delivery(self, order, interaction):
        success, note = await deliver(order)
        if success:
            await db.update_order(order["id"], status="completed", delivery_note=note)
            try:
                embed = discord.Embed(
                    title="🎉 Delivered!",
                    description=(
                        f"Order `{order['package_id']}#{order['id']}` delivered!\n"
                        f"📦 {order['package_name']} → `{order['game_account']}`\n"
                        f"📝 {note}"
                    ),
                    color=discord.Color.green(),
                )
                await interaction.user.send(embed=embed)
            except discord.Forbidden:
                pass
        else:
            # No auto-handler — leave as pending for dashboard staff to pick up
            await db.update_order(order["id"], status="pending", delivery_note=note)
            logger.info(f"Order #{order['id']} ({order['package_id']}) queued for manual delivery: {note}")

    @app_commands.command(name="buy", description="Purchase a game top-up package using your balance.")
    @require_main_guild()
    async def buy(self, interaction):
        locale = get_locale(interaction.user)
        user = await db.get_user(str(interaction.user.id))
        if not user:
            await interaction.response.send_message(tl(locale, 'shop_no_account'), ephemeral=True)
            return
        balance = user.get("balance", 0.0)
        games = await get_active_games()
        if not games:
            await interaction.response.send_message(tl(locale, 'shop_no_games'), ephemeral=True)
            return
        view = GameSelectView(self, games, balance, locale)
        await interaction.response.send_message(embed=_game_list_embed(games, balance, locale), view=view, ephemeral=True)

    @app_commands.command(name="orders", description="View your recent purchase orders.")
    @require_main_guild()
    async def orders(self, interaction):
        rows = await db.get_user_orders(str(interaction.user.id), limit=10)
        if not rows:
            await interaction.response.send_message("📭 You have no orders yet.", ephemeral=True)
            return
        lines = [
            f"{ORDER_STATUS_ICON.get(o['status'], '❓')} `{o['package_id']}#{o['id']}` — **{o['package_name']}** `${o['price_usd']:.2f}` — {str(o.get('created_at',''))[:10]}"
            for o in rows
        ]
        embed = discord.Embed(title="🧾 Your Orders", description="\n".join(lines), color=discord.Color.blurple())
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot):
    await bot.add_cog(ShopCog(bot))