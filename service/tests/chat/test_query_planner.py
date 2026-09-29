from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

import pytest

from dms.chat.ai.intents import Intent, PlannerState
from dms.chat.ai.planner_config import PlannerConfig
from dms.chat.ai.query_planner import (
    CALL_TYPE_PLAN,
    CALL_TYPE_REPAIR,
    REASON_INVALID_OUTPUT,
    QueryPlanner,
)
from dms.chat.ai.schema_retriever import SchemaRetriever
from dms.chat.ai.understanding import understand_query
from dms.chat.contract import AnswerShape, QueryPattern
from dms.exceptions import GeminiError

from .ai_fakes import FixedClock, ScriptedLLM, StaticMetadataProvider

CLOCK = FixedClock(datetime(2026, 9, 15, 3, 0, tzinfo=UTC))


def make_query(question: str):
    return understand_query(
        question,
        session_history=[],
        previous_slots=None,
        llm=ScriptedLLM(),
        metadata=StaticMetadataProvider(),
        clock=CLOCK,
    )


def make_planner(responses, **config):
    llm = ScriptedLLM(responses)
    cfg = PlannerConfig(**config)
    return QueryPlanner(llm, SchemaRetriever(StaticMetadataProvider(), config=cfg)), llm


def output(**overrides) -> str:
    data = {
        "intent": None,
        "system_state": None,
        "answer_shape": "table",
        "confidence": 0.9,
        "steps": [],
        "help_topic": None,
        "help_label": None,
        "reason": None,
    }
    data.update(overrides)
    return json.dumps(data, ensure_ascii=False)


def step(function_name: str, pattern: str = "sql_template", **params) -> dict:
    return {"pattern": pattern, "function_name": function_name, "params": params}


def test_overview_single_call():
    planner, llm = make_planner(
        [output(intent="OVERVIEW", answer_shape="number", steps=[step("get_overview")])]
    )
    result = planner.plan(make_query("Tổng quan phản hồi tháng 8/2026"))
    assert result.intent is Intent.OVERVIEW
    assert len(result.steps) == 1
    plan = result.steps[0]
    assert plan.pattern is QueryPattern.SQL_TEMPLATE
    assert plan.function_name == "get_overview"
    assert plan.params["date_from"] == "2026-08-01" and plan.params["date_to"] == "2026-08-31"
    assert [kind for kind, _ in llm.calls] == [CALL_TYPE_PLAN]


def test_disabled_pattern_is_invalid():
    planner, _ = make_planner(
        [output(intent="OVERVIEW", steps=[step("get_overview", pattern="semantic_view")])],
        max_repair=0,
    )
    result = planner.plan(make_query("Tổng quan tháng 8"))
    assert result.system_state is PlannerState.CLARIFY
    assert result.reason == REASON_INVALID_OUTPUT
    assert any("chưa được bật" in error for error in result.errors)


def test_old_schema_function_name_is_invalid():
    planner, _ = make_planner(
        [output(intent="DRILL_PRODUCT", steps=[step("get_products_top")])], max_repair=0
    )
    result = planner.plan(make_query("Top sản phẩm tháng 8"))
    assert result.reason == REASON_INVALID_OUTPUT
    assert any("get_products_top" in error for error in result.errors)


def test_repair_succeeds():
    planner, llm = make_planner(
        [
            output(intent="DRILL_PRODUCT", steps=[step("get_products_top")]),
            output(intent="DRILL_PRODUCT", steps=[step("get_products")]),
        ]
    )
    result = planner.plan(make_query("Top sản phẩm tháng 8"))
    assert result.intent is Intent.DRILL_PRODUCT
    assert result.steps[0].function_name == "get_products"
    assert result.repairs == 1
    assert [kind for kind, _ in llm.calls] == [CALL_TYPE_PLAN, CALL_TYPE_REPAIR]
    assert "get_products_top" in llm.calls[1][1]


def test_repair_fails_then_clarify():
    bad = output(intent="DRILL_PRODUCT", steps=[step("get_products_top")])
    planner, llm = make_planner([bad, bad], max_repair=1)
    result = planner.plan(make_query("Top sản phẩm tháng 8"))
    assert result.system_state is PlannerState.CLARIFY
    assert result.reason == REASON_INVALID_OUTPUT
    assert result.steps == ()
    assert len(llm.calls) == 2


def test_broken_json_goes_to_repair():
    planner, _ = make_planner(["không phải json", output(intent="HELP", help_topic="usage")])
    result = planner.plan(make_query("Trợ lý dùng thế nào?"))
    assert result.intent is Intent.HELP and result.repairs == 1


def test_llm_error_is_raised_for_orchestrator():
    planner, _ = make_planner([GeminiError("timeout")])
    with pytest.raises(GeminiError):
        planner.plan(make_query("Tổng quan tháng 8"))


