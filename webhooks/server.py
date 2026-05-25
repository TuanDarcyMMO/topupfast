"""
Webhook server (aiohttp) nhận thông báo thanh toán từ SePay và NowPayments.
Chạy cùng process với Discord bot thông qua asyncio.gather().

Endpoints:
  GET  /health                  - health check
  POST /webhook/sepay           - nhận từ SePay khi có chuyển khoản ngân hàng
  POST /webhook/nowpayments     - IPN từ NowPayments khi crypto payment finished

  POST /api/login               - đăng nhập dashboard (trả JWT-like token)
  GET  /api/me                  - thông tin staff hiện tại
  GET  /api/orders              - danh sách đơn (?status=pending|delivering|completed)
  POST /api/orders/{id}/claim   - staff nhận đơn
  POST /api/orders/{id}/complete- staff hoàn thành đơn
  POST /api/orders/{id}/reject  - staff từ chối đơn (có thể yêu cầu hoàn tiền)
  POST /api/orders/{id}/refund  - admin hoàn tiền trực tiếp
  POST /api/orders/{id}/approve-refund - admin duyệt yêu cầu hoàn tiền
  POST /api/orders/{id}/deny-refund    - admin từ chối yêu cầu hoàn tiền
  GET  /api/stats               - admin dashboard stats

  GET  /dashboard               - serve dashboard HTML
"""

import asyncio
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
from pathlib import Path

import aiohttp.web
import bcrypt
import discord

import services.database as db
from config import EXCHANGE_RATE, WEBHOOK_HOST, WEBHOOK_PORT
from services.nowpayments import verify_ipn as np_verify_ipn, NP_STATUS_MAP, round2 as np_round2
from services.sepay import validate_webhook as sepay_validate, extract_tfa_code
from services.chat_guard import check_staff_message

logger = logging.getLogger(__name__)

# ─── Simple in-memory session store ──────────────────────────────────────────
# token → {"discord_id": str, "username": str, "role": str, "expires": float}
_SESSIONS: dict[str, dict] = {}
_SESSION_TTL = 86400 * 7  # 7 ngày

_DASHBOARD_DIR = Path(__file__).parent.parent / "dashboard"


def _new_token(staff: dict) -> str:
    token = secrets.token_urlsafe(32)
    _SESSIONS[token] = {
        "discord_id": staff["discord_id"],
        "username": staff["username"],
        "role": staff["role"],
        "expires": time.time() + _SESSION_TTL,
    }
    return token


def _get_session(request: aiohttp.web.Request) -> dict | None:
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        token = auth[7:]
    else:
        token = request.cookies.get("session_token", "")
    sess = _SESSIONS.get(token)
    if sess and sess["expires"] > time.time():
        return sess
    if token in _SESSIONS:
        del _SESSIONS[token]
    return None


def _require_auth(role: str | None = None):
    """Decorator-like helper: returns (sess, None) or (None, error_response)."""
    async def check(request):
        sess = _get_session(request)
        if not sess:
            return None, aiohttp.web.json_response({"error": "Unauthorized"}, status=401)
        if role and sess["role"] != role:
            return None, aiohttp.web.json_response({"error": "Forbidden"}, status=403)
        return sess, None
    return check


_check_any   = _require_auth(None)
_check_admin = _require_auth("admin")


