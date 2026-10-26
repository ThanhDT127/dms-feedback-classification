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
MAX_SENTENCE_CHARS = 400

# Ngoại lệ số thứ tự, số đếm nhỏ (1..10)
_ORDINAL_RE = re.compile(r"\btop\s+([1-9]|10)\b", re.IGNORECASE)
_SMALL_COUNT_RE = re.compile(
    r"\b([1-9]|10)\s+(?:nhóm|vấn\s+đề|sản\s+phẩm|đơn\s+vị|loại|nguyên\s+nhân)\b",
    re.IGNORECASE,
)

# Mốc thời gian
_TIMEFRAME_MONTH_RE = re.compile(r"\btháng\s+([1-9]|1[0-2])\b", re.IGNORECASE)
_TIMEFRAME_QUARTER_RE = re.compile(r"\bquý\s+([1-4])\b", re.IGNORECASE)
_TIMEFRAME_YEAR_RE = re.compile(r"\bnăm\s+(20\d{2})\b", re.IGNORECASE)

# Tỷ lệ phần trăm
_PERCENT_RE = re.compile(r"(\d[\d.,]*)\s*%")

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
        self.allowed_codes = self._build_allowed_codes(sheet, question)
        self.allowed_tokens = self._build_allowed_tokens(sheet, question, self.allowed_codes)
        self.allowed_percentages = self._build_allowed_percentages(sheet)
        # ``None`` = câu trả lời thường: không có luật phần nào.
        self.sections: frozenset[str] | None = None if sections is None else frozenset(sections)

    @staticmethod
    def _build_allowed_codes(sheet: FactSheet, question: str) -> frozenset[str]:
        """Tập các mã vấn đề hợp lệ trích từ quotes trong FactSheet và từ câu hỏi."""
        codes: set[str] = set()
        for fact in sheet:
            if fact.key.startswith("q.") or ".q." in fact.key:
                if fact.raw and not isinstance(fact.raw, int | float):
                    raw_str = str(fact.raw).strip()
                    if raw_str and not raw_str.isdigit():
                        codes.add(raw_str)
                if fact.display:
                    for m in _ISSUE_CODE.finditer(fact.display):
                        codes.add(m.group(0))
        for m in _ISSUE_CODE.finditer(question):
            codes.add(m.group(0))
        return frozenset(codes)

    @classmethod
    def _build_allowed_tokens(
        cls,
        sheet: FactSheet,
        question: str,
        _allowed_codes: frozenset[str] | None = None,
    ) -> frozenset[str]:
        """Số trong câu hỏi gốc, tên thực thể ("Vùng 1") và fact ngày tháng."""
        tokens: set[str] = set(number_tokens(question))
        for name in sheet.entity_names():
            tokens |= number_tokens(name)
        # Bổ sung token ngày tháng từ fact ngày tháng (range.current, range.compare, *.date)
        for fact in sheet:
            if (
                fact.key in ("range.current", "range.compare")
                or fact.key.endswith(".date")
                or ".date." in fact.key
            ):
                date_toks = set(number_tokens(fact.display))
                if fact.raw:
                    date_toks |= set(number_tokens(str(fact.raw)))
                for tok in date_toks:
                    tokens.add(tok)
                    if tok.isdigit():
                        tokens.add(str(int(tok)))
                        tokens.add(f"{int(tok):02d}")
        return frozenset(tokens)

    @staticmethod
    def _build_allowed_percentages(sheet: FactSheet) -> frozenset[str]:
        """Tập các giá trị phần trăm hợp lệ từ FactSheet (*.pct, delta.*, fact chứa %)."""
        pcts: set[str] = set()
        for fact in sheet:
            is_pct = (
                fact.key.endswith(".pct")
                or ".pct." in fact.key
                or fact.key.startswith("delta.")
                or ".delta." in fact.key
                or "%" in fact.display
            )
            if not is_pct:
                continue
            for tok in number_tokens(fact.display):
                pcts.add(tok)
                pcts.add(tok.replace(",", "."))
                pcts.add(tok.replace(".", ","))
            if isinstance(fact.raw, int | float):
                val_abs = abs(fact.raw)
                if val_abs == int(val_abs):
                    pcts.add(str(int(val_abs)))
                pcts.add(f"{val_abs:g}".replace(".", ","))
                pcts.add(f"{val_abs:g}")
                pcts.add(f"{val_abs:.1f}".replace(".", ","))
                pcts.add(f"{val_abs:.1f}")
        return frozenset(pcts)

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

        # 1. Spans của issue codes:
        code_matches = list(_ISSUE_CODE.finditer(without_placeholders))
        code_spans: list[tuple[int, int]] = [match.span() for match in code_matches]
        for code in self.allowed_codes:
            if code in without_placeholders:
                for m in re.finditer(rf"\b{re.escape(code)}\b", without_placeholders):
                    code_spans.append(m.span())

        # 2. Spans của ordinals và small counts:
        allowed_context_spans: list[tuple[int, int]] = []
        for m in _ORDINAL_RE.finditer(without_placeholders):
            allowed_context_spans.append(m.span(1))
        for m in _SMALL_COUNT_RE.finditer(without_placeholders):
            allowed_context_spans.append(m.span(1))

        # 3. Spans của timeframes:
        for m in _TIMEFRAME_MONTH_RE.finditer(without_placeholders):
            allowed_context_spans.append(m.span(1))
        for m in _TIMEFRAME_QUARTER_RE.finditer(without_placeholders):
            allowed_context_spans.append(m.span(1))
        for m in _TIMEFRAME_YEAR_RE.finditer(without_placeholders):
            allowed_context_spans.append(m.span(1))

        # 4. Spans của percentages hợp lệ:
        for m in _PERCENT_RE.finditer(without_placeholders):
            num_part = m.group(1).rstrip(".,")
            if (
                num_part in self.allowed_percentages
                or num_part.replace(",", ".") in self.allowed_percentages
                or num_part.replace(".", ",") in self.allowed_percentages
            ):
                allowed_context_spans.append(m.span(1))

        exempt_spans = code_spans + allowed_context_spans

        stray_numbers = [
            run.group(0).rstrip(".,")
            for run in _NUMBER_RUN.finditer(without_placeholders)
            if not _overlaps(run.span(), exempt_spans)
            and run.group(0).rstrip(".,") not in self.allowed_tokens
        ]
        if stray_numbers:
            return self._drop(DropReason.UNGROUNDED_NUMBER)

        # Kiểm tra mã vấn đề không có căn cứ (ungrounded):
        ungrounded_codes = [
            match.group(0)
            for match in code_matches
            if match.group(0) not in self.allowed_codes
        ]
        if ungrounded_codes:
            return self._drop(DropReason.UNGROUNDED_CODE)

        # Chỉ soi phần văn xuôi do LLM tự viết. Khoá placeholder là của hệ thống và đã được
        # đối chiếu với fact sheet ở trên; namespace của Pattern 2 đúng bằng chữ "sql" nên nếu
        # soi cả khoá thì mọi câu trích số của Pattern 2 đều bị coi là rò rỉ schema (b09).
        lowered = without_placeholders.casefold()
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
