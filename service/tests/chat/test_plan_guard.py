from __future__ import annotations

from datetime import UTC, datetime

import pytest

from dms.chat.ai.date_resolver import DateResolver
from dms.chat.ai.intents import Intent, PlannerState
from dms.chat.ai.query_normalizer import QueryNormalizer
from dms.chat.ai.query_planner import PlannerOutput
from dms.chat.ai.types import (
    FLAG_COMPARISON_DISALLOWED,
    Decision,
    Dimension,
    GuardDecision,
    NormalizedQuery,
    QueryIssue,
    Reason,
)
from dms.chat.contract import AnswerShape, QueryPattern, QueryPlan, UserScope
from dms.chat.guardrails.plan_guard import PlanGuard, PlanGuardConfig

from .ai_fakes import FixedClock, ScriptedLLM, StaticMetadataProvider

CLOCK = FixedClock(datetime(2026, 9, 15, 3, 0, tzinfo=UTC))
TV1 = "Truyền thống Vùng 1"
TV2 = "Truyền thống Vùng 2"
TV3 = "Truyền thống Vùng 3"


@pytest.fixture
def guard() -> PlanGuard:
    return PlanGuard(StaticMetadataProvider(), config=PlanGuardConfig())


def make_query(text: str, *, issues=(), compare_range=None) -> NormalizedQuery:
    """Dùng Query Normalizer thật để entity giống hệt luồng chạy."""
    normalized = QueryNormalizer(
        StaticMetadataProvider(),
        date_resolver=DateResolver(CLOCK),
        fuzzy_threshold=0.88,
    ).normalize(text)
    return NormalizedQuery(
        request_id="req-1",
        original_query=text,
        rewritten_query=text,
        match_text=normalized.match_text,
        guard=GuardDecision.allow(),
        date_range=normalized.dates.date_range,
        compare_range=compare_range,
        entities=normalized.entities,
        dimension_hints=normalized.dimension_hints,
        issues=issues,
    )


def make_output(
    *,
    intent: Intent | None = Intent.OVERVIEW,
    function_name: str = "get_overview",
    params: dict | None = None,
    confidence: float = 0.9,
    system_state: PlannerState | None = None,
    steps: tuple[QueryPlan, ...] | None = None,
) -> PlannerOutput:
    if steps is None and system_state is None:
        steps = (
            QueryPlan(
                pattern=QueryPattern.SQL_TEMPLATE,
                answer_shape=AnswerShape.NUMBER,
                original_query="câu hỏi",
                confidence=confidence,
                function_name=function_name,
                params=dict(params or {}),
            ),
        )
    return PlannerOutput(
        intent=intent,
        system_state=system_state,
        steps=steps or (),
        answer_shape=AnswerShape.NUMBER,
        confidence=confidence,
    )


def user_scope(*units: str) -> UserScope:
    return UserScope(username="nv", role="user", display_name="NV", unit_ids=list(units))


ADMIN = UserScope(username="admin", role="admin", display_name="Admin", unit_ids=[])


# ── Không dùng LLM (spec: Plan Guard không gọi LLM) ──


def test_plan_guard_never_calls_the_llm(guard):
    llm = ScriptedLLM()
    cases = [
        (make_output(), make_query("Tháng 8 có bao nhiêu vấn đề?"), user_scope(TV1)),
        (make_output(confidence=0.4), make_query("gì đó"), ADMIN),
        (make_output(system_state=PlannerState.OUT_OF_DOMAIN, intent=None), make_query("x"), ADMIN),
    ]
    for output, query, scope in cases:
        guard.check(output, query, scope)
    assert llm.calls == []


# ── Bước 1–3: system_state, HELP, intent chưa hỗ trợ ──


def test_out_of_domain_is_refused(guard):
    result = guard.check(
        make_output(system_state=PlannerState.OUT_OF_DOMAIN, intent=None),
        make_query("thời tiết hôm nay thế nào"),
        ADMIN,
    )
    assert result.decision is Decision.REFUSE
    assert result.reason is Reason.OUT_OF_DOMAIN


def test_planner_clarify_state_is_clarify(guard):
    result = guard.check(
        make_output(system_state=PlannerState.CLARIFY, intent=None), make_query("ơ"), ADMIN
    )
    assert result.decision is Decision.CLARIFY


def test_help_intent_needs_no_executor(guard):
    result = guard.check(
        make_output(intent=Intent.HELP, steps=()), make_query("trợ lý làm được gì"), ADMIN
    )
    assert result.decision is Decision.HELP
    assert result.steps == ()


