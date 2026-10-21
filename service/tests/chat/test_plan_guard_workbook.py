"""Quy tắc Plan Guard sửa từ bảng kiểm thử 05/10 (SRC-16, SRC-19b, SRC-20-M1, SRC-39)."""

from __future__ import annotations

import pytest

from dms.chat.ai.intents import Intent, PlannerState
from dms.chat.ai.types import Decision, Reason
from dms.chat.contract import AnswerShape, QueryPattern, QueryPlan
from dms.chat.guardrails.plan_guard import PlanGuard, PlanGuardConfig

from .ai_fakes import StaticMetadataProvider
from .test_plan_guard import ADMIN, CLOCK, TV1, TV3, make_output, make_query, user_scope

M1 = PlanGuardConfig(milestone="M1", enabled_patterns=frozenset({"sql_template"}))
M3 = PlanGuardConfig(milestone="M3", enabled_patterns=frozenset({"sql_template", "fts5_search"}))
M5 = PlanGuardConfig(
    milestone="M5", enabled_patterns=frozenset({"sql_template", "fts5_search", "semantic_view"})
)


def guard_for(config: PlanGuardConfig) -> PlanGuard:
    return PlanGuard(StaticMetadataProvider(), config=config, clock=CLOCK)


# ── SRC-39: "Báo cáo" không nêu kỳ ──


@pytest.mark.parametrize(
    "output",
    [
        make_output(intent=Intent.OVERVIEW),
        make_output(intent=Intent.TREND, function_name="get_daily_trend"),
        make_output(intent=None, system_state=PlannerState.CLARIFY),
    ],
)
def test_bare_report_request_asks_for_range(output):
    plan = guard_for(M5).check(output, make_query("Báo cáo"), ADMIN)
    assert plan.decision is Decision.CLARIFY
    assert plan.reason is Reason.REPORT_RANGE_REQUIRED
    assert plan.intent is Intent.REPORT_CUSTOM


def test_weekly_report_read_as_overview_builds_weekly_report():
    plan = guard_for(M5).check(make_output(), make_query("Cho tôi báo cáo tuần"), ADMIN)
    assert plan.decision is Decision.RUN and plan.intent is Intent.REPORT_WEEKLY
    span = plan.report_range.date_to - plan.report_range.date_from
    assert span.days == 6 and plan.report_range.date_from.weekday() == 0


def test_report_with_month_read_as_overview_builds_custom_report():
    plan = guard_for(M5).check(make_output(), make_query("Báo cáo tháng 8/2026"), ADMIN)
    assert plan.decision is Decision.RUN and plan.intent is Intent.REPORT_CUSTOM
    assert str(plan.report_range.date_from) == "2026-08-01"


@pytest.mark.parametrize(
    "question",
    ["Có bao nhiêu phản hồi được báo cáo tháng 8/2026?", "Xuất báo cáo ra file Excel"],
)
def test_report_word_without_report_request_keeps_planner_intent(question):
    plan = guard_for(M5).check(make_output(), make_query(question), ADMIN)
    assert plan.intent is Intent.OVERVIEW


def test_report_request_before_reports_are_enabled_keeps_overview():
    plan = guard_for(M3).check(make_output(), make_query("Báo cáo"), ADMIN)
    assert plan.decision is Decision.RUN and plan.intent is Intent.OVERVIEW


# ── SRC-16: admin nhắc hai đơn vị ──


def test_two_units_when_planner_gives_up_reports_multi_unit_limit():
    plan = guard_for(M5).check(
        make_output(intent=None, system_state=PlannerState.CLARIFY),
        make_query("Vùng 1 và Vùng 2 cái nào nhiều hơn?"),
        ADMIN,
    )
    assert plan.decision is Decision.NOT_SUPPORTED
    assert plan.reason is Reason.MULTI_UNIT_FILTER_UNAVAILABLE


def test_two_units_with_invalid_structure_reports_multi_unit_limit():
    plan = guard_for(M5).check(
        make_output(intent=Intent.COMPARISON, function_name="get_units"),
        make_query("Vùng 1 và Vùng 2 cái nào nhiều hơn?"),
        ADMIN,
    )
    assert plan.reason is Reason.MULTI_UNIT_FILTER_UNAVAILABLE


def test_unclear_question_with_one_unit_still_clarifies():
    plan = guard_for(M5).check(
        make_output(intent=None, system_state=PlannerState.CLARIFY),
        make_query("Vùng 1 thì sao nhỉ"),
        ADMIN,
    )
    assert plan.reason is Reason.INVALID_PLAN


# ── SRC-19b: so sánh không có kỳ ──


def test_comparison_without_any_period_asks_for_range():
    plan = guard_for(M5).check(
        make_output(intent=Intent.COMPARISON), make_query("So sánh với kỳ trước"), ADMIN
    )
    assert plan.decision is Decision.CLARIFY and plan.reason is Reason.FILTER_REQUIRED
    assert "khoảng thời gian" in plan.message


def test_comparison_with_period_runs():
    plan = guard_for(M5).check(
        make_output(
            intent=Intent.COMPARISON,
            params={"date_from": "2026-08-01", "date_to": "2026-08-31"},
        ),
        make_query("So sánh tháng 8/2026 với tháng trước"),
        ADMIN,
    )
    assert plan.decision is Decision.RUN


def test_comparison_of_two_objects_does_not_need_a_period():
    plan = guard_for(M5).check(
        make_output(intent=Intent.COMPARISON),
        make_query("So sánh Vùng 1 với Vùng 3"),
        user_scope(TV1, TV3),
    )
    assert plan.reason is not Reason.FILTER_REQUIRED


# ── SRC-20-M1: tìm theo chữ khi chưa bật FTS ──


def test_content_search_at_m1_is_not_supported():
    plan = guard_for(M1).check(
        make_output(intent=Intent.LOOKUP_FEEDBACK, function_name="get_issues"),
        make_query("Liệt kê phản hồi có chữ chập chờn"),
        ADMIN,
    )
    assert plan.decision is Decision.NOT_SUPPORTED
    assert plan.reason is Reason.FILTER_NOT_SUPPORTED
    assert "nội dung phản hồi" in plan.message and "content" not in plan.message
    assert plan.steps == ()


def test_listing_without_content_search_at_m1_runs():
    plan = guard_for(M1).check(
        make_output(intent=Intent.LOOKUP_FEEDBACK, function_name="get_issues"),
        make_query("Liệt kê phản hồi tháng 8/2026"),
        ADMIN,
    )
    assert plan.decision is Decision.RUN


def test_content_search_with_fts_enabled_runs():
    fts_step = QueryPlan(
        pattern=QueryPattern.FTS5_SEARCH,
        answer_shape=AnswerShape.LIST,
        original_query="câu hỏi",
        params={"search_terms": ["chập chờn"]},
    )
    plan = guard_for(M3).check(
        make_output(intent=Intent.LOOKUP_FEEDBACK, steps=(fts_step,)),
        make_query("Liệt kê phản hồi có chữ chập chờn"),
        ADMIN,
    )
    assert plan.decision is Decision.RUN
