"""Fact sheet: tập dữ kiện có tên cho phần nhận định (design b05 D5).

LLM chỉ được nhắc số bằng placeholder ``{{key}}`` trỏ vào đây, nên mọi con số lọt ra ngoài
đều đã do Python tính và định dạng. Fact chỉ sinh từ bước có trạng thái ``ok``.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field

from .block_builders import DataBlock
from .vi_format import (
    delta_from_change_percent,
    format_date,
    format_date_range,
    format_int,
    format_percent,
    truncate_text,
)

MAX_RANK_FACTS = 5
MAX_QUOTE_FACTS = 3
QUOTE_FACT_MAX_CHARS = 160

# Mọi cụm chữ số trong chuỗi hiển thị, kể cả "27.554" và "12,5".
_NUMBER_RUN = re.compile(r"\d[\d.,]*")


@dataclass(frozen=True)
class Fact:
    key: str
    display: str
    raw: int | float | str | None = None
    tokens: frozenset[str] = field(default_factory=frozenset)
    # Nhãn người dùng đang nhìn thấy ở khối dữ liệu ("Đã xử lý"). Đưa vào prompt để LLM biết
    # dữ kiện là số đếm hay tỉ lệ — "2" không kèm nhãn từng bị viết thành "tỉ lệ xử lý là 2".
    label: str = ""

    def to_dict(self) -> dict[str, object]:
        return {"key": self.key, "display": self.display, "raw": self.raw}


def number_tokens(text: str) -> frozenset[str]:
    """Các cụm số xuất hiện trong chuỗi hiển thị; sentence gate dùng để đối chiếu."""
    return frozenset(match.group(0).rstrip(".,") for match in _NUMBER_RUN.finditer(text or ""))


def make_fact(
    key: str, display: str, raw: int | float | str | None = None, label: str = ""
) -> Fact:
    return Fact(key=key, display=display, raw=raw, tokens=number_tokens(display), label=label)


@dataclass(frozen=True)
class FactSheet:
    facts: Mapping[str, Fact] = field(default_factory=dict)

    def __contains__(self, key: object) -> bool:
        return str(key) in self.facts

    def __len__(self) -> int:
        return len(self.facts)

    def __iter__(self) -> Iterator[Fact]:
        return iter(self.facts.values())

    def get(self, key: str) -> Fact | None:
        return self.facts.get(key)

    def keys(self) -> list[str]:
        return list(self.facts)

    def all_tokens(self) -> frozenset[str]:
        tokens: set[str] = set()
        for fact in self.facts.values():
            tokens |= fact.tokens
        return frozenset(tokens)

    def entity_names(self) -> tuple[str, ...]:
        """Tên thực thể (đơn vị, sản phẩm, nhãn) — dùng để nới tập token cho phép."""
        return tuple(
            str(fact.raw)
            for fact in self.facts.values()
            if isinstance(fact.raw, str) and fact.key.endswith(".label")
        )

    def as_prompt_lines(self) -> list[str]:
        return [f"{fact.key} → {fact.display}" for fact in self.facts.values()]

    def to_dict(self) -> dict[str, dict[str, object]]:
        return {key: fact.to_dict() for key, fact in self.facts.items()}


def build_fact_sheet(
    blocks: Sequence[DataBlock],
    *,
    date_from: str | None = None,
    date_to: str | None = None,
    compare_from: str | None = None,
    compare_to: str | None = None,
) -> FactSheet:
    """Gom fact từ các khối đã dựng; khối thứ hai trở đi dùng tiền tố ``s<n>.``."""
    facts: dict[str, Fact] = {}

    current_range = format_date_range(date_from, date_to)
    if current_range:
        _add(facts, make_fact("range.current", current_range, f"{date_from}..{date_to}"))
    compare_range = format_date_range(compare_from, compare_to)
    if compare_range:
        _add(facts, make_fact("range.compare", compare_range, f"{compare_from}..{compare_to}"))

    for position, block in enumerate(blocks, start=1):
        # Báo cáo: fact mang tiền tố phần (``products.rank.1.label``) thay cho ``s<n>.`` (b10 D6).
        section_id = str((block.section or {}).get("id") or "")
        prefix = f"{section_id}." if section_id else ("" if position == 1 else f"s{position}.")
        for fact in _facts_for_block(block):
            _add(
                facts,
                Fact(prefix + fact.key, fact.display, fact.raw, fact.tokens, fact.label),
            )

    return FactSheet(facts=facts)


def _facts_for_block(block: DataBlock) -> list[Fact]:
    facts: list[Fact] = []
    if block.kind == "kpi":
        facts = _kpi_facts(block)
    elif block.kind == "ranking":
        facts = _ranking_facts(block)
    elif block.kind == "timeseries":
        facts = _trend_facts(block)
    elif block.kind == "quote":
        facts = _quote_facts(block)
    return facts + _sql_facts(block)


def _sql_facts(block: DataBlock) -> list[Fact]:
    """Kết quả Pattern 2: ``sql.r<i>.<alias>`` cho tối đa 10 dòng đầu (b09 D8)."""
    scope = (block.fact_scope or {}).get("sql")
    if not isinstance(scope, dict):
        return []
    from .block_builders import _format_sql_value

    facts: list[Fact] = []
    for index, row in enumerate(scope.get("rows") or [], start=1):
        for alias in scope.get("columns") or []:
            value = row.get(alias)
            raw = value if isinstance(value, int | float | str) else None
            facts.append(make_fact(f"sql.r{index}.{alias}", _format_sql_value(value), raw))
    return facts


def _kpi_facts(block: DataBlock) -> list[Fact]:
    facts: list[Fact] = []
    for item in block.payload.get("items", []):
        # KPI chưa đủ dữ liệu thì không sinh fact (spec chat-data-blocks).
        if not item.get("available"):
            continue
        key = str(item.get("key"))
        label = str(item.get("label") or "")
        facts.append(
            make_fact(f"kpi.{key}", str(item.get("display", "")), item.get("value"), label)
        )
        comparison = item.get("comparison")
        if isinstance(comparison, dict) and comparison.get("available"):
            facts.append(
                make_fact(
                    f"kpi.{key}.prev",
                    str(comparison.get("display", "")),
                    comparison.get("value"),
                    f"{label} kỳ trước" if label else "",
                )
            )
            facts.append(
                make_fact(
                    f"delta.{key}",
                    delta_from_change_percent(comparison.get("change_percent")),
                    comparison.get("change_percent"),
                    f"Thay đổi của {label}" if label else "",
                )
            )
    return facts


def _ranking_facts(block: DataBlock) -> list[Fact]:
    facts: list[Fact] = []
    for item in block.payload.get("items", [])[:MAX_RANK_FACTS]:
        rank = item.get("rank")
        facts.append(make_fact(f"rank.{rank}.label", str(item.get("label", "")), item.get("label")))
        facts.append(
            make_fact(f"rank.{rank}.value", str(item.get("display", "")), item.get("value"))
        )
        if item.get("percent") is not None:
            facts.append(
                make_fact(
                    f"rank.{rank}.pct",
                    str(item.get("percent_display") or format_percent(item.get("percent"))),
                    item.get("percent"),
                )
            )
    total = block.payload.get("total_issues")
    if total is not None:
        facts.append(make_fact("total.issues", format_int(total), total))
    return facts


def _trend_facts(block: DataBlock) -> list[Fact]:
    """Fact dẫn xuất của chuỗi thời gian: chỉ max/min, **không** tổng các điểm."""
    points = [point for point in block.payload.get("points", []) if point.get("value") is not None]
    if not points:
        return []
    highest = max(points, key=lambda point: point["value"])
    lowest = min(points, key=lambda point: point["value"])
    return [
        make_fact("trend.max.date", format_date(highest.get("date")), highest.get("date")),
        make_fact("trend.max.value", format_int(highest.get("value")), highest.get("value")),
        make_fact("trend.min.date", format_date(lowest.get("date")), lowest.get("date")),
        make_fact("trend.min.value", format_int(lowest.get("value")), lowest.get("value")),
        make_fact("trend.points", format_int(len(points)), len(points)),
    ]


def _quote_facts(block: DataBlock) -> list[Fact]:
    facts: list[Fact] = []
    for index, quote in enumerate(block.payload.get("quotes", [])[:MAX_QUOTE_FACTS], start=1):
        content = truncate_text(str(quote.get("content") or ""), QUOTE_FACT_MAX_CHARS)
        code = str(quote.get("issue_code") or "").strip()
        display = f"“{content}” ({code})" if code else f"“{content}”"
        facts.append(make_fact(f"q.{index}", display, code or None))
    return facts


def _add(facts: dict[str, Fact], fact: Fact) -> None:
    if fact.display and fact.key not in facts:
        facts[fact.key] = fact