def test_report_weekly_not_supported_at_m1(guard):
    result = guard.check(
        make_output(intent=Intent.REPORT_WEEKLY, steps=()), make_query("báo cáo tuần"), ADMIN
    )
    assert result.decision is Decision.NOT_SUPPORTED
    assert result.reason is Reason.INTENT_NOT_SUPPORTED


# ── Bước 4–6: cấu trúc, confidence, ngày ──


def test_function_outside_intent_is_invalid_plan(guard):
    result = guard.check(
        make_output(intent=Intent.DRILL_GEOGRAPHY, function_name="get_products"),
        make_query("địa bàn nào nhiều phản hồi"),
        ADMIN,
    )
    assert result.decision is Decision.CLARIFY
    assert result.reason is Reason.INVALID_PLAN


def test_low_confidence_asks_again(guard):
    result = guard.check(make_output(confidence=0.45), make_query("cái đó"), ADMIN)
    assert result.decision is Decision.CLARIFY
    assert result.reason is Reason.LOW_CONFIDENCE


def test_invalid_date_issue_is_clarify(guard):
    result = guard.check(
        make_output(), make_query("ngày 32 tháng 8", issues=(QueryIssue.INVALID_DATE,)), ADMIN
    )
    assert result.decision is Decision.CLARIFY
    assert result.reason is Reason.INVALID_DATE


def test_reversed_date_range_is_clarify(guard):
    result = guard.check(
        make_output(params={"date_from": "2026-08-31", "date_to": "2026-08-01"}),
        make_query("từ 31/8 đến 1/8"),
        ADMIN,
    )
    assert result.decision is Decision.CLARIFY
    assert result.reason is Reason.INVALID_DATE


# ── Bước 7: chuẩn hoá giá trị ──


def test_accent_free_exact_match(guard):
    result = guard.check(
        make_output(params={"province": "ha noi"}), make_query("ha noi tháng 8"), ADMIN
    )
    assert result.decision is Decision.RUN
    assert result.steps[0].params["province"] == "Hà Nội"


def test_small_typo_is_corrected(guard):
    result = guard.check(
        make_output(params={"unit_name": "Nha Trag"}), make_query("Nha Trag tháng 8"), ADMIN
    )
    assert result.decision is Decision.RUN
    assert result.steps[0].params["unit_name"] == "Nha Trang"


def test_ambiguous_value_asks_with_options(guard):
    result = guard.check(
        make_output(params={"unit_name": "vùng"}), make_query("vùng nào nhiều nhất"), ADMIN
    )
    assert result.decision is Decision.CLARIFY
    assert result.reason is Reason.ENTITY_AMBIGUOUS
    assert set(result.clarify_options) == {TV1, TV2, TV3}
    assert len(result.clarify_options) <= 5


def test_unknown_value_says_not_found(guard):
    result = guard.check(
        make_output(params={"unit_name": "Chi nhánh Sao Hoả"}),
        make_query("Chi nhánh Sao Hoả thế nào"),
        ADMIN,
    )
    assert result.decision is Decision.CLARIFY
    assert result.reason is Reason.ENTITY_NOT_FOUND
    assert "Sao Hoả" in (result.message or "")


def test_alias_is_resolved_before_fuzzy(guard):
    result = guard.check(make_output(params={"unit_name": "tv1"}), make_query("tv1 tháng 8"), ADMIN)
    assert result.steps[0].params["unit_name"] == TV1


# ── Bước 8: phạm vi đơn vị (D4) ──


def test_user_with_two_units_and_no_mention_uses_both(guard):
    result = guard.check(
        make_output(), make_query("Tháng 8 có bao nhiêu vấn đề?"), user_scope(TV1, TV2)
    )
    assert result.decision is Decision.RUN
    assert result.scope_units == (TV1, TV2)
    assert "unit_name" not in result.steps[0].params


def test_admin_has_no_scope_limit(guard):
    result = guard.check(make_output(), make_query("Tháng 8 có bao nhiêu vấn đề?"), ADMIN)
    assert result.decision is Decision.RUN
    assert result.scope_units == ()


def test_admin_single_unit_sets_param(guard):
    result = guard.check(make_output(), make_query("Nha Trang tháng 8 có bao nhiêu vấn đề?"), ADMIN)
    assert result.decision is Decision.RUN
    assert result.steps[0].params["unit_name"] == "Nha Trang"


