"""
SePay integration - Bank VN payment (chuyển khoản nội địa)

Flow:
  1. Bot tạo mã TFA duy nhất (VD: TFA12345)
  2. Bot gửi QR code VietQR cho user kèm nội dung chuyển khoản
  3. User chuyển khoản với nội dung đúng TFA code
  4. Bot polling SePay API mỗi 5 giây để kiểm tra giao dịch mới
  5. Bot tìm giao dịch có nội dung chứa TFA code -> cộng tiền cho user

API SePay:
  GET https://my.sepay.vn/userapi/transactions/list
  Header: Authorization: Bearer {SEPAY_API_TOKEN}
"""

import logging
import re
import urllib.parse
from datetime import datetime

import aiohttp

from config import SEPAY_API_TOKEN, SEPAY_BANK_CODE, SEPAY_ACCOUNT_NUMBER, SEPAY_ACCOUNT_NAME

logger = logging.getLogger(__name__)

_SEPAY_API_BASE = "https://my.sepay.vn/userapi"


def generate_qr_url(amount_vnd: int, tfa_code: str) -> str:
    """
    Tạo URL ảnh QR VietQR qua API của SePay.
    Ảnh QR được tạo tự động phía server, không cần thư viện bên ngoài.
    """
    desc = urllib.parse.quote(tfa_code)
    return (
        f"https://qr.sepay.vn/img"
        f"?acc={SEPAY_ACCOUNT_NUMBER}"
        f"&bank={SEPAY_BANK_CODE}"
        f"&amount={amount_vnd}"
        f"&des={desc}"
        f"&template=compact2"
    )


async def fetch_transactions(limit: int = 20) -> list[dict]:
    """
    Lấy danh sách giao dịch gần nhất từ SePay API.
    Trả về list các transaction dict, [] nếu lỗi.
    """
    if not SEPAY_API_TOKEN:
        logger.warning("SEPAY_API_TOKEN chưa được cấu hình.")
        return []

    headers = {"Authorization": f"Bearer {SEPAY_API_TOKEN}"}
    params = {"limit": limit}
    if SEPAY_ACCOUNT_NUMBER:
        params["account_number"] = SEPAY_ACCOUNT_NUMBER

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{_SEPAY_API_BASE}/transactions/list",
                headers=headers,
                params=params,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    logger.error(f"SePay API trả về HTTP {resp.status}")
                    return []
                data = await resp.json()
                return data.get("transactions", [])
    except Exception as exc:
        logger.error(f"Lỗi khi gọi SePay API: {exc}")
        return []


async def find_tfa_in_transactions(tfa_code: str, created_after: datetime) -> dict | None:
    """
    Polling SePay API và tìm giao dịch thỏa mãn:
      1. Nội dung chứa TFA code
      2. Thời gian giao dịch >= created_after (tránh nhận nhầm CK cũ)

    Trả về dict giao dịch nếu tìm thấy, None nếu chưa có.
    """
    transactions = await fetch_transactions(limit=20)
    tfa_upper = tfa_code.upper()
    for tx in transactions:
        content = (tx.get("transaction_content") or "").upper()
        if tfa_upper not in content:
            continue

        # Filter by time: chỉ nhận giao dịch sau khi lệnh được tạo
        tx_date_str = tx.get("transaction_date") or tx.get("when") or ""
        if tx_date_str:
            try:
                # SePay trả về format: "YYYY-MM-DD HH:MM:SS"
                tx_date = datetime.strptime(tx_date_str[:19], "%Y-%m-%d %H:%M:%S")
                if tx_date < created_after:
                    logger.debug(
                        f"Bỏ qua SePay tx {tx.get('id')} vì thời gian "
                        f"{tx_date_str} < lệnh tạo {created_after}"
                    )
                    continue
            except ValueError:
                pass  # Không parse được thời gian -> cho qua, để guard sau xử lý

        return tx
    return None


def extract_tfa_code(content: str) -> str | None:
    """Trích xuất mã TFA từ nội dung chuyển khoản (VD: 'chuyen tien TFA12345 ck')."""
    match = re.search(r"TFA\d{5}", content.upper())
    return match.group(0) if match else None


def validate_webhook(headers: dict) -> bool:
    """
    Kiểm tra tính hợp lệ của webhook từ SePay.
    SePay gửi token trong header Authorization: Bearer <token>
    """
    if not SEPAY_API_TOKEN:
        return True
    auth = headers.get("Authorization") or headers.get("authorization") or ""
    return auth == f"Bearer {SEPAY_API_TOKEN}"
