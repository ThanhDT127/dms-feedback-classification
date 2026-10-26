from __future__ import annotations

import json
from datetime import UTC, date, datetime

from dms.chat.ai.block_builders import build_blocks
from dms.chat.ai.contextualizer import CALL_TYPE as CTX_CALL_TYPE
from dms.chat.ai.fact_sheet import build_fact_sheet
from dms.chat.ai.orchestrator import ChatOrchestrator, OrchestratorConfig
from dms.chat.ai.planner_config import PlannerConfig
from dms.chat.ai.query_planner import QueryPlanner
from dms.chat.ai.schema_retriever import SchemaRetriever
from dms.chat.ai.types import (
    DateRange,
    Decision,
    HistoryTurn,
    Reason,
    StepState,
    TurnRequest,
)
from dms.chat.ai.understanding import (
    UnderstandingConfig,
    is_self_contained_query,
    understand_query,
)
from dms.chat.contract import QueryPlan, QueryResult, UserScope
from dms.chat.guardrails.plan_guard import PlanGuard, PlanGuardConfig

from .ai_fakes import FixedClock, ScriptedLLM, StaticMetadataProvider

CLOCK = FixedClock(datetime(2026, 9, 15, 3, 0, tzinfo=UTC))
TV1 = "Truyền thống Vùng 1"


class AnalyticalFakeExecutor:
    """Fake executor mô phỏng cả bước thống kê lẫn bước lấy mẫu phản hồi get_issues."""

    def __init__(self, *, fail_sample: bool = False, timeout_sample: bool = False) -> None:
        self.calls: list[tuple[QueryPlan, UserScope]] = []
        self.fail_sample = fail_sample
        self.timeout_sample = timeout_sample

    def execute(self, plan: QueryPlan, scope: UserScope) -> QueryResult:
        self.calls.append((plan, scope))
        fn = plan.function_name

        if fn == "get_overview":
            data = [{
                "total_issues": {"available": True, "value": 500, "denominator": 500},
                "processed_issues": {"available": True, "value": 450, "denominator": 500},
            }]
            return QueryResult.ok(data, total_rows=1)

        if fn == "get_products":
            data = [{
                "items": [
                    {"rank": 1, "product": "Đèn LED Bulb", "label": "Đèn LED Bulb", "count": 120, "percent": 24.0},
                    {"rank": 2, "product": "Đèn LED Tube", "label": "Đèn LED Tube", "count": 80, "percent": 16.0},
                ],
                "total_issues": 200,
            }]
            return QueryResult.ok(data, total_rows=1)

        if fn == "get_units":
            data = [{
                "items": [
                    {"rank": 1, "unit": TV1, "label": TV1, "count": 200, "percent": 40.0},
                ],
                "total_issues": 200,
            }]
            return QueryResult.ok(data, total_rows=1)

        if fn == "get_issue_types":
            data = [{
                "items": [
                    {"rank": 1, "label": "Báo lỗi", "count": 150, "percent": 30.0},
                ],
                "total_issues": 150,
            }]
            return QueryResult.ok(data, total_rows=1)

        if fn == "get_issues":
            if self.fail_sample:
                return QueryResult.error("Lỗi cơ sở dữ liệu khi lấy mẫu phản hồi")
            if self.timeout_sample:
                import time
                time.sleep(5.0)

            quotes = [
                {
                    "issue_code": "FB-2026-10001",
                    "content": "Đèn LED Bulb bị nhấp nháy liên tục sau vài ngày lắp ráp",
                    "unit_name": TV1,
                    "issue_date": "2026-08-10",
                    "sentiment": "Tiêu cực",
                },
                {
                    "issue_code": "FB-2026-10002",
                    "content": "Bóng đèn bật không sáng, nghi bị hỏng nguồn driver",
                    "unit_name": TV1,
                    "issue_date": "2026-08-12",
                    "sentiment": "Tiêu cực",
                },
                {
                    "issue_code": "FB-2026-10003",
                    "content": "Hàng giao bị móp méo vỏ hộp và trầy xước thân đèn",
                    "unit_name": TV1,
                    "issue_date": "2026-08-15",
                    "sentiment": "Tiêu cực",
                },
            ]
            return QueryResult.ok([{"items": quotes, "total": 3}], total_rows=3)

        return QueryResult.ok([{"total": 10}], total_rows=1)


