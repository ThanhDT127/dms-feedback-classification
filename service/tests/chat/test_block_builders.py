from __future__ import annotations

import logging

import pytest
from analytics_support import seed_classified_records

from dms.analytics import AnalyticsFilter, FeedbackAnalyticsRepository, FeedbackAnalyticsService
from dms.chat.ai.block_builders import BlockConfig, build_block, build_blocks, subtitle_for_range
from dms.chat.ai.function_catalog import FUNCTION_CATALOG
from dms.chat.ai.intents import functions_for_milestone
from dms.chat.ai.types import ErrorCode, StepResult, StepState, StepStatus
from dms.chat.ai.vi_format import NOT_ENOUGH_DATA
from dms.chat.contract import QueryResult

OVERVIEW_ROW = {
    "total_issues": {
        "available": True,
        "value": 1230,
        "denominator": 1230,
        "excluded_missing_issue_code": 0,
        "reason": None,
        "comparison": {
            "available": True,
            "value": 1000,
            "change_percent": 23.0,
            "direction": "up",
            "reason": None,
        },
    },
    "label_coverage": {
        "available": True,
        "value": 87.5,
        "denominator": 1230,
        "numerator": 1076,
        "excluded_missing_issue_code": 0,
        "reason": None,
    },
    "model_accuracy": {
        "available": False,
        "value": None,
        "denominator": 0,
        "excluded_missing_issue_code": 0,
        "reason": "No human-verified ground-truth labels are stored.",
    },
}


def products_row(count: int = 25) -> dict:
    return {
        "items": [
            {"label": f"Sản phẩm {index}", "issue_count": 100 - index, "percentage": 2.5}
            for index in range(count)
        ],
        "membership_count": 400,
        "total_issues": 500,
        "excluded_missing_issue_code": 3,
    }


# ── Khai báo ──


def test_every_function_of_current_milestone_has_a_block_spec():
    missing = [
        name for name in functions_for_milestone("M1") if FUNCTION_CATALOG[name].block_spec is None
    ]
    assert missing == []


def test_function_without_spec_falls_back_to_table_and_logs(caplog):
    with caplog.at_level(logging.INFO, logger="dms-chat-blocks"):
        block = build_block(function_name="get_data_quality", row={"total_records": 500})

    assert block is not None and block.kind == "table"
    assert any(record.message == "block_spec_missing" for record in caplog.records)


# ── KPI ──


def test_kpi_block_keeps_semantics_and_formats_vietnamese():
    block = build_block(function_name="get_overview", row=OVERVIEW_ROW)

    items = {item["key"]: item for item in block.payload["items"]}
    assert block.kind == "kpi"
    assert items["total_issues"]["display"] == "1.230"
    assert items["total_issues"]["denominator"] == 1230
    assert items["total_issues"]["comparison"]["display"] == "1.000"
    assert items["total_issues"]["comparison"]["change_display"] == "tăng 23%"
    assert items["label_coverage"]["display"] == "87,5%"
    assert items["model_accuracy"]["display"] == NOT_ENOUGH_DATA
    assert items["model_accuracy"]["available"] is False


# ── Ranking ──


def test_ranking_block_truncates_to_table_max_rows():
    block = build_block(
        function_name="get_products", row=products_row(25), config=BlockConfig(table_max_rows=20)
    )

    assert block.kind == "ranking"
    assert block.chart_hint == "bar"
    assert len(block.payload["items"]) == 20
    assert block.payload["total_rows"] == 25
    assert block.payload["truncated"] is True
    assert block.payload["items"][0]["rank"] == 1
    assert block.payload["total_display"] == "500"


def test_ranking_block_not_truncated_when_short():
    block = build_block(function_name="get_products", row=products_row(3))
    assert block.payload["truncated"] is False
    assert block.payload["total_rows"] == 3


# ── Timeseries ──


def test_daily_trend_is_never_aggregated():
    row = {
        "items": [{"date": f"2026-08-{day:02d}", "issue_count": day} for day in range(1, 32)],
        "total_issues": 496,
    }

    block = build_block(function_name="get_daily_trend", row=row)

    assert block.kind == "timeseries"
    assert len(block.payload["points"]) == 31
    assert block.payload["aggregate_allowed"] is False
    assert "total" not in block.payload
    assert block.payload["points"][0]["display_date"] == "01/08/2026"


# ── Quote ──


def test_quote_block_truncates_content_and_keeps_issue_code():
    row = {
        "items": [
            {
                "issue_code": "FB-2026-10001",
                "content": "x" * 1000,
                "unit_name": "Nha Trang",
                "issue_date": "2026-08-05",
                "source_file_name": "a.xlsx",
                "source_row_number": 7,
                "raw_data_json": '{"bí mật": 1}',
            }
        ],
        "total": 12,
    }

    block = build_block(
        function_name="get_issues", row=row, config=BlockConfig(quote_max_chars=240)
    )

    quote = block.payload["quotes"][0]
    assert block.kind == "quote"
    assert len(quote["content"]) <= 241
    assert quote["content"].endswith("…")
    assert quote["issue_code"] == "FB-2026-10001"
    assert "raw_data_json" not in quote


