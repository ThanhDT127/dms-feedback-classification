"""``WS /ws/chat`` (design b06 D1–D6, D10).

Một kết nối cho mỗi tab. Handler nhận message và gửi event đọc từ Answer Buffer; việc sinh câu
trả lời nằm ở ``ChatTurnRunner`` nên ngắt kết nối không huỷ lượt.
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import logging
import time
import uuid
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from starlette.concurrency import run_in_threadpool

from ..ai.types import HistoryWindow, TurnRequest
from ..contract import UserScope
from .protocol import (
    AskMessage,
    BadMessage,
    CancelMessage,
    PingMessage,
    ProtocolError,
    ResumeMessage,
    ack,
    answer_event,
    answer_expired,
    error,
    parse_client_message,
    pong,
)
from .services import ChatServices
from .session_service import SessionNotFound
from .turn_runner import RunnerBusy

logger = logging.getLogger("dms-chat")

router = APIRouter(tags=["ws"])

LIMITER_ROUTE = "chat"
AUTH_CLOSE_CODE = 4001
READ_TIMEOUT_MS = 1000


@router.websocket("/ws/chat")
async def ws_chat(websocket: WebSocket) -> None:
    from ...web import deps
    from ...web.ws.connection_limiter import WS_LIMIT_CLOSE_CODE, ws_connection_limiter

    auth = await _authenticate(websocket)
    if auth is None:
        return
    payload, user = auth
    username = str(user.get("username") or payload["sub"])

    services = deps.get_chat_services()
    if services is None or not services.settings.chat_enabled:
        await websocket.close(code=1011, reason="Chat unavailable")
        return

    identity = ws_connection_limiter.identity_for(websocket, username)
    if not ws_connection_limiter.acquire(LIMITER_ROUTE, identity):
        await websocket.close(
            code=WS_LIMIT_CLOSE_CODE, reason="WebSocket connection limit exceeded"
        )
        return

    try:
        await websocket.accept()
        connection = ChatConnection(
            websocket, username=username, payload=payload, services=services
        )
        await connection.run()
    finally:
        ws_connection_limiter.release(LIMITER_ROUTE, identity)


async def _authenticate(websocket: WebSocket) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """Giống ``web/ws/progress.py``: token query, thu hồi, user active; sai thì đóng 4001."""
    import jwt as pyjwt

    from ...jwt_utils import decode_token
    from ...token_blacklist import is_revoked
    from ...web import deps

    token = websocket.query_params.get("token")
    if not token:
        await websocket.close(code=AUTH_CLOSE_CODE, reason="Authentication required")
        return None
    settings = deps.get_settings()
    if not settings:
        await websocket.close(code=AUTH_CLOSE_CODE, reason="Server configuration unavailable")
        return None
    try:
        payload = decode_token(token, settings.jwt_secret_key, expected_type="access")
    except (pyjwt.ExpiredSignatureError, pyjwt.InvalidTokenError, ValueError):
        await websocket.close(code=AUTH_CLOSE_CODE, reason="Invalid or expired token")
        return None
    if payload.get("jti") and is_revoked(payload["jti"]):
        await websocket.close(code=AUTH_CLOSE_CODE, reason="Token has been revoked")
        return None
    if not payload.get("sub"):
        await websocket.close(code=AUTH_CLOSE_CODE, reason="Invalid token payload")
        return None
    user = await _load_active_user(str(payload["sub"]))
    if user is None:
        await websocket.close(code=AUTH_CLOSE_CODE, reason="User unavailable")
        return None
    return payload, user


async def _load_active_user(username: str) -> dict[str, Any] | None:
    from ...web import deps

    user_store = deps.get_user_store()
    if user_store is None:
        return None
    user = await run_in_threadpool(user_store.get_user, username)
    if not user or user.get("is_active", True) is False:
        return None
    return user


class _Close(Exception):
    def __init__(self, code: int, reason: str) -> None:
        super().__init__(reason)
        self.code = code
        self.reason = reason


class ChatConnection:
    def __init__(
        self,
        websocket: WebSocket,
        *,
        username: str,
        payload: dict[str, Any],
        services: ChatServices,
    ) -> None:
        self.websocket = websocket
        self.username = username
        self.payload = payload
        self.services = services
        self.settings = services.settings
        self._send_lock = asyncio.Lock()
        self._followers: dict[str, asyncio.Task[None]] = {}

    # ── Vòng nhận ──

    async def run(self) -> None:
        idle = float(self.settings.chat_ws_idle_timeout_seconds)
        try:
            while True:
                try:
                    raw = await asyncio.wait_for(self._receive(), timeout=idle)
                except TimeoutError:
                    if self._following():
                        continue  # đang gửi câu trả lời thì không tính là rảnh
                    await self.websocket.close(code=1000, reason="Idle timeout")
                    return
                await self._dispatch(raw)
        except WebSocketDisconnect:
            logger.debug("chat_ws_disconnected", extra={"username": self.username})
        except _Close as close:
            with contextlib.suppress(Exception):
                await self.websocket.close(code=close.code, reason=close.reason)
        finally:
            for task in list(self._followers.values()):
                task.cancel()
            for task in list(self._followers.values()):
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task

    async def _receive(self) -> str | bytes:
        message = await self.websocket.receive()
        if message["type"] == "websocket.disconnect":
            raise WebSocketDisconnect(code=message.get("code", 1000))
        if message.get("text") is not None:
            return str(message["text"])
        return bytes(message.get("bytes") or b"")

    async def _dispatch(self, raw: str | bytes) -> None:
        try:
            message = parse_client_message(raw)
        except BadMessage as exc:
            await self.send(error(ProtocolError.BAD_MESSAGE, client_msg_id=exc.client_msg_id))
            return
        if isinstance(message, PingMessage):
            await self.send(pong())
        elif isinstance(message, AskMessage):
            await self._handle_ask(message)
        elif isinstance(message, ResumeMessage):
            await self._handle_resume(message)
        elif isinstance(message, CancelMessage):
            await self._handle_cancel(message)

    # ── ask ──

    async def _handle_ask(self, message: AskMessage) -> None:
        user = await _load_active_user(self.username)
        if user is None:
            raise _Close(AUTH_CLOSE_CODE, "User unavailable")
        if float(self.payload.get("exp", 0)) <= time.time():
            raise _Close(AUTH_CLOSE_CODE, "Token expired")

        reply_id = message.client_msg_id
        if len(message.question) > int(self.settings.chat_max_question_chars):
            await self.send(error(ProtocolError.INPUT_TOO_LONG, client_msg_id=reply_id))
            return
        services = self.services
        max_active = int(self.settings.chat_max_active_turns_per_user)
        if services.buffer.active_count(self.username) >= max_active:
            await self.send(error(ProtocolError.BUSY, client_msg_id=reply_id))
            return
        if not services.rate_limiter.allow(self.username):
            await self.send(error(ProtocolError.RATE_LIMITED, client_msg_id=reply_id))
            return
        # Hạn mức token theo ngày (b11 D9): kiểm trước khi tạo answer; giới hạn mềm.
        if services.budget is not None:
            status = await run_in_threadpool(
                services.budget.status,
                self.username,
                is_admin=str(user.get("role") or "") == "admin",
            )
            if status.exceeded:
                await self.send(
                    error(
                        ProtocolError.BUDGET_EXCEEDED,
                        client_msg_id=reply_id,
                        data={"reset_at": status.reset_at, "used": status.used,
                              "limit": status.limit},
                    )
                )
                return

        try:
            session = await run_in_threadpool(
                services.sessions.open_for_ask, self.username, message.session_id, message.question
            )
        except SessionNotFound:
            await self.send(error(ProtocolError.SESSION_NOT_FOUND, client_msg_id=reply_id))
            return
        # Tóm tắt phiên + các lượt gần nhất vừa ngân sách ký tự (b11 D5).
        history = (
            await run_in_threadpool(
                functools.partial(
                    services.history.window,
                    session.session_id,
                    max_turns=int(self.settings.chat_history_turns),
                    budget_chars=int(self.settings.chat_history_char_budget),
                )
            )
            if message.session_id
            else HistoryWindow()
        )
        previous_quotes = (
            await run_in_threadpool(services.history.last_quotes, session.session_id)
            if message.session_id
            else []
        )
        previous_plans = (
            await run_in_threadpool(services.history.last_plans, session.session_id)
            if message.session_id
            else []
        )
        answer_id = uuid.uuid4().hex
        turn = TurnRequest(
            question=message.question,
            scope=UserScope.from_user_dict(user),  # dựng lại mỗi lượt (design D2)
            session_id=session.session_id,
            history=history,
            previous_slots=services.sessions.previous_slots(session),
            request_id=uuid.uuid4().hex,
            previous_quotes=tuple(previous_quotes),
            previous_plans=tuple(previous_plans),
            display_name=str(user.get("display_name") or user.get("username") or ""),
        )
        try:
            services.runner.submit(
                answer_id=answer_id,
                username=self.username,
                session_id=session.session_id,
                client_msg_id=reply_id,
                turn=turn,
            )
        except RunnerBusy:
            await self.send(error(ProtocolError.BUSY, client_msg_id=reply_id))
            return
        except RuntimeError:
            logger.exception("chat_submit_failed", extra={"username": self.username})
            await self.send(error(ProtocolError.INTERNAL, client_msg_id=reply_id))
            return

        await self.send(
            ack(client_msg_id=reply_id, answer_id=answer_id, session_id=session.session_id)
        )
        self._follow(answer_id, 0)

    # ── resume / cancel ──

    async def _handle_resume(self, message: ResumeMessage) -> None:
        meta = self.services.buffer.meta(message.answer_id)
        if meta is None or meta.owner != self.username:
            await self.send(error(ProtocolError.ANSWER_NOT_FOUND))
            return
        if meta.expired:
            await self.send(answer_expired(message.answer_id))
            return
        self._follow(message.answer_id, message.last_seq)

    async def _handle_cancel(self, message: CancelMessage) -> None:
        meta = self.services.buffer.meta(message.answer_id)
        if meta is None or meta.owner != self.username or meta.expired:
            await self.send(error(ProtocolError.ANSWER_NOT_FOUND))
            return
        self.services.buffer.request_cancel(message.answer_id)

    # ── Gửi event từ buffer ──

    def _follow(self, answer_id: str, after_seq: int) -> None:
        previous = self._followers.pop(answer_id, None)
        if previous is not None:
            previous.cancel()
        task = asyncio.create_task(self._pump(answer_id, after_seq))
        self._followers[answer_id] = task
        task.add_done_callback(lambda t: self._forget(answer_id, t))

    def _forget(self, answer_id: str, task: asyncio.Task[None]) -> None:
        if self._followers.get(answer_id) is task:
            del self._followers[answer_id]

    def _following(self) -> bool:
        return any(not task.done() for task in self._followers.values())

    async def _pump(self, answer_id: str, after_seq: int) -> None:
        buffer = self.services.buffer
        seq = after_seq
        try:
            while True:
                read = await run_in_threadpool(
                    buffer.read, answer_id, seq, timeout_ms=READ_TIMEOUT_MS
                )
                if read.expired:
                    await self.send(answer_expired(answer_id))
                    return
                for event in read.events:
                    await self.send(answer_event(answer_id, event))
                    seq = int(event["seq"])
                if read.complete:
                    return
        except (WebSocketDisconnect, RuntimeError):
            return  # kết nối đã đóng; lượt vẫn chạy tiếp trong runner

    async def send(self, message: dict[str, Any]) -> None:
        async with self._send_lock:
            await self.websocket.send_json(message)
