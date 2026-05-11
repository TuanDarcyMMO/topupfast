"""Admin cog - lệnh quản trị bot."""

import hashlib
import json
import logging
import re

import discord
from discord import app_commands
from discord.ext import commands

import services.database as db
from bot.i18n import t, tl, get_locale, ROLE_FOUNDER
from products import get_active_games, get_game, get_categories, get_packages, get_package

logger = logging.getLogger(__name__)

# ─── Custom ID format: "buy_game:{game_id}" ──────────────────────────────────
_CUSTOM_ID_PREFIX = "buy_game:"
_TICKET_CATEGORY  = "📋 Đơn Nạp"
_CLOSE_CUSTOM_ID  = "close_ticket"
_RULES_CHANNEL_ID = 1498368083499159694


def _ticket_embed(game: dict, package: dict, order: dict, game_account: str, note: str) -> discord.Embed:
    """Embed chi tiết đơn hàng gửi vào ticket channel."""
    parts = package["id"].split(".")
    pkg_display = " › ".join(p.capitalize() for p in parts)
    embed = discord.Embed(
        title=f"🎫 Đơn #{order['id']} — {game['name']}",
        description=f"📦 **{pkg_display}**",
        color=discord.Color.gold(),
    )
    embed.add_field(name="💵 Giá",        value=f"`${package['price_usd']:.2f} USD`", inline=True)
    embed.add_field(name="📱 Platform",   value="🍎 iOS only" if game.get("platform") == "ios" else "📱 iOS & Android", inline=True)
    embed.add_field(name="👤 Tài khoản",  value=f"`{game_account}`",  inline=False)
    if note:
        embed.add_field(name="📝 Ghi chú", value=note, inline=False)
    embed.add_field(name="🔖 Order ID",   value=f"`{package['id']}#{order['id']}`", inline=False)
    embed.add_field(name="⏳ Trạng thái", value="Đang chờ xử lý", inline=False)
    if game.get("icon_url"):
        embed.set_thumbnail(url=game["icon_url"])
    embed.set_footer(text="Admin sẽ xử lý đơn sớm nhất có thể.")
    return embed


async def create_order_ticket(
    guild: discord.Guild,
    member: discord.Member,
    game: dict,
    package: dict,
    order: dict,
    game_account: str,
    note: str,
) -> discord.TextChannel:
    """Tạo ticket channel cho đơn hàng, add user vào, gửi embed."""
    # Tìm hoặc tạo category ticket
    cat = discord.utils.get(guild.categories, name=_TICKET_CATEGORY)
    if not cat:
        cat = await guild.create_category(_TICKET_CATEGORY)

    # Tên channel: {game_id}-{tier}-{username}
    parts = package["id"].split(".")
    tier = parts[-1] if parts else package["id"]
    raw = f"{game['id']}-{tier}-{member.name}"
    ch_name = re.sub(r"[^\w-]", "", raw.lower())[:80].strip("-")

    # Permissions: chỉ user + bot thấy (admin role thấy nếu có)
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(read_messages=False),
        member:             discord.PermissionOverwrite(read_messages=True, send_messages=True),
        guild.me:           discord.PermissionOverwrite(read_messages=True, send_messages=True, manage_channels=True),
    }

    channel = await guild.create_text_channel(
        name=ch_name,
        category=cat,
        overwrites=overwrites,
        topic=f"Order #{order['id']} | {member.display_name} | {package['name']}",
    )

    embed = _ticket_embed(game, package, order, game_account, note)
    view  = CloseTicketView()
    msg   = await channel.send(
        content=t(member, 'ticket_mention', mention=member.mention),
        embed=embed,
        view=view,
    )
    await msg.pin()
    return channel