# ── build_blocks trên StepResult ──


def test_build_blocks_skips_steps_that_are_not_ok():
    ok_step = StepResult(
        index=1,
        function_name="get_products",
        status=StepStatus(StepState.OK),
        result=QueryResult.ok([products_row(3)]),
    )
    failed_step = StepResult(
        index=2,
        function_name="get_issues",
        status=StepStatus(StepState.ERROR, ErrorCode.INTERNAL),
        result=QueryResult.error("hỏng"),
    )

    blocks = build_blocks([ok_step, failed_step], subtitle="01/08/2026 – 31/08/2026")

    assert len(blocks) == 1
    assert blocks[0].kind == "ranking"
    assert blocks[0].subtitle == "01/08/2026 – 31/08/2026"
    assert blocks[0].block_id == "s1.get_products"


def test_subtitle_for_range_joins_range_and_units():
    assert subtitle_for_range("2026-08-01", "2026-08-31", ("Nha Trang",)) == (
        "01/08/2026 – 31/08/2026 · Nha Trang"
    )
    assert subtitle_for_range(None, None, ()) == ""


# ── Task 2.4: dựng khối trên kết quả THẬT của FeedbackAnalyticsService ──


@pytest.fixture
def analytics_service(settings) -> FeedbackAnalyticsService:
    repo = FeedbackAnalyticsRepository(settings.classification_jobs_db_path)
    seed_classified_records(
        repo,
        db_path=settings.classification_jobs_db_path,
        entries=[
            {
                "content": "Đèn LED nhấp nháy sau một tuần",
                "issue_code": "FB-1",
                "issue_date": "2026-08-01",
                "unit_name": "Nha Trang",
                "business_status": "Chờ xử lý",
                "labels": ["Báo lỗi"],
                "product": "Đèn LED Bulb",
                "sentiment": "Tiêu cực",
            },
            {
                "content": "Giao hàng chậm hai ngày",
                "issue_code": "FB-2",
                "issue_date": "2026-08-02",
                "unit_name": "Biên Hòa",
                "business_status": "Đã xử lý",
                "labels": ["Bảo hành"],
                "product": "Đèn LED Tube",
                "sentiment": "Trung tính",
            },
            {
                "content": "Sản phẩm dùng tốt, rất hài lòng",
                "issue_code": "FB-3",
                "issue_date": "2026-08-03",
                "unit_name": "Nha Trang",
                "business_status": "Đã xử lý",
                "labels": ["Báo CL tốt"],
                "product": "Đèn LED Bulb",
                "sentiment": "Tích cực",
            },
        ],
    )
    return FeedbackAnalyticsService(repo)


AUGUST = AnalyticsFilter(date_from="2026-08-01", date_to="2026-08-31")


def test_blocks_from_real_overview_with_comparison(analytics_service):
    row = analytics_service.overview(
        AnalyticsFilter(
            date_from="2026-08-01",
            date_to="2026-08-31",
            compare_from="2026-07-01",
            compare_to="2026-07-31",
        )
    )

    block = build_block(function_name="get_overview", row=row)

    items = {item["key"]: item for item in block.payload["items"]}
    assert block.kind == "kpi"
    assert items["total_issues"]["value"] == 3
    assert items["total_issues"]["display"] == "3"


@pytest.mark.parametrize(
    ("function_name", "method", "expected_kind"),
    [
        ("get_products", "products", "ranking"),
        ("get_units", "units", "ranking"),
        ("get_issue_types", "issue_types", "ranking"),
        ("get_daily_trend", "daily_trend", "timeseries"),
        ("get_unit_issue_type_matrix", "unit_issue_type_matrix", "table"),
    ],
)
def test_blocks_from_real_service_results(analytics_service, function_name, method, expected_kind):
    row = getattr(analytics_service, method)(AUGUST)

    block = build_block(function_name=function_name, row=row)

    assert block is not None
    assert block.kind == expected_kind
    assert block.title
    # Khối phải dựng được mà không ném lỗi và có payload không rỗng.
    assert block.payload


def test_quote_block_from_real_priority_issues(analytics_service):
    row = analytics_service.priority_issues(AUGUST, limit=5)

    block = build_block(function_name="get_priority_issues", row=row)

    assert block.kind == "quote"
    assert block.payload["quotes"]
    first = block.payload["quotes"][0]
    assert first["issue_code"]
    assert "raw_data_json" not in first
