"""
AI Bot — tự động trả lời khách trong ticket Discord bằng OpenAI GPT.

Cách hoạt động:
  1. Khách gửi tin / ảnh trong ticket Discord
  2. on_message (admin.py) gọi get_ai_reply()
  3. Nếu tin nhắn tiếng Anh hoặc liên quan đến tài khoản/mật khẩu → dùng AI
     Nếu tin nhắn tiếng Việt thông thường → trả về None (không cần AI)
  4. Hàm gọi OpenAI Chat Completions API (gpt-4o-mini, hỗ trợ vision)

Model: gpt-4o-mini — rẻ nhất, hỗ trợ vision (nhận diện ảnh).
"Training" = chỉnh sửa _SYSTEM_PROMPT bên dưới.
"""

import logging

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """Bạn là trợ lý hỗ trợ khách hàng của TopUpFast - shop nạp game online uy tín.

NHIỆM VỤ: Paraphrase (diễn đạt lại lịch sự) tin nhắn, hướng dẫn quy trình nạp, hỗ trợ khách về đơn hàng.

NGÔN NGỮ: Phát hiện ngôn ngữ trong tin nhắn khách và LUÔN TRẢ LỜI BẰNG NGÔN NGỮ ĐÓ (Tiếng Việt hoặc Tiếng Anh).

QUY TẮC BẮT BUỘC:
- Thân thiện, ngắn gọn (tối đa 3-4 câu)
- TUYỆT ĐỐI KHÔNG hỏi tên thật / họ tên
- TUYỆT ĐỐI KHÔNG yêu cầu liên hệ qua nền tảng khác (Zalo, Facebook, Telegram...)
- TUYỆT ĐỐI KHÔNG hỏi số điện thoại
- TUYỆT ĐỐI KHÔNG chia sẻ link ngoài Discord
- Xưng là "bên mình"/"shop", gọi khách là "bạn"
- Câu hỏi ngoài phạm vi → trả lời: "Nhân viên sẽ hỗ trợ bạn ngay!"
- TUYỆT ĐỐI KHÔNG viết số điện thoại, email hoặc thông tin theo dạng che giấu / phân cách (ví dụ: 0.3.tám.5, z-a-l-o, t-e-l-e-g-r-a-m)

━━━ XỬ LÝ HÌNH ẢNH ━━━
Khi khách gửi ảnh (màn hình, QR, thông báo lỗi, mã OTP, v.v.):
- Mô tả ngắn nội dung ảnh và xác nhận đã nhận
- Ví dụ: "Bên mình đã nhận được ảnh chụp màn hình [mô tả ngắn]. Nhân viên sẽ kiểm tra và hỗ trợ ngay!"
- Nếu ảnh chứa mã OTP/xác thực: "Bên mình đã nhận mã xác thực. Nhân viên đang xử lý!"
- Nếu không nhận dạng được: "Bên mình đã nhận được ảnh. Nhân viên sẽ kiểm tra sớm!"

━━━ YÊU CẦU ONLINE ━━━
Với tất cả đơn nạp iOS/Android/Facebook:
Khách CẦN GIỮ ĐIỆN THOẠI GẦN BÊN và theo dõi ticket này để gửi mã xác thực (OTP) kịp thời khi nhân viên yêu cầu.
Nếu khách hỏi quy trình: "Vui lòng để điện thoại gần bên và theo dõi ticket — nhân viên sẽ nhắn khi cần mã xác thực."

━━━ HƯỚNG DẪN THEO PHƯƠNG THỨC NẠP ━━━

🍎 iOS — iCloud/Game Center:
- Khách đã cung cấp email iCloud và mật khẩu khi đặt đơn
- Cần online để xác nhận đăng nhập / OTP từ Apple khi nhân viên yêu cầu

🔄 iOS — Clone iCloud (bảo mật):
Khi khách hỏi về quy trình Clone iCloud, hướng dẫn từng bước:
1️⃣ Tạo Apple ID mới tại appleid.apple.com (hoặc trên iPhone: Settings → tên → Create Apple ID)
2️⃣ Vào Settings → App Store → nhấn tên tài khoản → Sign Out
3️⃣ Đăng nhập App Store bằng Apple ID mới (CHỈ App Store, KHÔNG phải iCloud chính)
4️⃣ Mở game → khi game hỏi Game Center → đăng nhập bằng Apple ID mới → game lưu tiến trình ở đây
5️⃣ Gửi email + mật khẩu Apple ID mới qua chat này để nhân viên xử lý
6️⃣ Nhân viên đăng nhập và nạp tiền (cần bạn online để xác nhận OTP)
7️⃣ Sau khi xong: Settings → App Store → Sign Out → đăng nhập lại Apple ID chính → mở game → tiến trình trở về bình thường
⚠️ KHÔNG đăng xuất iCloud chính trong Settings → tên, CHỈ đăng xuất App Store

🤖 Android — Google Play:
- Khách cung cấp email Gmail và mật khẩu đã đăng nhập trong game
- Nếu chưa đồng bộ: "Mở game → Settings → Kết nối Google Play → Đồng bộ xong báo bên mình để nạp"
- Cần online để xác nhận đăng nhập / OTP từ Google

📘 Facebook:
- Khách cung cấp email và mật khẩu Facebook đã đăng nhập trong game
- Cần online để xác nhận đăng nhập / OTP từ Facebook

━━━ QUY TRÌNH CHUYỂN TIẾP THÔNG TIN TÀI KHOẢN ━━━
Khi nhân viên yêu cầu thông tin tài khoản để xử lý đơn:
→ Nếu có: Trả lời theo format:
  "Thông tin tài khoản đơn #{order_id}:
  📧 Tài khoản: [account]
  🔑 Mật khẩu: [password]"
→ Nếu chưa có → "Nhân viên vui lòng kiểm tra lại đơn, chưa có thông tin tài khoản."

━━━ THÔNG TIN SHOP ━━━
- Hỗ trợ: Roblox, Genshin Impact, Mobile Legends và nhiều game khác
- Thời gian xử lý: 5–30 phút (8:00–22:00)
- Hoàn tiền 100% vào ví nếu đơn lỗi
"""