class WebhookServer:
    def __init__(self, bot: discord.Client):
        self.bot = bot
        self.app = aiohttp.web.Application()
        # order_id → set of active WebSocket connections from dashboard
        self._chat_connections: dict[int, set[aiohttp.web.WebSocketResponse]] = {}

        self.app.router.add_get("/health", self._health)
        self.app.router.add_post("/webhook/sepay", self._sepay)
        self.app.router.add_post("/webhook/nowpayments", self._nowpayments)
        # Dashboard API
        self.app.router.add_post("/api/login",                  self._api_login)
        self.app.router.add_get("/api/me",                      self._api_me)
        self.app.router.add_get("/api/orders",                  self._api_orders)
        self.app.router.add_post("/api/orders/{id}/claim",          self._api_claim)
        self.app.router.add_post("/api/orders/{id}/complete",        self._api_complete)
        self.app.router.add_post("/api/orders/{id}/reject",          self._api_reject)
        self.app.router.add_post("/api/orders/{id}/refund",          self._api_refund)
        self.app.router.add_post("/api/orders/{id}/approve-refund",  self._api_approve_refund)
        self.app.router.add_post("/api/orders/{id}/deny-refund",     self._api_deny_refund)
        self.app.router.add_get("/api/stats",                        self._api_stats)
        self.app.router.add_get("/api/staff",                   self._api_staff_list)        # Staff commission & bank accounts
        self.app.router.add_get("/api/staff/balance",                self._api_staff_balance)
        self.app.router.add_get("/api/staff/bank-accounts",          self._api_bank_accounts_list)
        self.app.router.add_post("/api/staff/bank-accounts",         self._api_bank_accounts_add)
        self.app.router.add_delete("/api/staff/bank-accounts/{id}",  self._api_bank_accounts_delete)
        # Withdrawal requests
        self.app.router.add_post("/api/staff/withdraw",              self._api_withdraw_request)
        self.app.router.add_get("/api/withdrawals",                  self._api_withdrawals_list)
        self.app.router.add_post("/api/withdrawals/{id}/complete",   self._api_withdrawal_complete)
        self.app.router.add_post("/api/withdrawals/{id}/reject",     self._api_withdrawal_reject)
        # Orders filtered (admin)
        self.app.router.add_get("/api/orders/export",                self._api_orders_export)        # Serve dashboard
        self.app.router.add_post("/api/logout",                   self._api_logout)
        # Games & Packages CRUD (admin only)
        self.app.router.add_get("/api/games",                        self._api_games_list)
        self.app.router.add_post("/api/games",                       self._api_games_create)
        self.app.router.add_patch("/api/games/{id}",                 self._api_games_update)
        self.app.router.add_delete("/api/games/{id}",                self._api_games_delete)
        self.app.router.add_get("/api/games/{id}/packages",          self._api_packages_list)
        self.app.router.add_post("/api/games/{id}/packages",         self._api_packages_create)
        self.app.router.add_patch("/api/packages/{id}",              self._api_packages_update)
        self.app.router.add_delete("/api/packages/{id}",             self._api_packages_delete)
        # Chat WebSocket + history + inbox
        self.app.router.add_get("/ws/chat",                          self._ws_chat)
        self.app.router.add_get("/api/orders/{id}/chat",             self._api_chat_history)
        self.app.router.add_get("/api/chats",                        self._api_chats)
        self.app.router.add_get("/dashboard", self._dashboard_html)
        self.app.router.add_get("/dashboard/", self._dashboard_html)

    async def start(self) -> None:
        """Khởi động aiohttp server và chạy mãi cho đến khi bị cancel."""
        runner = aiohttp.web.AppRunner(self.app, access_log=None)
        await runner.setup()
        site = aiohttp.web.TCPSite(runner, WEBHOOK_HOST, WEBHOOK_PORT)
        await site.start()
        logger.info("WebhookServer đang chạy tại %s:%s", WEBHOOK_HOST, WEBHOOK_PORT)
        try:
            await asyncio.Event().wait()   # chạy mãi đến khi task bị cancel
        finally:
            await runner.cleanup()

    # -------------------------------------------------------------- routes --

    async def _health(self, _: aiohttp.web.Request) -> aiohttp.web.Response:
        return aiohttp.web.json_response({"status": "ok"})

    async def _sepay(self, request: aiohttp.web.Request) -> aiohttp.web.Response:
        """Xử lý webhook từ SePay."""
        try:
            if not sepay_validate(dict(request.headers)):
                logger.warning("SePay webhook: xác thực thất bại")
                return aiohttp.web.Response(status=401, text="Unauthorized")

            data: dict = await request.json()
            logger.info(f"SePay webhook: {data}")

            content: str = (
                data.get("content")
                or data.get("transferContent")
                or data.get("description")
                or ""
            )
            amount_vnd: int = int(data.get("transferAmount") or data.get("amount") or 0)

            tfa = extract_tfa_code(content)
            if not tfa:
                logger.warning(f"SePay: không tìm thấy TFA trong nội dung: {content!r}")
                return aiohttp.web.json_response({"success": False, "msg": "TFA not found"})

            tx = await db.get_transaction_by_tfa(tfa)
            if not tx:
                logger.warning(f"SePay: không có giao dịch pending cho TFA {tfa}")
                return aiohttp.web.json_response({"success": False, "msg": "Transaction not found"})

            amount_usd = round(amount_vnd / EXCHANGE_RATE, 4)

            await db.update_transaction(tx["id"], status="completed",
                                        amount_usd=amount_usd, amount_vnd=amount_vnd)
            await db.add_balance(tx["discord_id"], amount_usd)

            logger.info(f"SePay OK: TFA={tfa} | {amount_vnd:,} VND = ${amount_usd} USD | user={tx['discord_id']}")
            asyncio.create_task(
                self._notify(tx, amount_usd=amount_usd, amount_vnd=amount_vnd, kind="bank")
            )
            return aiohttp.web.json_response({"success": True})

        except Exception:
            logger.exception("SePay webhook lỗi")
            return aiohttp.web.Response(status=500, text="Internal error")

    async def _nowpayments(self, request: aiohttp.web.Request) -> aiohttp.web.Response:
        """IPN từ NowPayments khi payment status thay đổi."""
        try:
            raw_body = await request.read()
            signature = request.headers.get("x-nowpayments-sig", "")

            if not np_verify_ipn(raw_body, signature):
                logger.warning("NowPayments IPN: xác thực HMAC thất bại")
                return aiohttp.web.Response(status=401, text="Unauthorized")

            data: dict = json.loads(raw_body)
            logger.info(f"NowPayments IPN: {data}")

            np_status = str(data.get("payment_status", "")).lower()
            if np_status != "finished":
                return aiohttp.web.json_response({"success": True, "msg": f"ignored status={np_status}"})

            np_payment_id = str(data.get("payment_id", ""))
            if not np_payment_id:
                return aiohttp.web.json_response({"success": False, "msg": "missing payment_id"})

            # Tìm tx theo provider_ref (np_payment_id)
            tx = await db.get_transaction_by_provider_ref(np_payment_id)
            if not tx:
                logger.warning(f"NowPayments IPN: không tìm thấy tx cho payment_id={np_payment_id}")
                return aiohttp.web.json_response({"success": True, "msg": "tx not found"})  # 200 tránh NP retry

            # Guard: chỉ credit nếu tx vẫn pending
            if tx["status"] != "pending":
                if tx["status"] == "expired":
                    # Thanh toán đến muộn sau khi session hết hạn → alert admin
                    actual_usd = np_round2(float(data.get("price_amount") or tx.get("amount_usd", 0)))
                    logger.error(
                        f"NowPayments IPN LATE PAYMENT: payment_id={np_payment_id}, "
                        f"user={tx['discord_id']}, ${actual_usd} USD - tx đã expired, KHÔNG cộng tiền!"
                    )
                    asyncio.create_task(self._alert_admin_late_payment(tx, np_payment_id, actual_usd))
                return aiohttp.web.json_response({"success": True, "msg": f"tx status={tx['status']}, skipped"})

            # Guard: idempotency
            if await db.is_sepay_tx_processed(np_payment_id):
                logger.warning(f"NowPayments IPN: payment_id={np_payment_id} đã xử lý, bỏ qua.")
                return aiohttp.web.json_response({"success": True, "msg": "already processed"})

            actual_usd = np_round2(float(data.get("price_amount") or tx.get("amount_usd", 0)))

            # Cập nhật DB trước, cộng tiền sau
            await db.update_transaction(tx["id"], status="completed", amount_usd=actual_usd)
            await db.add_balance(tx["discord_id"], actual_usd)

            logger.info(f"NowPayments IPN OK: payment_id={np_payment_id} | ${actual_usd} | user={tx['discord_id']}")
            asyncio.create_task(
                self._notify(tx, amount_usd=actual_usd, amount_vnd=None, kind="crypto")
            )
            return aiohttp.web.json_response({"success": True})

        except Exception:
            logger.exception("NowPayments IPN webhook lỗi")
            return aiohttp.web.Response(status=500, text="Internal error")

    async def _alert_admin_late_payment(self, tx: dict, np_payment_id: str, amount_usd: float) -> None:
        """Gửi cảnh báo admin khi có late payment sau khi session đã expired."""
        try:
            # Log rõ ràng để admin có thể manual credit
            logger.error(
                f"[ADMIN ALERT] LATE PAYMENT - NP payment_id={np_payment_id}, "
                f"user={tx['discord_id']}, amount=${amount_usd:.2f} USD, "
                f"tx_id={tx['id']}. Cần xử lý thủ công!"
            )
            # TODO: gửi DM cho admin Discord nếu cần
        except Exception:
            pass

    # --------------------------------------------------------- notification --

    async def _notify(
        self,
        tx: dict,
        *,
        amount_usd: float,
        amount_vnd: int | None,
        kind: str,
    ) -> None:
        """Cập nhật tin nhắn Discord khi thanh toán xong."""
        try:
            channel_id = tx.get("discord_channel_id")
            message_id = tx.get("discord_message_id")
            if not channel_id:
                return

            channel = self.bot.get_channel(int(channel_id))
            if channel is None:
                return

            if kind == "bank":
                desc = (
                    f"💰 Số tiền: `{amount_vnd:,} VND` (~`${amount_usd:.4f} USD`)\n"
                    f"📊 Kiểm tra số dư: `/sodu`"
                )
            else:
                coin = tx.get("coin", "")
                desc = (
                    f"💰 Số tiền: `${amount_usd:.4f} USD` ({coin})\n"
                    f"📊 Kiểm tra số dư: `/sodu`"
                )

            embed = discord.Embed(
                title="✅ Nạp tiền thành công!",
                description=desc,
                color=discord.Color.green(),
            )

            if message_id:
                try:
                    msg = await channel.fetch_message(int(message_id))
                    await msg.edit(embed=embed, view=None)
                    return
                except discord.NotFound:
                    pass

            # Fallback: gửi message mới
            await channel.send(f"<@{tx['discord_id']}>", embed=embed)

        except Exception:
            logger.exception("Không thể notify Discord")

    # -------------------------------------------------------------- start --

    async def _dashboard_html(self, request: aiohttp.web.Request) -> aiohttp.web.Response:
        html_file = _DASHBOARD_DIR / "index.html"
        if not html_file.exists():
            return aiohttp.web.Response(status=404, text="Dashboard not found")
        return aiohttp.web.Response(
            body=html_file.read_bytes(),
            content_type="text/html",
        )

    # ─── Auth ────────────────────────────────────────────────────────────────

    async def _api_login(self, request: aiohttp.web.Request) -> aiohttp.web.Response:
        try:
            body = await request.json()
        except Exception:
            return aiohttp.web.json_response({"error": "Invalid JSON"}, status=400)
        username = body.get("username", "").strip()
        password = body.get("password", "")
        if not username or not password:
            return aiohttp.web.json_response({"error": "Username and password required"}, status=400)
        staff = await db.get_staff_by_username(username)
        if not staff:
            return aiohttp.web.json_response({"error": "Invalid credentials"}, status=401)
        if not bcrypt.checkpw(password.encode(), staff["password_hash"].encode()):
            return aiohttp.web.json_response({"error": "Invalid credentials"}, status=401)
        token = _new_token(staff)
        resp = aiohttp.web.json_response({
            "username": staff["username"],
            "role": staff["role"],
        })
        resp.set_cookie(
            "session_token", token,
            httponly=True, samesite="Strict", path="/",
            max_age=_SESSION_TTL,
        )
        return resp

    async def _api_logout(self, request: aiohttp.web.Request) -> aiohttp.web.Response:
        token = request.cookies.get("session_token", "")
        if token in _SESSIONS:
            del _SESSIONS[token]
        resp = aiohttp.web.json_response({"ok": True})
        resp.del_cookie("session_token", path="/")
        return resp

    async def _api_me(self, request: aiohttp.web.Request) -> aiohttp.web.Response:
        sess, err = await _check_any(request)
        if err:
            return err
        return aiohttp.web.json_response({
            "username": sess["username"],
            "role": sess["role"],
            "discord_id": sess["discord_id"],
        })

    # ─── Orders ──────────────────────────────────────────────────────────────

    async def _api_orders(self, request: aiohttp.web.Request) -> aiohttp.web.Response:
        sess, err = await _check_any(request)
        if err:
            return err
        status = request.rel_url.query.get("status")  # None = all
        mine = request.rel_url.query.get("mine") == "true"
        claimed_by = sess["discord_id"] if mine else None
        orders = await db.get_orders_for_dashboard(status=status, claimed_by=claimed_by, limit=200)
        return aiohttp.web.json_response(orders)

    async def _api_claim(self, request: aiohttp.web.Request) -> aiohttp.web.Response:
        sess, err = await _check_any(request)
        if err:
            return err
        order_id = int(request.match_info["id"])
        order = await db.get_order(order_id)
        if not order:
            return aiohttp.web.json_response({"error": "Order not found"}, status=404)
        if order["status"] != "pending":
            return aiohttp.web.json_response({"error": "Order is not pending"}, status=409)
        from datetime import datetime
        await db.update_order(order_id,
            status="delivering",
            claimed_by=sess["discord_id"],
            claimed_at=datetime.utcnow().isoformat(),
        )
        # Notify ticket channel
        asyncio.create_task(self._notify_ticket(order, "📦 **Order claimed** by staff `{}`".format(sess["username"]), discord.Color.blue()))
        return aiohttp.web.json_response({"ok": True})

    async def _api_complete(self, request: aiohttp.web.Request) -> aiohttp.web.Response:
        sess, err = await _check_any(request)
        if err:
            return err
        order_id = int(request.match_info["id"])
        order = await db.get_order(order_id)
        if not order:
            return aiohttp.web.json_response({"error": "Order not found"}, status=404)
        if order["status"] not in ("pending", "delivering"):
            return aiohttp.web.json_response({"error": "Order already closed"}, status=409)
        try:
            body = await request.json()
            delivery_note = body.get("note", "")
        except Exception:
            delivery_note = ""
        from datetime import datetime
        await db.update_order(order_id,
            status="completed",
            completed_by=sess["discord_id"],
            completed_at=datetime.utcnow().isoformat(),
            delivery_note=delivery_note,
        )
        # Re-fetch to get ticket_channel_id
        order = await db.get_order(order_id)
        asyncio.create_task(self._notify_order_done(order, sess["username"], delivery_note))
        return aiohttp.web.json_response({"ok": True})

    async def _api_refund(self, request: aiohttp.web.Request) -> aiohttp.web.Response:
        sess, err = await _check_admin(request)   # admin only
        if err:
            return err
        order_id = int(request.match_info["id"])
        order = await db.get_order(order_id)
        if not order:
            return aiohttp.web.json_response({"error": "Order not found"}, status=404)
        if order["status"] in ("refunded", "completed"):
            return aiohttp.web.json_response({"error": f"Cannot refund order with status '{order['status']}'"}, status=409)
        try:
            body = await request.json()
            refund_note = body.get("note", "")
        except Exception:
            refund_note = ""
        from datetime import datetime
        # Refund balance to user
        await db.add_balance(order["discord_id"], order["price_usd"])
        await db.update_order(order_id,
            status="refunded",
            completed_by=sess["discord_id"],
            completed_at=datetime.utcnow().isoformat(),
            delivery_note=refund_note or "Refunded by admin",
        )
        order = await db.get_order(order_id)
        asyncio.create_task(self._notify_refund(order, sess["username"], refund_note))
        return aiohttp.web.json_response({"ok": True})

    async def _notify_refund(self, order: dict, staff_name: str, note: str) -> None:
        from bot.i18n import get_locale_from_bot
        locale = get_locale_from_bot(self.bot, order["discord_id"])
        amount = order["price_usd"]

        # DM user
        try:
            user_id = int(order["discord_id"])
            user = self.bot.get_user(user_id) or await self.bot.fetch_user(user_id)
            if user:
                if locale == "vi":
                    embed = discord.Embed(
                        title="💰 Hoàn tiền thành công!",
                        description=(
                            f"Đơn **#{order['id']}** đã được hoàn tiền.\n"
                            f"🎮 **Game:** {order['game_id']}\n"
                            f"📦 **Gói:** {order['package_name']}\n"
                            f"💵 **Số tiền hoàn:** `${amount:.2f} USD`"
                        ),
                        color=discord.Color.gold(),
                    )
                    if note:
                        embed.add_field(name="📝 Ghi chú", value=note, inline=False)
                    embed.set_footer(text=f"Xử lý bởi {staff_name}")
                else:
                    embed = discord.Embed(
                        title="💰 Refund Processed!",
                        description=(
                            f"Order **#{order['id']}** has been refunded.\n"
                            f"🎮 **Game:** {order['game_id']}\n"
                            f"📦 **Package:** {order['package_name']}\n"
                            f"💵 **Amount refunded:** `${amount:.2f} USD`"
                        ),
                        color=discord.Color.gold(),
                    )
                    if note:
                        embed.add_field(name="📝 Note", value=note, inline=False)
                    embed.set_footer(text=f"Processed by {staff_name}")
                await user.send(embed=embed)
        except Exception:
            logger.exception("Could not DM user for refund")

        # Notify ticket channel
        if locale == "vi":
            ticket_msg = f"💰 **Đơn #{order['id']} đã được hoàn tiền** bởi `{staff_name}`" + (f"\n📝 {note}" if note else "")
        else:
            ticket_msg = f"💰 **Order #{order['id']} refunded** by `{staff_name}`" + (f"\n📝 {note}" if note else "")
        asyncio.create_task(self._notify_ticket(order, ticket_msg, discord.Color.gold()))

    # ── Staff reject ──────────────────────────────────────────────────────────

    async def _api_reject(self, request: aiohttp.web.Request) -> aiohttp.web.Response:
        """Staff từ chối đơn. Nếu request_refund=True → gửi yêu cầu hoàn tiền cho admin."""
        sess, err = await _check_any(request)
        if err:
            return err
        order_id = int(request.match_info["id"])
        order = await db.get_order(order_id)
        if not order:
            return aiohttp.web.json_response({"error": "Order not found"}, status=404)
        if order["status"] not in ("pending", "delivering"):
            return aiohttp.web.json_response({"error": "Order is already closed"}, status=409)
        try:
            body = await request.json()
            reason = body.get("reason", "").strip()
            request_refund = bool(body.get("request_refund", False))
        except Exception:
            reason = ""
            request_refund = False

        from datetime import datetime
        if request_refund:
            await db.update_order(order_id,
                status="refund_requested",
                completed_by=sess["discord_id"],
                delivery_note=reason or "Yêu cầu hoàn tiền từ staff",
            )
            order = await db.get_order(order_id)
            asyncio.create_task(self._notify_refund_requested(order, sess["username"], reason))
        else:
            await db.update_order(order_id,
                status="failed",
                completed_by=sess["discord_id"],
                completed_at=datetime.utcnow().isoformat(),
                delivery_note=reason or "Đơn bị từ chối",
            )
            order = await db.get_order(order_id)
            asyncio.create_task(self._notify_rejected(order, sess["username"], reason))
        return aiohttp.web.json_response({"ok": True})

    async def _api_approve_refund(self, request: aiohttp.web.Request) -> aiohttp.web.Response:
        """Admin duyệt yêu cầu hoàn tiền."""
        sess, err = await _check_admin(request)
        if err:
            return err
        order_id = int(request.match_info["id"])
        order = await db.get_order(order_id)
        if not order:
            return aiohttp.web.json_response({"error": "Order not found"}, status=404)
        if order["status"] != "refund_requested":
            return aiohttp.web.json_response({"error": "Order is not in refund_requested state"}, status=409)
        try:
            body = await request.json()
            note = body.get("note", "").strip()
        except Exception:
            note = ""
        from datetime import datetime
        await db.add_balance(order["discord_id"], order["price_usd"])
        await db.update_order(order_id,
            status="refunded",
            completed_by=sess["discord_id"],
            completed_at=datetime.utcnow().isoformat(),
            delivery_note=note or "Hoàn tiền được duyệt bởi admin",
        )
        order = await db.get_order(order_id)
        asyncio.create_task(self._notify_refund(order, sess["username"], note))
        return aiohttp.web.json_response({"ok": True})

    async def _api_deny_refund(self, request: aiohttp.web.Request) -> aiohttp.web.Response:
        """Admin từ chối yêu cầu hoàn tiền."""
        sess, err = await _check_admin(request)
        if err:
            return err
        order_id = int(request.match_info["id"])
        order = await db.get_order(order_id)
        if not order:
            return aiohttp.web.json_response({"error": "Order not found"}, status=404)
        if order["status"] != "refund_requested":
            return aiohttp.web.json_response({"error": "Order is not in refund_requested state"}, status=409)
        try:
            body = await request.json()
            reason = body.get("reason", "").strip()
        except Exception:
            reason = ""
        from datetime import datetime
        await db.update_order(order_id,
            status="failed",
            completed_by=sess["discord_id"],
            completed_at=datetime.utcnow().isoformat(),
            delivery_note=reason or "Yêu cầu hoàn tiền bị từ chối",
        )
        order = await db.get_order(order_id)
        asyncio.create_task(self._notify_refund_denied(order, sess["username"], reason))
        return aiohttp.web.json_response({"ok": True})

    # ── Notification helpers ──────────────────────────────────────────────────

    async def _notify_refund_requested(self, order: dict, staff_name: str, reason: str) -> None:
        """Thông báo ticket khi staff yêu cầu hoàn tiền (chờ admin duyệt)."""
        from bot.i18n import ROLE_FOUNDER
        # Ping FOUNDER role in ticket so admin sees it
        ticket_msg = (
            f"⏳ **Yêu cầu hoàn tiền cho Đơn #{order['id']}** từ staff `{staff_name}`\n"
            f"💵 **Số tiền:** `${order['price_usd']:.2f} USD`"
            + (f"\n📝 **Lý do:** {reason}" if reason else "")
            + f"\n\n<@&{ROLE_FOUNDER}> Vui lòng duyệt hoặc từ chối trên dashboard."
        )
        asyncio.create_task(self._notify_ticket(order, ticket_msg, discord.Color.yellow()))

    async def _notify_rejected(self, order: dict, staff_name: str, reason: str) -> None:
        """Thông báo khi đơn bị từ chối (không hoàn tiền)."""
        from bot.i18n import get_locale_from_bot
        locale = get_locale_from_bot(self.bot, order["discord_id"])
        # DM user
        try:
            user_id = int(order["discord_id"])
            user = self.bot.get_user(user_id) or await self.bot.fetch_user(user_id)
            if user:
                if locale == "vi":
                    embed = discord.Embed(
                        title="❌ Đơn bị từ chối",
                        description=(
                            f"Đơn **#{order['id']}** đã bị từ chối.\n"
                            f"🎮 **Game:** {order['game_id']}\n"
                            f"📦 **Gói:** {order['package_name']}"
                        ),
                        color=discord.Color.red(),
                    )
                    if reason:
                        embed.add_field(name="📝 Lý do", value=reason, inline=False)
                    embed.set_footer(text=f"Xử lý bởi {staff_name}")
                else:
                    embed = discord.Embed(
                        title="❌ Order Rejected",
                        description=(
                            f"Order **#{order['id']}** has been rejected.\n"
                            f"🎮 **Game:** {order['game_id']}\n"
                            f"📦 **Package:** {order['package_name']}"
                        ),
                        color=discord.Color.red(),
                    )
                    if reason:
                        embed.add_field(name="📝 Reason", value=reason, inline=False)
                    embed.set_footer(text=f"Handled by {staff_name}")
                await user.send(embed=embed)
        except Exception:
            logger.exception("Could not DM user for order rejection")
        # Notify ticket
        if locale == "vi":
            ticket_msg = f"❌ **Đơn #{order['id']} bị từ chối** bởi `{staff_name}`" + (f"\n📝 {reason}" if reason else "")
        else:
            ticket_msg = f"❌ **Order #{order['id']} rejected** by `{staff_name}`" + (f"\n📝 {reason}" if reason else "")
        asyncio.create_task(self._notify_ticket(order, ticket_msg, discord.Color.red()))

    async def _notify_refund_denied(self, order: dict, staff_name: str, reason: str) -> None:
        """Thông báo khi admin từ chối yêu cầu hoàn tiền."""
        from bot.i18n import get_locale_from_bot
        locale = get_locale_from_bot(self.bot, order["discord_id"])
        try:
            user_id = int(order["discord_id"])
            user = self.bot.get_user(user_id) or await self.bot.fetch_user(user_id)
            if user:
                if locale == "vi":
                    embed = discord.Embed(
                        title="❌ Yêu cầu hoàn tiền bị từ chối",
                        description=(
                            f"Yêu cầu hoàn tiền cho Đơn **#{order['id']}** đã bị admin từ chối.\n"
                            f"🎮 **Game:** {order['game_id']}\n"
                            f"📦 **Gói:** {order['package_name']}"
                        ),
                        color=discord.Color.red(),
                    )
                    if reason:
                        embed.add_field(name="📝 Lý do", value=reason, inline=False)
                    embed.set_footer(text=f"Xử lý bởi {staff_name}")
                else:
                    embed = discord.Embed(
                        title="❌ Refund Request Denied",
                        description=(
                            f"The refund request for Order **#{order['id']}** was denied by admin.\n"
                            f"🎮 **Game:** {order['game_id']}\n"
                            f"📦 **Package:** {order['package_name']}"
                        ),
                        color=discord.Color.red(),
                    )
                    if reason:
                        embed.add_field(name="📝 Reason", value=reason, inline=False)
                    embed.set_footer(text=f"Handled by {staff_name}")
                await user.send(embed=embed)
        except Exception:
            logger.exception("Could not DM user for refund denial")
        # Notify ticket
        if locale == "vi":
            ticket_msg = f"❌ **Yêu cầu hoàn tiền Đơn #{order['id']} bị từ chối** bởi admin `{staff_name}`" + (f"\n📝 {reason}" if reason else "")
        else:
            ticket_msg = f"❌ **Refund request for Order #{order['id']} denied** by admin `{staff_name}`" + (f"\n📝 {reason}" if reason else "")
        asyncio.create_task(self._notify_ticket(order, ticket_msg, discord.Color.red()))

    async def _notify_order_done(self, order: dict, staff_name: str, note: str) -> None:
        try:
            user_id = int(order["discord_id"])
            user = self.bot.get_user(user_id) or await self.bot.fetch_user(user_id)
            if user:
                embed = discord.Embed(
                    title="✅ Order Completed!",
                    description=(
                        f"Your order **#{order['id']}** has been delivered.\n"
                        f"🎮 **Game:** {order['game_id']}\n"
                        f"📦 **Package:** {order['package_name']}\n"
                        f"👤 **Account:** `{order.get('game_account', '-')}`"
                    ),
                    color=discord.Color.green(),
                )
                if note:
                    embed.add_field(name="📝 Note", value=note, inline=False)
                embed.set_footer(text=f"Processed by {staff_name}")
                await user.send(embed=embed)
        except Exception:
            logger.exception("Could not DM user for order completion")

        # Also update ticket channel
        ticket_msg = f"✅ **Order #{order['id']} completed** by staff `{staff_name}`" + (f"\n📝 {note}" if note else "")
        asyncio.create_task(self._notify_ticket(order, ticket_msg, discord.Color.green()))

    async def _notify_ticket(self, order: dict, message: str, color: discord.Color) -> None:
        """Send a status update to the ticket channel associated with the order, pinging the user."""
        try:
            ch_id = order.get("ticket_channel_id")
            if not ch_id:
                return
            channel = self.bot.get_channel(int(ch_id))
            if channel is None:
                return
            # Ping the user so they see the update
            user_mention = f"<@{order['discord_id']}>"
            embed = discord.Embed(description=message, color=color)
            await channel.send(content=user_mention, embed=embed)
        except Exception:
            logger.exception("Could not notify ticket channel")

    # ─── Stats ───────────────────────────────────────────────────────────────

    async def _api_stats(self, request: aiohttp.web.Request) -> aiohttp.web.Response:
        sess, err = await _check_admin(request)
        if err:
            return err
        stats = await db.get_dashboard_stats()
        return aiohttp.web.json_response(stats)

    async def _api_staff_list(self, request: aiohttp.web.Request) -> aiohttp.web.Response:
        sess, err = await _check_admin(request)
        if err:
            return err
        staff_list = await db.get_all_staff()
        safe = [
            {k: v for k, v in s.items() if k != "password_hash"}
            for s in staff_list
        ]
        return aiohttp.web.json_response(safe)

    # ── Staff balance ─────────────────────────────────────────────────────────

    async def _api_staff_balance(self, request: aiohttp.web.Request) -> aiohttp.web.Response:
        sess, err = await _check_any(request)
        if err:
            return err
        balance = await db.get_staff_commission_balance(sess["discord_id"])
        return aiohttp.web.json_response({"commission_balance": balance})

    # ── Bank accounts ─────────────────────────────────────────────────────────

    async def _api_bank_accounts_list(self, request: aiohttp.web.Request) -> aiohttp.web.Response:
        sess, err = await _check_any(request)
        if err:
            return err
        accounts = await db.get_staff_bank_accounts(sess["discord_id"])
        # Strip qr_image from list response to keep payload small; full detail on demand
        light = [{k: v for k, v in a.items() if k != "qr_image"} for a in accounts]
        return aiohttp.web.json_response(light)

    async def _api_bank_accounts_add(self, request: aiohttp.web.Request) -> aiohttp.web.Response:
        sess, err = await _check_any(request)
        if err:
            return err
        try:
            body = await request.json()
        except Exception:
            return aiohttp.web.json_response({"error": "Invalid JSON"}, status=400)
        bank_name = (body.get("bank_name") or "").strip()
        account_number = (body.get("account_number") or "").strip()
        account_holder = (body.get("account_holder") or "").strip()
        if not bank_name or not account_number or not account_holder:
            return aiohttp.web.json_response({"error": "bank_name, account_number, account_holder required"}, status=400)
        branch = (body.get("branch") or "").strip()
        qr_image = (body.get("qr_image") or "").strip()
        # Limit QR image to 500KB base64
        if len(qr_image) > 700_000:
            return aiohttp.web.json_response({"error": "QR image too large (max 500KB)"}, status=400)
        acc = await db.add_staff_bank_account(
            sess["discord_id"], bank_name, account_number, account_holder, branch, qr_image
        )
        return aiohttp.web.json_response({k: v for k, v in acc.items() if k != "qr_image"})

    async def _api_bank_accounts_delete(self, request: aiohttp.web.Request) -> aiohttp.web.Response:
        sess, err = await _check_any(request)
        if err:
            return err
        acc_id = int(request.match_info["id"])
        await db.delete_staff_bank_account(acc_id, sess["discord_id"])
        return aiohttp.web.json_response({"ok": True})

    # ── Withdrawal requests ───────────────────────────────────────────────────

    async def _api_withdraw_request(self, request: aiohttp.web.Request) -> aiohttp.web.Response:
        sess, err = await _check_any(request)
        if err:
            return err
        try:
            body = await request.json()
        except Exception:
            return aiohttp.web.json_response({"error": "Invalid JSON"}, status=400)
        amount_usd = float(body.get("amount_usd", 0))
        bank_account_id = int(body.get("bank_account_id", 0))
        if amount_usd < 5.0:
            return aiohttp.web.json_response({"error": "Minimum withdrawal is $5.00"}, status=400)
        # Verify bank account belongs to staff
        acc = await db.get_bank_account(bank_account_id)
        if not acc or acc["staff_discord_id"] != sess["discord_id"]:
            return aiohttp.web.json_response({"error": "Bank account not found"}, status=404)
        # Check balance
        balance = await db.get_staff_commission_balance(sess["discord_id"])
        if balance < amount_usd:
            return aiohttp.web.json_response({"error": f"Insufficient balance (${balance:.2f})"}, status=400)
        # Deduct immediately (hold until completed/rejected)
        ok = await db.deduct_staff_commission(sess["discord_id"], amount_usd)
        if not ok:
            return aiohttp.web.json_response({"error": "Balance changed, please retry"}, status=409)
        from config import EXCHANGE_RATE
        req = await db.create_withdrawal_request(
            sess["discord_id"], sess["username"], bank_account_id, amount_usd, float(EXCHANGE_RATE)
        )
        # Notify admin in Discord (optional DM to founders)
        asyncio.create_task(self._notify_withdrawal_request(req, acc))
        return aiohttp.web.json_response({"ok": True, "id": req["id"]})

    async def _api_withdrawals_list(self, request: aiohttp.web.Request) -> aiohttp.web.Response:
        sess, err = await _check_any(request)
        if err:
            return err
        if sess["role"] == "admin":
            status = request.rel_url.query.get("status")
            reqs = await db.get_withdrawal_requests(status=status)
        else:
            reqs = await db.get_withdrawal_requests(discord_id=sess["discord_id"])
        # Enrich with bank account details
        enriched = []
        for r in reqs:
            row = dict(r)
            if r.get("bank_account_id"):
                acc = await db.get_bank_account(int(r["bank_account_id"]))
                if acc:
                    row["bank_name"] = acc["bank_name"]
                    row["account_number"] = acc["account_number"]
                    row["account_holder"] = acc["account_holder"]
                    row["branch"] = acc.get("branch")
            enriched.append(row)
        return aiohttp.web.json_response(enriched)

    async def _api_withdrawal_complete(self, request: aiohttp.web.Request) -> aiohttp.web.Response:
        sess, err = await _check_admin(request)
        if err:
            return err
        req_id = int(request.match_info["id"])
        req = await db.get_withdrawal_request(req_id)
        if not req:
            return aiohttp.web.json_response({"error": "Not found"}, status=404)
        if req["status"] != "pending":
            return aiohttp.web.json_response({"error": "Already processed"}, status=409)
        try:
            body = await request.json()
            note = (body.get("note") or "").strip()
        except Exception:
            note = ""
        await db.complete_withdrawal(req_id, sess["discord_id"], note)
        asyncio.create_task(self._notify_withdrawal_done(req, sess["username"], note))
        return aiohttp.web.json_response({"ok": True})

    async def _api_withdrawal_reject(self, request: aiohttp.web.Request) -> aiohttp.web.Response:
        sess, err = await _check_admin(request)
        if err:
            return err
        req_id = int(request.match_info["id"])
        req = await db.get_withdrawal_request(req_id)
        if not req:
            return aiohttp.web.json_response({"error": "Not found"}, status=404)
        if req["status"] != "pending":
            return aiohttp.web.json_response({"error": "Already processed"}, status=409)
        try:
            body = await request.json()
            note = (body.get("note") or "").strip()
        except Exception:
            note = ""
        await db.reject_withdrawal(req_id, sess["discord_id"], note)
        # Refund commission back to staff
        await db.add_staff_commission(req["staff_discord_id"], float(req["amount_usd"]))
        asyncio.create_task(self._notify_withdrawal_rejected(req, sess["username"], note))
        return aiohttp.web.json_response({"ok": True})

    # ── Orders export/filter ──────────────────────────────────────────────────

    async def _api_orders_export(self, request: aiohttp.web.Request) -> aiohttp.web.Response:
        sess, err = await _check_admin(request)
        if err:
            return err
        q = request.rel_url.query
        params: dict = {"order": "created_at.desc", "limit": "1000"}
        if q.get("status"):
            params["status"] = f"eq.{q['status']}"
        if q.get("game_id"):
            params["game_id"] = f"eq.{q['game_id']}"
        if q.get("completed_by"):
            params["completed_by"] = f"eq.{q['completed_by']}"
        if q.get("order_id"):
            params["id"] = f"eq.{q['order_id']}"
        orders = await db._get("orders", params)
        return aiohttp.web.json_response(orders)

    # ── Withdrawal notification helpers ──────────────────────────────────────

    async def _notify_withdrawal_request(self, req: dict, bank_acc: dict) -> None:
        """DM founders that a staff member has requested a withdrawal."""
        try:
            from bot.i18n import ROLE_FOUNDER
            for guild in self.bot.guilds:
                founder_role = guild.get_role(ROLE_FOUNDER)
                if not founder_role:
                    continue
                for member in founder_role.members:
                    try:
                        embed = discord.Embed(
                            title="💸 Yêu cầu rút tiền mới",
                            description=(
                                f"Staff **{req['staff_username']}** yêu cầu rút tiền.\n"
                                f"💵 **Số tiền:** `${req['amount_usd']:.2f}` (~{req['amount_vnd']:,} VND)\n"
                                f"🏦 **Ngân hàng:** {bank_acc['bank_name']}\n"
                                f"💳 **STK:** `{bank_acc['account_number']}` — {bank_acc['account_holder']}"
                            ),
                            color=discord.Color.orange(),
                        )
                        embed.set_footer(text=f"Withdrawal #{req['id']} • Vào dashboard để xử lý")
                        await member.send(embed=embed)
                    except Exception:
                        pass
        except Exception:
            logger.exception("Could not notify founders about withdrawal")

    async def _notify_withdrawal_done(self, req: dict, admin_name: str, note: str) -> None:
        """DM staff khi withdrawal được duyệt."""
        try:
            user_id = int(req["staff_discord_id"])
            user = self.bot.get_user(user_id) or await self.bot.fetch_user(user_id)
            if user:
                embed = discord.Embed(
                    title="✅ Rút tiền thành công!",
                    description=(
                        f"Yêu cầu rút tiền **#{req['id']}** đã được xử lý.\n"
                        f"💵 **Số tiền:** `${req['amount_usd']:.2f}` (~{req['amount_vnd']:,} VND)"
                    ),
                    color=discord.Color.green(),
                )
                if note:
                    embed.add_field(name="📝 Ghi chú", value=note, inline=False)
                embed.set_footer(text=f"Xử lý bởi {admin_name}")
                await user.send(embed=embed)
        except Exception:
            logger.exception("Could not DM staff about withdrawal completion")

    async def _notify_withdrawal_rejected(self, req: dict, admin_name: str, note: str) -> None:
        """DM staff khi withdrawal bị từ chối (tiền đã được hoàn lại)."""
        try:
            user_id = int(req["staff_discord_id"])
            user = self.bot.get_user(user_id) or await self.bot.fetch_user(user_id)
            if user:
                embed = discord.Embed(
                    title="❌ Yêu cầu rút tiền bị từ chối",
                    description=(
                        f"Yêu cầu rút tiền **#{req['id']}** đã bị từ chối.\n"
                        f"💵 **Số tiền:** `${req['amount_usd']:.2f}` đã được hoàn lại vào tài khoản của bạn."
                    ),
                    color=discord.Color.red(),
                )
                if note:
                    embed.add_field(name="📝 Lý do", value=note, inline=False)
                embed.set_footer(text=f"Xử lý bởi {admin_name}")
                await user.send(embed=embed)
        except Exception:
            logger.exception("Could not DM staff about withdrawal rejection")

    # ─────────────────────────────────────── Games & Packages CRUD (admin) ──

    async def _api_games_list(self, request: aiohttp.web.Request) -> aiohttp.web.Response:
        sess = await _check_admin(request)
        if isinstance(sess, aiohttp.web.Response):
            return sess
        games = await db.get_all_games(include_inactive=True)
        return aiohttp.web.json_response(games)

    async def _api_games_create(self, request: aiohttp.web.Request) -> aiohttp.web.Response:
        sess = await _check_admin(request)
        if isinstance(sess, aiohttp.web.Response):
            return sess
        try:
            body = await request.json()
        except Exception:
            raise aiohttp.web.HTTPBadRequest(reason="Invalid JSON")
        game_id = (body.get("id") or "").strip().lower().replace(" ", "_")
        name    = (body.get("name") or "").strip()
        if not game_id or not name:
            raise aiohttp.web.HTTPBadRequest(reason="id và name là bắt buộc")
        try:
            game = await db.create_game(
                game_id=game_id,
                name=name,
                emoji=body.get("emoji") or "🎮",
                icon_url=body.get("icon_url") or "",
                platform=body.get("platform") or "all",
                sort_order=int(body.get("sort_order") or 0),
            )
        except Exception as e:
            raise aiohttp.web.HTTPConflict(reason=str(e))
        return aiohttp.web.json_response(game, status=201)

    async def _api_games_update(self, request: aiohttp.web.Request) -> aiohttp.web.Response:
        sess = await _check_admin(request)
        if isinstance(sess, aiohttp.web.Response):
            return sess
        game_id = request.match_info["id"]
        try:
            body = await request.json()
        except Exception:
            raise aiohttp.web.HTTPBadRequest(reason="Invalid JSON")
        allowed = {"name", "emoji", "icon_url", "platform", "active", "sort_order"}
        fields = {k: v for k, v in body.items() if k in allowed}
        if not fields:
            raise aiohttp.web.HTTPBadRequest(reason="Không có trường hợp lệ")
        await db.update_game(game_id, **fields)
        return aiohttp.web.json_response({"ok": True})

    async def _api_games_delete(self, request: aiohttp.web.Request) -> aiohttp.web.Response:
        sess = await _check_admin(request)
        if isinstance(sess, aiohttp.web.Response):
            return sess
        game_id = request.match_info["id"]
        await db.delete_game(game_id)
        return aiohttp.web.json_response({"ok": True})

    async def _api_packages_list(self, request: aiohttp.web.Request) -> aiohttp.web.Response:
        sess = await _check_admin(request)
        if isinstance(sess, aiohttp.web.Response):
            return sess
        game_id = request.match_info["id"]
        packages = await db.get_packages_by_game(game_id, include_inactive=True)
        return aiohttp.web.json_response(packages)

    async def _api_packages_create(self, request: aiohttp.web.Request) -> aiohttp.web.Response:
        sess = await _check_admin(request)
        if isinstance(sess, aiohttp.web.Response):
            return sess
        game_id = request.match_info["id"]
        try:
            body = await request.json()
        except Exception:
            raise aiohttp.web.HTTPBadRequest(reason="Invalid JSON")
        name     = (body.get("name") or "").strip()
        category = (body.get("category") or "").strip().lower().replace(" ", "_")
        price    = body.get("price_usd")
        if not name or not category or price is None:
            raise aiohttp.web.HTTPBadRequest(reason="name, category, price_usd là bắt buộc")
        # Tự tạo ID: {game_id}.{category}.{slug(name)}
        import re
        slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
        pkg_id = f"{game_id}.{category}.{slug}"
        try:
            pkg = await db.create_package(
                package_id=pkg_id,
                game_id=game_id,
                category=category,
                name=name,
                price_usd=float(price),
                description=body.get("description") or "",
                sort_order=int(body.get("sort_order") or 0),
                platform=body.get("platform") or "all",
            )
        except Exception as e:
            logger.error("create_package error: %s", e)
            return aiohttp.web.json_response({"error": str(e)}, status=400)
        return aiohttp.web.json_response(pkg, status=201)

    async def _api_packages_update(self, request: aiohttp.web.Request) -> aiohttp.web.Response:
        sess = await _check_admin(request)
        if isinstance(sess, aiohttp.web.Response):
            return sess
        pkg_id = request.match_info["id"]
        try:
            body = await request.json()
        except Exception:
            raise aiohttp.web.HTTPBadRequest(reason="Invalid JSON")
        allowed = {"name", "category", "price_usd", "description", "active", "sort_order", "platform"}
        fields = {k: v for k, v in body.items() if k in allowed}
        if not fields:
            raise aiohttp.web.HTTPBadRequest(reason="Không có trường hợp lệ")
        await db.update_package(pkg_id, **fields)
        return aiohttp.web.json_response({"ok": True})

    async def _api_packages_delete(self, request: aiohttp.web.Request) -> aiohttp.web.Response:
        sess = await _check_admin(request)
        if isinstance(sess, aiohttp.web.Response):
            return sess
        pkg_id = request.match_info["id"]
        await db.delete_package(pkg_id)
        return aiohttp.web.json_response({"ok": True})

    # ──────────────────────────────────────────────── Chat / WebSocket ───────

    async def _ws_chat(self, request: aiohttp.web.Request) -> aiohttp.web.WebSocketResponse:
        """
        WebSocket endpoint: GET /ws/chat?order_id=<id>&token=<session_token>

        Protocol (JSON frames):
          Client → Server:
            {"type": "message", "content": "..."}   — nhân viên gửi tin nhắn đến khách

          Server → Client:
            {"type": "history", "messages": [...]}  — lịch sử chat khi mở kết nối
            {"type": "message", ...msgObj}           — tin mới (customer hoặc staff)
            {"type": "blocked", "reason": "..."}     — tin bị chặn (chỉ gửi lại cho sender)
            {"type": "system", "content": "..."}     — thông báo hệ thống
        """
        # ── Authenticate ──────────────────────────────────────────────────────
        token = request.rel_url.query.get("token", "") or request.cookies.get("session_token", "")
        sess = _SESSIONS.get(token)
        if not sess or sess["expires"] <= time.time():
            raise aiohttp.web.HTTPUnauthorized(reason="Invalid or expired session")

        order_id_str = request.rel_url.query.get("order_id", "")
        if not order_id_str.isdigit():
            raise aiohttp.web.HTTPBadRequest(reason="order_id required")
        order_id = int(order_id_str)

        # Verify order exists
        order = await db.get_order(order_id)
        if not order:
            raise aiohttp.web.HTTPNotFound(reason="Order not found")

        ws = aiohttp.web.WebSocketResponse(heartbeat=30)
        await ws.prepare(request)

        # Register connection
        self._chat_connections.setdefault(order_id, set()).add(ws)
        logger.info(f"Chat WS connected: staff={sess['username']} order={order_id}")

        try:
            # Send chat history on connect
            history = await db.get_chat_history(order_id)
            await ws.send_json({"type": "history", "messages": history})

            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    try:
                        data = json.loads(msg.data)
                    except Exception:
                        continue

                    if data.get("type") != "message":
                        continue

                    content = str(data.get("content", "")).strip()
                    if not content or len(content) > 2000:
                        continue

                    # ── Guard check ───────────────────────────────────────────
                    is_violation, reason = check_staff_message(content)
                    if is_violation:
                        # Log blocked attempt
                        await db.save_chat_message(
                            order_id=order_id,
                            sender_type="staff",
                            sender_id=sess["discord_id"],
                            sender_name=sess["username"],
                            content=content,
                            blocked=True,
                            block_reason=reason,
                        )
                        logger.warning(
                            f"Chat blocked: staff={sess['username']} order={order_id} "
                            f"reason={reason!r} content={content!r}"
                        )
                        await ws.send_json({"type": "blocked", "reason": reason})
                        continue

                    # ── Save to DB ────────────────────────────────────────────
                    saved = await db.save_chat_message(
                        order_id=order_id,
                        sender_type="staff",
                        sender_id=sess["discord_id"],
                        sender_name=sess["username"],
                        content=content,
                    )

                    # ── Broadcast to all dashboard clients for this order ─────
                    await self._broadcast_chat(order_id, saved)

                    # ── Send to Discord ticket channel ────────────────────────
                    asyncio.create_task(
                        self._send_to_discord_ticket(order, sess["username"], content)
                    )

                elif msg.type in (aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSE):
                    break
        finally:
            conns = self._chat_connections.get(order_id)
            if conns:
                conns.discard(ws)
                if not conns:
                    del self._chat_connections[order_id]
            logger.info(f"Chat WS disconnected: staff={sess['username']} order={order_id}")

        return ws

    async def _api_chat_history(self, request: aiohttp.web.Request) -> aiohttp.web.Response:
        """GET /api/orders/{id}/chat — REST fallback to load chat history."""
        sess, err = await _check_any(request)
        if err:
            return err
        order_id = int(request.match_info["id"])
        messages = await db.get_chat_history(order_id)
        return aiohttp.web.json_response(messages)

    async def _api_chats(self, request: aiohttp.web.Request) -> aiohttp.web.Response:
        """GET /api/chats — Chat inbox: danh sách đơn hàng có tin nhắn (admin/staff)."""
        sess, err = await _check_any(request)
        if err:
            return err
        inbox = await db.get_chat_inbox()
        return aiohttp.web.json_response(inbox)

    async def _broadcast_chat(self, order_id: int, message: dict) -> None:
        """Gửi message tới tất cả WS client đang xem order đó."""
        conns = self._chat_connections.get(order_id)
        if not conns:
            return
        payload = json.dumps({"type": "message", **message})
        dead: set[aiohttp.web.WebSocketResponse] = set()
        for ws in list(conns):
            try:
                await ws.send_str(payload)
            except Exception:
                dead.add(ws)
        conns -= dead

    async def _send_to_discord_ticket(self, order: dict, staff_name: str, content: str) -> None:
        """Bot gửi reply của nhân viên vào Discord ticket channel."""
        try:
            ch_id = order.get("ticket_channel_id")
            if not ch_id:
                return
            channel = self.bot.get_channel(int(ch_id))
            if channel is None:
                return
            embed = discord.Embed(
                description=content,
                color=discord.Color.blurple(),
            )
            embed.set_author(name=f"💬 Staff: {staff_name}")
            await channel.send(embed=embed)
        except Exception:
            logger.exception("Could not send staff message to Discord ticket")

    async def broadcast_discord_message(
        self,
        order_id: int,
        discord_id: str,
        display_name: str,
        content: str,
    ) -> None:
        """
        Được gọi bởi AdminCog khi khách gửi tin nhắn trong ticket Discord.
        Lưu vào DB và broadcast tới tất cả dashboard WS client.
        """
        saved = await db.save_chat_message(
            order_id=order_id,
            sender_type="customer",
            sender_id=discord_id,
            sender_name=display_name,
            content=content,
        )
        await self._broadcast_chat(order_id, saved)

    # -------------------------------------------------------------- start --
        # Custom access log format: hide User-Agent, only show method/path/status/size
        access_log_format = '%a [%t] "%r" %s %b'

        class _NoFaviconFilter(logging.Filter):
            def filter(self, record: logging.LogRecord) -> bool:
                msg = record.getMessage()
                return "favicon.ico" not in msg

        access_logger = logging.getLogger("aiohttp.access")
        access_logger.addFilter(_NoFaviconFilter())

        runner = aiohttp.web.AppRunner(
            self.app,
            access_log=access_logger,
            access_log_format=access_log_format,
        )
        await runner.setup()
        site = aiohttp.web.TCPSite(runner, WEBHOOK_HOST, WEBHOOK_PORT)
        await site.start()
        logger.info(f"Webhook server đang chạy tại http://{WEBHOOK_HOST}:{WEBHOOK_PORT}")
        # Giữ task chạy mãi
        while True:
            await asyncio.sleep(3600)