def test_admin_multi_unit_is_not_supported_on_contract_v1(guard):
    result = guard.check(
        make_output(),
        make_query("Tổng vấn đề của Nha Trang và Biên Hòa tháng 8"),
        ADMIN,
    )
    assert result.decision is Decision.NOT_SUPPORTED
    assert result.reason is Reason.MULTI_UNIT_FILTER_UNAVAILABLE


def test_user_narrowing_to_subset_of_allowed_units(guard):
    result = guard.check(
        make_output(),
        make_query("TV1 và TV2 tháng 8 có bao nhiêu vấn đề?"),
        user_scope(TV1, TV2, TV3),
    )
    assert result.decision is Decision.RUN
    assert result.scope_units == (TV1, TV2)
    assert "unit_name" not in result.steps[0].params


def test_asking_only_forbidden_unit_is_refused(guard):
    result = guard.check(make_output(), make_query("Nha Trang tháng 8 thế nào?"), user_scope(TV1))
    assert result.decision is Decision.REFUSE
    assert result.reason is Reason.UNAUTHORIZED_SCOPE
    assert TV1 in (result.message or "")
    assert result.steps == ()


# ── Bước 8: từ chối một phần và hạ intent (D5) ──


def test_comparison_with_forbidden_unit_downgrades_to_overview(guard):
    result = guard.check(
        make_output(intent=Intent.COMPARISON),
        make_query("So sánh TV1 với Nha Trang tháng 8"),
        user_scope(TV1),
    )
    assert result.decision is Decision.RUN
    assert result.scope_units == (TV1,)
    assert [entity.value for entity in result.dropped_entities] == ["Nha Trang"]
    assert result.intent is Intent.OVERVIEW
    assert result.intent_downgraded_from is Intent.COMPARISON
    assert FLAG_COMPARISON_DISALLOWED in result.flags
    assert any(notice.kind is Reason.PARTIAL_REFUSAL for notice in result.notices)
    assert "Nha Trang" in result.notices[0].message


def test_sum_of_two_units_drops_the_forbidden_one(guard):
    result = guard.check(
        make_output(), make_query("Tổng số vấn đề của TV1 và Nha Trang"), user_scope(TV1)
    )
    assert result.decision is Decision.RUN
    assert result.scope_units == (TV1,)
    assert [entity.value for entity in result.dropped_entities] == ["Nha Trang"]
    assert result.dropped_entities[0].dimension == Dimension.UNIT.value


# ── Bước 9: bộ lọc không được hỗ trợ ──


def test_unsupported_filter_is_not_supported_and_names_it_in_vietnamese(guard):
    result = guard.check(
        make_output(
            intent=Intent.DRILL_PRODUCT,
            function_name="get_products",
            params={"sentiment": "Tiêu cực"},
        ),
        make_query("sản phẩm nào bị phản hồi tiêu cực"),
        ADMIN,
    )
    assert result.decision is Decision.NOT_SUPPORTED
    assert result.reason is Reason.FILTER_NOT_SUPPORTED
    assert "cảm xúc" in (result.message or "")
    assert "get_products" not in (result.message or "")
    assert "sentiment" not in (result.message or "")


# ── Bước 10: không sửa plan gốc ──


def test_original_planner_output_is_never_mutated(guard):
    output = make_output(params={"unit_name": "tv1"})
    before = dict(output.steps[0].params)
    result = guard.check(output, make_query("tv1 tháng 8"), user_scope(TV1))
    assert output.steps[0].params == before
    assert result.steps[0].params["unit_name"] == TV1
    assert result.planner_output is output


# ── Bộ lọc Planner tự nghĩ ra (D3) ──


def _issues_step(params: dict) -> tuple[QueryPlan, ...]:
    return (
        QueryPlan(
            pattern=QueryPattern.SQL_TEMPLATE,
            answer_shape=AnswerShape.TABLE,
            original_query="q",
            function_name="get_issues",
            params=params,
            confidence=0.9,
        ),
    )


def test_label_planner_invented_is_sent_back_for_confirmation(guard):
    """Câu hỏi mô tả tự do, Planner tự quy nó về một nhãn có thật -> hỏi lại, không chạy."""
    output = make_output(
        intent=Intent.LOOKUP_FEEDBACK,
        steps=_issues_step({"label": "Bảng giá, Catalogue"}),
    )
    query = make_query("sản phẩm nào bị chê giá cao quá khó bán nhiều nhất")

    plan = guard.check(output, query, UserScope(username="admin", role="admin"))

    assert plan.decision is Decision.CLARIFY
    assert plan.reason is Reason.FILTER_VALUE_UNGROUNDED
    assert plan.clarify_options == ("Bảng giá, Catalogue",)
    assert "Bảng giá, Catalogue" in plan.message
    assert "nhãn" in plan.message


