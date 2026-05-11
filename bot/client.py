import logging
import random

import discord
from discord import app_commands
from discord.ext import commands, tasks

from config import DISCORD_GUILD_ID

logger = logging.getLogger(__name__)

# ── Require-main-guild error sentinel ────────────────────────────────────────
class NotInMainGuild(app_commands.CheckFailure):
    pass


def require_main_guild():
    """app_commands check: user phải là member của DISCORD_GUILD_ID."""
    async def predicate(interaction: discord.Interaction) -> bool:
        if not DISCORD_GUILD_ID:
            return True
        guild = interaction.client.get_guild(DISCORD_GUILD_ID)
        if guild and guild.get_member(interaction.user.id):
            return True
        raise NotInMainGuild()
    return app_commands.check(predicate)


class TopUpBot(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True          # cần để đọc danh sách member
        super().__init__(
            command_prefix="!",
            intents=intents,
            description="TopUpFast Bot",
        )
        self._main_invite: str | None = None   # cache link invite vĩnh viễn

    async def setup_hook(self) -> None:
        await self.load_extension("bot.cogs.topup")
        await self.load_extension("bot.cogs.shop")
        await self.load_extension("bot.cogs.admin")

        # Khôi phục persistent views sau khi restart
        from bot.cogs.admin import GameTopUpView
        from products import get_active_games
        games = await get_active_games()
        for game in (games or []):
            self.add_view(GameTopUpView(game["id"]))

        if DISCORD_GUILD_ID:
            guild = discord.Object(id=DISCORD_GUILD_ID)
            self.tree.copy_global_to(guild=guild)
            try:
                await self.tree.sync(guild=guild)
                logger.info(f"Slash commands synced to guild {DISCORD_GUILD_ID}")
            except discord.errors.Forbidden:
                logger.warning(
                    f"Cannot sync to guild {DISCORD_GUILD_ID} (bot not in guild or missing access). "
                    "Falling back to global sync (may take up to 1 hour)."
                )
                await self.tree.sync()
        else:
            await self.tree.sync()
            logger.info("Slash commands synced globally (may take up to 1 hour)")

        # ── Global error handler: chặn user không ở server chính ─────────────
        @self.tree.error
        async def on_app_command_error(
            interaction: discord.Interaction,
            error: app_commands.AppCommandError,
        ) -> None:
            if isinstance(error, NotInMainGuild):
                invite = await self.get_main_invite()
                embed = discord.Embed(
                    title="🚫 Bạn chưa vào server chính!",
                    description=(
                        "Bạn cần tham gia server chính của TopUpFast để dùng lệnh này.\n\n"
                        f"🔗 **Join ngay:** {invite}\n\n"
                        "---\n"
                        "You need to join the main TopUpFast server to use this command.\n\n"
                        f"🔗 **Join here:** {invite}"
                    ),
                    color=discord.Color.red(),
                )
                try:
                    await interaction.response.send_message(embed=embed, ephemeral=True)
                except discord.InteractionResponded:
                    await interaction.followup.send(embed=embed, ephemeral=True)
                return
            # Các lỗi khác — log lại và báo user
            logger.error(f"App command error [{interaction.command}]: {error}")
            msg = "❌ Có lỗi xảy ra. Vui lòng thử lại sau."
            try:
                await interaction.response.send_message(msg, ephemeral=True)
            except discord.InteractionResponded:
                await interaction.followup.send(msg, ephemeral=True)

    async def get_main_invite(self) -> str:
        """Trả về link invite vĩnh viễn vào server chính (tạo 1 lần, cache mãi)."""
        if self._main_invite:
            return self._main_invite
        guild = self.get_guild(DISCORD_GUILD_ID) if DISCORD_GUILD_ID else None
        if guild is None:
            return "https://discord.gg/topupfast"
        # Dùng channel đầu tiên có thể tạo invite
        for channel in guild.text_channels:
            try:
                inv = await channel.create_invite(max_age=0, max_uses=0, unique=False, reason="Auto-invite for non-members")
                self._main_invite = inv.url
                logger.info(f"Created permanent invite: {inv.url}")
                return self._main_invite
            except discord.Forbidden:
                continue
        return "https://discord.gg/topupfast"

    async def on_ready(self) -> None:
        logger.info(f"Đăng nhập: {self.user} (ID: {self.user.id})")
        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name="💰 /deposit to deposit money ",
            )
        )
        await self._sync_members()
        if not self._reminder_task.is_running():
            self._reminder_task.start()
        if not self._commission_payout_task.is_running():
            self._commission_payout_task.start()

    @tasks.loop(hours=6)
    async def _reminder_task(self) -> None:
        """Gửi tin nhắn nhắc nhở nạp game định kỳ vào general channel."""
        from config import GENERAL_CHANNEL_ID
        if not GENERAL_CHANNEL_ID:
            return
        channel = self.get_channel(GENERAL_CHANNEL_ID)
        if not channel:
            return

        games = []
        try:
            from products import get_active_games
            games = await get_active_games()
        except Exception:
            pass

        # Pick 3 random games, show rest as "+ X more"
        if games:
            sample = random.sample(games, min(3, len(games)))
            lines = [f"• {g['emoji']} {g['name']}" for g in sample]
            remaining = len(games) - len(sample)
            if remaining > 0:
                lines.append(f"• _...and {remaining} more_")
            game_block = "\n".join(lines)
        else:
            game_block = ""

        _FOUNDER_ROLE = 1498363612245262487
        vi_desc = (
            "🎮 **Hỗ trợ nạp game 24/7!**\n"
            "Dùng lệnh `/buy` để nạp ngay.\n"
            + (f"\n**Danh sách game:**\n{game_block}" if game_block else "")
            + f"\n💬 Muốn thêm game mới? Ping <@&{_FOUNDER_ROLE}> để yêu cầu!"
        )
        en_desc = (
            "🎮 **Top up your games 24/7!**\n"
            "Use `/buy` to top up instantly.\n"
            + (f"\n**Available games:**\n{game_block}" if game_block else "")
            + f"\n💬 Want a new game added? Ping <@&{_FOUNDER_ROLE}> to request!"
        )

        _MESSAGES_VI = [
            vi_desc,
            "💰 **Còn số dư chưa dùng?**\nHãy nạp game ngay với `/buy`!\n"
            + (f"\n{game_block}" if game_block else "")
            + f"\n💬 Muốn thêm game? Ping <@&{_FOUNDER_ROLE}>!",
        ]
        _MESSAGES_EN = [
            en_desc,
            "💰 **Got unused balance?**\nTop up a game now with `/buy`!\n"
            + (f"\n{game_block}" if game_block else "")
            + f"\n💬 Want more games? Ping <@&{_FOUNDER_ROLE}>!",
        ]

        idx = random.randrange(len(_MESSAGES_VI))

        embed_vi = discord.Embed(description=_MESSAGES_VI[idx], color=discord.Color.gold())
        embed_vi.set_footer(text="TopUpFast — Nạp nhanh, giá tốt 🚀")

        embed_en = discord.Embed(description=_MESSAGES_EN[idx], color=discord.Color.gold())
        embed_en.set_footer(text="TopUpFast — Fast top-up, best price 🚀")

        try:
            await channel.send(embeds=[embed_vi, embed_en])
        except Exception as exc:
            logger.warning(f"Reminder task failed to send: {exc}")

    @_reminder_task.before_loop
    async def _before_reminder(self) -> None:
        await self.wait_until_ready()

    @tasks.loop(minutes=30)
    async def _commission_payout_task(self) -> None:
        """
        Mỗi 30 phút: kiểm tra các đơn completed đã qua 24h mà chưa trả hoa hồng.
        Nếu không có khiếu nại → cộng 50% giá trị đơn vào tài khoản staff.
        """
        import services.database as db
        from services.database import COMMISSION_RATE
        try:
            orders = await db.get_orders_pending_commission()
            for order in orders:
                staff_discord_id = order.get("completed_by")
                if not staff_discord_id:
                    # Mark as paid to avoid retrying orders without a staff member
                    await db.mark_commission_paid(order["id"], 0)
                    continue
                commission = round(float(order.get("price_usd", 0)) * COMMISSION_RATE, 2)
                await db.add_staff_commission(staff_discord_id, commission)
                await db.mark_commission_paid(order["id"], commission)
                logger.info(
                    f"Commission ${commission:.2f} paid to staff {staff_discord_id} "
                    f"for order #{order['id']}"
                )
        except Exception:
            logger.exception("Commission payout task error")

    @_commission_payout_task.before_loop
    async def _before_commission(self) -> None:
        await self.wait_until_ready()

    async def _sync_members(self) -> None:
        """Thêm toàn bộ member của guild chính + các guild sync phụ vào DB (trừ bot).
        Dùng batch upsert (100 user/request) + semaphore để tránh spam Supabase.
        Tự retry khi gặp lỗi mạng/rate limit.
        """
        import asyncio
        import services.database as db
        from config import EXTRA_SYNC_GUILD_IDS

        guild_ids = ([DISCORD_GUILD_ID] if DISCORD_GUILD_ID else []) + EXTRA_SYNC_GUILD_IDS
        sem = asyncio.Semaphore(5)   # tối đa 5 batch request Supabase đồng thời
        BATCH = 100                  # số user mỗi request
        RETRY = 5                    # số lần retry tối đa

        async def upsert_batch(batch: list[dict], default_lang: str = "vi", attempt: int = 0) -> None:
            try:
                async with sem:
                    await db.upsert_users_bulk(batch, default_language=default_lang)
            except Exception as exc:
                if attempt >= RETRY:
                    logger.error(f"_sync_members: batch thất bại sau {RETRY} lần retry: {exc}")
                    return
                wait = 2 ** attempt   # 1, 2, 4, 8, 16 giây
                logger.warning(f"_sync_members: batch lỗi ({exc}), retry sau {wait}s...")
                await asyncio.sleep(wait)
                await upsert_batch(batch, attempt + 1)

        total = 0
        for gid in guild_ids:
            guild = self.get_guild(gid)
            if guild is None:
                logger.warning(f"_sync_members: guild {gid} not found (bot not in guild?).")
                continue

            is_extra = (gid != DISCORD_GUILD_ID)
            default_lang = "en" if is_extra else "vi"

            # Thu thập members trước, không yield dữ liệu khi đang upsert
            members: list[dict] = []
            async for member in guild.fetch_members(limit=None):
                if member.bot:
                    continue
                members.append({
                    "discord_id": str(member.id),
                    "avatar_url": str(member.display_avatar.url) if member.display_avatar else None,
                })

            # Tạo task upsert theo từng batch 100
            tasks = [
                upsert_batch(members[i : i + BATCH], default_lang)
                for i in range(0, len(members), BATCH)
            ]
            await asyncio.gather(*tasks)
            logger.info(f"Đồng bộ {len(members)} member từ guild {guild.name} ({gid}) [{default_lang}] vào database.")
            total += len(members)

        logger.info(f"Tổng đồng bộ: {total} member.")

    async def on_member_join(self, member: discord.Member) -> None:
        """Auto-add new member to DB, sync roles from old server, and send welcome message."""
        if member.bot:
            return
        import services.database as db
        from config import WELCOME_CHANNEL_ID, RULES_CHANNEL_ID, VERIFY_CHANNEL_ID, OLD_GUILD_ID, EXTRA_SYNC_GUILD_IDS
        from bot.i18n import ROLE_VN, ROLE_EN, ROLE_FOUNDER
        avatar = str(member.display_avatar.url) if member.display_avatar else None
        # Members join server phụ → default language = 'en'
        default_lang = "en" if member.guild.id in EXTRA_SYNC_GUILD_IDS else "vi"
        await db.get_or_create_user(str(member.id), avatar, default_language=default_lang)
        logger.info(f"New member: {member} ({member.id}) guild={member.guild.id} lang={default_lang} added to DB.")

        # ── Role sync from old server ────────────────────────────────────────
        # Role mapping: old role ID → name to find in new server
        _SYNC_ROLES = {
            1498363612115243105: "customer",  # Customer
            ROLE_VN:             "lang-vn",   # Language VN
            ROLE_EN:             "lang-en",   # Language EN
        }
        if OLD_GUILD_ID:
            old_guild = self.get_guild(OLD_GUILD_ID)
            if old_guild:
                old_member = old_guild.get_member(member.id)
                if old_member:
                    old_role_ids = {r.id for r in old_member.roles}
                    roles_to_add = []
                    for old_role_id, role_name in _SYNC_ROLES.items():
                        if old_role_id in old_role_ids:
                            # Find matching role in NEW server by name (case-insensitive)
                            new_role = discord.utils.find(
                                lambda r, n=role_name: r.name.lower() == n.lower(),
                                member.guild.roles,
                            )
                            if new_role:
                                roles_to_add.append(new_role)
                    if roles_to_add:
                        try:
                            await member.add_roles(*roles_to_add, reason="Role sync from old server")
                            logger.info(
                                f"Synced roles {[r.name for r in roles_to_add]} "
                                f"to {member} in new server."
                            )
                        except discord.Forbidden:
                            logger.warning(f"Missing permissions to assign roles to {member}.")

        if WELCOME_CHANNEL_ID:
            channel = self.get_channel(WELCOME_CHANNEL_ID)
            if channel:
                rules_mention = f"<#{RULES_CHANNEL_ID}>" if RULES_CHANNEL_ID else "#rules"
                verify_mention = f"<#{VERIFY_CHANNEL_ID}>" if VERIFY_CHANNEL_ID else "#verify"
                embed = discord.Embed(
                    title="👋 Welcome to the server!",
                    description=(
                        f"Hey {member.mention}, welcome to **{member.guild.name}**!\n\n"
                        f"📖 Please read the rules in {rules_mention}\n"
                        f"✅ Then head over to {verify_mention} to verify and unlock all channels."
                    ),
                    color=discord.Color.green(),
                )
                embed.set_thumbnail(url=member.display_avatar.url if member.display_avatar else None)
                embed.set_footer(text=f"Member #{member.guild.member_count}")
                await channel.send(embed=embed)

    async def on_member_remove(self, member: discord.Member) -> None:
        """Send message when a member leaves."""
        if member.bot:
            return
        from config import WELCOME_CHANNEL_ID
        if WELCOME_CHANNEL_ID:
            channel = self.get_channel(WELCOME_CHANNEL_ID)
            if channel:
                embed = discord.Embed(
                    title="👋 Member Left",
                    description=f"**{member.name}** has left the server.",
                    color=discord.Color.red(),
                )
                embed.set_thumbnail(url=member.display_avatar.url if member.display_avatar else None)
                await channel.send(embed=embed)
