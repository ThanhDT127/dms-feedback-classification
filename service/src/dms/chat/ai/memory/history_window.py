"""Cửa sổ ngữ cảnh theo ngân sách ký tự (spec ``chat-conversation-memory``, design b11 D5).

Hàm thuần: nhận danh sách lượt đã ghép và bản trí nhớ, trả ``HistoryWindow`` cho Contextualizer
và danh sách lượt cần tóm tắt cho ``SessionSummarizer``. Không đọc DB ở đây.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ..types import HistoryTurn, HistoryWindow
from .session_summarizer import PendingTurn
from .store import SessionMemory

DEFAULT_BUDGET_CHARS = 3000


@dataclass(frozen=True)
class StoredTurn:
    """Một cặp câu hỏi – câu trả lời đã lưu; ``message_id`` là id tin nhắn trả lời."""

    message_id: int
    question: str
    answer_summary: str
    quotes: tuple[str, ...] = ()

    def as_history(self) -> HistoryTurn:
        return HistoryTurn(question=self.question, answer_summary=self.answer_summary)

    def as_pending(self) -> PendingTurn:
        return PendingTurn(
            message_id=self.message_id,
            question=self.question,
            answer_summary=self.answer_summary,
            quotes=self.quotes,
        )


def unsummarized(turns: Sequence[StoredTurn], memory: SessionMemory | None) -> list[StoredTurn]:
    """Các lượt nằm sau điểm đã tóm tắt."""
    until = memory.summarized_until_message_id if memory is not None else None
    if until is None:
        return list(turns)
    return [turn for turn in turns if turn.message_id > until]


def build_window(
    turns: Sequence[StoredTurn],
    memory: SessionMemory | None,
    *,
    max_turns: int,
    budget_chars: int = DEFAULT_BUDGET_CHARS,
    include_summary: bool = True,
) -> HistoryWindow:
    """Tóm tắt trước, rồi thêm lượt từ mới nhất tới cũ nhất cho tới khi hết ngân sách."""
    summary = (memory.summary.strip() if memory is not None else "") if include_summary else ""
    used = len(summary)
    picked: list[HistoryTurn] = []
    if max_turns > 0:
        for turn in reversed(unsummarized(turns, memory)):
            if len(picked) >= max_turns:
                break
            history = turn.as_history()
            if used + history.char_size() > budget_chars:
                break
            picked.append(history)
            used += history.char_size()
    picked.reverse()
    return HistoryWindow(summary=summary, turns=tuple(picked))


def pending_for_summary(
    turns: Sequence[StoredTurn], memory: SessionMemory | None, *, window_turns: int
) -> list[PendingTurn]:
    """Lượt đã ra khỏi cửa sổ ``CHAT_HISTORY_TURNS`` mà chưa được gộp vào tóm tắt (design D2)."""
    remaining = unsummarized(turns, memory)
    outside = remaining[:-window_turns] if window_turns > 0 else remaining
    return [turn.as_pending() for turn in outside]


__all__ = [
    "DEFAULT_BUDGET_CHARS",
    "StoredTurn",
    "build_window",
    "pending_for_summary",
    "unsummarized",
]
