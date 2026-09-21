from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

import pytest

from dms.chat.ai.intents import Intent
from dms.chat.ai.orchestrator import ChatOrchestrator, OrchestratorConfig
from dms.chat.ai.planner_config import PlannerConfig
from dms.chat.ai.query_planner import CALL_TYPE_PLAN, QueryPlanner
from dms.chat.ai.schema_retriever import SchemaRetriever
from dms.chat.ai.types import (
    Decision,
    HistoryTurn,
    Reason,
    StepState,
    TurnCancelled,
    TurnRequest,
)
from dms.chat.ai.understanding import UnderstandingConfig
from dms.chat.contract import QueryPlan, QueryResult, UserScope
from dms.chat.guardrails.plan_guard import PlanGuard, PlanGuardConfig
from dms.chat.mock_executor import MockQueryExecutor

from .ai_fakes import (
    FORBIDDEN_UNIT_MESSAGE,
    FixedClock,
    ScopeRespectingFakeExecutor,
    ScriptedLLM,
    SlowFakeExecutor,
    StaticMetadataProvider,
)

CLOCK = FixedClock(datetime(2026, 9, 15, 3, 0, tzinfo=UTC))
TV1 = "Truyền thống Vùng 1"
TV2 = "Truyền thống Vùng 2"
TV3 = "Truyền thống Vùng 3"

PLAN_JSON = json.dumps(
    {
        "intent": "OVERVIEW",
        "answer_shape": "number",
        "confidence": 0.9,
        "steps": [{"pattern": "sql_template", "function_name": "get_overview", "params": {}}],
    }
)
NARRATIVE_PLAN_JSON = json.dumps(
    {
        "intent": "OVERVIEW",
        "answer_shape": "narrative",
        "confidence": 0.9,
        "steps": [
            {"pattern": "sql_template", "function_name": "get_overview", "params": {}},
            {
                "pattern": "sql_template",
                "function_name": "get_issues",
                "params": {"page_size": 3},
            },
        ],
    }
)


class FailingExecutor:
    """Trả lỗi định sẵn; dùng để ghim cách ResultInterpreter đọc executor v1.0."""

    def __init__(self, message: str, *, fail_on_call: int = 1) -> None:
        self.message = message
        self.fail_on_call = fail_on_call
        self.calls: list[tuple[QueryPlan, UserScope]] = []

    def execute(self, plan: QueryPlan, scope: UserScope) -> QueryResult:
        self.calls.append((plan, scope))
        if len(self.calls) == self.fail_on_call:
            return QueryResult.error(self.message)
        return QueryResult.ok([{"total_issues": 12}], total_rows=1)


class NoDataExecutor:
    def __init__(self) -> None:
        self.calls: list[tuple[QueryPlan, UserScope]] = []

    def execute(self, plan: QueryPlan, scope: UserScope) -> QueryResult:
        self.calls.append((plan, scope))
        return QueryResult.no_data()


def build(
    executor,
    *,
    responses: str = PLAN_JSON,
    config: OrchestratorConfig | None = None,
    understanding: UnderstandingConfig | None = None,
) -> tuple[ChatOrchestrator, ScriptedLLM]:
    llm = ScriptedLLM(default=responses)
    metadata = StaticMetadataProvider()
    planner_config = PlannerConfig()
    planner = QueryPlanner(llm, SchemaRetriever(metadata, config=planner_config))
    orchestrator = ChatOrchestrator(
        llm=llm,
        planner=planner,
        plan_guard=PlanGuard(metadata, config=PlanGuardConfig()),
        executor=executor,
        metadata=metadata,
        config=config or OrchestratorConfig(),
        understanding=understanding or UnderstandingConfig(),
        clock=CLOCK,
    )
    return orchestrator, llm


def turn(question: str, *units: str, admin: bool = False) -> TurnRequest:
    scope = (
        UserScope(username="admin", role="admin", display_name="Admin", unit_ids=[])
        if admin
        else UserScope(username="nv", role="user", display_name="NV", unit_ids=list(units))
    )
    return TurnRequest(question=question, scope=scope, session_id="s1")


# ── Luồng chạy thành công ──


