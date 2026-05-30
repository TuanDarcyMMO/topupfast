import random
import string
from datetime import datetime, timedelta

import aiohttp

from config import SUPABASE_URL, SUPABASE_KEY, PAYMENT_EXPIRY_MINUTES

_HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=representation",
}
_REST = f"{SUPABASE_URL}/rest/v1"

_session: aiohttp.ClientSession | None = None


# ------------------------------------------------------------------ init --

async def init_db() -> None:
    global _session
    _session = aiohttp.ClientSession(headers=_HEADERS)


def _sess() -> aiohttp.ClientSession:
    if _session is None:
        raise RuntimeError("Database chua duoc khoi tao. Goi init_db() truoc.")
    return _session


async def _get(table: str, params: dict) -> list:
    async with _sess().get(f"{_REST}/{table}", params=params) as r:
        r.raise_for_status()
        return await r.json()


async def _post(table: str, data: dict) -> dict:
    async with _sess().post(f"{_REST}/{table}", json=data) as r:
        if not r.ok:
            body = await r.text()
            raise RuntimeError(f"Supabase {r.status}: {body}")
        rows = await r.json()
        return rows[0] if isinstance(rows, list) else rows


async def _upsert_bulk(table: str, rows: list[dict], on_conflict: str = "") -> None:
    """Batch upsert — gửi nhiều row trong 1 request, merge theo unique key."""
    headers = {**_HEADERS, "Prefer": "resolution=merge-duplicates,return=minimal"}
    params = {"on_conflict": on_conflict} if on_conflict else {}
    async with _sess().post(f"{_REST}/{table}", json=rows, headers=headers, params=params) as r:
        r.raise_for_status()


async def _patch(table: str, match: dict, data: dict) -> None:
    params = {k: f"eq.{v}" for k, v in match.items()}
    async with _sess().patch(f"{_REST}/{table}", params=params, json=data) as r:
        r.raise_for_status()


async def get_or_create_user(discord_id: str, avatar_url=None, default_language: str = "vi") -> dict:
    rows = await _get("users", {"discord_id": f"eq.{discord_id}"})
    if rows:
        user = rows[0]
        if avatar_url and user.get("avatar_url") != avatar_url:
            await _patch("users", {"discord_id": discord_id}, {
                "avatar_url": avatar_url,
                "updated_at": datetime.utcnow().isoformat(),
            })
        return user
    return await _post("users", {"discord_id": discord_id, "avatar_url": avatar_url, "language": default_language})


async def upsert_users_bulk(users: list[dict], default_language: str = "vi") -> None:
    """
    Upsert nhiều user cùng lúc — 1 HTTP request cho cả batch.
    users: list of {"discord_id": str, "avatar_url": str | None}
    default_language: ngôn ngữ mặc định cho user mới ('vi' cho guild chính, 'en' cho guild phụ).
    Chỉ set language khi INSERT mới — không ghi đè user đã có.
    """
    if not users:
        return
    rows = [{**u, "language": default_language} for u in users]
    await _upsert_bulk("users", rows, on_conflict="discord_id")


async def get_all_user_discord_ids() -> list[str]:
    """Lấy toàn bộ discord_id trong DB (dùng cho DM blast)."""
    # Supabase mặc định limit 1000, dùng Range header để lấy hết
    all_ids: list[str] = []
    offset = 0
    batch = 1000
    while True:
        async with _sess().get(
            f"{_REST}/users",
            params={"select": "discord_id", "order": "id.asc", "limit": str(batch), "offset": str(offset)},
        ) as r:
            r.raise_for_status()
            rows = await r.json()
        all_ids.extend(row["discord_id"] for row in rows)
        if len(rows) < batch:
            break
        offset += batch
    return all_ids


async def get_user(discord_id: str):
    rows = await _get("users", {"discord_id": f"eq.{discord_id}"})
    return rows[0] if rows else None


async def add_balance(discord_id: str, amount_usd: float) -> None:
    """Cộng tiền vào balance. Dùng round2 để tránh lỗi floating point (5.9999 → 6.00)."""
    def _round2(n: float) -> float:
        return round(float(n) * 100) / 100

    user = await get_user(discord_id)
    if user is None:
        return
    new_balance = _round2((user.get("balance") or 0.0) + _round2(amount_usd))
    await _patch("users", {"discord_id": discord_id}, {
        "balance": new_balance,
        "updated_at": datetime.utcnow().isoformat(),
    })


def generate_tfa_code() -> str:
    digits = "".join(__import__("random").choices(__import__("string").digits, k=5))
    return f"TFA{digits}"


