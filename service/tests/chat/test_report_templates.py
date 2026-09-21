"""Template báo cáo (spec ``chat-report-composition``, b10 task 2.1, 2.2, 2.4)."""

from __future__ import annotations

from datetime import date

import pytest

from dms.chat.ai.intents import Intent
from dms.chat.ai.reports.templates import (
    PRIORITY_TOP_N,
    TREND_MAX_DAYS,
    ReportContext,
    ReportRange,
    build_report_plan,
    resolve_range,
    sections_from_hints,
)
from dms.chat.ai.types import DateRange, Reason

TODAY = date(2026, 9, 15)  # thứ Ba


def _range(intent: Intent, **kwargs) -> ReportRange:
    report_range, _ = resolve_range(intent, today=TODAY, **kwargs)
    assert report_range is not None
    return report_range


def _sections(intent: Intent, report_range: ReportRange, context: ReportContext) -> list[str]:
    plan = build_report_plan(intent, report_range, context)
    return [spec.section_id for spec in plan.sections]


# ── Khoảng ngày mặc định ──


def test_weekly_default_range_is_last_full_week():
    report_range, assumptions = resolve_range(Intent.REPORT_WEEKLY, today=TODAY)
    assert report_range is not None
    assert report_range.current.date_from == date(2026, 9, 7)
    assert report_range.current.date_to == date(2026, 9, 13)
    assert report_range.compare is not None
    assert report_range.compare.date_from == date(2026, 8, 31)
    assert report_range.compare.date_to == date(2026, 9, 6)
    assert any("tuần trước" in text for text in assumptions)


def test_daily_default_range_is_yesterday():
    report_range, assumptions = resolve_range(Intent.REPORT_DAILY, today=TODAY)
    assert report_range is not None
    assert report_range.current.date_from == date(2026, 9, 14)
    assert report_range.current.date_to == date(2026, 9, 14)
    assert report_range.compare is not None
    assert report_range.compare.date_from == date(2026, 9, 13)
    assert any("hôm qua" in text for text in assumptions)


def test_custom_report_without_range_has_no_default():
    report_range, assumptions = resolve_range(Intent.REPORT_CUSTOM, today=TODAY)
    assert report_range is None
    assert assumptions == ()


def test_explicit_range_is_capped_at_today_with_assumption():
    given = DateRange(date_from=date(2026, 9, 1), date_to=date(2026, 9, 30))
    report_range, assumptions = resolve_range(
        Intent.REPORT_CUSTOM, today=TODAY, date_range=given
    )
    assert report_range is not None
    assert report_range.current.date_to == TODAY
    assert any("15/09/2026" in text for text in assumptions)


def test_compare_period_has_same_length_and_is_adjacent():
    given = DateRange(date_from=date(2026, 8, 1), date_to=date(2026, 8, 10))
    report_range = _range(Intent.REPORT_CUSTOM, date_range=given)
    assert report_range.days == 10
    assert report_range.compare is not None
    assert report_range.compare.date_to == date(2026, 7, 31)
    assert report_range.compare.date_from == date(2026, 7, 22)


# ── Các phần của template ──


def test_weekly_sections_for_multi_unit_user():
    report_range = _range(Intent.REPORT_WEEKLY)
    context = ReportContext(scope_units=("TV1", "TV2"))
    assert _sections(Intent.REPORT_WEEKLY, report_range, context) == [
        "overview",
        "trend",
        "products",
        "geography",
        "units",
        "priority",
    ]


def test_weekly_sections_for_single_unit_user_have_no_units_section():
    report_range = _range(Intent.REPORT_WEEKLY)
    context = ReportContext(scope_units=("TV1",))
    assert "units" not in _sections(Intent.REPORT_WEEKLY, report_range, context)


def test_admin_without_unit_filter_sees_units_section():
    report_range = _range(Intent.REPORT_WEEKLY)
    context = ReportContext(scope_units=(), is_admin=True)
    assert "units" in _sections(Intent.REPORT_WEEKLY, report_range, context)


