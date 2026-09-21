"""Phiên chat và lưu lượt hỏi đáp trên ``ChatStore`` của Dev A (design b06 D8, D9).

Quy tắc chung: phiên không tồn tại, hết hạn hoặc thuộc người khác đều là ``SessionNotFound``
(kể cả với admin) để không lộ việc phiên có tồn tại hay không.
"""

from __future__ import annotations

import copy
import json
import logging
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from ...settings import Settings
from ..ai.answer_events import EventType
from ..ai.types import SessionSlots
from ..contract import ChatMessage, ChatSession, MessageRole
from ..db.chat_store import ChatStore
from .turn_runner import TurnRecord

logger = logging.getLogger("dms-chat")

TITLE_MAX_CHARS = 60
RENAME_MAX_CHARS = 120
SESSION_TTL_DAYS = 7
# Tin nhắn đọc tối đa khi dựng lịch sử/hiển thị; ChatStore trả theo thứ tự tăng dần.
MESSAGES_READ_LIMIT = 1000
# ``error`` không có trong danh sách của spec nhưng cần để vẽ lại câu trả lời lỗi.
STORED_EVENT_TYPES = frozenset(
    {
        EventType.DATA_BLOCK.value,
        EventType.COMMENTARY.value,
        EventType.REFUSAL.value,
        EventType.CLARIFY.value,
        EventType.SUGGESTIONS.value,
        EventType.ERROR.value,
    }
)
_TRUNCATABLE_KINDS = ("table", "ranking")


class SessionNotFound(LookupError):
    pass


@dataclass(frozen=True)
class SessionServiceConfig:
    metadata_max_bytes: int = 262144
    ttl_days: int = SESSION_TTL_DAYS

    @classmethod
    def from_settings(cls, settings: Settings) -> SessionServiceConfig:
        return cls(
            metadata_max_bytes=int(settings.chat_message_metadata_max_bytes),
            ttl_days=int(settings.chat_session_ttl_days),
        )


class ChatSessionService:
    def __init__(
        self,
        store: ChatStore,
        *,
        config: SessionServiceConfig | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        new_id: Callable[[], str] = lambda: uuid.uuid4().hex,
    ) -> None:
        self.store = store
        self.config = config or SessionServiceConfig()
        self._now = now
        self._new_id = new_id

    # ── Phiên ──

    def get_owned(self, username: str, session_id: str) -> ChatSession:
        session = self.store.get_session(session_id)
        if session is None or session.user_id != username or self._expired(session):
            raise SessionNotFound(session_id)
        return session

    def open_for_ask(self, username: str, session_id: str | None, question: str) -> ChatSession:
        if session_id:
            return self.get_owned(username, session_id)
        return self.store.create_session(
            session_id=self._new_id(),
            user_id=username,
            title=make_title(question),
            ttl_days=self.config.ttl_days,
        )

    def list_sessions(self, username: str, limit: int = 50) -> list[ChatSession]:
        return [s for s in self.store.list_sessions(username, limit=limit) if not self._expired(s)]

    def rename(self, username: str, session_id: str, title: str) -> ChatSession:
        cleaned = " ".join((title or "").split())
        if not 1 <= len(cleaned) <= RENAME_MAX_CHARS:
            raise ValueError("title must be 1-120 characters")
        self.get_owned(username, session_id)
        self.store.update_session(session_id, title=cleaned)
        return self.get_owned(username, session_id)

    def delete(self, username: str, session_id: str) -> None:
        self.get_owned(username, session_id)
        self.store.delete_session(session_id)

    def messages(self, username: str, session_id: str) -> list[dict[str, Any]]:
        self.get_owned(username, session_id)
        return [
            message_to_dict(m) for m in self.store.get_messages(session_id, MESSAGES_READ_LIMIT)
        ]

    @staticmethod
    def previous_slots(session: ChatSession) -> SessionSlots:
        try:
            return SessionSlots.from_dict(json.loads(session.slots_json or "{}"))
        except (TypeError, ValueError, KeyError, AttributeError):
            return SessionSlots()

    # ── Lưu lượt ──

    def persist_user_message(
        self, session_id: str, question: str, *, client_msg_id: str, answer_id: str
    ) -> None:
        self.store.add_message(
            session_id,
            MessageRole.USER,
            question,
            metadata={"client_msg_id": client_msg_id, "answer_id": answer_id},
        )

    def persist_turn(self, record: TurnRecord) -> None:
        """Finalizer của runner; lỗi chỉ ghi log, không ảnh hưởng câu trả lời đã stream."""
        try:
            metadata = self.build_metadata(record)
            self.store.add_message(
                record.session_id, MessageRole.ASSISTANT, record.summary, metadata=metadata
            )
            # Hạn phiên trượt (b11 D10): mỗi lượt hoàn tất gia hạn thêm CHAT_SESSION_TTL_DAYS.
            expires_at = (self._now() + timedelta(days=self.config.ttl_days)).isoformat()
            if record.outcome is not None:
                self.store.update_session(
                    record.session_id,
                    slots_json=json.dumps(record.outcome.slots.to_dict(), ensure_ascii=False),
                    expires_at=expires_at,
                )
            else:
                self.store.update_session(record.session_id, expires_at=expires_at)
        except Exception as exc:
            logger.error(
                "chat_persist_failed",
                extra={
                    "answer_id": record.answer_id,
                    "session_id": record.session_id,
                    "request_id": record.turn.request_id,
                    "error_type": type(exc).__name__,
                },
            )

    def build_metadata(self, record: TurnRecord) -> dict[str, Any]:
        outcome = record.outcome
        metadata: dict[str, Any] = {
            "answer_id": record.answer_id,
            "request_id": outcome.request_id if outcome else record.turn.request_id,
            "intent": outcome.intent.value if outcome and outcome.intent else None,
            "decision": outcome.decision.value if outcome else None,
            "done_status": record.done_status.value,
            "slots": outcome.slots.to_dict() if outcome else {},
            # Plan đã qua Plan Guard, để "xuất kết quả vừa rồi" chạy lại được (b10 D7).
            "plans": _plans_metadata(outcome),
            "events": [
                {"seq": e["seq"], "type": e["type"], "data": e.get("data", {})}
                for e in record.events
                if e.get("type") in STORED_EVENT_TYPES
            ],
        }
        return fit_metadata(metadata, self.config.metadata_max_bytes)

    def _expired(self, session: ChatSession) -> bool:
        if not session.expires_at:
            return False
        try:
            expires = datetime.fromisoformat(session.expires_at)
        except ValueError:
            return False
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=UTC)
        return expires <= self._now()


