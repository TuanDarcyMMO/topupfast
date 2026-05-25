import os
from dotenv import load_dotenv

load_dotenv()

# ---- Discord ----
DISCORD_TOKEN: str = os.getenv("DISCORD_TOKEN", "")
DISCORD_GUILD_ID: int = int(os.getenv("DISCORD_GUILD_ID", 0) or 0)
# ID of the OLD server — used to look up roles when syncing members to the new server
OLD_GUILD_ID: int = int(os.getenv("OLD_GUILD_ID", 0) or 0)
# Extra guilds bot joins ONLY to sync members to DB (no commands available there)
# Comma-separated list of guild IDs, e.g. "123456,789012"
EXTRA_SYNC_GUILD_IDS: list[int] = [
    int(gid.strip()) for gid in os.getenv("EXTRA_SYNC_GUILD_IDS", "").split(",")
    if gid.strip().isdigit()
]
WELCOME_CHANNEL_ID: int = int(os.getenv("WELCOME_CHANNEL_ID", 0) or 0)
RULES_CHANNEL_ID: int = int(os.getenv("RULES_CHANNEL_ID", 0) or 0)
VERIFY_CHANNEL_ID: int = int(os.getenv("VERIFY_CHANNEL_ID", 0) or 0)
GENERAL_CHANNEL_ID: int = int(os.getenv("GENERAL_CHANNEL_ID", 0) or 0)  # channel to send periodic reminders

# ---- SePay ----
SEPAY_API_TOKEN: str = os.getenv("SEPAY_API_TOKEN", "")
SEPAY_BANK_CODE: str = os.getenv("SEPAY_BANK_CODE", "BIDV")
SEPAY_ACCOUNT_NUMBER: str = os.getenv("SEPAY_ACCOUNT_NUMBER", "")
SEPAY_ACCOUNT_NAME: str = os.getenv("SEPAY_ACCOUNT_NAME", "")

# ---- NowPayments (Crypto) ----
NOWPAYMENTS_API_KEY: str = os.getenv("NOWPAYMENTS_API_KEY", "")
NOWPAYMENTS_IPN_SECRET: str = os.getenv("NOWPAYMENTS_IPN_SECRET", "")
# Coins enabled - thêm coin khác vào đây sau khi test LTC ổn
NOWPAYMENTS_COINS: list[str] = [c.strip().upper() for c in os.getenv("NOWPAYMENTS_COINS", "LTC").split(",") if c.strip()]

# ---- Webhook ----
WEBHOOK_HOST: str = os.getenv("WEBHOOK_HOST", "0.0.0.0")
# Railway cung cấp PORT, VPS dùng WEBHOOK_PORT (default 8080)
WEBHOOK_PORT: int = int(os.getenv("PORT") or os.getenv("WEBHOOK_PORT", 8080))
WEBHOOK_BASE_URL: str = os.getenv("WEBHOOK_BASE_URL", "http://localhost:8080").rstrip("/")

# ---- Supabase ----
SUPABASE_URL: str = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY: str = os.getenv("SUPABASE_KEY", "")

# ---- Cài đặt chung ----
EXCHANGE_RATE: float = float(os.getenv("EXCHANGE_RATE", 26000))   # VND per 1 USD
MIN_DEPOSIT_VND: int = int(os.getenv("MIN_DEPOSIT_VND", 10000))
MIN_DEPOSIT_USD: float = float(os.getenv("MIN_DEPOSIT_USD", 1.0))
PAYMENT_EXPIRY_MINUTES: int = int(os.getenv("PAYMENT_EXPIRY_MINUTES", 30))

# ---- OpenAI (AI chatbot tự động trả lời khách trong ticket) ----
# Lấy key tại: https://platform.openai.com/api-keys
# Để trống = tắt tính năng AI bot
OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL: str = os.getenv("OPENAI_MODEL", "gpt-5.4-mini")