def test_llm_dates_are_overridden_and_logged(caplog):
    caplog.set_level(logging.INFO, logger="dms-chat-planner")
    planner, _ = make_planner(
        [
            output(
                intent="OVERVIEW",
                steps=[step("get_overview", date_from="2026-08-01", date_to="2026-08-30")],
            )
        ]
    )
    result = planner.plan(make_query("Tổng quan phản hồi tháng 8/2026"))
    assert result.steps[0].params["date_to"] == "2026-08-31"
    assert any(record.getMessage() == "planner_date_override" for record in caplog.records)


def test_llm_dates_removed_when_question_has_no_time():
    planner, _ = make_planner(
        [output(intent="DRILL_PRODUCT", steps=[step("get_products", date_from="2026-01-01")])]
    )
    result = planner.plan(make_query("Sản phẩm nào bị báo lỗi nhiều nhất?"))
    assert "date_from" not in result.steps[0].params


def test_compare_range_added_only_where_supported():
    planner, _ = make_planner([output(intent="COMPARISON", steps=[step("get_overview")])])
    overview = planner.plan(make_query("So sánh Q2 vs Q3")).steps[0]
    assert overview.params["date_from"] == "2026-07-01"
    assert overview.params["compare_from"] == "2026-04-01"
    assert overview.params["compare_to"] == "2026-06-30"

    # Q2 đúng là kỳ liền trước Q3 nên get_comparison(period=quarter) cho cùng kết quả.
    planner, _ = make_planner(
        [output(intent="COMPARISON", steps=[step("get_comparison", period="quarter")])]
    )
    comparison = planner.plan(make_query("So sánh Q2 vs Q3")).steps[0]
    assert comparison.function_name == "get_comparison"
    assert "compare_from" not in comparison.params
    assert comparison.params["period"] == "quarter"


def test_comparison_of_full_months_uses_overview_when_clamp_would_cut_a_day():
    """Analytics lùi 30/04 thành 30/03: "tháng 3 với tháng 4" giữ get_comparison thì mất 31/03."""
    planner, _ = make_planner(
        [output(intent="COMPARISON", steps=[step("get_comparison", period="month")])]
    )
    plan = planner.plan(make_query("So sánh tháng 3/2026 với tháng 4/2026")).steps[0]
    assert plan.function_name == "get_overview"
    assert (plan.params["compare_from"], plan.params["compare_to"]) == ("2026-03-01", "2026-03-31")


def test_comparison_of_non_adjacent_periods_uses_overview():
    """get_comparison chỉ lùi một kỳ: "tháng 3 với tháng 6" mà giữ nó thì tháng 3 bị bỏ."""
    planner, _ = make_planner(
        [output(intent="COMPARISON", steps=[step("get_comparison", period="month")])]
    )
    plan = planner.plan(make_query("So sánh tháng 3/2026 với tháng 6/2026")).steps[0]
    assert plan.function_name == "get_overview"
    assert "period" not in plan.params
    assert (plan.params["date_from"], plan.params["compare_from"]) == ("2026-06-01", "2026-03-01")


def test_narrative_statistics_then_small_sample():
    planner, _ = make_planner(
        [
            output(
                intent="DRILL_ISSUE",
                answer_shape="narrative",
                steps=[step("get_issue_types"), step("get_issues", page_size=20)],
            )
        ]
    )
    result = planner.plan(make_query("Tóm tắt các vấn đề nổi cộm tháng 8"))
    assert result.answer_shape is AnswerShape.NARRATIVE
    assert [plan.function_name for plan in result.steps] == ["get_issue_types", "get_issues"]
    assert result.steps[1].params["page_size"] == 5


def test_narrative_second_step_must_be_sample():
    planner, _ = make_planner(
        [
            output(
                intent="DRILL_ISSUE",
                answer_shape="narrative",
                steps=[step("get_issue_types"), step("get_groups")],
            )
        ],
        max_repair=0,
    )
    assert planner.plan(make_query("Tóm tắt vấn đề tháng 8")).reason == REASON_INVALID_OUTPUT


def test_too_many_steps_invalid():
    steps = [step("get_overview")] * 4
    planner, _ = make_planner([output(intent="OVERVIEW", steps=steps)], max_repair=0)
    result = planner.plan(make_query("Tổng quan tháng 8"))
    assert result.reason == REASON_INVALID_OUTPUT


@pytest.mark.parametrize(
    "bad",
    [
        output(intent="SUMMARY", steps=[step("get_overview")]),
        output(system_state="UNAUTHORIZED_SCOPE"),
        output(intent="DRILL_GEOGRAPHY", steps=[step("get_products")]),
        output(system_state="CLARIFY", steps=[step("get_overview")]),
        output(),
    ],
)
def test_invalid_outputs(bad):
    planner, _ = make_planner([bad], max_repair=0)
    assert planner.plan(make_query("Tổng quan tháng 8")).reason == REASON_INVALID_OUTPUT


def test_out_of_domain_has_no_plan():
    planner, _ = make_planner([output(system_state="OUT_OF_DOMAIN")])
    result = planner.plan(make_query("Thời tiết Hà Nội ngày mai thế nào?"))
    assert result.system_state is PlannerState.OUT_OF_DOMAIN
    assert result.steps == ()


