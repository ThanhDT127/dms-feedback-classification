"""Khối "Điểm chính" của báo cáo (spec ``chat-report-composition``, design b10 D5).

Chọn tối đa 3 ý theo luật Python cố định. **Không gọi LLM**: đây là phần người đọc tin nhất
nên phải xác định được và kiểm được bằng test.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from ..block_builders import DataBlock
from ..vi_format import format_int, truncate_text
from .templates import (
    SECTION_BACKLOG,
    SECTION_HIGHLIGHTS,
    SECTION_OVERVIEW,
    SECTION_PRIORITY,
    SECTION_PRODUCTS,
    SECTION_TITLES_VI,
)

MAX_HIGHLIGHTS = 3
# Ngưỡng của luật 1 và luật 2 (design D5; nghiệp vụ còn đang duyệt).
DELTA_MIN_PERCENT = 10.0
TOP_SHARE_MIN_PERCENT = 20.0
PRIORITY_LABEL_MAX_CHARS = 120
DEFAULT_COMPARE_LABEL = "kỳ trước"


@dataclass(frozen=True)
class SectionData:
    """Dữ liệu một phần đã lấy được: khối đã dựng và dòng kết quả thô."""

    section_id: str
    block: DataBlock | None = None
    row: Mapping[str, Any] = field(default_factory=dict)


def build_highlights(
    sections: Sequence[SectionData], *, compare_label: str = DEFAULT_COMPARE_LABEL
) -> tuple[str, ...]:
    """Tối đa 3 ý, theo đúng thứ tự ưu tiên của 4 luật."""
    by_id = {section.section_id: section for section in sections}
    rules = (
        lambda: _delta_rule(by_id.get(SECTION_OVERVIEW), compare_label),
        lambda: _top_share_rule(by_id.get(SECTION_PRODUCTS)),
        lambda: _backlog_rule(by_id.get(SECTION_BACKLOG)),
        lambda: _priority_rule(by_id.get(SECTION_PRIORITY)),
    )
    found: list[str] = []
    for rule in rules:
        text = rule()
        if text and text not in found:
            found.append(text)
        if len(found) >= MAX_HIGHLIGHTS:
            break
    return tuple(found)


def highlights_block(texts: Sequence[str]) -> DataBlock | None:
    """Khối ``kpi`` mở đầu báo cáo; không có ý nào thì không sinh khối."""
    if not texts:
        return None
    items = [
        {
            "key": f"highlight_{index}",
            "label": "",
            "available": True,
            "value": None,
            "display": text,
            "display_text": text,
        }
        for index, text in enumerate(texts, start=1)
    ]
    return DataBlock(
        block_id="report.highlights",
        kind="kpi",
        title=SECTION_TITLES_VI[SECTION_HIGHLIGHTS],
        payload={"items": items},
        section={
            "id": SECTION_HIGHLIGHTS,
            "title": SECTION_TITLES_VI[SECTION_HIGHLIGHTS],
            "index": 0,
        },
    )


# ── Từng luật ──


def _delta_rule(section: SectionData | None, compare_label: str) -> str:
    """Luật 1: tổng số vấn đề thay đổi từ 10% trở lên so với kỳ trước."""
    item = _kpi_item(section, "total_issues")
    comparison = (item or {}).get("comparison")
    if not isinstance(comparison, dict) or not comparison.get("available"):
        return ""
    change = _as_float(comparison.get("change_percent"))
    display = str(comparison.get("change_display") or "").strip()
    if change is None or abs(change) < DELTA_MIN_PERCENT or not display:
        return ""
    return f"Số vấn đề {display} so với {compare_label}."


def _top_share_rule(section: SectionData | None) -> str:
    """Luật 2: sản phẩm dẫn đầu chiếm từ 20% trở lên."""
    items = _ranking_items(section)
    if not items:
        return ""
    top = items[0]
    percent = _as_float(top.get("percent"))
    label = str(top.get("label") or "").strip()
    display = str(top.get("percent_display") or "").strip()
    if percent is None or percent < TOP_SHARE_MIN_PERCENT or not label or not display:
        return ""
    return f"{label} chiếm {display} số vấn đề."


def _backlog_rule(section: SectionData | None) -> str:
    """Luật 3: còn vấn đề chưa xử lý."""
    if section is None:
        return ""
    count = _as_float(section.row.get("backlog_count"))
    if count is None:
        return ""
    if count <= 0:
        return ""
    return f"Còn {format_int(count)} vấn đề chưa xử lý."


def _priority_rule(section: SectionData | None) -> str:
    """Luật 4: vấn đề ưu tiên nhất."""
    if section is None or section.block is None:
        return ""
    quotes = section.block.payload.get("quotes") or []
    if not quotes:
        return ""
    top = quotes[0]
    label = truncate_text(str(top.get("content") or "").strip(), PRIORITY_LABEL_MAX_CHARS)
    if not label:
        return ""
    code = str(top.get("issue_code") or "").strip()
    return f"Vấn đề ưu tiên nhất: {label} ({code})." if code else f"Vấn đề ưu tiên nhất: {label}."


# ── Tiện ích ──


def _kpi_item(section: SectionData | None, key: str) -> dict[str, Any] | None:
    if section is None or section.block is None:
        return None
    for item in section.block.payload.get("items") or []:
        if item.get("key") == key and item.get("available"):
            return dict(item)
    return None


def _ranking_items(section: SectionData | None) -> list[dict[str, Any]]:
    if section is None or section.block is None or section.block.kind != "ranking":
        return []
    return [dict(item) for item in section.block.payload.get("items") or []]


def _as_float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


__all__ = [
    "DELTA_MIN_PERCENT",
    "MAX_HIGHLIGHTS",
    "TOP_SHARE_MIN_PERCENT",
    "SectionData",
    "build_highlights",
    "highlights_block",
]
