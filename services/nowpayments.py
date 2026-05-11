"""
NowPayments integration - Crypto payment

Flow:
  1. Bot gọi create_payment() → NowPayments trả về pay_address + pay_amount (exact)
  2. User gửi ĐÚNG 100% số coin hiển thị đến địa chỉ ví
  3. Bot polling NowPayments API mỗi 5 giây để check status
  4. Status mapping:
       NP "waiting"    → Discord: ⏳ Pending (chờ giao dịch)
       NP "confirming" → Discord: 🔄 Confirming (đang xác nhận trên blockchain)
       NP "finished"   → Discord: ✅ Completed (cộng tiền)
       NP "expired/failed" → Discord: ❌ Expired/Failed

  QUAN TRỌNG về thời hạn:
    - NowPayments invoice sống 7 ngày trên hệ thống NP
    - Bot chỉ polling trong 30 phút (PAYMENT_EXPIRY_MINUTES)
    - Sau 30 phút bot đánh dấu expired trong DB local → ngừng polling
    - Nếu user trả tiền sau 30 phút, IPN webhook sẽ xử lý (fallback)
    - Nếu session đã expired trong DB → không credit, alert admin

IPN Webhook:
  NowPayments gửi POST đến /webhook/nowpayments khi có cập nhật
  Xác thực bằng HMAC-SHA512 với IPN_SECRET
"""

import hashlib
import hmac
import json
import logging

import aiohttp

from config import NOWPAYMENTS_API_KEY, NOWPAYMENTS_IPN_SECRET, NOWPAYMENTS_COINS

logger = logging.getLogger(__name__)

_BASE = "https://api.nowpayments.io/v1"

# Tên hiển thị cho từng coin
_COIN_LABELS: dict[str, str] = {
    "LTC":  "Litecoin (LTC)",
    "BTC":  "Bitcoin (BTC)",
    "ETH":  "Ethereum (ETH)",
    "USDT": "Tether USDT (TRC20)",
    "SOL":  "Solana (SOL)",
    "TRX":  "TRON (TRX)",
}


def get_available_coins() -> dict[str, str]:
    """Trả về các coin đã được bật trong config."""
    return {
        coin: _COIN_LABELS.get(coin, coin)
        for coin in NOWPAYMENTS_COINS
    }


def round2(n: float) -> float:
    """
    Làm tròn 2 chữ số thập phân - tránh lỗi floating point (5.9999 thay vì 6.00).
    Dùng round(n * 100) / 100 thay vì round(n, 2) để chính xác hơn.
    """
    return round(float(n) * 100) / 100


async def create_payment(
    amount_usd: float,
    coin: str,
    discord_id: str,
    callback_url: str,
) -> dict:
    """
    Tạo payment trên NowPayments.

    Returns dict gồm:
        payment_id    - ID của payment trên NowPayments
        pay_address   - Địa chỉ ví cần gửi coin đến
        pay_amount    - Số coin cần gửi (CHÍNH XÁC 100%)
        pay_currency  - Coin (ltc, btc, ...)
        price_amount  - Số USD gốc
        payment_status - waiting/confirming/finished/...

    Raises:
        Exception nếu NowPayments trả lỗi
    """
    coin_lower = coin.lower()
    payload = {
        "price_amount": amount_usd,
        "price_currency": "usd",
        "pay_currency": coin_lower,
        "order_id": str(discord_id),
        "ipn_callback_url": callback_url,
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{_BASE}/payment",
            json=payload,
            headers={
                "x-api-key": NOWPAYMENTS_API_KEY,
                "Content-Type": "application/json",
            },
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            data = await resp.json()
            if not resp.ok:
                raise Exception(
                    f"NowPayments error {resp.status}: {data.get('message', str(data))}"
                )
            return data


async def get_payment_status(np_payment_id: str) -> dict:
    """
    Lấy trạng thái hiện tại của payment từ NowPayments.
    Trả về {} nếu lỗi (caller tự handle).
    """
    async with aiohttp.ClientSession() as session:
        async with session.get(
            f"{_BASE}/payment/{np_payment_id}",
            headers={"x-api-key": NOWPAYMENTS_API_KEY},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if not resp.ok:
                logger.warning(f"NP get_payment_status {np_payment_id}: HTTP {resp.status}")
                return {}
            return await resp.json()


def verify_ipn(raw_body: bytes | str, signature: str) -> bool:
    """
    Xác thực chữ ký HMAC-SHA512 từ NowPayments IPN webhook.
    Header: x-nowpayments-sig
    raw_body: raw bytes đọc từ request (chưa parse JSON)
    """
    if not NOWPAYMENTS_IPN_SECRET:
        return True  # dev mode - không có secret thì pass hết
    if not signature:
        return False
    # Parse JSON → sắp xếp key → re-serialize (NowPayments spec)
    try:
        data = json.loads(raw_body)
        ordered = json.dumps(dict(sorted(data.items())), separators=(",", ":"))
    except Exception:
        return False
    digest = hmac.new(
        NOWPAYMENTS_IPN_SECRET.encode(),
        ordered.encode(),
        hashlib.sha512,
    ).hexdigest()
    return hmac.compare_digest(digest, signature.lower())


# Status mapping NP → internal
NP_STATUS_MAP: dict[str, str] = {
    "waiting":    "pending",
    "confirming": "confirming",
    "confirmed":  "confirming",
    "sending":    "confirming",
    "finished":   "completed",
    "failed":     "failed",
    "refunded":   "failed",
    "expired":    "expired",
}

# Status display strings
STATUS_DISPLAY: dict[str, str] = {
    "pending":    "⏳ Waiting for payment...",
    "confirming": "🔄 Confirming on blockchain...",
    "completed":  "✅ Completed!",
    "failed":     "❌ Failed",
    "expired":    "⏰ Expired",
}