async def create_transaction(*, discord_id, user_id, type, provider, amount_usd,
    amount_vnd=0, currency=None, coin=None, tfa_code=None, provider_ref=None,
    invoice_url=None, qr_url=None, discord_channel_id=None, discord_message_id=None):
    from datetime import timedelta
    expires_at = (datetime.utcnow() + timedelta(minutes=PAYMENT_EXPIRY_MINUTES)).isoformat()
    return await _post("transactions", {
        "discord_id": discord_id, "user_id": user_id, "type": type,
        "provider": provider, "amount_usd": amount_usd, "amount_vnd": amount_vnd,
        "currency": currency, "coin": coin, "tfa_code": tfa_code,
        "provider_ref": provider_ref, "invoice_url": invoice_url, "qr_url": qr_url,
        "discord_channel_id": discord_channel_id, "discord_message_id": discord_message_id,
        "expires_at": expires_at,
    })


async def update_transaction(tx_id: int, **fields) -> None:
    if not fields:
        return
    fields["updated_at"] = datetime.utcnow().isoformat()
    await _patch("transactions", {"id": tx_id}, fields)


async def get_transaction(tx_id: int):
    rows = await _get("transactions", {"id": f"eq.{tx_id}"})
    return rows[0] if rows else None


async def get_transaction_by_tfa(tfa_code: str):
    rows = await _get("transactions", {"tfa_code": f"eq.{tfa_code}", "status": "eq.pending", "order": "created_at.desc", "limit": "1"})
    return rows[0] if rows else None


async def get_transaction_by_provider_ref(provider_ref: str):
    rows = await _get("transactions", {"provider_ref": f"eq.{provider_ref}", "order": "created_at.desc", "limit": "1"})
    return rows[0] if rows else None


async def is_sepay_tx_processed(sepay_tx_id: str) -> bool:
    """
    Kiểm tra xem một SePay transaction ID đã được xử lý chưa.
    Dùng để tránh cộng tiền kép khi cùng 1 SePay tx được tìm thấy nhiều lần.
    """
    if not sepay_tx_id:
        return False
    rows = await _get(
        "transactions",
        {
            "provider_ref": f"eq.{sepay_tx_id}",
            "status": "eq.completed",
            "limit": "1",
        },
    )
    return len(rows) > 0


async def get_user_transactions(discord_id: str, limit: int = 10) -> list:
    rows = await _get("transactions", {"discord_id": f"eq.{discord_id}", "order": "created_at.desc", "limit": str(limit)})
    return rows or []


# ================================================================= Orders ==

async def create_order(*, discord_id: str, user_id: int, game_id: str,
                       package_id: str, package_name: str, price_usd: float,
                       game_account: str, game_account_note: str = "") -> dict:
    return await _post("orders", {
        "discord_id": discord_id,
        "user_id": user_id,
        "game_id": game_id,
        "package_id": package_id,
        "package_name": package_name,
        "price_usd": price_usd,
        "game_account": game_account,
        "game_account_note": game_account_note,
        "status": "pending",
    })


async def update_order(order_id: int, **fields) -> None:
    if not fields:
        return
    fields["updated_at"] = datetime.utcnow().isoformat()
    await _patch("orders", {"id": order_id}, fields)


async def get_order(order_id: int) -> dict | None:
    rows = await _get("orders", {"id": f"eq.{order_id}"})
    return rows[0] if rows else None


async def get_user_orders(discord_id: str, limit: int = 10) -> list:
    rows = await _get("orders", {
        "discord_id": f"eq.{discord_id}",
        "order": "created_at.desc",
        "limit": str(limit),
    })
    return rows or []


async def deduct_balance(discord_id: str, amount_usd: float) -> bool:
    """
    Trừ balance. Trả về True nếu thành công, False nếu không đủ tiền.
    Dùng round(float*100)/100 để tránh floating point.
    """
    user = await get_user(discord_id)
    if not user:
        return False
    r2 = lambda n: round(float(n) * 100) / 100
    current = r2(user.get("balance", 0))
    cost = r2(amount_usd)
    if current < cost:
        return False
    new_balance = r2(current - cost)
    await _patch("users", {"discord_id": discord_id}, {
        "balance": new_balance,
        "updated_at": datetime.utcnow().isoformat(),
    })
    return True


# ================================================================= Staff ==

async def get_staff_by_username(username: str) -> dict | None:
    rows = await _get("staff", {"username": f"eq.{username}", "active": "eq.true"})
    return rows[0] if rows else None


async def get_staff_by_discord(discord_id: str) -> dict | None:
    rows = await _get("staff", {"discord_id": f"eq.{discord_id}"})
    return rows[0] if rows else None


