"""
Bilingual support (EN / VI) based on Discord role.

Role IDs
--------
ROLE_VN  = 1503255690775367680  → Tiếng Việt
ROLE_EN  = 1503255999035740333  → English

Logic: if member has VN role → "vi", otherwise → "en" (default).
"""

from __future__ import annotations
import discord

ROLE_VN: int = 1503255690775367680
ROLE_EN: int = 1503255999035740333
ROLE_FOUNDER: int = 1498363612245262487


def get_locale(member: discord.Member | discord.User | None) -> str:
    """Return 'vi' or 'en' based on member's Discord roles. Default: 'en'."""
    if isinstance(member, discord.Member):
        ids = {r.id for r in member.roles}
        if ROLE_VN in ids:
            return "vi"
    return "en"


def get_locale_from_bot(bot: discord.Client, discord_id: str) -> str:
    """Look up locale for a user ID via bot's guild cache (for background tasks)."""
    try:
        uid = int(discord_id)
    except (ValueError, TypeError):
        return "en"
    for guild in bot.guilds:
        member = guild.get_member(uid)
        if member:
            return get_locale(member)
    return "en"


# ─── Translation strings ──────────────────────────────────────────────────────

_S: dict[str, dict[str, str]] = {

    # ── Shop ──────────────────────────────────────────────────────────────────
    "shop_no_account": {
        "vi": "❌ Bạn chưa có tài khoản. Dùng `/create-payment` để bắt đầu.",
        "en": "❌ No account found. Use `/create-payment` first.",
    },
    "shop_no_games": {
        "vi": "❌ Không có game nào. Liên hệ admin.",
        "en": "❌ No games available. Contact admin.",
    },
    "shop_no_packages": {
        "vi": "❌ Chưa có gói nạp cho game này. Vui lòng liên hệ admin.",
        "en": "❌ No packages available for this game. Contact admin.",
    },
    "shop_game_unavailable": {
        "vi": "❌ Game không khả dụng.",
        "en": "❌ This game is currently unavailable.",
    },
    "shop_select_game_title": {
        "vi": "🛒 Chọn Game",
        "en": "🛒 Select Game",
    },
    "shop_balance_label": {
        "vi": "💰 **Số dư của bạn:**",
        "en": "💰 **Your balance:**",
    },
    "shop_choose_game": {
        "vi": "Chọn game...",
        "en": "Choose a game...",
    },
    "shop_select_type_title": {
        "vi": "Chọn loại",
        "en": "Select Type",
    },
    "shop_choose_type": {
        "vi": "Chọn loại...",
        "en": "Choose type...",
    },
    "shop_choose_package": {
        "vi": "Chọn gói...",
        "en": "Choose a package...",
    },
    "shop_back_btn": {
        "vi": "◀ Quay lại",
        "en": "◀ Back",
    },
    "shop_confirm_title": {
        "vi": "🛒 Xác nhận đơn",
        "en": "🛒 Confirm Order",
    },
    "shop_price_label": {
        "vi": "💵 **Giá:**",
        "en": "💵 **Price:**",
    },
    "shop_account_label": {
        "vi": "👤 **Tài khoản:**",
        "en": "👤 **Account:**",
    },
    "shop_note_label": {
        "vi": "📝 **Ghi chú:**",
        "en": "📝 **Note:**",
    },
    "shop_your_balance": {
        "vi": "💰 **Số dư:**",
        "en": "💰 **Your balance:**",
    },
    "shop_after_purchase": {
        "vi": "Sau khi mua:",
        "en": "After purchase:",
    },
    "shop_insufficient_inline": {
        "vi": "❌ **Số dư không đủ.** Nạp tiền với `/create-payment`.",
        "en": "❌ **Insufficient balance.** Top up with `/create-payment`.",
    },
    "shop_balance_footer": {
        "vi": "Số dư: ${bal:.2f} USD",
        "en": "Your balance: ${bal:.2f} USD",
    },
    "shop_cancelled_title": {
        "vi": "🚫 Đã hủy",
        "en": "🚫 Order Cancelled",
    },
    "shop_cancelled_desc": {
        "vi": "Không có khoản nào bị trừ.",
        "en": "No charge was made.",
    },
    "shop_insufficient_error": {
        "vi": "❌ **Số dư không đủ.**\nHiện có: `${bal:.2f}` | Cần: `${price:.2f}`\nNạp tiền với `/create-payment`.",
        "en": "❌ **Insufficient balance.**\nYours: `${bal:.2f}` | Required: `${price:.2f}`\nTop up with `/create-payment`.",
    },
    "shop_order_success_title": {
        "vi": "✅ Đặt đơn thành công!",
        "en": "✅ Order Placed!",
    },
    "shop_paid_label": {
        "vi": "💵 **Đã trừ:**",
        "en": "💵 **Paid:**",
    },
    "shop_error": {
        "vi": "❌ Có lỗi xảy ra. Vui lòng liên hệ admin.",
        "en": "❌ An error occurred. Please contact admin.",
    },

    # ── Admin / GameTopUpView (persistent button) ─────────────────────────────
    "topup_no_account": {
        "vi": "❌ Bạn chưa có tài khoản. Dùng `/create-payment` để bắt đầu.",
        "en": "❌ You don't have an account yet. Use `/create-payment` to add funds.",
    },
    "topup_zero_balance": {
        "vi": "❌ **Số dư của bạn là $0.00.**\nBạn cần nạp tiền trước khi mua gói.\n👉 Dùng `/create-payment` để nạp.",
        "en": "❌ **Your balance is $0.00.**\nYou need to top up before purchasing.\n👉 Use `/create-payment` to add funds.",
    },
    "topup_no_packages": {
        "vi": "❌ Chưa có gói nạp cho game này. Vui lòng liên hệ admin.",
        "en": "❌ No packages available for this game. Please contact admin.",
    },
    "topup_game_unavailable": {
        "vi": "❌ Game không khả dụng.",
        "en": "❌ This game is not available.",
    },
    "topup_select_type": {
        "vi": "Chọn loại",
        "en": "Select Type",
    },

    # ── Ticket ────────────────────────────────────────────────────────────────
    "ticket_title": {
        "vi": "🎫 Đơn #{id} — {name}",
        "en": "🎫 Order #{id} — {name}",
    },
    "ticket_price_field": {
        "vi": "💵 Giá",
        "en": "💵 Price",
    },
    "ticket_account_field": {
        "vi": "👤 Tài khoản",
        "en": "👤 Account",
    },
    "ticket_note_field": {
        "vi": "📝 Ghi chú",
        "en": "📝 Note",
    },
    "ticket_order_id_field": {
        "vi": "🔖 Order ID",
        "en": "🔖 Order ID",
    },
    "ticket_status_field": {
        "vi": "⏳ Trạng thái",
        "en": "⏳ Status",
    },
    "ticket_status_pending": {
        "vi": "Đang chờ xử lý",
        "en": "Waiting for processing",
    },
    "ticket_footer": {
        "vi": "Admin sẽ xử lý đơn sớm nhất có thể.",
        "en": "Admin will process your order as soon as possible.",
    },
    "ticket_mention": {
        "vi": "{mention} Đơn nạp của bạn đã được tạo! Admin sẽ xử lý sớm.",
        "en": "{mention} Your top-up order has been created! Admin will process it soon.",
    },
    "close_ticket_no_perm": {
        "vi": "❌ Chỉ admin mới được đóng ticket.",
        "en": "❌ Only admins can close tickets.",
    },

    # ── Game channel embed ────────────────────────────────────────────────────
    "game_channel_desc": {
        "vi": (
            "Nhấn nút bên dưới để xem các gói nạp và mua ngay!\n"
            "Bạn cần có balance trước — dùng `/create-payment` để nạp tiền."
        ),
        "en": (
            "Press the button below to browse packages and purchase!\n"
            "You need balance first — use `/create-payment` to add funds."
        ),
    },
    "game_channel_footer": {
        "vi": "Dùng /buy để mua",
        "en": "Use /buy to purchase",
    },

    # ── Admin command responses ───────────────────────────────────────────────
    "admin_no_perm": {
        "vi": "❌ Bạn không có quyền Administrator.",
        "en": "❌ You don't have Administrator permission.",
    },
    "founder_no_perm": {
        "vi": "❌ Lệnh này chỉ dành cho **Founder**.",
        "en": "❌ This command is restricted to **Founder** only.",
    },
    "bot_forbidden": {
        "vi": "❌ Bot không có quyền thực hiện thao tác này trên server. Hãy kiểm tra lại quyền của bot (Manage Channels, Manage Roles).",
        "en": "❌ The bot is missing permissions to perform this action. Please check the bot's server permissions (Manage Channels, Manage Roles).",
    },
    "admin_no_games": {
        "vi": "❌ Không có game nào trong database.",
        "en": "❌ No games found in the database.",
    },
    "admin_no_category": {
        "vi": "❌ Không tìm thấy category `{name}`.",
        "en": "❌ Category `{name}` not found.",
    },
    "admin_setup_done": {
        "vi": "**Setup Shop hoàn tất!**\nCategory: `{cat}`\n\n{lines}",
        "en": "**Setup Shop complete!**\nCategory: `{cat}`\n\n{lines}",
    },
    "admin_setup_created": {
        "vi": "✅ **Tạo mới ({n}):**",
        "en": "✅ **Created ({n}):**",
    },
    "admin_setup_skipped": {
        "vi": "⏭️ **Đã tồn tại ({n}):**",
        "en": "⏭️ **Already exists ({n}):**",
    },
    "admin_refresh_done": {
        "vi": "✅ Đã refresh {n} channel: {names}",
        "en": "✅ Refreshed {n} channel(s): {names}",
    },

    # ── TopUpCog (deposit flow) ───────────────────────────────────────────────
    "balance_no_account": {
        "vi": "❌ Bạn chưa có tài khoản. Hãy dùng `/create-payment` để nạp tiền.",
        "en": "❌ You don't have an account yet. Use `/create-payment` to add funds.",
    },
    "balance_title": {
        "vi": "💰 Số dư tài khoản",
        "en": "💰 Account Balance",
    },
    "history_no_tx": {
        "vi": "📋 Chưa có giao dịch nào.",
        "en": "📋 No transactions found.",
    },
    "history_deposit_title": {
        "vi": "📋 Lịch sử nạp tiền",
        "en": "📋 Deposit History",
    },
    "history_purchase_na": {
        "vi": "🛒 Lịch sử mua hàng chưa sẵn sàng.",
        "en": "🛒 Purchase history is not available yet.",
    },
    "cancel_done": {
        "vi": "❌ Giao dịch đã hủy.",
        "en": "❌ Transaction cancelled.",
    },
    "expired_desc": {
        "vi": "Giao dịch hết hạn. Dùng `/create-payment` để tạo lại.",
        "en": "Payment expired. Use `/create-payment` to start a new one.",
    },
    "deposit_success_desc": {
        "vi": "💰 `{vnd:,} VND` → `${usd:.4f} USD` đã được cộng vào tài khoản.\n📊 Xem số dư: `/balance`",
        "en": "💰 `{vnd:,} VND` → `${usd:.4f} USD` has been added to your account.\n📊 Check balance: `/balance`",
    },
    "bank_modal_title": {
        "vi": "💳 Nạp tiền qua Bank VN",
        "en": "💳 Bank VN Deposit",
    },
    "bank_modal_label": {
        "vi": "Số tiền (VND)",
        "en": "Amount (VND)",
    },
    "bank_modal_placeholder": {
        "vi": "Ví dụ: 100000 (tối thiểu {min:,} VND)",
        "en": "Example: 100000 (minimum {min:,} VND)",
    },
    "bank_amount_invalid": {
        "vi": "❌ Số tiền không hợp lệ. Vui lòng nhập số nguyên.",
        "en": "❌ Invalid amount. Please enter a whole number.",
    },
    "bank_embed_title": {
        "vi": "💳 Nạp tiền qua Bank VN",
        "en": "💳 Bank VN Deposit",
    },
    "bank_embed_desc": {
        "vi": (
            "Quét mã QR hoặc chuyển khoản thủ công với thông tin bên dưới.\n\n"
            "💰 **Số tiền:** `{vnd:,} VND` (~`${usd:.4f} USD`)\n"
            "📝 **Nội dung CK:** `{tfa}` *(bắt buộc)*\n"
            "⏱️ **Hết hạn:** <t:{exp}:R>\n\n"
            "⚠️ Nhập **đúng** nội dung `{tfa}` để bot xác nhận tự động."
        ),
        "en": (
            "Scan the QR code or transfer manually with the details below.\n\n"
            "💰 **Amount:** `{vnd:,} VND` (~`${usd:.4f} USD`)\n"
            "📝 **Transfer note:** `{tfa}` *(required)*\n"
            "⏱️ **Expires:** <t:{exp}:R>\n\n"
            "⚠️ Enter **exactly** `{tfa}` as the transfer note for auto-confirmation."
        ),
    },
    "bank_embed_footer": {
        "vi": "Bot tự động cộng tiền sau khi nhận được thanh toán.",
        "en": "Bot auto-credits your balance after payment is received.",
    },
    "vnd_min_error": {
        "vi": "❌ Số tiền tối thiểu là **{min:,} VND**. Bạn đã nhập `{amt:,} VND`. Vui lòng tạo lệnh mới.",
        "en": "❌ Minimum deposit is **{min:,} VND**. You entered `{amt:,} VND`. Please create a new order.",
    },
    "usd_min_error": {
        "vi": "❌ Số tiền tối thiểu là **${min:.2f} USD**. Bạn đã nhập `${amt:.2f} USD`. Vui lòng tạo lệnh mới.",
        "en": "❌ Minimum deposit is **${min:.2f} USD**. You entered `${amt:.2f} USD`. Please create a new order.",
    },
}


def tl(locale: str, key: str, **kwargs) -> str:
    """Translate by locale string ('en' or 'vi')."""
    strings = _S.get(key, {})
    text = strings.get(locale, strings.get("en", f"[{key}]"))
    return text.format(**kwargs) if kwargs else text


def t(member: discord.Member | discord.User | None, key: str, **kwargs) -> str:
    """Translate for the given Discord member."""
    return tl(get_locale(member), key, **kwargs)
