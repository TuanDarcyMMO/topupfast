"""
Chat Guard — kiểm tra tin nhắn của nhân viên trước khi gửi đến khách.

Rules (tất cả đều bị chặn):
1. Yêu cầu họ tên / tên thật của khách
2. Hướng khách liên hệ ngoài nền tảng (Facebook, Zalo, Telegram, v.v.)
3. Yêu cầu tài khoản / nick các nền tảng khác của khách
4. Yêu cầu số điện thoại của khách
5. Chia sẻ link / handle của các nền tảng bên ngoài
"""

import re
from typing import Tuple

# (pattern, mô tả vi phạm)
_RULES: list[Tuple[str, str]] = [
    # ── 1. Hỏi tên thật ───────────────────────────────────────────────────────
    (
        r"(?:cho\s+(?:mình|tôi|tao|em|anh|chị|bạn)\s+)?(?:biết\s+)?(?:tên|họ\s+tên|full\s+name|"
        r"tên\s+thật|tên\s+đầy\s+đủ|họ\s+và\s+tên|tên\s+của\s+(?:bạn|anh|chị|em))\b",
        "⚠️ Không được yêu cầu khách cung cấp tên thật / họ tên.",
    ),

    # ── 2. Chuyển hướng ra ngoài nền tảng ────────────────────────────────────
    (
        r"(?:liên\s+hệ|nhắn\s+tin|chat|inbox|pm|dm|nhắn|ib|hmu)\s+"
        r"(?:qua|trên|sang|tới|vào|ở|bằng|với)\s+"
        r"(?:ngoài|facebook|fb|zalo|telegram|tele|instagram|insta|"
        r"whatsapp|wa|viber|line|wechat|skype|kik|snapchat|nền\s+tảng\s+khác|kênh\s+khác|chỗ\s+khác)",
        "⚠️ Không được hướng khách liên hệ ngoài nền tảng.",
    ),
    (
        r"(?:liên\s+hệ|nhắn\s+tin)\s+ngoài",
        "⚠️ Không được hướng khách liên hệ ngoài nền tảng.",
    ),
    (
        r"\b(?:bảo\s+khách|yêu\s+cầu\s+khách|nhắc\s+khách)\s+(?:nhắn|liên\s+hệ|chat)\s+(?:qua|trên|ở)\b",
        "⚠️ Không được hướng khách liên hệ ngoài nền tảng.",
    ),

    # ── 3. Hỏi tài khoản / nick nền tảng khác ────────────────────────────────
    (
        r"(?:nick|acc|account|tài\s+khoản|id|user)\s*"
        r"(?:facebook|fb|zalo|telegram|tele|instagram|insta|whatsapp|wa|twitter|tiktok)\b",
        "⚠️ Không được yêu cầu tài khoản nền tảng bên ngoài của khách.",
    ),
    (
        r"(?:facebook|fb|zalo|telegram|tele|instagram|insta)\s+(?:của\s+)?(?:bạn|anh|chị|em|khách)\b",
        "⚠️ Không được yêu cầu tài khoản nền tảng bên ngoài của khách.",
    ),
    (
        r"\badd\s+(?:me|mình|tôi|tao|em|anh|chị)\b",
        "⚠️ Không được yêu cầu kết bạn trên nền tảng khác.",
    ),
    (
        r"\bkết\s+bạn\b",
        "⚠️ Không được yêu cầu kết bạn trên nền tảng khác.",
    ),

    # ── 4. Hỏi số điện thoại ─────────────────────────────────────────────────
    (
        r"(?:số\s+(?:điện\s+thoại|dt|đt|zalo)|phone\s+(?:number)?|sdt|sdđt|phone\s+của)\b",
        "⚠️ Không được yêu cầu số điện thoại của khách.",
    ),

    # ── 5. Chia sẻ link nền tảng bên ngoài ───────────────────────────────────
    (
        r"(?:fb\.com|facebook\.com|zalo\.me|t\.me|telegram\.me|wa\.me|"
        r"instagram\.com|twitter\.com|tiktok\.com|line\.me|viber\.com|"
        r"snapchat\.com|wechat\.com)/",
        "⚠️ Không được chia sẻ link nền tảng bên ngoài.",
    ),
]

_compiled: list[Tuple[re.Pattern, str]] = [
    (re.compile(pattern, re.IGNORECASE | re.UNICODE), reason)
    for pattern, reason in _RULES
]


def check_staff_message(text: str) -> Tuple[bool, str]:
    """
    Kiểm tra tin nhắn của nhân viên trước khi gửi đến khách.

    Returns:
        (True, reason)  — vi phạm, chặn tin nhắn, trả reason cho nhân viên
        (False, "")     — tin nhắn hợp lệ
    """
    for pattern, reason in _compiled:
        if pattern.search(text):
            return True, reason
    return False, ""
