"""Reset chủ đề và slot theo phạm vi hiện tại (spec ``chat-conversation-memory``, b11 task 3.3–3.5)."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime

import pytest

from dms.chat.ai.answer_events import EventType, ListSink
from dms.chat.ai.contextualizer import CALL_TYPE as CALL_TYPE_CONTEXTUALIZE
from dms.chat.ai.memory.topic_reset import RESET_ASSUMPTION, detect_topic_reset
from dms.chat.ai.orchestrator import ChatOrchestrator, OrchestratorConfig
from dms.chat.ai.planner_config import PlannerConfig
from dms.chat.ai.query_planner import QueryPlanner
from dms.chat.ai.response_shaper import AnswerComposer
from dms.chat.ai.schema_retriever import SchemaRetriever
from dms.chat.ai.types import (
    DateRange,
    Decision,
    EntityCandidate,
    HistoryTurn,
    HistoryWindow,
    Reason,
    SessionSlots,
    TurnRequest,
)
from dms.chat.ai.understanding import UnderstandingConfig, understand_query
from dms.chat.contract import UserScope
from dms.chat.guardrails.plan_guard import PlanGuard, PlanGuardConfig

from .ai_fakes import FixedClock, ScopeRespectingFakeExecutor, ScriptedLLM, StaticMetadataProvider

CLOCK = FixedClock(datetime(2026, 9, 15, 3, 0, tzinfo=UTC))
TV1 = "Truyền thống Vùng 1"
TV2 = "Truyền thống Vùng 2"
OVERVIEW_PLAN = json.dumps(
    {
        "intent": "OVERVIEW",
        "answer_shape": "number",
        "confidence": 0.9,
        "steps": [{"pattern": "sql_template", "function_name": "get_overview", "params": {}}],
    }
)
PRODUCTS_PLAN = json.dumps(
    {
        "intent": "DRILL_PRODUCT",
        "answer_shape": "table",
        "confidence": 0.9,
        "steps": [{"pattern": "sql_template", "function_name": "get_products", "params": {}}],
    }
)


def unit(value: str) -> EntityCandidate:
    return EntityCandidate("unit", value, value, 1.0)


AUGUST = DateRange(date(2026, 8, 1), date(2026, 8, 31))
HISTORY = HistoryWindow(
    summary="Người dùng đã hỏi Nha Trang tháng 8.",
    turns=(HistoryTurn("Tổng quan Nha Trang tháng 8", "Tổng số vấn đề 1.234"),),
)


# ── Nhận diện cụm reset ──

BUSINESS_QUESTIONS = [
    "phản hồi về chủ đề giao hàng tháng 8",
    "Chủ đề nào bị phản hồi nhiều nhất?",
    "Tổng quan tháng 8",
    "Sản phẩm nào bị kêu nhiều nhất quý 3?",
    "So sánh tháng 8 với tháng 7",
    "Còn Hà Nội thì sao?",
    "Xu hướng phản hồi theo ngày tuần trước",
    "Liệt kê phản hồi tiêu cực về bóng đèn",
    "Báo cáo tuần trước",
    "Đơn vị nào đứng đầu về phản hồi?",
    "Có phản hồi nào giống cái thứ 2 không?",
    "Vấn đề mới phát sinh tháng này là gì?",
    "Khách muốn bắt đầu dùng sản phẩm mới",
    "Phản hồi về việc lắp đặt lại đèn",
    "Tỉ lệ đã xử lý của Biên Hòa",
    "Nhóm vấn đề khác chiếm bao nhiêu phần trăm?",
    "Xuất kết quả vừa rồi ra Excel",
    "Các câu hỏi trước có liên quan tới Nha Trang không?",
    "Đại lý phản hồi về chuyện khác ngoài giá",
    "Hỏi về chương trình khuyến mãi mới",
]


@pytest.mark.parametrize("question", BUSINESS_QUESTIONS)
def test_business_questions_are_not_topic_resets(question):
    assert not detect_topic_reset(question)


@pytest.mark.parametrize(
    ("question", "only_reset"),
    [
        ("chủ đề khác nhé, top sản phẩm quý 3", False),
        ("Bỏ qua các câu trước, tổng quan tháng 8", False),
        ("bắt đầu lại", True),
        ("Bắt đầu lại nhé bạn!", True),
        ("bat dau lai, chu de moi nha", True),
        ("Hãy bắt đầu lại từ đầu", True),
        ("Hỏi chuyện khác: tồn đọng của Nha Trang", False),
    ],
)
def test_reset_phrases(question, only_reset):
    reset = detect_topic_reset(question)
    assert reset.matched and reset.only_reset is only_reset


# ── Reset trong bước hiểu câu hỏi ──


def understand(question: str, *, llm=None, history=HISTORY, previous=None, scope_units=None):
    return understand_query(
        question,
        session_history=history,
        previous_slots=previous,
        llm=llm or ScriptedLLM(),
        metadata=StaticMetadataProvider(),
        clock=CLOCK,
        scope_units=scope_units,
    )


def test_topic_reset_with_question_skips_context_and_slots():
    llm = ScriptedLLM()
    previous = SessionSlots(date_range=AUGUST, entities={"unit": (unit("Nha Trang"),)})

    query = understand("chủ đề khác nhé, top sản phẩm quý 3", llm=llm, previous=previous)

    assert llm.calls_for(CALL_TYPE_CONTEXTUALIZE) == []  # không gọi Contextualizer
    assert "unit" not in query.slots.entities
    assert query.date_range == DateRange(
        date(2026, 7, 1), date(2026, 9, 15), query.date_range.label
    )
    assert query.topic_reset and not query.reset_only
    assert query.assumptions[0] == RESET_ASSUMPTION


def test_reset_only_is_marked():
    query = understand("bắt đầu lại", previous=SessionSlots(date_range=AUGUST))
    assert query.reset_only
    assert query.slots.is_empty()


# ── Reset qua orchestrator ──


def build(executor, responses):
    llm = ScriptedLLM(responses)
    metadata = StaticMetadataProvider()
    planner = QueryPlanner(llm, SchemaRetriever(metadata, config=PlannerConfig()))
    return (
        ChatOrchestrator(
            llm=llm,
            planner=planner,
            plan_guard=PlanGuard(metadata, config=PlanGuardConfig()),
            executor=executor,
            metadata=metadata,
            config=OrchestratorConfig(),
            understanding=UnderstandingConfig(),
            clock=CLOCK,
        ),
        llm,
    )


def user_turn(question: str, *units: str, previous=None, history=HISTORY) -> TurnRequest:
    scope = UserScope(username="nv", role="user", display_name="NV", unit_ids=list(units))
    return TurnRequest(
        question=question, scope=scope, session_id="s1", history=history, previous_slots=previous
    )


def test_reset_only_turn_is_help_without_querying():
    executor = ScopeRespectingFakeExecutor()
    orchestrator, llm = build(executor, [])

    outcome = orchestrator.handle(user_turn("bắt đầu lại", TV1))

    assert outcome.decision is Decision.HELP
    assert outcome.reason is Reason.TOPIC_RESET
    assert executor.calls == [] and llm.calls == []
    sink = ListSink()
    AnswerComposer().compose(outcome, sink)
    text = sink.of_type(EventType.COMMENTARY)[0].data["text"]
    assert text.startswith("Đã bắt đầu chủ đề mới. Bạn muốn hỏi gì tiếp theo?")
    assert sink.done is not None and sink.done.data["status"] == "help"


def test_topic_reset_turn_runs_without_old_unit():
    executor = ScopeRespectingFakeExecutor()
    orchestrator, llm = build(executor, [PRODUCTS_PLAN])
    previous = SessionSlots(date_range=AUGUST, entities={"unit": (unit("Nha Trang"),)})

    outcome = orchestrator.handle(
        user_turn("chủ đề khác nhé, top sản phẩm quý 3", TV1, "Nha Trang", previous=previous)
    )

    assert outcome.decision is Decision.RUN
    assert llm.calls_for(CALL_TYPE_CONTEXTUALIZE) == []
    assert "unit" not in outcome.slots.entities


# ── Slot theo phạm vi hiện tại (D7) ──


def test_lost_unit_is_dropped_from_inherited_slot_without_refusal():
    executor = ScopeRespectingFakeExecutor()
    # Contextualizer chép lại cả hai đơn vị từ lịch sử — đúng điều LLM thật hay làm.
    contextualized = json.dumps(
        {"standalone_question": f"Tổng quan {TV1} và {TV2} tháng 7", "is_follow_up": True},
        ensure_ascii=False,
    )
    orchestrator, _ = build(executor, [contextualized, OVERVIEW_PLAN])
    previous = SessionSlots(date_range=AUGUST, entities={"unit": (unit(TV1), unit(TV2))})
    history = HistoryWindow(turns=(HistoryTurn(f"Tổng quan {TV1} và {TV2} tháng 8", "…"),))

    # Admin đã thu quyền TV2 giữa hai lượt.
    outcome = orchestrator.handle(
        user_turn("còn tháng 7 thì sao", TV1, previous=previous, history=history)
    )

    assert outcome.decision is Decision.RUN
    assert {plan.params.get("unit_name") for plan, _ in executor.calls} == {TV1}
    assert any(TV2 in text and "không còn quyền" in text for text in outcome.assumptions)
    assert [c.value for c in outcome.slots.entities["unit"]] == [TV1]
    sink = ListSink()
    AnswerComposer().compose(outcome, sink)
    assert sink.of_type(EventType.REFUSAL) == []


def test_unit_mentioned_directly_still_goes_through_plan_guard():
    executor = ScopeRespectingFakeExecutor()
    orchestrator, _ = build(executor, [OVERVIEW_PLAN])
    previous = SessionSlots(entities={"unit": (unit(TV2),)})

    outcome = orchestrator.handle(
        user_turn(f"Tổng quan {TV2} tháng 7", TV1, previous=previous, history=HistoryWindow())
    )

    # Nhắc thẳng TV2 ở lượt này thì không phải "bỏ khỏi ngữ cảnh": Plan Guard từ chối như cũ.
    assert outcome.reason is Reason.UNAUTHORIZED_SCOPE
    assert [d.value for d in outcome.dropped_entities] == [TV2]
    assert executor.calls == []
    assert not any("không còn quyền" in text for text in outcome.assumptions)


def test_own_unit_by_full_name_is_not_a_partial_refusal():
    """Hồi quy b03: "Vùng 1" khớp gần "Vùng 2/3" không được coi là user nhắc tới Vùng 2/3."""
    executor = ScopeRespectingFakeExecutor()
    orchestrator, _ = build(executor, [OVERVIEW_PLAN])

    outcome = orchestrator.handle(
        user_turn(f"Tổng quan {TV1} tháng 8", TV1, history=HistoryWindow())
    )

    assert outcome.decision is Decision.RUN
    assert outcome.dropped_entities == ()
    assert not any(n.kind is Reason.PARTIAL_REFUSAL for n in outcome.notices)


def test_admin_keeps_inherited_units():
    query = understand(
        "còn tháng 7 thì sao",
        history=HistoryWindow(),
        previous=SessionSlots(entities={"unit": (unit(TV2),)}),
        scope_units=None,
    )
    assert query.dropped_slot_units == ()
