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

# Tên viết tắt các nền tảng bị cấm (dùng chung cho nhiều rule)
_PLATFORMS = (
    r"zalo|zl\b|telegram|tele\b|tg\b|facebook|fb\b|mess(?:enger)?"
    r"|instagram|insta\b|whatsapp|wa\b|viber|line\b|wechat|skype|kik|snapchat"
)

# (pattern, mô tả vi phạm)
_RULES: list[Tuple[str, str]] = [
    # ── 1. Hỏi tên thật ───────────────────────────────────────────────────────
    # 1a. họ tên / họ và tên (có và không dấu)
    (
        r"\b(?:họ\s+(?:và\s+)?tên|ho\s+(?:va\s+)?ten)\b",
        "⚠️ Không được yêu cầu khách cung cấp tên thật / họ tên.",
    ),
    # 1b. tên thật / tên khai sinh / full name / real name / tên chính chủ
    (
        r"\b(?:tên\s+thật|ten\s+that|tên\s+khai\s+sinh|ten\s+khai\s+sinh"
        r"|full\s+name|real\s+name|tên\s+chính\s+chủ|tên\s+đầy\s+đủ)\b",
        "⚠️ Không được yêu cầu khách cung cấp tên thật / họ tên.",
    ),
    # 1c. (cho|xin|gửi) + khoảng 0-25 ký tự + (họ tên|full name|tên thật)
    (
        r"(?:cho|xin|gửi|gưi)\s+.{0,25}(?:họ\s+tên|ho\s+ten|full\s+name|tên\s+thật|ten\s+that)",
        "⚠️ Không được yêu cầu khách cung cấp tên thật / họ tên.",
    ),

    # ── 2. Chuyển hướng ra ngoài nền tảng ────────────────────────────────────
    # 2a. [động từ liên hệ] + (0-20 ký tự) + [tên nền tảng]
    (
        r"(?:liên\s*hệ|liên\s*lạc|nhắn\s*tin|chat|inbox|ib\b|pm\b|dm\b|nhắn|hmu|trao\s*đổi)"
        r".{0,20}(?:" + _PLATFORMS + r")",
        "⚠️ Không được hướng khách liên hệ ngoài nền tảng.",
    ),
    # 2b. qua / bên / sang + [nền tảng]
    (
        r"\b(?:qua|bên|sang)\s+(?:" + _PLATFORMS + r")",
        "⚠️ Không được hướng khách liên hệ ngoài nền tảng.",
    ),
    # 2c. liên hệ / trao đổi ngoài (hệ thống)
    (
        r"(?:liên\s*hệ|liên\s*lạc|nhắn\s*tin|trao\s*đổi)\s*ngoài",
        "⚠️ Không được hướng khách liên hệ ngoài nền tảng.",
    ),
    # 2d. add / kết bạn + [nền tảng]
    (
        r"\b(?:add|kết\s*bạn)\s*.{0,15}(?:" + _PLATFORMS + r")",
        "⚠️ Không được yêu cầu kết bạn trên nền tảng khác.",
    ),
    (
        r"\badd\s+(?:me|mình|tôi|tao|em|anh|chị)\b",
        "⚠️ Không được yêu cầu kết bạn trên nền tảng khác.",
    ),
    (
        r"\bkết\s+bạn\b",
        "⚠️ Không được yêu cầu kết bạn trên nền tảng khác.",
    ),
    # 2e. bảo/yêu cầu/nhắc khách liên hệ/chat qua ...
    (
        r"\b(?:bảo\s+khách|yêu\s+cầu\s+khách|nhắc\s+khách)\s+(?:nhắn|liên\s+hệ|chat)\s+(?:qua|trên|ở)\b",
        "⚠️ Không được hướng khách liên hệ ngoài nền tảng.",
    ),

    # ── 3. Hỏi nick / tài khoản nền tảng khác ────────────────────────────────
    # 3a. (nick|acc|username|handle) + (0-15 ký tự) + [nền tảng]
    (
        r"(?:nick|acc\b|account|tài\s*khoản|username|user\b|id\b|handle)\s*.{0,15}"
        r"(?:" + _PLATFORMS + r")",
        "⚠️ Không được yêu cầu tài khoản nền tảng bên ngoài của khách.",
    ),
    # 3b. [nền tảng] + của bạn/anh/chị (kể cả không dấu)
    (
        r"(?:" + _PLATFORMS + r")\s+(?:của\s+)?(?:bạn|anh|chị|em|khách|ban\b)",
        "⚠️ Không được yêu cầu tài khoản nền tảng bên ngoài của khách.",
    ),
    # 3c. cho (xin) + (nick|acc|user|id|handle) + [nền tảng]
    (
        r"(?:cho|xin)\s*.{0,15}(?:nick\b|acc\b|username|user\b|id\b|handle\b)\s*.{0,15}"
        r"(?:" + _PLATFORMS + r")",
        "⚠️ Không được yêu cầu tài khoản nền tảng bên ngoài của khách.",
    ),

    # ── 4. Hỏi số điện thoại ─────────────────────────────────────────────────
    # 4a. từ khoá SĐT (có và không dấu, các biến thể)
    (
        r"\b(?:sđt|sdt|sdđt|số\s*điện\s*thoại|so\s*dien\s*thoai"
        r"|phone(?:\s*number)?|mobile|contact\s*number"
        r"|số\s*liên\s*hệ|so\s*lien\s*he|số\s*liên\s*lạc|so\s*lien\s*lac)\b",
        "⚠️ Không được yêu cầu số điện thoại của khách.",
    ),
    # 4b. (cho|xin|gửi|để lại|drop) + (0-20 ký tự) + (sdt|phone|mobile)
    (
        r"(?:cho|xin|gửi|gưi|để\s*lại|drop)\s*.{0,20}(?:sđt|sdt|số\s*điện\s*thoại|phone|mobile)",
        "⚠️ Không được yêu cầu số điện thoại của khách.",
    ),
    # 4c. sdt/số điện thoại là gì / bao nhiêu
    (
        r"(?:sđt|sdt|số\s*(?:điện\s*thoại|dt|đt))\s*.{0,10}(?:là\s*gì|la\s*gi|bao\s*nhiêu)",
        "⚠️ Không được yêu cầu số điện thoại của khách.",
    ),

    # ── 5. Chia sẻ link / handle nền tảng bên ngoài ──────────────────────────
    # 5a. domain links
    (
        r"(?:fb\.com|facebook\.com|zalo\.me|t\.me|telegram\.me|wa\.me|m\.me|"
        r"instagram\.com|twitter\.com|tiktok\.com|line\.me|viber\.com|"
        r"snapchat\.com|wechat\.com)/",
        "⚠️ Không được chia sẻ link nền tảng bên ngoài.",
    ),
    # 5b. [nền tảng] : @handle hoặc [nền tảng] : username
    (
        r"(?:telegram|tele|tg|zalo|zl|facebook|fb|mess(?:enger)?)\s*[:\-]\s*@?[a-z0-9_.]{3,}",
        "⚠️ Không được chia sẻ handle / ID nền tảng bên ngoài.",
    ),
    # 5c. liên hệ / tìm mình + @handle
    (
        r"(?:liên\s*hệ|contact|tìm\s*mình|nhắn)\s*.{0,20}@[a-z0-9_]{3,}",
        "⚠️ Không được chia sẻ handle nền tảng bên ngoài.",
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