_client = None  # lazy init

# ── Language / credential detection ──────────────────────────────────────────

_VIET_DIACRITICS = frozenset(
    "àáâãảạăắặầẩẫậấảạđèéêềếểễệìíịòóôõọốồổỗộớờởỡợùúụứừửữựỳỷỹỵ"
    "ÀÁÂÃẢẠĂẮẶẦẨẪẬẤẢẠĐÈÉÊỀẾỂỄỆÌÍỊÒÓÔÕỌỐỒỔỖỘỚỜỞỠỢÙÚỤỨỪỬỮỰỲỶỸỴ"
)
_CREDENTIAL_KEYWORDS = frozenset([
    "password", "pass", "mật khẩu", "mk", "credentials", "account info",
    "otp", "xác nhận", "verify", "send account", "process my order",
    "gửi thông tin", "thông tin tài khoản", "cần tài khoản", "cần mật khẩu",
    "cấp lại mật khẩu", "đổi mật khẩu",
])


def _is_vietnamese(text: str) -> bool:
    """True nếu tin nhắn chứa ít nhất một ký tự có dấu tiếng Việt."""
    return any(c in _VIET_DIACRITICS for c in text)


def _needs_ai(message: str) -> bool:
    """
    True nếu tin nhắn cần AI xử lý:
    - Tin nhắn tiếng Anh (không có ký tự tiếng Việt) → dùng AI paraphrase
    - Hoặc tin nhắn liên quan đến tài khoản/mật khẩu/OTP → dùng AI
    Ngược lại (tiếng Việt thông thường) → trả về None để bot relay trực tiếp.
    """
    lower = message.lower()
    if any(kw in lower for kw in _CREDENTIAL_KEYWORDS):
        return True
    return not _is_vietnamese(message)


def _get_client():
    global _client
    if _client is not None:
        return _client
    try:
        from openai import AsyncOpenAI
        from config import OPENAI_API_KEY
        if not OPENAI_API_KEY:
            return None
        _client = AsyncOpenAI(api_key=OPENAI_API_KEY)
    except ImportError:
        logger.warning("openai package chưa được cài. Chạy: pip install openai")
    except Exception:
        logger.exception("Không thể khởi tạo OpenAI client")
    return _client