def test_label_written_in_the_question_still_runs(guard):
    output = make_output(intent=Intent.LOOKUP_FEEDBACK, steps=_issues_step({"label": "Báo lỗi"}))
    query = make_query("liệt kê phản hồi Báo lỗi")

    plan = guard.check(output, query, UserScope(username="admin", role="admin"))

    assert plan.decision is Decision.RUN
    assert plan.steps[0].params["label"] == "Báo lỗi"


def test_label_inherited_from_the_session_still_runs(guard):
    """Lượt trước đã chốt nhãn; lượt này không nhắc lại thì vẫn được kế thừa."""
    from dms.chat.ai.types import EntityCandidate, SessionSlots

    slots = SessionSlots(entities={"label": (EntityCandidate("label", "bao loi", "Báo lỗi", 1.0),)})
    query = make_query("còn tháng trước thì sao")
    query = type(query)(**{**vars(query), "slots": slots})
    output = make_output(intent=Intent.LOOKUP_FEEDBACK, steps=_issues_step({"label": "Báo lỗi"}))

    plan = guard.check(output, query, UserScope(username="admin", role="admin"))

    assert plan.decision is Decision.RUN


def test_unsupported_filter_message_names_the_value(guard):
    """get_products không lọc theo nhãn: thông báo phải nói rõ nhãn nào bị chặn."""
    steps = (
        QueryPlan(
            pattern=QueryPattern.SQL_TEMPLATE,
            answer_shape=AnswerShape.TABLE,
            original_query="q",
            function_name="get_products",
            params={"label": "Báo lỗi"},
            confidence=0.9,
        ),
    )
    output = make_output(intent=Intent.DRILL_PRODUCT, steps=steps)

    plan = guard.check(
        output,
        make_query("sản phẩm nào bị Báo lỗi nhiều nhất"),
        UserScope(username="admin", role="admin"),
    )

    assert plan.decision is Decision.NOT_SUPPORTED
    assert plan.reason is Reason.FILTER_NOT_SUPPORTED
    assert "Báo lỗi" in plan.message


# ── Tham số bắt buộc của hàm (D6) ──


def _comparison_step(params: dict) -> tuple[QueryPlan, ...]:
    return (
        QueryPlan(
            pattern=QueryPattern.SQL_TEMPLATE,
            answer_shape=AnswerShape.TABLE,
            original_query="q",
            function_name="get_comparison",
            params=params,
            confidence=0.9,
        ),
    )


def test_comparison_without_dates_asks_instead_of_crashing(guard):
    """``get_comparison`` thiếu ngày sẽ ném lỗi ở Executor; người dùng chỉ thấy "Có lỗi"."""
    output = make_output(intent=Intent.COMPARISON, steps=_comparison_step({"period": "month"}))

    plan = guard.check(output, make_query("so sánh với kỳ trước"), ADMIN)

    assert plan.decision is Decision.CLARIFY
    assert plan.reason is Reason.FILTER_REQUIRED
    assert "khoảng thời gian" in plan.message
    assert plan.steps == ()


def test_comparison_missing_only_one_end_names_that_parameter(guard):
    output = make_output(
        intent=Intent.COMPARISON,
        steps=_comparison_step({"period": "month", "date_from": "2026-08-01"}),
    )

    plan = guard.check(output, make_query("so sánh từ đầu tháng 8"), ADMIN)

    assert plan.decision is Decision.CLARIFY
    assert "đến ngày" in plan.message


def test_comparison_with_both_dates_runs(guard):
    output = make_output(
        intent=Intent.COMPARISON,
        steps=_comparison_step(
            {"period": "month", "date_from": "2026-08-01", "date_to": "2026-08-31"}
        ),
    )

    plan = guard.check(output, make_query("so sánh tháng 8/2026 với tháng trước"), ADMIN)

    assert plan.decision is Decision.RUN


def test_functions_without_required_params_are_untouched(guard):
    output = make_output(intent=Intent.OVERVIEW, function_name="get_overview", params={})

    plan = guard.check(output, make_query("tổng quan"), ADMIN)

    assert plan.decision is Decision.RUN