async def create_staff(discord_id: str, username: str, password_hash: str, role: str = "staff") -> dict:
    return await _post("staff", {
        "discord_id": discord_id,
        "username": username,
        "password_hash": password_hash,
        "role": role,
        "active": True,
    })


async def deactivate_staff(discord_id: str) -> None:
    await _patch("staff", {"discord_id": discord_id}, {"active": False})


async def get_all_staff() -> list:
    return await _get("staff", {"order": "created_at.desc"})


# ─── Dashboard stats ─────────────────────────────────────────────────────────

async def get_dashboard_stats() -> dict:
    """Revenue + order counts for admin dashboard."""
    all_orders = await _get("orders", {"order": "created_at.desc", "limit": "1000"})
    all_txs    = await _get("transactions", {"status": "eq.completed", "limit": "1000"})

    total_revenue_usd = sum(float(t.get("amount_usd", 0)) for t in all_txs)
    total_orders      = len(all_orders)
    pending_orders    = sum(1 for o in all_orders if o.get("status") == "pending")
    delivering_orders = sum(1 for o in all_orders if o.get("status") == "delivering")
    completed_orders  = sum(1 for o in all_orders if o.get("status") == "completed")
    failed_orders     = sum(1 for o in all_orders if o.get("status") in ("failed", "refunded"))

    return {
        "total_revenue_usd": round(total_revenue_usd, 2),
        "total_orders": total_orders,
        "pending_orders": pending_orders,
        "delivering_orders": delivering_orders,
        "completed_orders": completed_orders,
        "failed_orders": failed_orders,
    }


async def get_orders_for_dashboard(status: str | None = None, claimed_by: str | None = None, limit: int = 100) -> list:
    params: dict = {"order": "created_at.desc", "limit": str(limit)}
    if status:
        params["status"] = f"eq.{status}"
    if claimed_by:
        params["claimed_by"] = f"eq.{claimed_by}"
    return await _get("orders", params)


async def get_open_ticket_channel(discord_id: str) -> str | None:
    """Return the ticket_channel_id of the user's most recent open (pending/delivering) order, or None."""
    rows = await _get("orders", {
        "discord_id": f"eq.{discord_id}",
        "status": "in.(pending,delivering)",
        "ticket_channel_id": "not.is.null",
        "order": "created_at.desc",
        "limit": "1",
    })
    if rows:
        return rows[0].get("ticket_channel_id")
    return None


# ============================================================ Commission ==

COMMISSION_RATE = 0.50   # Staff nhận 50% giá trị đơn hàng


async def get_orders_pending_commission() -> list:
    """Lấy danh sách đơn completed, chưa trả hoa hồng, và đã qua 24h."""
    cutoff = (datetime.utcnow() - timedelta(hours=24)).isoformat()
    return await _get("orders", {
        "status": "eq.completed",
        "commission_paid": "eq.false",
        "completed_by": "not.is.null",
        "completed_at": f"lt.{cutoff}",
        "order": "completed_at.asc",
        "limit": "200",
    })


async def mark_commission_paid(order_id: int, commission_amount: float) -> None:
    await _patch("orders", {"id": order_id}, {
        "commission_paid": True,
        "commission_amount": commission_amount,
        "updated_at": datetime.utcnow().isoformat(),
    })


async def add_staff_commission(discord_id: str, amount: float) -> None:
    """Cộng hoa hồng vào tài khoản staff."""
    staff = await get_staff_by_discord(discord_id)
    if not staff:
        return
    r2 = lambda n: round(float(n) * 100) / 100
    new_balance = r2((staff.get("commission_balance") or 0.0) + r2(amount))
    await _patch("staff", {"discord_id": discord_id}, {
        "commission_balance": new_balance,
    })


async def get_staff_commission_balance(discord_id: str) -> float:
    staff = await get_staff_by_discord(discord_id)
    if not staff:
        return 0.0
    return float(staff.get("commission_balance") or 0.0)


async def deduct_staff_commission(discord_id: str, amount: float) -> bool:
    """Trừ hoa hồng khi staff rút tiền. Trả về False nếu không đủ."""
    staff = await get_staff_by_discord(discord_id)
    if not staff:
        return False
    r2 = lambda n: round(float(n) * 100) / 100
    current = r2(staff.get("commission_balance") or 0.0)
    amt = r2(amount)
    if current < amt:
        return False
    await _patch("staff", {"discord_id": discord_id}, {
        "commission_balance": r2(current - amt),
    })
    return True


