"""``EventSink`` ghi thẳng vào Answer Buffer (design b06 D3, D6)."""

from __future__ import annotations

from typing import Any

from ..ai.answer_events import AnswerEvent
from .answer_buffer import AnswerBuffer


class BufferSink:
    """``emit`` → ``buffer.append``; ``is_cancelled`` → ``buffer.is_cancel_requested``.

    Giữ ``last_seq`` và bản sao event đã ghi để runner nối ``seq`` và lưu lượt.
    """

    def __init__(self, buffer: AnswerBuffer, answer_id: str) -> None:
        self.buffer = buffer
        self.answer_id = answer_id
        self.last_seq = 0
        self.events: list[dict[str, Any]] = []

    def emit(self, event: AnswerEvent) -> None:
        payload = event.to_dict()
        self.buffer.append(self.answer_id, payload)
        self.last_seq = event.seq
        self.events.append(payload)

    def is_cancelled(self) -> bool:
        return self.buffer.is_cancel_requested(self.answer_id)
