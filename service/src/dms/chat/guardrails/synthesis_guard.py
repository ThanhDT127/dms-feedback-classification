"""Sentence gate cho phần nhận định (spec ``chat-grounded-commentary``, design b05 D7).

LLM chỉ được viết số bằng placeholder ``{{key}}``. Mỗi câu ra khỏi stream đều bị kiểm trước
khi phát: placeholder phải có thật, không có chữ số tự viết, không có mã vấn đề bịa, không lộ
tên hàm/bảng/SQL. Câu vi phạm bị bỏ, không sửa.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from enum import StrEnum

from ..ai.fact_sheet import FactSheet, number_tokens

logger = logging.getLogger("dms-chat-synthesis")

PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z0-9_.]+)\s*\}\}")
_NUMBER_RUN = re.compile(r"\d[\d.,]*")
# Mã vấn đề kiểu FB-2026-10001, A-12; cần chữ in hoa rồi tới chữ số.
_ISSUE_CODE = re.compile(r"\b[A-Z]{1,6}[-_]\d[\w-]*\b")

MIN_SENTENCE_CHARS = 3
MAX_SENTENCE_CHARS = 300

# So khớp trên bản chữ thường của câu.
LEAK_MARKERS: tuple[str, ...] = (
    "get_",
    "select ",
    "from ",
    "where ",
    "group by",
    "v_issues",
    "feedback_records",
    "feedback_fts",
    "raw_data_json",
    "sql",
)

_TERMINATORS = ".!?…"
# Viết tắt hay đứng trước dấu chấm mà không kết thúc câu.
_ABBREVIATIONS = frozenset({"tp", "q", "p", "vd", "tr", "đ/c", "kg", "stt"})


class DropReason(StrEnum):
    UNKNOWN_FACT = "UNKNOWN_FACT"
    UNGROUNDED_NUMBER = "UNGROUNDED_NUMBER"
    UNGROUNDED_CODE = "UNGROUNDED_CODE"
    LEAK = "LEAK"
    BAD_LENGTH = "BAD_LENGTH"
    # Báo cáo (b10 D6): tiền tố phần sai, hoặc dùng fact của phần khác.
    UNKNOWN_SECTION = "UNKNOWN_SECTION"
    CROSS_SECTION_FACT = "CROSS_SECTION_FACT"


# Nhận định của báo cáo: mỗi dòng mở đầu bằng "[<section_id>]" (b10 D6).
SECTION_PREFIX = re.compile(r"^\s*\[\s*([a-z][a-z0-9_]*)\s*\]\s*")


@dataclass(frozen=True)
class SentenceCheck:
    ok: bool
    text: str = ""  # câu đã thay placeholder, chỉ có nghĩa khi ok
    reason: DropReason | None = None
    section: str = ""  # phần của câu, chỉ có nghĩa khi guard chạy ở chế độ báo cáo


class SentenceSplitter:
    """Tách câu trên dòng stream: gọi ``feed`` cho từng chunk, ``flush`` khi hết."""

    def __init__(self) -> None:
        self._buffer = ""

    def feed(self, chunk: str) -> list[str]:
        self._buffer += chunk or ""
        sentences: list[str] = []
        while True:
            index = self._boundary_index(self._buffer)
            if index is None:
                break
            sentence = self._buffer[: index + 1].strip()
            self._buffer = self._buffer[index + 1 :].lstrip()
            if sentence:
                sentences.append(sentence)
        return sentences

    def flush(self) -> list[str]:
        remaining = self._buffer.strip()
        self._buffer = ""
        return [remaining] if remaining else []

    # ── Nội bộ ──

    def _boundary_index(self, text: str) -> int | None:
        for index, char in enumerate(text):
            if char == "\n":
                return index
            if char not in _TERMINATORS:
                continue
            # Phải nhìn thấy ký tự kế tiếp mới chắc đây là hết câu.
            if index + 1 >= len(text):
                return None
            if not text[index + 1].isspace():
                continue
            if _inside_placeholder(text, index):
                continue
            if char == "." and _is_decimal_point(text, index):
                continue
            if char == "." and _is_abbreviation(text, index):
                continue
            return index
        return None


def _overlaps(span: tuple[int, int], spans: list[tuple[int, int]]) -> bool:
    return any(span[0] < end and start < span[1] for start, end in spans)


def _inside_placeholder(text: str, index: int) -> bool:
    head = text[:index]
    return head.count("{{") > head.count("}}")


def _is_decimal_point(text: str, index: int) -> bool:
    before = text[index - 1] if index > 0 else ""
    after = text[index + 1] if index + 1 < len(text) else ""
    return before.isdigit() and after.isdigit()


def _is_abbreviation(text: str, index: int) -> bool:
    word = re.split(r"[\s(]", text[:index])[-1]
    return word.casefold() in _ABBREVIATIONS


class SynthesisGuard:
    """Kiểm từng câu theo thứ tự của design D7; không bao giờ sửa câu của LLM."""

    def __init__(
        self,
        sheet: FactSheet,
        *,
        question: str = "",
        min_chars: int = MIN_SENTENCE_CHARS,
        max_chars: int = MAX_SENTENCE_CHARS,
        sections: Iterable[str] | None = None,
    ) -> None:
        self.sheet = sheet
        self.min_chars = min_chars
        self.max_chars = max_chars
        self.allowed_tokens = self._build_allowed_tokens(sheet, question)
        # ``None`` = câu trả lời thường: không có luật phần nào.
        self.sections: frozenset[str] | None = (
            None if sections is None else frozenset(sections)
        )

    @staticmethod
    def _build_allowed_tokens(sheet: FactSheet, question: str) -> frozenset[str]:
        """Số trong câu hỏi gốc và số nằm trong tên thực thể ("Vùng 1") được phép."""
        tokens: set[str] = set(number_tokens(question))
        for name in sheet.entity_names():
            tokens |= number_tokens(name)
        return frozenset(tokens)

    def check(self, sentence: str) -> SentenceCheck:
        text = (sentence or "").strip()

        section = ""
        if self.sections is not None:
            found = SECTION_PREFIX.match(text)
            if found is None or found.group(1) not in self.sections:
                return self._drop(DropReason.UNKNOWN_SECTION)
            section = found.group(1)
            text = text[found.end() :].strip()

        keys = PLACEHOLDER.findall(text)
        unknown = [key for key in keys if key not in self.sheet]
        if unknown:
            return self._drop(DropReason.UNKNOWN_FACT)
        if self.sections is not None and any(
            key.split(".", 1)[0] in self.sections and key.split(".", 1)[0] != section
            for key in keys
        ):
            return self._drop(DropReason.CROSS_SECTION_FACT)

        without_placeholders = PLACEHOLDER.sub(" ", text)

        # Chữ số nằm trong một mã vấn đề được để dành cho bước UNGROUNDED_CODE ngay dưới,
        # nếu không "FB-2026-99999" sẽ bị báo nhầm là số tự viết.
        code_spans = [match.span() for match in _ISSUE_CODE.finditer(without_placeholders)]
        stray_numbers = [
            run.group(0).rstrip(".,")
            for run in _NUMBER_RUN.finditer(without_placeholders)
            if not _overlaps(run.span(), code_spans)
            and run.group(0).rstrip(".,") not in self.allowed_tokens
        ]
        if stray_numbers:
            return self._drop(DropReason.UNGROUNDED_NUMBER)

        if code_spans:
            return self._drop(DropReason.UNGROUNDED_CODE)

        lowered = text.casefold()
        if any(marker in lowered for marker in LEAK_MARKERS):
            return self._drop(DropReason.LEAK)

        rendered = self.render(text)
        if not self.min_chars <= len(rendered) <= self.max_chars:
            return self._drop(DropReason.BAD_LENGTH)

        return SentenceCheck(ok=True, text=rendered, section=section)

    def render(self, sentence: str) -> str:
        """Thay ``{{key}}`` bằng chuỗi hiển thị của fact."""

        def replace(match: re.Match[str]) -> str:
            fact = self.sheet.get(match.group(1))
            return fact.display if fact is not None else match.group(0)

        return PLACEHOLDER.sub(replace, sentence).strip()

    @staticmethod
    def _drop(reason: DropReason) -> SentenceCheck:
        # Chỉ log lý do: nội dung câu có thể chứa dữ liệu của khách hàng.
        logger.info("synthesis_sentence_dropped", extra={"reason": reason.value})
        return SentenceCheck(ok=False, reason=reason)


def iter_sentences(chunks: Iterable[str]) -> Iterator[str]:
    """Tiện ích cho test: gom danh sách chunk thành các câu."""
    splitter = SentenceSplitter()
    for chunk in chunks:
        yield from splitter.feed(chunk)
    yield from splitter.flush()