@pytest.mark.parametrize(
    "executor_factory",
    [MockQueryExecutor, ScopeRespectingFakeExecutor],
    ids=["mock", "scope_respecting_fake"],
)
def test_successful_turn_runs_one_step(executor_factory):
    """Đổi executor mà không phải sửa Orchestrator."""
    executor = executor_factory()
    orchestrator, llm = build(executor)

    outcome = orchestrator.handle(turn("Tổng quan tháng 8", TV1))

    assert outcome.decision is Decision.RUN
    assert len(outcome.step_results) == 1
    assert outcome.step_results[0].status.state is StepState.OK
    assert outcome.intent is Intent.OVERVIEW
    # Lượt đầu không có lịch sử nên Contextualizer bỏ qua LLM; ngoài hai loại này thì không
    # được có lời gọi nào khác.
    call_types = {call_type for call_type, _ in llm.calls}
    assert CALL_TYPE_PLAN in call_types
    assert call_types <= {"chat_contextualize", CALL_TYPE_PLAN}
    assert outcome.request_id
    assert outcome.timings_ms["total"] >= 0
    assert "execute_1" in outcome.timings_ms
    assert outcome.llm_usage[CALL_TYPE_PLAN]["calls"] == 1


def test_turn_outcome_is_json_serializable():
    orchestrator, _ = build(MockQueryExecutor())
    outcome = orchestrator.handle(turn("Tổng quan tháng 8", TV1))
    assert json.loads(json.dumps(outcome.to_dict(), ensure_ascii=False))["decision"] == "run"


# ── Điểm dừng ──


def test_input_guard_layer_one_stops_everything():
    executor = ScopeRespectingFakeExecutor()
    orchestrator, llm = build(executor, understanding=UnderstandingConfig(max_question_chars=10))

    outcome = orchestrator.handle(turn("câu hỏi rất dài vượt quá giới hạn cho phép", TV1))

    assert outcome.decision is Decision.REFUSE
    assert outcome.message
    assert llm.calls == []  # Contextualizer và Planner đều không chạy
    assert executor.calls == []


def test_plan_guard_refusal_stops_before_executor():
    executor = ScopeRespectingFakeExecutor()
    orchestrator, _ = build(executor)

    outcome = orchestrator.handle(turn("Nha Trang tháng 8 thế nào?", TV1))

    assert outcome.decision is Decision.REFUSE
    assert outcome.reason is Reason.UNAUTHORIZED_SCOPE
    assert executor.calls == []


# ── Diễn giải kết quả ──


def test_no_data_is_not_an_error():
    orchestrator, _ = build(NoDataExecutor())
    outcome = orchestrator.handle(turn("Tổng quan tháng 8", TV1))

    assert outcome.decision is Decision.RUN
    assert outcome.step_results[0].status.state is StepState.NO_DATA


def test_executor_scope_refusal_becomes_a_notice():
    message = FORBIDDEN_UNIT_MESSAGE.format(unit="Nha Trang", allowed=TV1)
    orchestrator, _ = build(FailingExecutor(message))

    outcome = orchestrator.handle(turn("Tổng quan tháng 8", TV1))

    assert outcome.step_results[0].status.state is StepState.FORBIDDEN
    assert outcome.reason is Reason.UNAUTHORIZED_SCOPE
    assert any(notice.kind is Reason.UNAUTHORIZED_SCOPE for notice in outcome.notices)


def test_internal_error_is_not_leaked_to_the_user():
    orchestrator, _ = build(FailingExecutor("Lỗi thực thi: near GROUP: syntax error"))

    outcome = orchestrator.handle(turn("Tổng quan tháng 8", TV1))

    assert outcome.reason is Reason.INTERNAL
    assert "syntax error" not in (outcome.message or "")
    assert "GROUP" not in (outcome.message or "")


def test_sample_step_failure_keeps_the_statistics():
    orchestrator, _ = build(
        FailingExecutor("Lỗi thực thi: sample hỏng", fail_on_call=2),
        responses=NARRATIVE_PLAN_JSON,
    )

    outcome = orchestrator.handle(turn("Tổng quan tháng 8 kèm ví dụ", TV1))

    assert outcome.decision is Decision.RUN
    assert outcome.step_results[0].status.state is StepState.OK
    assert any(notice.kind is Reason.SAMPLE_UNAVAILABLE for notice in outcome.notices)


# ── Phạm vi và timeout ──


def test_execution_scope_is_narrowed_never_widened():
    executor = ScopeRespectingFakeExecutor()
    orchestrator, _ = build(executor)

    outcome = orchestrator.handle(turn("TV1 tháng 8 có bao nhiêu vấn đề?", TV1, TV2, TV3))

    assert outcome.decision is Decision.RUN
    _, scope = executor.calls[0]
    assert scope.unit_ids == [TV1]
    assert set(scope.unit_ids) <= {TV1, TV2, TV3}


