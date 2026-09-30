"""``ConversationHistory`` đọc từ ``ChatStore`` (design b06 D8, b11 D5)."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from ..ai.memory.history_window import (
    DEFAULT_BUDGET_CHARS,
    StoredTurn,
    build_window,
    pending_for_summary,
)
from ..ai.memory.session_summarizer import PendingTurn
from ..ai.memory.store import SessionMemory, SessionMemoryStore
from ..ai.types import HistoryTurn, HistoryWindow
from ..contract import ChatMessage, MessageRole
from ..db.chat_store import ChatStore
from .session_service import MESSAGES_READ_LIMIT


class HistoryAdapter:
    """Ghép cặp tin nhắn ``user``/``assistant`` liền nhau; câu trả lời là ``summary`` đã lưu."""

    def __init__(
        self,
        store: ChatStore,
        *,
        read_limit: int = MESSAGES_READ_LIMIT,
        memory: SessionMemoryStore | None = None,
    ) -> None:
        self.store = store
        self.read_limit = read_limit
        self.memory = memory

    # ── Lịch sử cho Contextualizer ──

    def last_turns(self, session_id: str, n: int) -> Sequence[HistoryTurn]:
        if n <= 0:
            return []
        return [turn.as_history() for turn in self.stored_turns(session_id)][-n:]

    def window(
        self,
        session_id: str,
        *,
        max_turns: int,
        budget_chars: int = DEFAULT_BUDGET_CHARS,
        include_summary: bool = True,
    ) -> HistoryWindow:
        """Tóm tắt phiên + các lượt sau điểm đã tóm tắt, vừa ngân sách ký tự (b11 D5)."""
        return build_window(
            self.stored_turns(session_id),
            self.load_memory(session_id),
            max_turns=max_turns,
            budget_chars=budget_chars,
            include_summary=include_summary,
        )

    # ── Trí nhớ phiên (b11 D2) ──

    def load_memory(self, session_id: str) -> SessionMemory | None:
        if self.memory is None:
            return None
        return self.memory.load(session_id)

    def pending_turns(self, session_id: str, *, window_turns: int) -> list[PendingTurn]:
        return pending_for_summary(
            self.stored_turns(session_id),
            self.load_memory(session_id),
            window_turns=window_turns,
        )

    def stored_turns(self, session_id: str) -> list[StoredTurn]:
        return pair_turns(self.store.get_messages(session_id, self.read_limit))

    # ── Dữ liệu của câu trả lời trước ──

    def last_quotes(self, session_id: str) -> list[dict[str, Any]]:
        """Trích dẫn của câu trả lời gần nhất có khối quote — dùng cho LOOKUP_SIMILAR (b08 D6).

        Chỉ đọc từ phiên của chính user (endpoint đã kiểm chủ sở hữu), nên không lộ dữ liệu
        ngoài những gì user đã được xem.
        """
        for message in reversed(self.store.get_messages(session_id, self.read_limit)):
            if MessageRole(message.role) is not MessageRole.ASSISTANT or not message.metadata_json:
                continue
            quotes = _quotes_of(message.metadata_json)
            if quotes:
                return quotes
        return []

    def last_plans(self, session_id: str) -> list[dict[str, Any]]:
        """Plan của câu trả lời gần nhất có dữ liệu — dùng cho "xuất kết quả vừa rồi" (b10 D7).

        Chỉ trả plan, không trả dữ liệu đã lưu: file xuất luôn được dựng lại với quyền hiện tại.
        """
        for message in reversed(self.store.get_messages(session_id, self.read_limit)):
            if MessageRole(message.role) is not MessageRole.ASSISTANT or not message.metadata_json:
                continue
            try:
                plans = json.loads(message.metadata_json).get("plans") or []
            except (ValueError, AttributeError):
                continue
            # ``params`` rỗng vẫn là plan hợp lệ: "đơn vị nào nhiều vấn đề nhất" không có bộ lọc.
            usable = [
                dict(plan)
                for plan in plans
                if isinstance(plan, dict)
                and plan.get("function_name")
                and isinstance(plan.get("params", {}), dict)
            ]
            if usable:
                return usable
        return []


def pair_turns(messages: Sequence[ChatMessage]) -> list[StoredTurn]:
    """Ghép ``user`` → ``assistant`` theo ``answer_id``; tin nhắn lẻ bị bỏ qua."""
    turns: list[StoredTurn] = []
    pending: str | None = None
    pending_answer_id: str | None = None
    for message in messages:
        role = MessageRole(message.role)
        if role is MessageRole.USER:
            pending = message.content
            pending_answer_id = _answer_id(message.metadata_json)
        elif role is MessageRole.ASSISTANT and pending is not None:
            answer_id = _answer_id(message.metadata_json)
            if pending_answer_id is None or answer_id is None or answer_id == pending_answer_id:
                turns.append(
                    StoredTurn(
                        message_id=int(message.message_id or 0),
                        question=pending,
                        answer_summary=message.content,
                        quotes=tuple(
                            str(quote.get("content") or "")
                            for quote in _quotes_of(message.metadata_json)
                            if quote.get("content")
                        ),
                    )
                )
            pending = None
    return turns


def _quotes_of(metadata_json: str | None) -> list[dict[str, Any]]:
    if not metadata_json:
        return []
    try:
        events = json.loads(metadata_json).get("events") or []
    except (ValueError, AttributeError):
        return []
    return [
        dict(quote)
        for event in events
        if event.get("type") == "data_block" and (event.get("data") or {}).get("kind") == "quote"
        for quote in (event["data"].get("payload") or {}).get("quotes", [])
    ]


def _answer_id(metadata_json: str | None) -> str | None:
    try:
        value = json.loads(metadata_json).get("answer_id") if metadata_json else None
    except (ValueError, AttributeError):
        return None
    return str(value) if value else None
