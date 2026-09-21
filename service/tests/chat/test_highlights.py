"""Khối "Điểm chính" sinh bằng luật (spec ``chat-report-composition``, b10 task 2.3)."""

from __future__ import annotations

import pytest

from dms.chat.ai.block_builders import build_block
from dms.chat.ai.reports.highlights import (
    DELTA_MIN_PERCENT,
    MAX_HIGHLIGHTS,
    TOP_SHARE_MIN_PERCENT,
    SectionData,
    build_highlights,
    highlights_block,
)


def _overview_row(total: int, previous: int | None, change_percent: float | None) -> dict:
    metric: dict = {"available": True, "value": total, "denominator": None}
    if previous is not None:
        metric["comparison"] = {
            "available": True,
            "value": previous,
            "change_percent": change_percent,
            "direction": "up" if (change_percent or 0) > 0 else "down",
        }
    return {"total_issues": metric}


def _overview_section(total: int, previous: int | None, change_percent: float | None):
    row = _overview_row(total, previous, change_percent)
    block = build_block(function_name="get_overview", row=row)
    return SectionData("overview", block=block, row=row)


def _products_section(percent: float):
    row = {
        "items": [
            {"label": "Đèn LED Bulb", "issue_count": 120, "percentage": percent},
            {"label": "Đèn LED Tube", "issue_count": 10, "percentage": 5.0},
        ],
        "total_issues": 200,
    }
    block = build_block(function_name="get_products", row=row)
    return SectionData("products", block=block, row=row)


def _backlog_section(backlog_count: int):
    row = {
        "statuses": [{"label": "Chờ xử lý", "issue_count": backlog_count, "percentage": 10.0}],
        "processed_count": 90,
        "backlog_count": backlog_count,
        "total_issues": 100,
    }
    block = build_block(function_name="get_status_backlog", row=row)
    return SectionData("backlog", block=block, row=row)


def _priority_section(items: list[dict] | None = None):
    row = {
        "items": items
        if items is not None
        else [
            {"issue_code": "NT-0106", "summary": "Đèn cháy sau 2 tuần", "department": "TV1"},
        ],
        "total": 1,
    }
    block = build_block(function_name="get_priority_issues", row=row)
    return SectionData("priority", block=block, row=row)


# ── Luật 1 ──


def test_rule_delta_uses_change_display():
    sections = [_overview_section(1230, 1000, 23.0)]
    highlights = build_highlights(sections, compare_label="tuần trước")
    assert highlights[0].startswith("Số vấn đề tăng 23%")
    assert "tuần trước" in highlights[0]


@pytest.mark.parametrize("change", [3.0, -3.0, 0.0])
def test_rule_delta_skipped_below_threshold(change):
    assert build_highlights([_overview_section(1030, 1000, change)]) == ()
    assert abs(change) < DELTA_MIN_PERCENT


def test_rule_delta_needs_comparison():
    assert build_highlights([_overview_section(1230, None, None)]) == ()


# ── Luật 2 ──


def test_rule_top_product_share():
    highlights = build_highlights([_products_section(TOP_SHARE_MIN_PERCENT)])
    assert highlights == ("Đèn LED Bulb chiếm 20,0% số vấn đề.",)


def test_rule_top_product_skipped_below_threshold():
    assert build_highlights([_products_section(12.0)]) == ()


# ── Luật 3 ──


def test_rule_backlog_counts_unprocessed():
    assert build_highlights([_backlog_section(1234)]) == ("Còn 1.234 vấn đề chưa xử lý.",)


def test_rule_backlog_skipped_when_zero():
    assert build_highlights([_backlog_section(0)]) == ()


# ── Luật 4 ──


def test_rule_priority_issue():
    highlights = build_highlights([_priority_section()])
    assert highlights == ("Vấn đề ưu tiên nhất: Đèn cháy sau 2 tuần (NT-0106).",)


def test_rule_priority_skipped_when_empty():
    assert build_highlights([_priority_section(items=[])]) == ()


# ── Kết hợp ──


def test_highlights_keep_rule_order_and_max_three():
    sections = [
        _overview_section(1230, 1000, 23.0),
        _products_section(35.0),
        _backlog_section(12),
        _priority_section(),
    ]
    highlights = build_highlights(sections)
    assert len(highlights) == MAX_HIGHLIGHTS
    assert highlights[0].startswith("Số vấn đề")
    assert "chiếm" in highlights[1]
    assert highlights[2].startswith("Còn")


def test_no_rule_matches_means_no_block():
    sections = [
        _overview_section(1030, 1000, 3.0),
        _products_section(12.0),
        _backlog_section(0),
        _priority_section(items=[]),
    ]
    assert build_highlights(sections) == ()
    assert highlights_block(()) is None


def test_highlights_block_shape():
    block = highlights_block(("Số vấn đề tăng 23% so với tuần trước.",))
    assert block is not None
    assert block.kind == "kpi"
    assert block.section == {"id": "highlights", "title": "Điểm chính", "index": 0}
    assert block.to_dict()["section"]["id"] == "highlights"
    item = block.payload["items"][0]
    assert item["display_text"] == item["display"] == "Số vấn đề tăng 23% so với tuần trước."


def test_highlights_never_call_llm(monkeypatch):
    import dms.chat.ai.reports.highlights as module

    assert "llm" not in module.__dict__
    assert "LLMClient" not in dir(module)
