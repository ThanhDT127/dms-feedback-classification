"""Giao thức event của một câu trả lời (spec ``chat-answer-events``, design b05 D1–D2).

Một câu trả lời là một chuỗi event ``{seq, type, data}``; ``seq`` bắt đầu từ 1 và tăng đúng 1.
``done`` luôn là event cuối. b06 ghi các event này vào Answer Buffer, b07 vẽ lên giao diện.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

EVENT_VERSION = 1


class EventType(StrEnum):
    STATUS = "status"
    DATA_BLOCK = "data_block"
    COMMENTARY = "commentary"
    SUGGESTIONS = "suggestions"
    REFUSAL = "refusal"
    CLARIFY = "clarify"
    DONE = "done"
    ERROR = "error"


class DoneStatus(StrEnum):
    OK = "ok"
    PARTIAL = "partial"
    NO_DATA = "no_data"
    REFUSED = "refused"
    CLARIFY = "clarify"
    HELP = "help"
    NOT_SUPPORTED = "not_supported"
    ERROR = "error"
    CANCELLED = "cancelled"


class CommentaryStatus(StrEnum):
    FULL = "full"  # LLM viết xong trong giới hạn câu
    PARTIAL = "partial"  # đã phát vài câu rồi LLM hỏng
    UNAVAILABLE = "unavailable"  # LLM hỏng trước câu đầu tiên
    SKIPPED = "skipped"  # cố ý không gọi LLM (KPI đơn, no_data, tắt setting)


class Stage(StrEnum):
    UNDERSTANDING = "understanding"
    PLANNING = "planning"
    QUERYING = "querying"
    WRITING = "writing"


@dataclass(frozen=True)
class AnswerEvent:
    seq: int
    type: EventType
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"seq": self.seq, "type": self.type.value, "data": dict(self.data)}


class EventSink(Protocol):
    """Nơi nhận event. ``emit`` được gọi từ thread sinh câu trả lời (design D2)."""

    def emit(self, event: AnswerEvent) -> None: ...

    def is_cancelled(self) -> bool: ...


class ListSink:
    """Sink trong bộ nhớ cho test và CLI; huỷ được bằng ``cancel()``."""

    def __init__(self) -> None:
        self.events: list[AnswerEvent] = []
        self._cancelled = False

    def emit(self, event: AnswerEvent) -> None:
        self.events.append(event)

    def is_cancelled(self) -> bool:
        return self._cancelled

    def cancel(self) -> None:
        self._cancelled = True

    # ── Tiện ích cho test ──

    def to_list(self) -> list[dict[str, Any]]:
        return [event.to_dict() for event in self.events]

    def types(self) -> list[str]:
        return [event.type.value for event in self.events]

    def of_type(self, event_type: EventType) -> list[AnswerEvent]:
        return [event for event in self.events if event.type is event_type]

    @property
    def done(self) -> AnswerEvent | None:
        events = self.of_type(EventType.DONE)
        return events[-1] if events else None
