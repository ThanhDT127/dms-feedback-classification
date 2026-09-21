"""Guard cho tóm tắt phiên (spec ``chat-conversation-memory``, design b11 D3).

Tóm tắt sai sẽ âm thầm làm lệch mọi lượt sau, nên bản của LLM chỉ được lưu khi qua hết 4 luật;
không qua thì dùng bản trích xuất bằng Python — kém gọn nhưng luôn đúng.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum

from ...guardrails.synthesis_guard import LEAK_MARKERS
from ..fact_sheet import number_tokens

logger = logging.getLogger("dms-chat-memory")

# Đoạn trùng nội dung trích dẫn từ ngần này ký tự trở lên là chép nguyên văn phản hồi.
QUOTE_OVERLAP_CHARS = 30
QUESTION_MAX_CHARS = 120
ANSWER_MAX_CHARS = 120
DEFAULT_MAX_CHARS = 800


class SummaryDropReason(StrEnum):
    BAD_LENGTH = "BAD_LENGTH"
    UNGROUNDED_NUMBER = "UNGROUNDED_NUMBER"
    QUOTED_CONTENT = "QUOTED_CONTENT"
    LEAK = "LEAK"


@dataclass(frozen=True)
class SummaryCheck:
    ok: bool
    reason: SummaryDropReason | None = None


@dataclass(frozen=True)
class SummarySource:
    """Nguồn của một lần tóm tắt: tóm tắt cũ + các lượt được gộp."""

    previous_summary: str = ""
    turns: tuple[tuple[str, str], ...] = ()  # (câu hỏi, summary của lượt)
    quotes: tuple[str, ...] = ()  # nội dung trích dẫn đã hiển thị trong các lượt đó

    def text(self) -> str:
        parts = [self.previous_summary]
        for question, answer in self.turns:
            parts.append(question)
            parts.append(answer)
        return "\n".join(part for part in parts if part)


def check_summary(
    summary: str, source: SummarySource, *, max_chars: int = DEFAULT_MAX_CHARS
) -> SummaryCheck:
    """Kiểm theo đúng thứ tự của design D3."""
    text = (summary or "").strip()
    if not 1 <= len(text) <= max_chars:
        return _drop(SummaryDropReason.BAD_LENGTH)

    allowed = number_tokens(source.text())
    if any(token not in allowed for token in number_tokens(text)):
        return _drop(SummaryDropReason.UNGROUNDED_NUMBER)

    if _copies_quote(text, source.quotes):
        return _drop(SummaryDropReason.QUOTED_CONTENT)

    lowered = text.casefold()
    if any(marker in lowered for marker in LEAK_MARKERS):
        return _drop(SummaryDropReason.LEAK)

    return SummaryCheck(ok=True)


def extractive_summary(
    source: SummarySource, *, max_chars: int = DEFAULT_MAX_CHARS
) -> str:
    """Bản dự phòng: nối tóm tắt cũ với từng lượt, cắt phần cũ nhất cho vừa giới hạn."""
    lines: list[str] = []
    if source.previous_summary.strip():
        lines.append(source.previous_summary.strip())
    for question, answer in source.turns:
        asked = _clip(question, QUESTION_MAX_CHARS)
        answered = _clip(answer, ANSWER_MAX_CHARS)
        if not asked and not answered:
            continue
        lines.append(f"{asked} → {answered}" if answered else asked)

    while lines and len("\n".join(lines)) > max_chars:
        lines.pop(0)
    text = "\n".join(lines)
    return text[:max_chars].rstrip() if len(text) > max_chars else text


# ── Nội bộ ──


def _copies_quote(summary: str, quotes: Iterable[str]) -> bool:
    """Có đoạn nào từ 30 ký tự trở lên của một trích dẫn nằm nguyên trong tóm tắt không."""
    folded = " ".join(summary.split()).casefold()
    for quote in quotes:
        cleaned = " ".join(str(quote or "").split()).casefold()
        if len(cleaned) < QUOTE_OVERLAP_CHARS:
            continue
        for start in range(len(cleaned) - QUOTE_OVERLAP_CHARS + 1):
            if cleaned[start : start + QUOTE_OVERLAP_CHARS] in folded:
                return True
    return False


def _clip(text: str, limit: int) -> str:
    cleaned = " ".join(str(text or "").split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1].rstrip() + "…"


def _drop(reason: SummaryDropReason) -> SummaryCheck:
    # Chỉ log lý do: bản tóm tắt có thể chứa dữ liệu của khách hàng.
    logger.info("memory_summary_dropped", extra={"reason": reason.value})
    return SummaryCheck(ok=False, reason=reason)


def source_from_turns(
    previous_summary: str,
    turns: Sequence[tuple[str, str]],
    quotes: Sequence[str] = (),
) -> SummarySource:
    return SummarySource(
        previous_summary=previous_summary or "",
        turns=tuple((str(q or ""), str(a or "")) for q, a in turns),
        quotes=tuple(str(quote or "") for quote in quotes),
    )


__all__ = [
    "DEFAULT_MAX_CHARS",
    "QUOTE_OVERLAP_CHARS",
    "SummaryCheck",
    "SummaryDropReason",
    "SummarySource",
    "check_summary",
    "extractive_summary",
    "source_from_turns",
]