# ====================================================== Staff bank accounts ==

async def get_staff_bank_accounts(discord_id: str) -> list:
    return await _get("staff_bank_accounts", {
        "staff_discord_id": f"eq.{discord_id}",
        "order": "created_at.asc",
    })


async def add_staff_bank_account(
    discord_id: str,
    bank_name: str,
    account_number: str,
    account_holder: str,
    branch: str = "",
    qr_image: str = "",
) -> dict:
    return await _post("staff_bank_accounts", {
        "staff_discord_id": discord_id,
        "bank_name": bank_name,
        "account_number": account_number,
        "account_holder": account_holder,
        "branch": branch or None,
        "qr_image": qr_image or None,
    })


async def delete_staff_bank_account(account_id: int, discord_id: str) -> None:
    """Xóa tài khoản ngân hàng (chỉ của chính staff đó)."""
    params = {"id": f"eq.{account_id}", "staff_discord_id": f"eq.{discord_id}"}
    async with _sess().delete(f"{_REST}/staff_bank_accounts", params=params) as r:
        r.raise_for_status()


# ================================================================== Games ==

async def get_all_games(include_inactive: bool = False) -> list:
    params: dict = {"order": "sort_order.asc,created_at.asc"}
    if not include_inactive:
        params["active"] = "eq.true"
    return await _get("games", params)


async def create_game(game_id: str, name: str, emoji: str = "🎮",
                      icon_url: str = "", platform: str = "all",
                      sort_order: int = 0) -> dict:
    return await _post("games", {
        "id": game_id,
        "name": name,
        "emoji": emoji,
        "icon_url": icon_url or None,
        "platform": platform,
        "active": True,
        "sort_order": sort_order,
    })


async def update_game(game_id: str, **fields) -> None:
    if not fields:
        return
    async with _sess().patch(f"{_REST}/games", params={"id": f"eq.{game_id}"}, json=fields) as r:
        r.raise_for_status()


async def delete_game(game_id: str) -> None:
    """Xóa game (cascade xóa packages liên quan)."""
    async with _sess().delete(f"{_REST}/games", params={"id": f"eq.{game_id}"}) as r:
        r.raise_for_status()


# =============================================================== Packages ==

async def get_packages_by_game(game_id: str, include_inactive: bool = False) -> list:
    params: dict = {"game_id": f"eq.{game_id}", "order": "sort_order.asc,created_at.asc"}
    if not include_inactive:
        params["active"] = "eq.true"
    return await _get("packages", params)


async def create_package(package_id: str, game_id: str, category: str,
                         name: str, price_usd: float,
                         description: str = "", sort_order: int = 0,
                         platform: str = "all") -> dict:
    return await _post("packages", {
        "id": package_id,
        "game_id": game_id,
        "category": category,
        "name": name,
        "price_usd": price_usd,
        "description": description or "",
        "active": True,
        "sort_order": sort_order,
        "platform": platform,
    })


async def update_package(package_id: str, **fields) -> None:
    if not fields:
        return
    async with _sess().patch(f"{_REST}/packages", params={"id": f"eq.{package_id}"}, json=fields) as r:
        r.raise_for_status()


async def delete_package(package_id: str) -> None:
    async with _sess().delete(f"{_REST}/packages", params={"id": f"eq.{package_id}"}) as r:
        r.raise_for_status()


async def get_bank_account(account_id: int) -> dict | None:
    rows = await _get("staff_bank_accounts", {"id": f"eq.{account_id}"})
    return rows[0] if rows else None


# ====================================================== Withdrawal requests ==

async def create_withdrawal_request(
    discord_id: str,
    username: str,
    bank_account_id: int,
    amount_usd: float,
    exchange_rate: float,
) -> dict:
    amount_vnd = int(round(amount_usd * exchange_rate))
    return await _post("withdrawal_requests", {
        "staff_discord_id": discord_id,
        "staff_username": username,
        "bank_account_id": bank_account_id,
        "amount_usd": round(amount_usd, 2),
        "amount_vnd": amount_vnd,
        "exchange_rate": exchange_rate,
        "status": "pending",
    })


async def get_withdrawal_requests(status: str | None = None, discord_id: str | None = None) -> list:
    params: dict = {"order": "created_at.desc", "limit": "200"}
    if status:
        params["status"] = f"eq.{status}"
    if discord_id:
        params["staff_discord_id"] = f"eq.{discord_id}"
    return await _get("withdrawal_requests", params)


async def get_withdrawal_request(req_id: int) -> dict | None:
    rows = await _get("withdrawal_requests", {"id": f"eq.{req_id}"})
    return rows[0] if rows else None