def test_step_timeout_produces_a_clear_error():
    orchestrator, _ = build(
        SlowFakeExecutor(0.3),
        config=OrchestratorConfig(step_timeout_seconds=0.05, turn_timeout_seconds=5),
    )

    outcome = orchestrator.handle(turn("Tổng quan tháng 8", TV1))

    assert outcome.reason is Reason.TIMEOUT
    assert outcome.message
    assert outcome.step_results[-1].status.state is StepState.ERROR


# ── Truy vết ──


def test_every_log_record_carries_the_request_id(caplog):
    orchestrator, _ = build(MockQueryExecutor())
    with caplog.at_level(logging.INFO, logger="dms-chat-orchestrator"):
        outcome = orchestrator.handle(turn("Tổng quan tháng 8", TV1))

    records = [r for r in caplog.records if r.name == "dms-chat-orchestrator"]
    assert records
    for record in records:
        assert record.request_id == outcome.request_id
        assert "raw_data_json" not in record.getMessage()
        assert not hasattr(record, "data")


def test_follow_up_turn_goes_through_the_contextualizer():
    orchestrator, llm = build(MockQueryExecutor())
    request = TurnRequest(
        question="còn tháng 8 thì sao?",
        scope=UserScope(username="nv", role="user", display_name="NV", unit_ids=[TV1]),
        session_id="s1",
        history=[HistoryTurn("Tổng quan tháng 7", "Tháng 7 có 10 vấn đề")],
    )

    outcome = orchestrator.handle(request)

    assert {call_type for call_type, _ in llm.calls} == {"chat_contextualize", CALL_TYPE_PLAN}
    assert outcome.decision is Decision.RUN


# ── Giai đoạn và huỷ (b06 D6–D7) ──


def test_on_stage_reports_stages_in_order():
    orchestrator, _ = build(MockQueryExecutor())
    stages: list[str] = []

    outcome = orchestrator.handle(turn("Tổng quan tháng 8", TV1), on_stage=stages.append)

    assert outcome.decision is Decision.RUN
    assert stages == ["understanding", "planning", "querying"]


def test_input_guard_refusal_reports_only_understanding_stage():
    orchestrator, _ = build(
        ScopeRespectingFakeExecutor(), understanding=UnderstandingConfig(max_question_chars=10)
    )
    stages: list[str] = []

    orchestrator.handle(turn("câu hỏi rất dài vượt quá giới hạn", TV1), on_stage=stages.append)

    assert stages == ["understanding"]


@pytest.mark.parametrize(
    ("cancel_at", "expected_stage", "llm_called", "executor_called"),
    [
        ("understanding", "understanding", False, False),
        ("planning", "planning", False, False),
        ("querying", "querying", True, False),
    ],
)
def test_cancel_between_stages_stops_the_turn(
    cancel_at, expected_stage, llm_called, executor_called
):
    executor = ScopeRespectingFakeExecutor()
    orchestrator, llm = build(executor)
    stages: list[str] = []
    cancelled = {"flag": False}

    def on_stage(stage: str) -> None:
        stages.append(stage)

    def should_cancel() -> bool:
        # Bật cờ ngay trước khi vào giai đoạn cần huỷ.
        upcoming = ["understanding", "planning", "querying"][len(stages)]
        if upcoming == cancel_at:
            cancelled["flag"] = True
        return cancelled["flag"]

    with pytest.raises(TurnCancelled) as caught:
        orchestrator.handle(
            turn("Tổng quan tháng 8", TV1), on_stage=on_stage, should_cancel=should_cancel
        )

    assert caught.value.stage == expected_stage
    assert (CALL_TYPE_PLAN in {c for c, _ in llm.calls}) is llm_called
    assert bool(executor.calls) is executor_called


def test_cancel_between_steps_skips_remaining_steps():
    executor = ScopeRespectingFakeExecutor()
    orchestrator, _ = build(executor, responses=NARRATIVE_PLAN_JSON)

    def should_cancel() -> bool:
        return len(executor.calls) >= 1

    with pytest.raises(TurnCancelled):
        orchestrator.handle(turn("Tổng quan tháng 8 kèm ví dụ", TV1), should_cancel=should_cancel)

    assert len(executor.calls) == 1