def _plans_metadata(outcome: Any | None) -> list[dict[str, Any]]:
    """Bước đã chạy được của lượt, kèm intent và khoảng ngày (b10 task 4.1)."""
    if outcome is None or outcome.validated_plan is None:
        return []
    steps = outcome.validated_plan.steps
    plans: list[dict[str, Any]] = []
    for result in outcome.step_results:
        if not getattr(result.status, "ok", False):
            continue
        planned = steps[result.index - 1] if 0 < result.index <= len(steps) else None
        name = result.function_name or (planned.function_name if planned else None)
        if not name:
            continue
        entry: dict[str, Any] = {
            "step": result.index,
            "function_name": name,
            "params": dict(planned.params) if planned is not None else {},
            "intent": outcome.intent.value if outcome.intent else None,
        }
        if getattr(result, "sql", None):
            entry["sql"] = result.sql
        plans.append(entry)
    return plans


def make_title(question: str) -> str:
    cleaned = " ".join((question or "").split())
    if len(cleaned) <= TITLE_MAX_CHARS:
        return cleaned
    return cleaned[: TITLE_MAX_CHARS - 1].rstrip() + "…"


def json_size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False).encode("utf-8"))


def fit_metadata(metadata: Mapping[str, Any], max_bytes: int) -> dict[str, Any]:
    """Cắt dòng của khối ``table``/``ranking`` cho tới khi vừa ``max_bytes``."""
    result = copy.deepcopy(dict(metadata))
    if json_size(result) <= max_bytes:
        return result
    result["truncated_for_storage"] = True
    rows_lists = [
        (payload, key)
        for event in result.get("events", [])
        if event.get("type") == EventType.DATA_BLOCK.value
        and event.get("data", {}).get("kind") in _TRUNCATABLE_KINDS
        for payload in [event["data"].get("payload", {})]
        for key in ("rows", "items")
        if isinstance(payload.get(key), list)
    ]
    while json_size(result) > max_bytes:
        longest = max(rows_lists, key=lambda pk: len(pk[0][pk[1]]), default=None)
        if longest is None or not longest[0][longest[1]]:
            break
        payload, key = longest
        payload[key] = payload[key][: len(payload[key]) // 2]
        payload["truncated"] = True
    if json_size(result) > max_bytes:
        # Vẫn quá lớn (vd. nhận định/trích dẫn rất dài): chỉ giữ phần định danh của lượt.
        result["events"] = []
    return result


def message_to_dict(message: ChatMessage) -> dict[str, Any]:
    try:
        metadata = json.loads(message.metadata_json) if message.metadata_json else {}
    except ValueError:
        metadata = {}
    role = message.role.value if hasattr(message.role, "value") else str(message.role)
    return {
        "message_id": message.message_id,
        "role": role,
        "content": message.content,
        "created_at": message.created_at,
        "metadata": metadata,
    }