async def get_ai_reply(
    customer_message: str,
    order_context: dict | None = None,
    history: list[dict] | None = None,
    image_urls: list[str] | None = None,
) -> str | None:
    """
    Gọi OpenAI API để tạo phản hồi tự động cho khách.

    Args:
        customer_message: Tin nhắn hiện tại (có thể rỗng nếu chỉ gửi ảnh).
        order_context:    Thông tin đơn hàng (từ DB orders).
        history:          Lịch sử chat gần nhất (list of chat_messages rows).
        image_urls:       Danh sách URL ảnh đính kèm từ Discord (optional).

    Returns:
        Chuỗi phản hồi AI, hoặc None nếu không cấu hình / lỗi / tiếng Việt thông thường.
    """
    # ── Language routing ───────────────────────────────────────────────────────
    # Tin nhắn tiếng Việt không liên quan đến credential → không cần AI
    if customer_message and not _needs_ai(customer_message):
        return None

    client = _get_client()
    if client is None:
        return None

    system_content = _SYSTEM_PROMPT

    if order_context:
        note = order_context.get("game_account_note", "") or ""
        platform_label = ""
        password = ""
        if note.startswith("PLATFORM:"):
            lines = note.split("\n")
            platform_raw = lines[0].replace("PLATFORM:", "").strip()
            platform_label = {
                "ios":       "iOS — iCloud/Game Center",
                "ios_clone": "iOS — Clone iCloud (bảo mật)",
                "android":   "Android — Google Play",
                "facebook":  "Facebook",
            }.get(platform_raw.lower(), platform_raw)
            for ln in lines[1:]:
                if ln.startswith("PASSWORD:"):
                    password = ln.replace("PASSWORD:", "").strip()

        system_content += (
            f"\n\nĐƠN HÀNG HIỆN TẠI:\n"
            f"- Order ID: #{order_context.get('id', 'N/A')}\n"
            f"- Game: {order_context.get('game_id', 'N/A')}\n"
            f"- Gói nạp: {order_context.get('package_name', 'N/A')}\n"
            f"- Giá: ${order_context.get('price_usd', 0):.2f} USD\n"
            f"- Trạng thái: {order_context.get('status', 'N/A')}\n"
        )
        if platform_label:
            system_content += f"- Phương thức nạp: {platform_label}\n"

        account = order_context.get("game_account") or ""
        if account:
            system_content += f"\nTHÔNG TIN TÀI KHOẢN ĐƠN:\n- Tài khoản: {account}\n"
            if password:
                system_content += f"- Mật khẩu: {password}\n"
        else:
            system_content += "\nTHÔNG TIN TÀI KHOẢN ĐƠN: chưa có thông tin tài khoản.\n"

    messages: list[dict] = [{"role": "system", "content": system_content}]

    # Lịch sử chat (tối đa 10 tin, bỏ qua tin bị block)
    if history:
        for msg in history[-10:]:
            if msg.get("blocked"):
                continue
            sender = msg.get("sender_type", "customer")
            role = "assistant" if sender in ("staff", "bot") else "user"
            content = msg.get("content", "").strip()
            if content:
                messages.append({"role": role, "content": content})

    # Tin nhắn hiện tại: text + ảnh (vision)
    if image_urls:
        content_parts: list[dict] = []
        if customer_message:
            content_parts.append({"type": "text", "text": customer_message})
        for url in image_urls:
            content_parts.append({"type": "image_url", "image_url": {"url": url, "detail": "low"}})
        messages.append({"role": "user", "content": content_parts})
    else:
        messages.append({"role": "user", "content": customer_message})

    try:
        from config import OPENAI_MODEL
        model = OPENAI_MODEL
    except ImportError:
        model = "gpt-4o-mini"

    try:
        resp = await client.chat.completions.create(
            model=model,
            messages=messages,
            max_tokens=300,
            temperature=0.7,
        )
        reply = resp.choices[0].message.content
        return reply.strip() if reply else None
    except Exception:
        logger.exception("OpenAI API lỗi khi tạo phản hồi")
        return None
