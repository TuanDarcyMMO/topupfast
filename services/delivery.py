"""
Delivery dispatcher - xử lý giao hàng sau khi order được đặt.
Mỗi game có handler riêng. Thêm game mới: tạo hàm _deliver_<game_id> và
đăng ký vào _HANDLERS.

Trả về (success: bool, note: str)
  success=True  → đã giao, note = thông tin giao hàng
  success=False → cần admin xử lý thủ công, note = lý do
"""

import logging

logger = logging.getLogger(__name__)


# ─── Game handlers ────────────────────────────────────────────────────────────

async def _deliver_roblox(order: dict) -> tuple[bool, str]:
    """
    Giao Robux qua API Roblox.
    TODO: tích hợp API thực tế (Roblox Gift Card / third-party reseller API).
    Hiện tại: manual delivery.
    """
    return False, "Roblox delivery requires manual processing."


async def _deliver_genshin(order: dict) -> tuple[bool, str]:
    """
    Giao Genesis Crystals / Primogems qua Mihoyo API.
    TODO: tích hợp API.
    """
    return False, "Genshin delivery requires manual processing."


async def _deliver_mlbb(order: dict) -> tuple[bool, str]:
    """
    Giao Diamonds qua Moonton API / Codashop API.
    TODO: tích hợp API.
    """
    return False, "MLBB delivery requires manual processing."


# ─── Dispatcher ───────────────────────────────────────────────────────────────

_HANDLERS: dict[str, callable] = {
    "roblox":  _deliver_roblox,
    "genshin": _deliver_genshin,
    "mlbb":    _deliver_mlbb,
}


async def deliver(order: dict) -> tuple[bool, str]:
    """
    Gọi handler tương ứng với game_id của order.
    Trả về (success, note).
    """
    game_id = order.get("game_id", "")
    handler = _HANDLERS.get(game_id)

    if handler is None:
        note = f"No delivery handler for game '{game_id}'. Needs manual delivery."
        logger.warning(f"[Delivery] {note} order_id={order.get('id')}")
        return False, note

    try:
        success, note = await handler(order)
        logger.info(
            f"[Delivery] game={game_id} order_id={order.get('id')} "
            f"success={success} note={note!r}"
        )
        return success, note
    except Exception as exc:
        note = f"Delivery error: {exc}"
        logger.exception(f"[Delivery] game={game_id} order_id={order.get('id')}: {exc}")
        return False, note