# ── Sửa theo bộ câu hỏi kiểm thử 2026-09-29 ──

M4 = PlanGuardConfig(
    milestone="M4", enabled_patterns=frozenset({"sql_template", "fts5_search", "semantic_view"})
)


def test_open_start_date_asks_for_end_date(guard):
    """Ca 19c: "từ đầu tháng 8" không được hiểu thành trọn tháng 8."""
    plan = guard.check(
        make_output(intent=Intent.COMPARISON, function_name="get_comparison",
                    params={"date_from": "2026-08-01", "date_to": "2026-08-31"}),
        make_query("So sánh từ đầu tháng 8", issues=(QueryIssue.MISSING_END_DATE,)),
        ADMIN,
    )
    assert (plan.decision, plan.reason) == (Decision.CLARIFY, Reason.FILTER_REQUIRED)
    assert "đến ngày" in plan.message


def test_numeric_code_lookup_is_not_supported(guard):
    """Ca 27: mã thật là số thuần; không lộ tên khoá issue_code."""
    plan = guard.check(
        make_output(intent=Intent.LOOKUP_FEEDBACK, function_name="get_issues",
                    params={"issue_code": "206501"}),
        make_query("Tra phản hồi mã 206501"),
        ADMIN,
    )
    assert (plan.decision, plan.reason) == (
        Decision.NOT_SUPPORTED, Reason.LOOKUP_BY_CODE_UNAVAILABLE
    )
    assert "issue_code" not in (plan.message or "")


def test_unknown_param_name_never_reaches_the_user(guard):
    """Ca 20-M1: tham số Planner tự chế được gọi bằng tên tiếng Việt."""
    plan = guard.check(
        make_output(intent=Intent.LOOKUP_FEEDBACK, function_name="get_issues",
                    params={"content_keyword": "chập chờn"}),
        make_query("Liệt kê phản hồi có chữ chập chờn"),
        ADMIN,
    )
    assert plan.reason is Reason.FILTER_NOT_SUPPORTED
    assert "content_keyword" not in plan.message and "nội dung phản hồi" in plan.message


def test_dropped_sentiment_filter_is_restored_to_semantic_view():
    """Ca 29: "tiêu cực" bị Planner bỏ mất thì chuyển Pattern 2 kèm bộ lọc."""
    guard = PlanGuard(StaticMetadataProvider(), config=M4)
    plan = guard.check(
        make_output(intent=Intent.DRILL_UNIT, function_name="get_units"),
        make_query("Đơn vị nào có nhiều phản hồi tiêu cực nhất?"),
        ADMIN,
    )
    assert plan.decision is Decision.RUN
    assert plan.steps[0].pattern is QueryPattern.SEMANTIC_VIEW
    assert plan.steps[0].params["sentiment"] == "Tiêu cực"


def test_sentiment_rate_is_not_a_where_filter():
    """Ca 30: "tỉ lệ tích cực" cần mẫu số nên không lọc, mà yêu cầu Pattern 2 tính tỉ lệ."""
    guard = PlanGuard(StaticMetadataProvider(), config=M4)
    plan = guard.check(
        make_output(intent=Intent.DRILL_UNIT, function_name="get_units"),
        make_query("Tỉ lệ phản hồi tích cực theo từng đơn vị"),
        ADMIN,
    )
    step = plan.steps[0]
    assert step.pattern is QueryPattern.SEMANTIC_VIEW
    assert "sentiment" not in step.params
    assert step.params["analysis_request"]["measures"] == ["ty_le"]


def test_ungrounded_fts_filter_is_dropped_with_notice():
    """Ca 22: bộ lọc cảm xúc Planner đoán thêm cho tra cứu bị bỏ, kèm thông báo."""
    guard = PlanGuard(StaticMetadataProvider(), config=M4)
    step = QueryPlan(
        pattern=QueryPattern.FTS5_SEARCH, answer_shape=AnswerShape.LIST, original_query="q",
        params={"sentiment": "Tiêu cực", "search_terms": ["giao hàng", "chậm"]},
        fts_query="giao hàng chậm",
    )
    plan = guard.check(
        make_output(intent=Intent.LOOKUP_FEEDBACK, steps=(step,)),
        make_query("Phản hồi nào nhắc tới giao hàng chậm?"),
        ADMIN,
    )
    assert plan.decision is Decision.RUN
    assert "sentiment" not in plan.steps[0].params
    assert any("Tiêu cực" in n.message for n in plan.notices)
