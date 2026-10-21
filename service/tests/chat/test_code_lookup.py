"""Tra phản hồi theo mã vấn đề: Plan Guard → Orchestrator → executor thật trên SQLite."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from analytics_support import seed_classified_records

from dms.analytics import AnalyticsFilter, FeedbackAnalyticsRepository
from dms.analytics.service import FeedbackAnalyticsService
from dms.chat.ai.block_builders import CODE_LOOKUP_FUNCTION, build_blocks
from dms.chat.ai.intents import Intent, PlannerState
from dms.chat.ai.issue_codes import codes_in, normalize_codes
from dms.chat.ai.orchestrator import ChatOrchestrator
from dms.chat.ai.planner_config import PlannerConfig
from dms.chat.ai.query_planner import QueryPlanner
from dms.chat.ai.schema_retriever import SchemaRetriever
from dms.chat.ai.types import FLAG_CODE_LOOKUP, Decision, Reason, TurnRequest
from dms.chat.contract import UserScope
from dms.chat.db.function_registry import FunctionRegistry
from dms.chat.db.query_executor import SecureQueryExecutor
from dms.chat.guardrails.plan_guard import PlanGuard, PlanGuardConfig

from .ai_fakes import FixedClock, ScriptedLLM, StaticMetadataProvider
from .test_plan_guard import make_output, make_query

CLOCK = FixedClock(datetime(2026, 9, 15, 3, 0, tzinfo=UTC))
TV1, TV2, TV3 = "Truyền thống Vùng 1", "Truyền thống Vùng 2", "Truyền thống Vùng 3"
ADMIN = UserScope(username="admin", role="admin", display_name="Admin", unit_ids=[])
PATTERNS = frozenset({"sql_template", "fts5_search"})


def user(*units: str) -> UserScope:
    return UserScope(username="nv", role="user", display_name="NV", unit_ids=list(units))


# ── Nhận mã trong câu hỏi ──


@pytest.mark.parametrize(
    ("text", "codes"),
    [
        ("Tra phản hồi mã 241659", ("241659",)),
        ("Mã phản hồi là #240625, 241659 và 251098?", ("240625", "241659", "251098")),
        ("cho xem mã vấn đề NT-0106", ("NT-0106",)),
        ("Phản hồi mã số 241659 thuộc đơn vị nào", ("241659",)),
        ("Tổng quan tháng 8/2026", ()),
        ("Có 241659 phản hồi không", ()),
        ("mã 12", ()),
    ],
)
def test_codes_in(text, codes):
    assert codes_in(text) == codes


def test_normalize_codes_drops_values_that_are_not_codes():
    assert normalize_codes(["241659", "#nt-0106", "12", "abc", ["251098"]]) == (
        "241659",
        "NT-0106",
        "251098",
    )


# ── Plan Guard ──


@pytest.fixture
def guard() -> PlanGuard:
    return PlanGuard(StaticMetadataProvider(), config=PlanGuardConfig())


def test_numeric_code_builds_lookup_step(guard):
    """Ca 27: "Tra phản hồi mã 206501" giờ tra thật, không còn LOOKUP_BY_CODE_UNAVAILABLE."""
    plan = guard.check(
        make_output(
            intent=Intent.LOOKUP_FEEDBACK,
            function_name="get_issues",
            params={"issue_code": "206501", "date_from": "2026-08-01"},
        ),
        make_query("Tra phản hồi mã 206501"),
        ADMIN,
    )
    assert plan.decision is Decision.RUN
    assert FLAG_CODE_LOOKUP in plan.flags
    assert [(s.function_name, s.params["issue_code"]) for s in plan.steps] == [
        ("get_issues", "206501")
    ]
    # Bộ lọc Planner đoán thêm không đi theo: phản hồi đúng mã có thể nằm ngoài khoảng ngày đó.
    assert "date_from" not in plan.steps[0].params


def test_code_lookup_runs_even_when_planner_asks_to_clarify(guard):
    plan = guard.check(
        make_output(intent=None, system_state=PlannerState.CLARIFY),
        make_query("mã 241659"),
        ADMIN,
    )
    assert plan.decision is Decision.RUN and plan.intent is Intent.LOOKUP_FEEDBACK


def test_out_of_domain_wins_over_code(guard):
    plan = guard.check(
        make_output(intent=None, system_state=PlannerState.OUT_OF_DOMAIN),
        make_query("Xoá phản hồi mã 241659"),
        ADMIN,
    )
    assert plan.decision is Decision.REFUSE


def test_other_intent_without_code_param_is_not_a_lookup(guard):
    plan = guard.check(
        make_output(intent=Intent.OVERVIEW, params={"date_from": "2026-08-01"}),
        make_query("Tổng quan tháng 8 có mã 241659 không"),
        ADMIN,
    )
    assert FLAG_CODE_LOOKUP not in plan.flags


def test_planner_code_param_on_other_intent_becomes_lookup(guard):
    plan = guard.check(
        make_output(intent=Intent.OVERVIEW, params={"issue_code": "241659"}),
        make_query("Phản hồi 241659 thuộc đơn vị nào"),
        ADMIN,
    )
    assert FLAG_CODE_LOOKUP in plan.flags
    assert plan.steps[0].params["issue_code"] == "241659"


def test_similar_by_code_stays_similar(guard):
    plan = guard.check(
        make_output(
            intent=Intent.LOOKUP_SIMILAR,
            function_name="get_issues",
            params={"issue_code": "241659"},
        ),
        make_query("Tìm phản hồi giống mã 241659"),
        ADMIN,
    )
    assert FLAG_CODE_LOOKUP not in plan.flags


def test_at_most_three_codes_and_the_rest_is_noticed(guard):
    plan = guard.check(
        make_output(intent=Intent.LOOKUP_FEEDBACK, function_name="get_issues"),
        make_query("Tra mã 100001, 100002, 100003 và 100004"),
        ADMIN,
    )
    assert [s.params["issue_code"] for s in plan.steps] == ["100001", "100002", "100003"]
    assert any("100004" in notice.message for notice in plan.notices)


def test_unrecognized_code_value_asks_for_the_code(guard):
    plan = guard.check(
        make_output(
            intent=Intent.LOOKUP_FEEDBACK, function_name="get_issues", params={"feedback_id": 12}
        ),
        make_query("Cho xem phản hồi số 12"),
        ADMIN,
    )
    assert (plan.decision, plan.reason) == (Decision.CLARIFY, Reason.FILTER_REQUIRED)
    assert "mã vấn đề" in plan.message
    assert "feedback_id" not in plan.message and "issue_code" not in plan.message


def test_user_without_units_is_refused(guard):
    plan = guard.check(
        make_output(intent=Intent.LOOKUP_FEEDBACK, function_name="get_issues"),
        make_query("Tra phản hồi mã 241659"),
        user(),
    )
    assert (plan.decision, plan.reason) == (Decision.REFUSE, Reason.UNAUTHORIZED_SCOPE)


def test_mentioned_unit_outside_scope_is_refused(guard):
    plan = guard.check(
        make_output(intent=Intent.LOOKUP_FEEDBACK, function_name="get_issues"),
        make_query("Tra phản hồi mã 241659 của Truyền thống Vùng 2"),
        user(TV1),
    )
    assert (plan.decision, plan.reason) == (Decision.REFUSE, Reason.UNAUTHORIZED_SCOPE)


# ── Đầu-cuối trên dữ liệu thật (FeedbackAnalyticsRepository + SecureQueryExecutor) ──


@pytest.fixture
def repo(tmp_path) -> FeedbackAnalyticsRepository:
    repo = FeedbackAnalyticsRepository(tmp_path / "analytics.db")
    seed_classified_records(
        repo,
        db_path=repo.db_path,
        entries=[
            {
                "issue_code": "241659",
                "unit_name": TV1,
                "content": "Ruột 2 l mỏng, giữ nhiệt kém",
                "issue_date": "2026-04-09",
                "business_status": "Chờ xử lý",
                "product": "Phích, ruột phích",
                "labels": ["Báo lỗi"],
            },
            {
                "issue_code": "240625",
                "unit_name": TV2,
                "content": "MPE lên giá",
                "issue_date": "2026-03-28",
            },
            {
                "issue_code": "251098",
                "unit_name": TV3,
                "content": "Đề nghị làm thêm đèn báo mới",
                "issue_date": "2026-07-29",
            },
        ],
    )
    return repo


def planner_json(code: str) -> str:
    step = {
        "pattern": "sql_template",
        "function_name": "get_issues",
        "params": {"issue_code": code},
    }
    return json.dumps(
        {"intent": "LOOKUP_FEEDBACK", "answer_shape": "list", "confidence": 0.9, "steps": [step]}
    )


def run(repo, question: str, scope: UserScope, code: str = "241659"):
    llm = ScriptedLLM([planner_json(code)])
    metadata = StaticMetadataProvider()
    config = PlannerConfig(enabled_patterns=PATTERNS, milestone="M3")
    orchestrator = ChatOrchestrator(
        llm=llm,
        planner=QueryPlanner(llm, SchemaRetriever(metadata, config=config), config=config),
        plan_guard=PlanGuard(
            metadata, config=PlanGuardConfig(enabled_patterns=PATTERNS, milestone="M3")
        ),
        executor=SecureQueryExecutor(repo),
        metadata=metadata,
        clock=CLOCK,
    )
    return orchestrator.handle(
        TurnRequest(question=question, scope=scope, session_id="s1", previous_quotes=())
    )


def test_lookup_by_numeric_code_shows_the_record(repo):
    outcome = run(repo, "Tra phản hồi mã 241659", ADMIN)

    assert outcome.decision is Decision.RUN and outcome.reason is None
    [step] = outcome.step_results
    assert step.function_name == CODE_LOOKUP_FUNCTION
    [row] = step.result.data
    assert (row["issue_code"], row["unit_name"]) == ("241659", TV1)

    table, quotes = build_blocks(outcome.step_results)
    assert table.kind == "table" and quotes.kind == "quote"
    cells = table.payload["rows"][0]
    assert cells["issue_code"] == "241659"
    assert cells["product"] == "Phích, ruột phích"
    assert cells["labels"] == "Báo lỗi"
    assert quotes.payload["quotes"][0]["content"] == "Ruột 2 l mỏng, giữ nhiệt kém"


def test_unknown_code_says_not_found(repo):
    outcome = run(repo, "Tra phản hồi mã 999999", ADMIN, code="999999")
    assert outcome.reason is Reason.CODE_NOT_FOUND
    assert "999999" in outcome.message
    assert outcome.step_results == ()


def test_code_of_another_unit_looks_like_not_found(repo):
    """Không nói "không có quyền": tránh lộ phản hồi của đơn vị khác có tồn tại."""
    outcome = run(repo, "Tra phản hồi mã 240625", user(TV1), code="240625")
    assert outcome.reason is Reason.CODE_NOT_FOUND
    assert TV2 not in outcome.message and "quyền" not in outcome.message


def test_multi_unit_user_only_sees_codes_in_scope(repo):
    outcome = run(repo, "Tra mã 241659, 240625 và 251098", user(TV1, TV3))

    [step] = outcome.step_results
    assert {row["issue_code"] for row in step.result.data} == {"241659", "251098"}
    missing = [n for n in outcome.notices if n.kind is Reason.CODE_NOT_FOUND]
    assert len(missing) == 1 and "240625" in missing[0].message


def test_get_issues_filters_by_code_and_unit_list(repo):
    service = FeedbackAnalyticsService(repo)
    options = dict(page=1, page_size=10, source=None, unit_name=None, label=None, product=None)
    by_code = service.issues(
        AnalyticsFilter(), business_status=None, issue_code=" 241659 ", **options
    )
    assert [item["issue_code"] for item in by_code["items"]] == ["241659"]
    in_units = service.issues(
        AnalyticsFilter(), business_status=None, unit_names=[TV2, TV3], **options
    )
    assert {item["unit_name"] for item in in_units["items"]} == {TV2, TV3}
    assert in_units["total"] == 2
    nothing = service.issues(AnalyticsFilter(), business_status=None, unit_names=[], **options)
    assert nothing["total"] == 0


def test_registry_get_issues_never_leaks_units_for_multi_unit_user(repo):
    [result] = FunctionRegistry(repo).execute("get_issues", {}, user(TV1, TV3))
    assert {item["unit_name"] for item in result["items"]} == {TV1, TV3}
    assert result["total"] == 2


def test_same_feedback_from_several_files_is_quoted_once():
    from dms.chat.ai.block_builders import build_code_lookup_blocks

    row = {"issue_code": "246847", "content": "Hàng phích bán chậm", "unit_name": TV1}
    table, quotes = build_code_lookup_blocks(
        [{**row, "source_file_name": "a.xlsx"}, {**row, "source_file_name": "b.xlsx"}]
    )
    assert [r["source_file_name"] for r in table.payload["rows"]] == ["a.xlsx", "b.xlsx"]
    assert len(quotes.payload["quotes"]) == 1