def build_orchestrator(executor, plan_json: str) -> ChatOrchestrator:
    llm = ScriptedLLM(default=plan_json)
    metadata = StaticMetadataProvider()
    planner_config = PlannerConfig()
    planner = QueryPlanner(llm, SchemaRetriever(metadata, config=planner_config))
    return ChatOrchestrator(
        llm=llm,
        planner=planner,
        plan_guard=PlanGuard(metadata, config=PlanGuardConfig()),
        executor=executor,
        metadata=metadata,
        config=OrchestratorConfig(),
        understanding=UnderstandingConfig(),
        clock=CLOCK,
    )


def make_turn(question: str) -> TurnRequest:
    scope = UserScope(username="admin", role="admin", display_name="Admin", unit_ids=[])
    return TurnRequest(question=question, scope=scope, session_id="s1")


# ── 1. Multi-step Enrichment for OVERVIEW ──


def test_enrichment_overview_generates_quotes_and_facts():
    plan_json = json.dumps({
        "intent": "OVERVIEW",
        "answer_shape": "table",
        "confidence": 0.9,
        "steps": [{"pattern": "sql_template", "function_name": "get_overview", "params": {"date_from": "2026-08-01", "date_to": "2026-08-31"}}],
    })
    executor = AnalyticalFakeExecutor()
    orchestrator = build_orchestrator(executor, plan_json)

    outcome = orchestrator.handle(make_turn("Tổng quan tháng 8"))

    assert outcome.decision is Decision.RUN
    # Phải có 2 bước: bước 1 overview, bước 2 auto-fetch get_issues
    assert len(outcome.step_results) == 2
    step1, step2 = outcome.step_results
    assert step1.function_name == "get_overview"
    assert step1.status.state is StepState.OK
    assert not step1.is_sample

    assert step2.function_name == "get_issues"
    assert step2.status.state is StepState.OK
    assert step2.is_sample is True
    # Kế thừa bộ lọc ngày
    sample_plan, _ = executor.calls[1]
    assert sample_plan.params.get("date_from") == "2026-08-01"
    assert sample_plan.params.get("date_to") == "2026-08-31"

    # Chuyển đổi thành DataBlocks và FactSheet
    blocks = build_blocks(outcome.step_results)
    assert len(blocks) == 2
    assert blocks[0].kind == "kpi"
    assert blocks[1].kind == "quote"

    sheet = build_fact_sheet(blocks)
    assert "q.1" in sheet
    assert "FB-2026-10001" in sheet.get("q.1").display
    assert "q.2" in sheet
    assert "q.3" in sheet


# ── 2. Multi-step Enrichment for DRILL_PRODUCT ──


def test_enrichment_drill_product_filters_by_top_product():
    plan_json = json.dumps({
        "intent": "DRILL_PRODUCT",
        "answer_shape": "table",
        "confidence": 0.9,
        "steps": [{"pattern": "sql_template", "function_name": "get_products", "params": {}}],
    })
    executor = AnalyticalFakeExecutor()
    orchestrator = build_orchestrator(executor, plan_json)

    outcome = orchestrator.handle(make_turn("Sản phẩm nào có nhiều phản hồi nhất?"))

    assert outcome.decision is Decision.RUN
    assert len(outcome.step_results) == 2
    step1, step2 = outcome.step_results
    assert step1.function_name == "get_products"
    assert step2.function_name == "get_issues"
    assert step2.is_sample is True

    # Bước lấy mẫu phải được tự động lọc theo sản phẩm top 1 từ kết quả bước 1
    sample_plan, _ = executor.calls[1]
    assert sample_plan.params.get("product") == "Đèn LED Bulb"