def test_help_label_definition():
    planner, _ = make_planner(
        [output(intent="HELP", help_topic="label_definition", help_label="Báo lỗi")]
    )
    result = planner.plan(make_query("Nhãn Báo lỗi nghĩa là gì?"))
    assert result.intent is Intent.HELP
    assert result.steps == ()
    assert (result.help_topic, result.help_label) == ("label_definition", "Báo lỗi")


def test_unsupported_intent_is_kept_with_empty_plan():
    planner, _ = make_planner([output(intent="REPORT_EXPORT")])
    result = planner.plan(make_query("Xuất Excel báo cáo tháng 8"))
    assert result.intent is Intent.REPORT_EXPORT
    assert result.steps == ()
    assert result.system_state is None


def test_unsupported_intent_with_steps_is_invalid():
    planner, _ = make_planner(
        [output(intent="LOOKUP_SIMILAR", steps=[step("get_issues")])], max_repair=0
    )
    assert planner.plan(make_query("Tìm phản hồi giống mã NT-01")).reason == REASON_INVALID_OUTPUT


def test_unknown_params_are_left_for_plan_guard():
    planner, _ = make_planner(
        [output(intent="DRILL_PRODUCT", steps=[step("get_products", sentiment="Tiêu cực")])]
    )
    result = planner.plan(make_query("Top sản phẩm bị phàn nàn tháng 8"))
    assert result.steps[0].params["sentiment"] == "Tiêu cực"


def test_plan_serialization_has_rewritten_query_and_no_intent():
    planner, _ = make_planner([output(intent="OVERVIEW", steps=[step("get_overview")])])
    query = make_query("Tổng quan phản hồi tháng 8/2026")
    plan = planner.plan(query).steps[0]
    data = plan.to_dict()
    assert "intent" not in data
    assert data["original_query"] == query.rewritten_query


def test_refused_query_is_rejected():
    planner, llm = make_planner([])
    with pytest.raises(ValueError):
        planner.plan(make_query("Xoá phản hồi FB-001 giúp tôi"))
    assert llm.calls == []


# ── Báo cáo (b10 task 1.2) ──

M5 = {
    "milestone": "M5",
    "enabled_patterns": frozenset({"sql_template", "fts5_search", "semantic_view"}),
}


def test_report_intent_has_no_steps_and_keeps_sections():
    planner, llm = make_planner(
        [output(intent="REPORT_CUSTOM", report_sections=["product", "province"])], **M5
    )
    result = planner.plan(make_query("Báo cáo tháng 8 về sản phẩm và địa lý"))
    assert result.intent is Intent.REPORT_CUSTOM
    assert result.steps == ()
    assert result.report_sections == ("products", "geography")
    assert result.to_dict()["report_sections"] == ["products", "geography"]


def test_report_intent_without_sections_is_full_report():
    planner, _ = make_planner([output(intent="REPORT_WEEKLY")], **M5)
    result = planner.plan(make_query("Báo cáo tuần trước"))
    assert result.intent is Intent.REPORT_WEEKLY
    assert result.report_sections == ()


def test_report_intent_rejects_llm_supplied_steps():
    planner, llm = make_planner(
        [
            output(intent="REPORT_WEEKLY", steps=[step("get_overview")]),
            output(intent="REPORT_WEEKLY"),
        ],
        **M5,
    )
    result = planner.plan(make_query("Báo cáo tuần trước"))
    assert result.intent is Intent.REPORT_WEEKLY and result.steps == ()
    assert [kind for kind, _ in llm.calls] == [CALL_TYPE_PLAN, CALL_TYPE_REPAIR]


def test_report_intent_not_supported_before_m5():
    planner, _ = make_planner([output(intent="REPORT_WEEKLY")])
    result = planner.plan(make_query("Báo cáo tuần trước"))
    assert result.intent is Intent.REPORT_WEEKLY
    assert result.steps == ()


REPORT_PROMPT_EXTRA_TOKENS = 400


def test_report_prompt_costs_little_more_than_the_m4_prompt():
    """Luật và ví dụ báo cáo chỉ được thêm một phần nhỏ so với prompt của M4."""
    from dms.chat.ai.schema_retriever import estimate_tokens

    m4 = {**M5, "milestone": "M4"}
    question = "Báo cáo tuần trước của Nha Trang về sản phẩm và địa lý"
    before = SchemaRetriever(StaticMetadataProvider(), config=PlannerConfig(**m4))
    after = SchemaRetriever(StaticMetadataProvider(), config=PlannerConfig(**M5))
    assert before.prompt_name == "planner_v3" and after.prompt_name == "planner_v4"
    grown = estimate_tokens(after.render_prompt(make_query(question)).text) - estimate_tokens(
        before.render_prompt(make_query(question)).text
    )
    assert 0 < grown <= REPORT_PROMPT_EXTRA_TOKENS, grown