def test_daily_sections():
    report_range = _range(Intent.REPORT_DAILY)
    context = ReportContext(scope_units=("TV1",))
    assert _sections(Intent.REPORT_DAILY, report_range, context) == [
        "overview",
        "priority",
        "issue_types",
        "backlog",
    ]


def test_requested_sections_filter_but_always_keep_overview():
    given = DateRange(date_from=date(2026, 8, 1), date_to=date(2026, 8, 31))
    report_range = _range(Intent.REPORT_CUSTOM, date_range=given)
    context = ReportContext(scope_units=("TV1", "TV2"), requested_sections=("products",))
    assert _sections(Intent.REPORT_CUSTOM, report_range, context) == ["overview", "products"]


def test_unknown_requested_section_is_skipped_with_assumption():
    given = DateRange(date_from=date(2026, 8, 1), date_to=date(2026, 8, 31))
    report_range = _range(Intent.REPORT_CUSTOM, date_range=given)
    context = ReportContext(
        scope_units=("TV1",), requested_sections=("products", "issue_types")
    )
    plan = build_report_plan(Intent.REPORT_CUSTOM, report_range, context)
    assert [spec.section_id for spec in plan.sections] == ["overview", "products"]
    assert any("Loại vấn đề" in text for text in plan.assumptions)


@pytest.mark.parametrize(
    ("date_from", "date_to", "has_trend"),
    [
        (date(2026, 9, 14), date(2026, 9, 15), False),  # 2 ngày
        (date(2026, 9, 9), date(2026, 9, 15), True),  # 7 ngày
        (date(2026, 1, 1), date(2026, 9, 15), False),  # > 92 ngày
    ],
)
def test_trend_section_depends_on_range_length(date_from, date_to, has_trend):
    given = DateRange(date_from=date_from, date_to=date_to)
    report_range = _range(Intent.REPORT_CUSTOM, date_range=given)
    context = ReportContext(scope_units=("TV1",))
    assert ("trend" in _sections(Intent.REPORT_CUSTOM, report_range, context)) is has_trend


def test_long_range_reports_trend_notice():
    given = DateRange(date_from=date(2026, 1, 1), date_to=date(2026, 9, 15))
    report_range = _range(Intent.REPORT_CUSTOM, date_range=given)
    plan = build_report_plan(
        Intent.REPORT_CUSTOM, report_range, ReportContext(scope_units=("TV1",))
    )
    assert report_range.days > TREND_MAX_DAYS
    assert [kind for kind, _ in plan.notices] == [Reason.TREND_RANGE_TOO_LONG]


# ── Bước dựng từ template ──


def test_steps_carry_range_and_compare_params():
    report_range = _range(Intent.REPORT_WEEKLY)
    plan = build_report_plan(
        Intent.REPORT_WEEKLY, report_range, ReportContext(scope_units=("TV1",))
    )
    overview = plan.steps[0]
    assert overview.function_name == "get_overview"
    assert overview.params["date_from"] == "2026-09-07"
    assert overview.params["date_to"] == "2026-09-13"
    assert overview.params["compare_from"] == "2026-08-31"
    assert overview.params["compare_to"] == "2026-09-06"
    # Chỉ bước tổng quan có vế so sánh.
    assert all("compare_from" not in step.params for step in plan.steps[1:])


def test_priority_step_asks_for_top_five():
    report_range = _range(Intent.REPORT_DAILY)
    plan = build_report_plan(
        Intent.REPORT_DAILY, report_range, ReportContext(scope_units=("TV1",))
    )
    priority = next(s for s in plan.steps if s.function_name == "get_priority_issues")
    assert priority.params["limit"] == PRIORITY_TOP_N


def test_template_steps_pass_query_plan_validation():
    report_range = _range(Intent.REPORT_WEEKLY)
    plan = build_report_plan(
        Intent.REPORT_WEEKLY,
        report_range,
        ReportContext(scope_units=("TV1", "TV2")),
        original_query="Báo cáo tuần trước",
    )
    for step in plan.steps:
        assert step.validate() == []


def test_sections_from_hints_maps_dimensions():
    assert sections_from_hints(["product", "province"]) == ("products", "geography")
    assert sections_from_hints(["khong-biet"]) == ()
