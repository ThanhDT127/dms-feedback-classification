"""Contextualizer — Bước 0 (spec ``chat-contextualization``, design b01 D6–D7)."""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .prompt_loader import load_prompt, sanitize_prompt_data
from .types import HistoryTurn, HistoryWindow, LLMClient, SessionSlots

logger = logging.getLogger("dms-chat-contextualizer")

PROMPT_NAME = "contextualize_v1"
CALL_TYPE = "chat_contextualize"
ANSWER_MAX_CHARS = 300
NO_SUMMARY = "(chưa có)"
SUMMARY_MAX_CHARS = 1200

CODE_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)


class _ContextualizeOutput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    standalone_question: str = Field(min_length=1, max_length=4000)
    is_follow_up: bool


@dataclass(frozen=True)
class ContextualizationResult:
    rewritten_query: str
    is_follow_up: bool = False
    failed: bool = False
    llm_usage: dict[str, Any] = field(default_factory=dict)


def render_history(turns: Sequence[HistoryTurn], answer_max_chars: int = ANSWER_MAX_CHARS) -> str:
    lines: list[str] = []
    for index, turn in enumerate(turns, start=1):
        lines.append(f"Lượt {index} – Người dùng: {sanitize_prompt_data(turn.question)}")
        answer = sanitize_prompt_data(turn.answer_summary, answer_max_chars)
        lines.append(f"Lượt {index} – Trợ lý: {answer}")
    return "\n".join(lines)


class Contextualizer:
    def __init__(
        self,
        llm: LLMClient,
        *,
        max_turns: int = 5,
        answer_max_chars: int = ANSWER_MAX_CHARS,
    ) -> None:
        self.llm = llm
        self.max_turns = max_turns
        self.answer_max_chars = answer_max_chars

    def rewrite(
        self, question: str, history: HistoryWindow | Sequence[HistoryTurn]
    ) -> ContextualizationResult:
        window = HistoryWindow.of(history)
        turns = list(window.turns)[-self.max_turns :] if self.max_turns > 0 else []
        summary = window.summary.strip()
        # Không có gì để tham chiếu thì không tốn một lần gọi LLM.
        if not turns and not summary:
            return ContextualizationResult(rewritten_query=question)

        prompt = load_prompt(
            PROMPT_NAME,
            {
                "session_summary": sanitize_prompt_data(summary, SUMMARY_MAX_CHARS) or NO_SUMMARY,
                "history": render_history(turns, self.answer_max_chars),
                "question": sanitize_prompt_data(question),
            },
        )
        try:
            result = self.llm.generate_json(prompt.text, call_type=CALL_TYPE)
            parsed = _ContextualizeOutput.model_validate_json(CODE_FENCE.sub("", result.text))
        except Exception as exc:  # timeout, lỗi SDK hoặc JSON hỏng: không chặn luồng
            logger.warning(
                "contextualization_failed",
                extra={
                    "error_type": type(exc).__name__,
                    "prompt_version": prompt.version,
                    "prompt_sha256": prompt.sha256,
                },
            )
            return ContextualizationResult(rewritten_query=question, failed=True)

        rewritten = parsed.standalone_question.strip()
        if not rewritten:
            return ContextualizationResult(rewritten_query=question, failed=True)
        logger.debug(
            "contextualized",
            extra={"prompt_version": prompt.version, "prompt_sha256": prompt.sha256},
        )
        return ContextualizationResult(
            rewritten_query=rewritten,
            is_follow_up=parsed.is_follow_up,
            llm_usage=dict(result.usage),
        )


def merge_slots(
    previous: SessionSlots | None,
    current: SessionSlots,
    *,
    is_follow_up: bool,
) -> SessionSlots:
    """Cùng loại thì giá trị mới ghi đè; loại không được nhắc giữ giá trị cũ."""
    if not is_follow_up or previous is None:
        return current
    return SessionSlots(
        date_range=current.date_range or previous.date_range,
        compare_range=current.compare_range or previous.compare_range,
        entities={**previous.entities, **current.entities},
    )
