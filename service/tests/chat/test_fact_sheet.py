from __future__ import annotations

from dms.chat.ai.block_builders import build_block
from dms.chat.ai.fact_sheet import build_fact_sheet, make_fact, number_tokens

from .test_block_builders import OVERVIEW_ROW, products_row

TREND_ROW = {
    "items": [
        {"date": "2026-08-01", "issue_count": 5},
        {"date": "2026-08-02", "issue_count": 42},
        {"date": "2026-08-03", "issue_count": 1},
    ],
    "total_issues": 48,
}
QUOTE_ROW = {
    "items": [
        {
            "issue_code": "FB-2026-10001",
            "content": "Đèn nhấp nháy sau một tuần sử dụng",
            "unit_name": "Nha Trang",
            "issue_date": "2026-08-05",
        }
    ],
    "total": 3,
}


def kpi_block():
    return build_block(function_name="get_overview", row=OVERVIEW_ROW)


def test_kpi_facts_include_previous_period_and_delta():
    sheet = build_fact_sheet([kpi_block()], date_from="2026-08-01", date_to="2026-08-31")

    assert sheet.get("kpi.total_issues").display == "1.230"
    assert sheet.get("kpi.total_issues.prev").display == "1.000"
    assert sheet.get("delta.total_issues").display == "tăng 23%"
    assert sheet.get("range.current").display == "01/08/2026 – 31/08/2026"


def test_unavailable_kpi_produces_no_fact():
    sheet = build_fact_sheet([kpi_block()])

    assert "kpi.model_accuracy" not in sheet
    assert sheet.get("kpi.label_coverage").display == "87,5%"


def test_ranking_facts_are_capped_at_five():
    sheet = build_fact_sheet([build_block(function_name="get_products", row=products_row(25))])

    assert sheet.get("rank.1.label").display == "Sản phẩm 0"
    assert sheet.get("rank.5.value") is not None
    assert sheet.get("rank.6.label") is None


def test_trend_facts_are_max_min_only_never_a_sum():
    sheet = build_fact_sheet([build_block(function_name="get_daily_trend", row=TREND_ROW)])

    assert sheet.get("trend.max.value").display == "42"
    assert sheet.get("trend.max.date").display == "02/08/2026"
    assert sheet.get("trend.min.value").display == "1"
    # Chuỗi theo ngày không được cộng dồn: không có fact tổng nào.
    assert all("sum" not in key and "total" not in key for key in sheet.keys())


def test_quote_facts_carry_issue_code():
    sheet = build_fact_sheet([build_block(function_name="get_issues", row=QUOTE_ROW)])

    quote = sheet.get("q.1")
    assert quote is not None
    assert "FB-2026-10001" in quote.display
    assert "Đèn nhấp nháy" in quote.display


def test_second_block_facts_get_a_step_prefix():
    sheet = build_fact_sheet(
        [kpi_block(), build_block(function_name="get_products", row=products_row(3))]
    )

    assert sheet.get("kpi.total_issues") is not None  # khối đầu không có tiền tố
    assert sheet.get("s2.rank.1.label") is not None
    assert sheet.get("rank.1.label") is None


def test_number_tokens_extracts_digit_runs():
    assert number_tokens("1.234 vấn đề") == frozenset({"1.234"})
    assert number_tokens("tăng 23%") == frozenset({"23"})
    assert number_tokens("12,5% của 1.000") == frozenset({"12,5", "1.000"})
    assert number_tokens("không có số") == frozenset()


def test_fact_sheet_exposes_tokens_and_prompt_lines():
    sheet = build_fact_sheet([kpi_block()], date_from="2026-08-01", date_to="2026-08-31")

    assert "1.230" in sheet.all_tokens()
    lines = sheet.as_prompt_lines()
    assert any(line.startswith("kpi.total_issues → ") for line in lines)
    assert len(sheet) == len(lines)


def test_entity_names_come_from_label_facts():
    sheet = build_fact_sheet([build_block(function_name="get_products", row=products_row(3))])

    assert "Sản phẩm 0" in sheet.entity_names()


def test_make_fact_computes_tokens():
    fact = make_fact("kpi.x", "1.234 vấn đề", 1234)
    assert fact.tokens == frozenset({"1.234"})
    assert fact.to_dict() == {"key": "kpi.x", "display": "1.234 vấn đề", "raw": 1234}