def test_enrichment_drill_unit_filters_by_top_unit():
    plan_json = json.dumps({
        "intent": "DRILL_UNIT",
        "answer_shape": "table",
        "confidence": 0.9,
        "steps": [{"pattern": "sql_template", "function_name": "get_units", "params": {}}],
    })
    executor = AnalyticalFakeExecutor()
    orchestrator = build_orchestrator(executor, plan_json)

    outcome = orchestrator.handle(make_turn("Đơn vị nào có nhiều phản hồi nhất?"))

    assert outcome.decision is Decision.RUN
    assert len(outcome.step_results) == 2
    step1, step2 = outcome.step_results
    assert step1.function_name == "get_units"
    assert step2.function_name == "get_issues"
    assert step2.is_sample is True

    sample_plan, _ = executor.calls[1]
    assert sample_plan.params.get("unit_name") == TV1


def test_enrichment_drill_issue_filters_by_top_issue():
    plan_json = json.dumps({
        "intent": "DRILL_ISSUE",
        "answer_shape": "table",
        "confidence": 0.9,
        "steps": [{"pattern": "sql_template", "function_name": "get_issue_types", "params": {}}],
    })
    executor = AnalyticalFakeExecutor()
    orchestrator = build_orchestrator(executor, plan_json)

    outcome = orchestrator.handle(make_turn("Loại lỗi nào phổ biến nhất?"))

    assert outcome.decision is Decision.RUN
    assert len(outcome.step_results) == 2
    step1, step2 = outcome.step_results
    assert step1.function_name == "get_issue_types"
    assert step2.function_name == "get_issues"
    assert step2.is_sample is True

    sample_plan, _ = executor.calls[1]
    assert sample_plan.params.get("label") == "Báo lỗi"


# ── 3. Enrichment Timeout / Error Safety ──


def test_enrichment_failure_emits_notice_without_failing_turn():
    plan_json = json.dumps({
        "intent": "OVERVIEW",
        "answer_shape": "table",
        "confidence": 0.9,
        "steps": [{"pattern": "sql_template", "function_name": "get_overview", "params": {}}],
    })
    executor = AnalyticalFakeExecutor(fail_sample=True)
    orchestrator = build_orchestrator(executor, plan_json)

    outcome = orchestrator.handle(make_turn("Tổng quan tình hình"))

    # Lượt phân tích vẫn RUN thành công, không bị lỗi
    assert outcome.decision is Decision.RUN
    assert len(outcome.step_results) == 1
    assert outcome.step_results[0].function_name == "get_overview"
    # Phát notice SAMPLE_UNAVAILABLE
    assert any(notice.kind is Reason.SAMPLE_UNAVAILABLE for notice in outcome.notices)


# ── 4. No Duplicate Sample Step If Already Planned ──


def test_no_duplicate_sample_step_when_already_in_plan():
    plan_json = json.dumps({
        "intent": "OVERVIEW",
        "answer_shape": "narrative",
        "confidence": 0.9,
        "steps": [
            {"pattern": "sql_template", "function_name": "get_overview", "params": {}},
            {"pattern": "sql_template", "function_name": "get_issues", "params": {"page_size": 2}},
        ],
    })
    executor = AnalyticalFakeExecutor()
    orchestrator = build_orchestrator(executor, plan_json)

    outcome = orchestrator.handle(make_turn("Tổng quan kèm trích dẫn"))

    assert outcome.decision is Decision.RUN
    # Không được tự sinh thêm bước thứ 3
    assert len(outcome.step_results) == 2


# ── 5. Contextualizer Optimization (R3.3) ──