async def post_order_to_ticket(
    channel: discord.TextChannel,
    member: discord.Member,
    game: dict,
    package: dict,
    order: dict,
    game_account: str,
    note: str,
) -> None:
    """Post a new order embed into an existing ticket channel (reuse flow)."""
    # Update channel topic to reflect latest order
    try:
        await channel.edit(topic=f"Order #{order['id']} | {member.display_name} | {package['name']}")
    except Exception:
        pass
    embed = _ticket_embed(game, package, order, game_account, note)
    await channel.send(
        content=f"{member.mention} New order added to your ticket!",
        embed=embed,
    )


class CloseTicketView(discord.ui.View):
    """Nút đóng ticket — persistent, admin dùng."""

    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="🔒 Close Ticket",
        style=discord.ButtonStyle.danger,
        custom_id=_CLOSE_CUSTOM_ID,
    )
    async def close_btn(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not interaction.user.guild_permissions.manage_channels:
            await interaction.response.send_message(t(interaction.user, 'close_ticket_no_perm'), ephemeral=True)
            return
        await interaction.response.send_message("🔒 Closing ticket...")
        await interaction.channel.delete(reason=f"Ticket closed by {interaction.user}")


def _safe_channel_name(name: str) -> str:
    """Chuyển tên game thành tên channel hợp lệ (lowercase, hyphens)."""
    name = name.lower()
    name = re.sub(r"[^\w\s-]", "", name)   # bỏ ký tự đặc biệt
    name = re.sub(r"[\s_]+", "-", name)     # space/underscore → hyphen
    name = re.sub(r"-+", "-", name).strip("-")
    return name[:100]


def _game_channel_embed(game: dict, packages: list[dict], locale: str = "en") -> discord.Embed:
    """Embed hiển thị trong channel của từng game, bao gồm bảng giá."""
    platform_text = "🍎 iOS only" if game.get("platform") == "ios" else "📱 iOS & Android"

    embed = discord.Embed(
        title=f"{game['emoji']}  {game['name']}",
        description=(
            f"**Platform:** {platform_text}\n\n"
            + tl(locale, 'game_channel_desc')
        ),
        color=discord.Color.blurple(),
    )

    # Group packages by category and add as fields
    categories: dict[str, list[dict]] = {}
    for pkg in packages:
        cat = pkg.get("category", "packages")
        categories.setdefault(cat, []).append(pkg)

    for cat, pkgs in categories.items():
        lines = [f"`${p['price_usd']:.2f}` — {p['name']}" for p in pkgs]
        embed.add_field(
            name=f"📦 {cat.capitalize()}",
            value="\n".join(lines),
            inline=True,
        )

    if game.get("icon_url"):
        embed.set_image(url=game["icon_url"])
    embed.set_footer(text=f"ID: {game['id']}  •  {tl(locale, 'game_channel_footer')}")
    return embed


def _embed_fingerprint(game: dict, packages: list[dict]) -> str:
    """Hash để so sánh embed có thay đổi không (icon, packages, prices)."""
    data = {
        "icon_url": game.get("icon_url"),
        "name": game.get("name"),
        "emoji": game.get("emoji"),
        "platform": game.get("platform"),
        "packages": sorted(
            [{"id": p["id"], "name": p["name"], "price_usd": p["price_usd"]} for p in packages],
            key=lambda x: x["id"],
        ),
    }
    return hashlib.md5(json.dumps(data, sort_keys=True).encode()).hexdigest()[:12]


async def _channel_needs_update(channel: discord.TextChannel, fingerprint: str) -> bool:
    """
    Kiểm tra channel có cần cập nhật không bằng cách đọc footer của tin nhắn được ghim.
    Footer format: "ID: {game_id}  •  ...  [fp:{hash}]"
    """
    try:
        pins = await channel.pins()
        for msg in pins:
            if msg.author.bot and msg.embeds:
                footer = msg.embeds[0].footer.text or ""
                if f"[fp:{fingerprint}]" in footer:
                    return False  # đã đúng fingerprint → không cần cập nhật
        return True  # không tìm thấy hoặc fingerprint khác
    except Exception:
        return True


async def _wipe_and_resend(
    channel: discord.TextChannel,
    game: dict,
    packages: list[dict],
    locale: str,
) -> None:
    """Xóa toàn bộ tin nhắn trong channel và gửi lại embed mới."""
    # Unpin all first
    try:
        pins = await channel.pins()
        for pin in pins:
            try:
                await pin.unpin()
            except Exception:
                pass
    except Exception:
        pass
    # Delete all messages
    try:
        await channel.purge(limit=200)
    except discord.Forbidden:
        pass
    # Send fresh embed
    fp = _embed_fingerprint(game, packages)
    embed = _game_channel_embed(game, packages, locale)
    # Embed footer includes fingerprint for future diff checks
    embed.set_footer(text=f"ID: {game['id']}  •  {tl(locale, 'game_channel_footer')}  [fp:{fp}]")
    view = GameTopUpView(game["id"])
    msg = await channel.send(embed=embed, view=view)
    await msg.pin()


async def _dedup_channel(channel: discord.TextChannel) -> int:
    """Xóa tin nhắn trùng (cùng embed title) và unpin trùng. Trả về số tin đã xóa."""
    deleted = 0
    try:
        seen_titles: set[str] = set()
        pins = await channel.pins()
        pin_ids = {p.id for p in pins}

        # Unpin duplicates first
        seen_pin_titles: set[str] = set()
        for pin in pins:
            title = pin.embeds[0].title if pin.embeds else str(pin.id)
            if title in seen_pin_titles:
                try:
                    await pin.unpin()
                except Exception:
                    pass
            else:
                seen_pin_titles.add(title)

        # Delete duplicate messages (keep first occurrence)
        msgs_to_delete = []
        async for msg in channel.history(limit=200, oldest_first=True):
            title = msg.embeds[0].title if msg.embeds else None
            key = title or str(msg.id)
            if title and title in seen_titles and msg.id not in pin_ids:
                msgs_to_delete.append(msg)
            elif title:
                seen_titles.add(title)

        for msg in msgs_to_delete:
            try:
                await msg.delete()
                deleted += 1
            except Exception:
                pass
    except Exception:
        pass
    return deleted


class GameTopUpView(discord.ui.View):
    """Persistent view gắn vào mỗi channel game."""

    def __init__(self, game_id: str) -> None:
        super().__init__(timeout=None)   # persistent — không expire
        self.game_id = game_id
        # custom_id cố định để Discord nhận lại sau restart
        btn = discord.ui.Button(
            label="🛒  Top Up Now",
            style=discord.ButtonStyle.success,
            custom_id=f"{_CUSTOM_ID_PREFIX}{game_id}",
        )
        btn.callback = self._on_click
        self.add_item(btn)

    async def _on_click(self, interaction: discord.Interaction) -> None:
        from bot.cogs.shop import (
            PackageSelectView, CategorySelectView, _package_embed, _game_list_embed, GameSelectView
        )
        from bot.i18n import get_locale, tl
        locale = get_locale(interaction.user)

        try:
            user = await db.get_user(str(interaction.user.id))
            if not user:
                await interaction.response.send_message(
                    tl(locale, 'topup_no_account'),
                    ephemeral=True,
                )
                return

            balance = user.get("balance", 0.0)
            if balance <= 0:
                await interaction.response.send_message(
                    tl(locale, 'topup_zero_balance'),
                    ephemeral=True,
                )
                return
            game = await get_game(self.game_id)
            if not game or not game.get("active"):
                await interaction.response.send_message(tl(locale, 'topup_game_unavailable'), ephemeral=True)
                return

            categories = await get_categories(self.game_id)
            if not categories:
                await interaction.response.send_message(
                    "⚠️ Game này hiện chưa có gói nạp. Vui lòng ping Admin để thêm gói!" if locale == "vi"
                    else "⚠️ This game has no packages yet. Please ping an Admin to add packages!",
                    ephemeral=True,
                )
                return

            # Skip game selection — go straight to category / package
            if len(categories) == 1:
                pkgs = await get_packages(self.game_id, categories[0])
                view = PackageSelectView(None, game, categories[0], pkgs, balance, locale)
                embed = _package_embed(game, categories[0], pkgs, balance, locale)
            else:
                view = CategorySelectView(None, game, categories, balance, locale)
                embed = discord.Embed(
                    title=f"{game['emoji']} {game['name']} — {tl(locale, 'topup_select_type')}",
                    description="\n".join(f"• **{c.capitalize()}**" for c in categories),
                    color=discord.Color.blurple(),
                )
                if game.get("icon_url"):
                    embed.set_thumbnail(url=game["icon_url"])

            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

        except Exception as exc:
            logger.error(f"GameTopUpView._on_click error: {exc}", exc_info=True)
            msg = "❌ Đã xảy ra lỗi, vui lòng thử lại sau." if locale == "vi" else "❌ An error occurred, please try again later."
            try:
                await interaction.response.send_message(msg, ephemeral=True)
            except discord.InteractionResponded:
                await interaction.followup.send(msg, ephemeral=True)


class AdminCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ─── /setup-shop ─────────────────────────────────────────────────────────

    @app_commands.command(
        name="setup-shop",
        description="[Founder] Tạo/cập nhật channel cho từng game (smart diff — chỉ gửi lại nếu có thay đổi).",
    )
    @app_commands.describe(category_name="Tên category (default: 🎮 Nap Game)")
    @app_commands.checks.has_role(ROLE_FOUNDER)
    async def setup_shop(
        self,
        interaction: discord.Interaction,
        category_name: str = "🎮 Nap Game",
    ) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        locale = get_locale(interaction.user)
        guild = interaction.guild

        games = await get_active_games()
        if not games:
            await interaction.followup.send(tl(locale, 'admin_no_games'), ephemeral=True)
            return

        category = discord.utils.get(guild.categories, name=category_name)
        if not category:
            category = await guild.create_category(category_name)
            logger.info(f"Created category: {category_name}")

        created, updated, skipped = [], [], []

        for game in games:
            ch_name = _safe_channel_name(game["name"])
            channel = discord.utils.get(category.channels, name=ch_name)

            # Fetch packages for diff
            cats = await get_categories(game["id"])
            packages: list[dict] = []
            for cat in cats:
                packages.extend(await get_packages(game["id"], cat))

            fp = _embed_fingerprint(game, packages)

            if channel is None:
                # New game — create channel
                channel = await guild.create_text_channel(
                    name=ch_name,
                    category=category,
                    topic=f"{game['name']} | {game.get('platform','all')}",
                )
                await _wipe_and_resend(channel, game, packages, locale)
                created.append(game["name"])
                logger.info(f"Created #{ch_name} for {game['id']}")
            else:
                needs = await _channel_needs_update(channel, fp)
                if needs:
                    await _wipe_and_resend(channel, game, packages, locale)
                    updated.append(game["name"])
                    logger.info(f"Updated #{ch_name} for {game['id']}")
                else:
                    skipped.append(game["name"])

        # Sort channels alphabetically in ONE API call (avoids rate-limit spam)
        sorted_channels = sorted(category.channels, key=lambda c: c.name.lower())
        if sorted_channels:
            try:
                await guild._state.http.bulk_channel_update(
                    guild.id,
                    [{"id": ch.id, "position": pos} for pos, ch in enumerate(sorted_channels)],
                )
            except discord.HTTPException:
                pass

        lines = []
        if created:
            lines.append(f"✅ Tạo mới ({len(created)}): " + ", ".join(created))
        if updated:
            lines.append(f"🔄 Cập nhật ({len(updated)}): " + ", ".join(updated))
        if skipped:
            lines.append(f"⏭️ Bỏ qua — không đổi ({len(skipped)}): " + ", ".join(skipped))

        await interaction.followup.send(
            f"**Setup Shop** — `{category_name}`\n" + "\n".join(lines),
            ephemeral=True,
        )

    # ─── /refresh-shop ───────────────────────────────────────────────────────

    @app_commands.command(
        name="refresh-shop",
        description="[Founder] Dọn dẹp trùng lặp và cập nhật embed trong category game.",
    )
    @app_commands.describe(category_name="Tên category chứa các channel game")
    @app_commands.checks.has_role(ROLE_FOUNDER)
    async def refresh_shop(
        self,
        interaction: discord.Interaction,
        category_name: str = "🎮 Nap Game",
    ) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        locale = get_locale(interaction.user)
        guild = interaction.guild

        category = discord.utils.get(guild.categories, name=category_name)
        if not category:
            await interaction.followup.send(tl(locale, 'admin_no_category', name=category_name), ephemeral=True)
            return

        games = await get_active_games()
        game_by_ch: dict[str, dict] = {_safe_channel_name(g["name"]): g for g in games}

        updated, deduped, skipped = [], [], []
        total_dupes = 0

        for channel in list(category.channels):
            if not isinstance(channel, discord.TextChannel):
                continue

            # Dedup messages/pins regardless
            removed = await _dedup_channel(channel)
            if removed:
                total_dupes += removed
                deduped.append(f"{channel.name}(-{removed})")

            game = game_by_ch.get(channel.name)
            if game is None:
                # Channel doesn't match any active game — skip update
                skipped.append(channel.name)
                continue

            # Fetch packages for diff
            cats = await get_categories(game["id"])
            packages: list[dict] = []
            for cat in cats:
                packages.extend(await get_packages(game["id"], cat))

            fp = _embed_fingerprint(game, packages)
            needs = await _channel_needs_update(channel, fp)
            if needs:
                await _wipe_and_resend(channel, game, packages, locale)
                updated.append(game["name"])
            else:
                skipped.append(game["name"])

        # Sort channels alphabetically in ONE API call (avoids rate-limit spam)
        sorted_channels = sorted(category.channels, key=lambda c: c.name.lower())
        if sorted_channels:
            try:
                await guild._state.http.bulk_channel_update(
                    guild.id,
                    [{"id": ch.id, "position": pos} for pos, ch in enumerate(sorted_channels)],
                )
            except discord.HTTPException:
                pass

        lines = []
        if updated:
            lines.append(f"🔄 Cập nhật ({len(updated)}): " + ", ".join(updated))
        if total_dupes:
            lines.append(f"🧹 Xóa tin trùng: {total_dupes} tin nhắn (" + ", ".join(deduped) + ")")
        if not updated and not total_dupes:
            lines.append("✅ Tất cả channel đã cập nhật, không cần thay đổi.")

        await interaction.followup.send(
            f"**Refresh Shop** — `{category_name}`\n" + "\n".join(lines),
            ephemeral=True,
        )

    # ─── /delete-channels ────────────────────────────────────────────────────

    @app_commands.command(
        name="delete-channels",
        description="[Founder] Xóa 1 kênh hoặc toàn bộ kênh trong 1 category.",
    )
    @app_commands.describe(
        category_name="Tên category cần xóa tất cả kênh (để trống nếu dùng category_id hoặc channel_id)",
        category_id="ID của category (ưu tiên hơn tên nếu nhập)",
        channel_id="ID của 1 kênh cụ thể cần xóa (không bắt buộc)",
    )
    @app_commands.checks.has_role(ROLE_FOUNDER)
    async def delete_channels(
        self,
        interaction: discord.Interaction,
        category_name: str = "",
        category_id: str = "",
        channel_id: str = "",
    ) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        guild = interaction.guild

        # Delete single channel
        if channel_id:
            ch = guild.get_channel(int(channel_id))
            if ch is None:
                await interaction.followup.send(f"❌ Không tìm thấy kênh ID `{channel_id}`.", ephemeral=True)
                return
            await ch.delete(reason=f"Deleted by {interaction.user}")
            await interaction.followup.send(f"🗑️ Đã xóa kênh `{ch.name}`.", ephemeral=True)
            return

        # Find category
        cat = None
        if category_id:
            cat = guild.get_channel(int(category_id))
        elif category_name:
            cat = discord.utils.get(guild.categories, name=category_name)

        if cat is None or not isinstance(cat, discord.CategoryChannel):
            await interaction.followup.send(
                "❌ Không tìm thấy category. Nhập `category_name` hoặc `category_id`.",
                ephemeral=True,
            )
            return

        channels = list(cat.channels)
        count = 0
        for ch in channels:
            try:
                await ch.delete(reason=f"Bulk delete by {interaction.user}")
                count += 1
            except Exception as e:
                logger.warning(f"Could not delete {ch.name}: {e}")

        # Optionally delete the category itself if now empty
        try:
            await cat.delete(reason=f"Category emptied by {interaction.user}")
        except Exception:
            pass

        await interaction.followup.send(
            f"🗑️ Đã xóa **{count}** kênh trong category `{cat.name}`.",
            ephemeral=True,
        )

    # ─── /setup-rules ────────────────────────────────────────────────────────

    @app_commands.command(
        name="setup-rules",
        description="[Founder] Clear and re-post server rules in the rules channel.",
    )
    @app_commands.checks.has_role(ROLE_FOUNDER)
    async def setup_rules(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        locale = get_locale(interaction.user)
        guild = interaction.guild

        channel = guild.get_channel(_RULES_CHANNEL_ID)
        if channel is None:
            await interaction.followup.send(
                "❌ Rules channel not found. Check `_RULES_CHANNEL_ID` in admin.py.",
                ephemeral=True,
            )
            return

        # Purge existing messages
        try:
            await channel.purge(limit=50)
        except discord.Forbidden:
            pass

        rules = [
            ("🔖 | general-rules", [
                "Be respectful to all members.",
                "No harassment, hate speech, or discrimination.",
                "No spamming or flooding the chat.",
                "Keep content relevant to the channel topic.",
                "No NSFW content outside designated channels.",
                "No advertising or self-promotion without permission.",
                "Do not share personal information of others.",
                "Follow Discord's [Terms of Service](https://discord.com/terms).",
            ]),
            ("🛋️ | server-rules", [
                "Respect the server hierarchy and staff decisions.",
                "Do not attempt to raid, nuke, or disrupt the server.",
                "Do not DM other members to advertise or scam.",
                "Disputes must be handled via staff tickets, not public channels.",
                "Attempting to exploit bugs or loopholes will result in a ban.",
            ]),
            ("💳 | topup-rules", [
                "Only purchase top-ups through official bot commands (`/buy`).",
                "Provide correct game account details — mistakes are your responsibility.",
                "No chargebacks or fraudulent payments.",
                "Contact staff if your order is delayed beyond 24 hours.",
                "Refunds are only issued if the order cannot be fulfilled.",
            ]),
        ]

        embeds = []
        for title, items in rules:
            desc = "\n".join(f"`{i+1}.` {rule}" for i, rule in enumerate(items))
            embeds.append(
                discord.Embed(title=title, description=desc, color=discord.Color.blurple())
            )

        # Header embed
        header = discord.Embed(
            title="📜 Server Rules",
            description=(
                "Welcome! Please read and follow all rules below.\n"
                "Breaking any rule may result in a **mute, kick, or permanent ban**.\n"
                "​"
            ),
            color=discord.Color.gold(),
        )
        header.set_footer(text="Last updated by Founder via /setup-rules")
        await channel.send(embed=header)

        for embed in embeds:
            await channel.send(embed=embed)

        await interaction.followup.send(
            f"✅ Rules posted in <#{_RULES_CHANNEL_ID}>.",
            ephemeral=True,
        )

    # ─── /dm-invite ──────────────────────────────────────────────────────────

    @app_commands.command(
        name="dm-invite",
        description="[Founder] DM all members an invite link to a new server.",
    )
    @app_commands.describe(invite_link="Invite link for the new server")
    @app_commands.checks.has_role(ROLE_FOUNDER)
    async def dm_invite(self, interaction: discord.Interaction, invite_link: str) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        guild = interaction.guild

        sent, failed = 0, 0
        embed = discord.Embed(
            title="📢 We've moved to a new server!",
            description=(
                f"Hey! **{guild.name}** has a new home.\n\n"
                f"Click the button below to join us 👇\n"
                f"➡️ {invite_link}"
            ),
            color=discord.Color.blurple(),
        )
        embed.set_footer(text=f"Sent by {guild.name} staff")
        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)

        async for member in guild.fetch_members(limit=None):
            if member.bot:
                continue
            try:
                await member.send(embed=embed)
                sent += 1
            except discord.Forbidden:
                failed += 1
            except discord.HTTPException:
                failed += 1

        await interaction.followup.send(
            f"✅ DM sent: **{sent}** members | ❌ Failed (DMs closed): **{failed}**",
            ephemeral=True,
        )

    @setup_shop.error
    @refresh_shop.error
    @setup_rules.error
    @dm_invite.error
    async def admin_error(self, interaction: discord.Interaction, error) -> None:
        if isinstance(error, (app_commands.MissingPermissions, app_commands.MissingRole)):
            await interaction.response.send_message(t(interaction.user, 'founder_no_perm'), ephemeral=True)
        elif isinstance(error, app_commands.CommandInvokeError) and isinstance(error.original, discord.Forbidden):
            locale = get_locale(interaction.user)
            send = interaction.followup.send if interaction.response.is_done() else interaction.response.send_message
            await send(tl(locale, 'bot_forbidden'), ephemeral=True)
        else:
            raise error

    # ─── /add-staff ──────────────────────────────────────────────────────────

    @app_commands.command(
        name="add-staff",
        description="[Founder] Add a Discord member as a dashboard staff account.",
    )
    @app_commands.describe(
        member="Discord member to add as staff",
        username="Login username for the dashboard",
        password="Login password for the dashboard",
        role="Role: staff (default) or admin (full access)",
    )
    @app_commands.choices(role=[
        app_commands.Choice(name="staff", value="staff"),
        app_commands.Choice(name="admin", value="admin"),
    ])
    @app_commands.checks.has_role(ROLE_FOUNDER)
    async def add_staff(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        username: str,
        password: str,
        role: str = "staff",
    ) -> None:
        import bcrypt
        existing = await db.get_staff_by_discord(str(member.id))
        if existing:
            await interaction.response.send_message(
                f"❌ {member.mention} is already a staff member (`{existing['username']}`, role: `{existing['role']}`).",
                ephemeral=True,
            )
            return
        password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
        await db.create_staff(
            discord_id=str(member.id),
            username=username,
            password_hash=password_hash,
            role=role,
        )
        role_icon = "🛡️" if role == "admin" else "👤"
        await interaction.response.send_message(
            f"✅ Added **{member.mention}** as `{role}`. {role_icon}\n"
            f"🔑 Username: `{username}` | Password: `{password}`\n"
            f"🌐 Dashboard: `/dashboard`",
            ephemeral=True,
        )

    @app_commands.command(
        name="remove-staff",
        description="[Founder] Deactivate a staff dashboard account.",
    )
    @app_commands.describe(member="Discord member to remove from staff")
    @app_commands.checks.has_role(ROLE_FOUNDER)
    async def remove_staff(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
    ) -> None:
        existing = await db.get_staff_by_discord(str(member.id))
        if not existing:
            await interaction.response.send_message(
                f"❌ {member.mention} is not a staff member.",
                ephemeral=True,
            )
            return
        await db.deactivate_staff(str(member.id))
        await interaction.response.send_message(
            f"✅ {member.mention} (`{existing['username']}`) has been deactivated.",
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AdminCog(bot))
    bot.add_view(CloseTicketView())   # đăng ký persistent view