async def complete_withdrawal(req_id: int, admin_discord_id: str, note: str = "") -> None:
    await _patch("withdrawal_requests", {"id": req_id}, {
        "status": "completed",
        "completed_by": admin_discord_id,
        "completed_at": datetime.utcnow().isoformat(),
        "admin_note": note or None,
    })


async def reject_withdrawal(req_id: int, admin_discord_id: str, note: str = "") -> None:
    await _patch("withdrawal_requests", {"id": req_id}, {
        "status": "rejected",
        "completed_by": admin_discord_id,
        "completed_at": datetime.utcnow().isoformat(),
        "admin_note": note or None,
    })


# ============================================================ Chat messages ==

async def save_chat_message(
    order_id: int,
    sender_type: str,
    sender_id: str,
    sender_name: str,
    content: str,
    blocked: bool = False,
    block_reason: str = "",
    image_urls: list[str] | None = None,
) -> dict:
    """Lưu tin nhắn vào bảng chat_messages.

    image_urls được encode vào cuối content dưới dạng ``\\n__IMGS__:url1||url2``
    để tránh cần thêm cột DB. Dashboard sẽ parse và render ảnh.
    """
    if image_urls:
        content = content + "\n__IMGS__:" + "||".join(image_urls)
    return await _post("chat_messages", {
        "order_id": order_id,
        "sender_type": sender_type,
        "sender_id": sender_id,
        "sender_name": sender_name,
        "content": content,
        "blocked": blocked,
        "block_reason": block_reason or None,
    })


async def get_chat_history(order_id: int, limit: int = 100) -> list:
    """Lấy lịch sử chat (chỉ tin chưa bị block) của 1 đơn hàng."""
    return await _get("chat_messages", {
        "order_id": f"eq.{order_id}",
        "blocked": "eq.false",
        "order": "created_at.asc",
        "limit": str(limit),
    })


async def get_order_by_ticket_channel(channel_id: str) -> dict | None:
    """Tìm đơn hàng theo ticket_channel_id Discord."""
    rows = await _get("orders", {
        "ticket_channel_id": f"eq.{channel_id}",
        "order": "created_at.desc",
        "limit": "1",
    })
    return rows[0] if rows else None


# ============================================================ Staff violations ==

async def save_staff_violation(
    staff_discord_id: str,
    staff_username: str,
    order_id: int | None,
    content: str,
    reason: str,
) -> dict:
    """Ghi lại một vi phạm chat của nhân viên."""
    return await _post("staff_violations", {
        "staff_discord_id": staff_discord_id,
        "staff_username": staff_username,
        "order_id": order_id,
        "content": content,
        "reason": reason,
    })


async def get_staff_violations(discord_id: str | None = None, limit: int = 50) -> list:
    """Lấy lịch sử vi phạm. Nếu discord_id=None → lấy tất cả."""
    params: dict = {"order": "created_at.desc", "limit": str(limit)}
    if discord_id:
        params["staff_discord_id"] = f"eq.{discord_id}"
    return await _get("staff_violations", params)


async def get_chat_inbox(limit: int = 50) -> list:
    """Trả về danh sách các đơn có tin nhắn chat, kèm tin nhắn cuối và thông tin đơn hàng."""
    # Lấy tin nhắn gần nhất (không bị block) để biết đơn nào có chat
    msgs = await _get("chat_messages", {
        "select": "order_id,content,sender_type,sender_name,created_at",
        "blocked": "eq.false",
        "order": "created_at.desc",
        "limit": "300",
    })
    if not msgs:
        return []

    # Gộp theo order_id, giữ tin nhắn cuối cùng mỗi đơn
    seen: dict[int, dict] = {}
    for m in msgs:
        oid = m["order_id"]
        if oid not in seen:
            seen[oid] = m

    if not seen:
        return []

    # Lấy thông tin đơn hàng cho các order_id đó
    ids_str = ",".join(str(i) for i in seen)
    orders = await _get("orders", {
        "select": "id,game_id,package_name,status,game_account",
        "id": f"in.({ids_str})",
    })
    order_map: dict[int, dict] = {o["id"]: o for o in (orders or [])}

    result = []
    for oid, last_msg in seen.items():
        result.append({
            "order_id": oid,
            "last_message": last_msg["content"],
            "last_sender_type": last_msg["sender_type"],
            "last_sender_name": last_msg.get("sender_name", ""),
            "last_at": last_msg["created_at"],
            "order": order_map.get(oid, {}),
        })

    result.sort(key=lambda x: x["last_at"], reverse=True)
    return result[:limit]