def test_is_self_contained_query_classification():
    # Câu có đại từ / từ nối tham chiếu -> False
    assert is_self_contained_query("còn cái đó thì sao?") is False
    assert is_self_contained_query("sản phẩm này có bao nhiêu lỗi?") is False
    assert is_self_contained_query("thế còn Hà Nội?") is False
    assert is_self_contained_query("ở đó có bao nhiêu đơn vị?") is False
    assert is_self_contained_query("kỳ trước thì sao?") is False

    # Câu hỏi đuôi / từ nối không dấu / khẩu ngữ hội thoại -> False
    assert is_self_contained_query("Đèn LED Bulb thì sao?") is False
    assert is_self_contained_query("Đèn LED Tube thế nào?") is False
    assert is_self_contained_query("con Vung 2 thi sao") is False
    assert is_self_contained_query("the con Ha Noi") is False
    assert is_self_contained_query("o do the nao") is False

    class FakeDates:
        date_range = DateRange(date(2026, 8, 1), date(2026, 8, 31))
        compare_range = None

    class FakeNorm:
        entities = {"unit": (1,)}
        dates = FakeDates()

    # Date-only refinement không có entity -> False (không được bypass Contextualizer)
    class DateOnlyNorm:
        entities = {}
        dates = FakeDates()

    assert is_self_contained_query("Trong tháng 8?", DateOnlyNorm()) is False

    # Entity-only refinement không có question predicate -> False
    class EntityOnlyNorm:
        entities = {"product": ("Đèn LED Bulb",)}
        dates = None

    assert is_self_contained_query("Đèn LED Bulb", EntityOnlyNorm()) is False

    # Entity-only kèm question predicate -> True
    assert is_self_contained_query("Sản phẩm Đèn LED Bulb có bao nhiêu phản hồi?", EntityOnlyNorm()) is True

    # Câu độc lập có cả entity và dates -> True
    assert is_self_contained_query("Sản phẩm Đèn LED Bulb tại Vùng 1 trong tháng 8", FakeNorm()) is True


def test_contextualizer_bypassed_on_self_contained_query():
    llm = ScriptedLLM()
    history = [HistoryTurn("Tháng 8 Vùng 1 có bao nhiêu vấn đề?", "1.234 vấn đề")]

    # Câu độc lập đầy đủ thực thể và thời gian
    independent_q = "Sản phẩm Đèn LED Bulb trong tháng 8 có bao nhiêu phản hồi?"
    result = understand_query(
        independent_q,
        session_history=history,
        previous_slots=None,
        llm=llm,
        metadata=StaticMetadataProvider(),
        clock=CLOCK,
    )

    assert result.guard.allowed
    # LLM Contextualizer hoàn toàn KHÔNG bị gọi -> tiết kiệm 100% thời gian gọi mạng
    assert len(llm.calls_for(CTX_CALL_TYPE)) == 0
    assert result.is_follow_up is False
    assert result.rewritten_query == independent_q


def test_enrichment_drill_product_skips_chua_xac_dinh_bucket():
    class UnknownFirstProductExecutor(AnalyticalFakeExecutor):
        def execute(self, plan: QueryPlan, scope: UserScope) -> QueryResult:
            if plan.function_name == "get_products":
                self.calls.append((plan, scope))
                data = [{
                    "items": [
                        {"rank": 1, "label": "Chưa xác định", "count": 900, "percent": 50.0},
                        {"rank": 2, "product": "Bulb", "label": "Bulb", "count": 400, "percent": 22.0},
                    ],
                    "total_issues": 1300,
                }]
                return QueryResult.ok(data, total_rows=1)
            return super().execute(plan, scope)

    plan_json = json.dumps({
        "intent": "DRILL_PRODUCT",
        "answer_shape": "table",
        "confidence": 0.9,
        "steps": [{"pattern": "sql_template", "function_name": "get_products", "params": {}}],
    })
    executor = UnknownFirstProductExecutor()
    orchestrator = build_orchestrator(executor, plan_json)
    outcome = orchestrator.handle(make_turn("Top sản phẩm có nhiều phản hồi nhất"))
    assert len(outcome.step_results) == 2
    sample_plan, _ = executor.calls[1]
    assert sample_plan.params.get("product") == "Bulb"


def test_contextualizer_strips_trailing_garbage_json():
    from dms.chat.ai.contextualizer import Contextualizer

    noisy_json = '{"standalone_question": "Phản hồi của Vùng 1 trong tháng 7?", "is_follow_up": true}\n}": true}'
    llm = ScriptedLLM(default=noisy_json)
    ctx = Contextualizer(llm)
    res = ctx.rewrite(
        "Thế còn tháng 7 thì sao?",
        [HistoryTurn("Tháng 8 Vùng 1 có bao nhiêu vấn đề?", "1.234 vấn đề")],
    )
    assert res.failed is False
    assert res.is_follow_up is True
    assert res.rewritten_query == "Phản hồi của Vùng 1 trong tháng 7?"

