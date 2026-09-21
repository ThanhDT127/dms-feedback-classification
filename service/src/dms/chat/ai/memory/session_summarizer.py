"""Tóm tắt phiên cuộn (spec ``chat-conversation-memory``, design b11 D2, D3).

Chạy **sau** khi lượt đã gửi ``done`` nên không làm chậm câu trả lời; gộp theo lô để không
phải gọi LLM mỗi lượt.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ..contextualizer import CODE_FENCE
from ..prompt_loader import load_prompt, sanitize_prompt_data
from ..types import LLMClient
from .store import (
    SUMMARY_METHOD_EXTRACTIVE,
    SUMMARY_METHOD_LLM,
    MemoryVersionConflict,
    SessionMemory,
    SessionMemoryStore,
)
from .summary_guard import (
    DEFAULT_MAX_CHARS,
    SummarySource,
    check_summary,
    extractive_summary,
)

logger = logging.getLogger("dms-chat-memory")

CALL_TYPE_MEMORY_SUMMARY = "chat_memory_summary"
PROMPT_NAME = "memory_summary_v1"
MAX_TOPICS = 5
TURN_TEXT_MAX_CHARS = 400


class _SummaryOutput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    summary: str = Field(default="")
    topics: list[str] = Field(default_factory=list)


@dataclass(frozen=True)
class PendingTurn:
    """Một lượt đã nằm ngoài cửa sổ và chưa được tóm tắt."""

    message_id: int
    question: str
    answer_summary: str
    quotes: tuple[str, ...] = ()


@dataclass(frozen=True)
class SummarizerConfig:
    max_chars: int = DEFAULT_MAX_CHARS
    batch: int = 3

    @classmethod
    def from_settings(cls, settings: Any) -> SummarizerConfig:
        return cls(
            max_chars=int(settings.chat_memory_summary_max_chars),
            batch=int(settings.chat_memory_compact_batch),
        )

    @property
    def enabled(self) -> bool:
        return self.batch > 0


class SessionSummarizer:
    def __init__(
        self,
        store: SessionMemoryStore,
        *,
        llm: LLMClient | None = None,
        config: SummarizerConfig | None = None,
    ) -> None:
        self.store = store
        self.llm = llm
        self.config = config or SummarizerConfig()

    def should_run(self, pending: Sequence[PendingTurn]) -> bool:
        return self.config.enabled and len(pending) >= self.config.batch

    def summarize(
        self,
        session_id: str,
        pending: Sequence[PendingTurn],
        *,
        request_id: str = "",
        use_llm: bool = True,
    ) -> SessionMemory | None:
        """Gộp ``pending`` vào tóm tắt của phiên; trả ``None`` khi bỏ qua hoặc lỗi."""
        if not self.should_run(pending):
            return None

        current = self.store.load(session_id) or SessionMemory()
        source = SummarySource(
            previous_summary=current.summary,
            turns=tuple((turn.question, turn.answer_summary) for turn in pending),
            quotes=tuple(quote for turn in pending for quote in turn.quotes),
        )

        summary, topics, method = self._write_summary(
            source, request_id=request_id, use_llm=use_llm
        )
        memory = SessionMemory(
            summary=summary,
            topics=topics,
            summarized_until_message_id=pending[-1].message_id,
            method=method,
        )
        try:
            saved = self.store.save(session_id, memory, if_version=current.version)
        except MemoryVersionConflict:
            # Lượt khác đã tóm tắt trước; bỏ lần này, lượt sau sẽ gộp lại (design D2).
            logger.info(
                "memory_summary_conflict",
                extra={"session_id": session_id, "request_id": request_id},
            )
            return None
        except Exception:
            logger.warning(
                "memory_summary_failed",
                extra={"session_id": session_id, "request_id": request_id, "stage": "save"},
            )
            return None
        logger.info(
            "memory_summary_saved",
            extra={
                "session_id": session_id,
                "request_id": request_id,
                "method": saved.method,
                "turns": len(pending),
                "chars": len(saved.summary),
            },
        )
        return saved

    # ── Nội bộ ──

    def _write_summary(
        self, source: SummarySource, *, request_id: str, use_llm: bool = True
    ) -> tuple[str, tuple[str, ...], str]:
        fallback = extractive_summary(source, max_chars=self.config.max_chars)
        if self.llm is None or not use_llm:
            return fallback, (), SUMMARY_METHOD_EXTRACTIVE

        prompt = render_summary_prompt(source, max_chars=self.config.max_chars)
        try:
            result = self.llm.generate_json(prompt.text, call_type=CALL_TYPE_MEMORY_SUMMARY)
            parsed = _SummaryOutput.model_validate_json(CODE_FENCE.sub("", result.text))
        except Exception as exc:  # LLM lỗi, timeout hoặc JSON hỏng
            logger.warning(
                "memory_summary_failed",
                extra={"request_id": request_id, "error_type": type(exc).__name__},
            )
            return fallback, (), SUMMARY_METHOD_EXTRACTIVE

        candidate = parsed.summary.strip()
        verdict = check_summary(candidate, source, max_chars=self.config.max_chars)
        if not verdict.ok:
            logger.info(
                "memory_summary_rejected",
                extra={
                    "request_id": request_id,
                    "reason": verdict.reason.value if verdict.reason else None,
                },
            )
            return fallback, (), SUMMARY_METHOD_EXTRACTIVE

        topics = tuple(
            " ".join(str(topic).split()) for topic in parsed.topics[:MAX_TOPICS] if str(topic).strip()
        )
        return candidate, topics, SUMMARY_METHOD_LLM


def render_summary_prompt(source: SummarySource, *, max_chars: int = DEFAULT_MAX_CHARS):
    previous = source.previous_summary.strip() or "(chưa có)"
    lines = [f"Tóm tắt trước: {sanitize_prompt_data(previous, max_chars)}"]
    for index, (question, answer) in enumerate(source.turns, start=1):
        lines.append(
            f"Lượt {index} – Người dùng: {sanitize_prompt_data(question, TURN_TEXT_MAX_CHARS)}"
        )
        lines.append(
            f"Lượt {index} – Trợ lý: {sanitize_prompt_data(answer, TURN_TEXT_MAX_CHARS)}"
        )
    return load_prompt(PROMPT_NAME, {"max_chars": str(max_chars), "content": "\n".join(lines)})


__all__ = [
    "CALL_TYPE_MEMORY_SUMMARY",
    "PendingTurn",
    "SessionSummarizer",
    "SummarizerConfig",
    "render_summary_prompt",
]
