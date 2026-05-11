"""
Product catalog helpers - đọc dữ liệu từ bảng `games` và `packages` trong DB.

Cấu trúc ID gói: {game_id}.{category}.{tier}
Ví dụ: 'roblox.robux.400', 'genshin.crystal.980', 'mlbb.diamond.86'

Thêm game/gói mới: chạy SQL INSERT vào bảng games/packages trên Supabase.
Xem seed data trong schema.sql.
"""

from services.database import _get


async def get_active_games() -> list[dict]:
    """Trả về tất cả game đang active, sắp xếp theo sort_order."""
    return await _get("games", {"active": "eq.true", "order": "sort_order.asc"})


async def get_game(game_id: str) -> dict | None:
    rows = await _get("games", {"id": f"eq.{game_id}", "active": "eq.true"})
    return rows[0] if rows else None


async def get_categories(game_id: str) -> list[str]:
    """
    Trả về danh sách category duy nhất của game, sắp xếp theo sort_order gói nhỏ nhất.
    """
    rows = await _get("packages", {
        "game_id": f"eq.{game_id}",
        "active": "eq.true",
        "order": "sort_order.asc",
        "select": "category",
    })
    seen: list[str] = []
    for r in rows:
        c = r["category"]
        if c not in seen:
            seen.append(c)
    return seen


async def get_packages(game_id: str, category: str) -> list[dict]:
    """Trả về các gói trong một category, sắp xếp theo giá."""
    return await _get("packages", {
        "game_id": f"eq.{game_id}",
        "category": f"eq.{category}",
        "active": "eq.true",
        "order": "sort_order.asc",
    })


async def get_package(package_id: str) -> dict | None:
    """Lấy gói theo ID đầy đủ (ví dụ: 'roblox.robux.400')."""
    rows = await _get("packages", {"id": f"eq.{package_id}", "active": "eq.true"})
    return rows[0] if rows else None
