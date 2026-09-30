"""Spec ``chat-sql-generation`` + hiển thị Pattern 2 (b09 task 4.6, 5.1–5.3).

Planner/SqlGenerator giả (ScriptedLLM) → Plan Guard → SQL Guard → executor SQLite trên fixture.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from dms.analytics import AnalyticsFilter
from dms.chat.ai.answer_events import ListSink
from dms.chat.ai.block_builders import build_sql_block
from dms.chat.ai.orchestrator import ChatOrchestrator, OrchestratorConfig
from dms.chat.ai.planner_config import PlannerConfig
from dms.chat.ai.query_planner import QueryPlanner
from dms.chat.ai.response_shaper import AnswerComposer
from dms.chat.ai.schema_retriever import SchemaRetriever
from dms.chat.ai.sql_generator import CALL_TYPE_SQL, CALL_TYPE_SQL_REPAIR, SqlGeneratorConfig
from dms.chat.ai.types import Decision, Reason, TurnRequest
from dms.chat.contract import QueryPattern, UserScope
from dms.chat.guardrails.plan_guard import PlanGuard, PlanGuardConfig

from .ai_fakes import FixedClock, ScriptedLLM, StaticMetadataProvider
from .sql_fixture import BH, NT, TV1, TV2, SqliteSemanticExecutor, build_sql_fixture

CLOCK = FixedClock(datetime(2026, 9, 15, 3, 0, tzinfo=UTC))
V = "v_issues_current_scoped"
ON = frozenset({"sql_template", "semantic_view"})
OFF = frozenset({"sql_template"})
ADMIN = UserScope(username="admin", role="admin", display_name="Admin", unit_ids=[])


@pytest.fixture(scope="module")
def fixture(tmp_path_factory):
    db = tmp_path_factory.mktemp("sqlgen") / "fixture.db"
    service = build_sql_fixture(db)
    everything = AnalyticsFilter()
    metadata = StaticMetadataProvider(
        {
            "units": [TV1, TV2, NT, BH],
            "provinces": [],
            "districts": [],
            "products": service.issue_filter_options(everything).get("products", []),
            "statuses": ["Chờ xử lý", "Đã xử lý"],
        }
    )
    return db, metadata


def products_plan(params):
    return json.dumps(
        {
            "intent": "DRILL_PRODUCT",
            "answer_shape": "table",
            "confidence": 0.9,
            "steps": [
                {"pattern": "sql_template", "function_name": "get_products", "params": params}
            ],
        }
    )


def semantic_plan_json(filters=None, dims=("product",)):
    return json.dumps(
        {
            "intent": "DRILL_PRODUCT",
            "answer_shape": "table",
            "confidence": 0.9,
            "steps": [
                {
                    "pattern": "semantic_view",
                    "analysis_request": {
                        "goal_vi": "Xếp hạng sản phẩm",
                        "measures": ["so_van_de"],
                        "dimensions": list(dims),
                        "filters": filters or {},
                    },
                }
            ],
        }
    )


def sql_json(sql):
    return json.dumps(
        {"sql": sql, "output_columns": [{"alias": "so_van_de", "meaning_vi": "Số vấn đề"}]}
    )


GOOD_SQL = (
    f"SELECT product, COUNT(DISTINCT issue_code) AS so_van_de FROM {V} "
    "WHERE sentiment = 'Tiêu cực' AND issue_date BETWEEN '2026-08-01' AND '2026-08-31' "
    "GROUP BY product ORDER BY so_van_de DESC LIMIT 5"
)

ALL_PRODUCTS_SQL = f"SELECT product, COUNT(DISTINCT issue_code) AS so_van_de FROM {V} GROUP BY product ORDER BY so_van_de DESC"


def build(
    fixture, responses, *, patterns=ON, max_repair=2, top_k=4, stream=("Kết quả đã hiển thị.",)
):
    db, metadata = fixture
    llm = ScriptedLLM(list(responses), stream_chunks=[list(stream)])
    planner_config = PlannerConfig(enabled_patterns=patterns, milestone="M4")
    orchestrator = ChatOrchestrator(
        llm=llm,
        planner=QueryPlanner(
            llm, SchemaRetriever(metadata, config=planner_config), config=planner_config
        ),
        plan_guard=PlanGuard(
            metadata, config=PlanGuardConfig(enabled_patterns=patterns, milestone="M4")
        ),
        executor=(executor := SqliteSemanticExecutor(db)),
        metadata=metadata,
        config=OrchestratorConfig(sql_max_repair=max_repair),
        clock=CLOCK,
        sql_generator_config=SqlGeneratorConfig(examples_top_k=top_k),
    )
    return orchestrator, llm, executor


def turn(question, *units):
    scope = (
        UserScope(username="nv", role="user", display_name="NV", unit_ids=list(units))
        if units
        else ADMIN
    )
    return TurnRequest(question=question, scope=scope, session_id="s1")


def llm_call_types(llm):
    return [c for c, _ in llm.calls if c in (CALL_TYPE_SQL, CALL_TYPE_SQL_REPAIR)]


QUESTION = "top 5 sản phẩm bị phản hồi tiêu cực tháng 8"


# ── Chỉ dùng Pattern 2 khi đã bật ──


def test_unsupported_filter_falls_back_to_one_semantic_step(fixture):
    orchestrator, llm, executor = build(
        fixture, [products_plan({"sentiment": "tiêu cực"}), sql_json(GOOD_SQL)]
    )
    outcome = orchestrator.handle(turn(QUESTION))
    steps = outcome.validated_plan.steps
    assert [s.pattern for s in steps] == [QueryPattern.SEMANTIC_VIEW]
    assert steps[0].params["sentiment"] == "Tiêu cực"
    assert outcome.decision is Decision.RUN and outcome.step_results[0].status.ok
    assert len(executor.calls) == 1


def test_fallback_disabled_is_not_supported(fixture):
    orchestrator, llm, executor = build(
        fixture, [products_plan({"sentiment": "tiêu cực"})], patterns=OFF
    )
    outcome = orchestrator.handle(turn(QUESTION))
    assert (
        outcome.decision is Decision.NOT_SUPPORTED and outcome.reason is Reason.FILTER_NOT_SUPPORTED
    )
    assert executor.calls == [] and llm_call_types(llm) == []


def test_province_filter_still_not_supported_with_pattern_2(fixture):
    orchestrator, _, executor = build(
        fixture, [products_plan({"province": "Hà Nội", "sentiment": "tiêu cực"})]
    )
    outcome = orchestrator.handle(turn("sản phẩm bị phản hồi tiêu cực ở tỉnh Hà Nội tháng 8"))
    assert outcome.decision is Decision.NOT_SUPPORTED and executor.calls == []


# ── Sinh SQL ──


def test_prompt_lists_allowed_values_but_not_user_scope(fixture):
    orchestrator, llm, _ = build(
        fixture,
        [
            semantic_plan_json({"unit_name": "Nha Trang"}),
            sql_json(
                GOOD_SQL.replace("sentiment = 'Tiêu cực' AND ", "unit_name = 'Nha Trang' AND ")
            ),
        ],
        top_k=0,
    )
    orchestrator.handle(turn("sản phẩm của Nha Trang tháng 8", NT, TV2))
    call_index = next(i for i, (c, _) in enumerate(llm.calls) if c == CALL_TYPE_SQL)
    prompt = llm.calls[call_index][1]
    assert "Nha Trang" in prompt and "2026-08-01" in prompt and "2026-08-31" in prompt
    assert TV2 not in prompt and "unit_ids" not in prompt
    system = llm.system_instructions[call_index]
    assert system and "v_issues_current_scoped" in system and "feedback_records" not in system


def test_planner_never_writes_sql_itself(fixture):
    orchestrator, _, executor = build(
        fixture, [semantic_plan_json({"sentiment": "Tiêu cực"}), sql_json(GOOD_SQL)]
    )
    outcome = orchestrator.handle(turn(QUESTION))
    assert outcome.validated_plan.steps[0].sql == ""
    assert executor.calls[0].sql.startswith(
        "SELECT product, COUNT(DISTINCT issue_code) AS so_van_de"
    )


# ── Vòng sửa lỗi ──


def test_repair_once_then_success(fixture):
    bad = f"SELECT status, COUNT(DISTINCT issue_code) AS so_van_de FROM {V} GROUP BY status"
    good = f"SELECT business_status, COUNT(DISTINCT issue_code) AS so_van_de FROM {V} GROUP BY business_status"
    orchestrator, llm, executor = build(
        fixture, [semantic_plan_json(dims=("business_status",)), sql_json(bad), sql_json(good)]
    )
    outcome = orchestrator.handle(turn("vấn đề theo trạng thái"))
    assert len(executor.calls) == 1 and "business_status" in executor.calls[0].sql
    assert outcome.step_results[0].status.ok and outcome.step_results[0].sql_repairs == 1
    assert llm_call_types(llm) == [CALL_TYPE_SQL, CALL_TYPE_SQL_REPAIR]
    repair_prompt = [p for c, p in llm.calls if c == CALL_TYPE_SQL_REPAIR][0]
    assert "SQL_COLUMN_UNKNOWN" in repair_prompt and "status" in repair_prompt


def test_runtime_sql_error_is_repaired_without_result_data(fixture):
    bad = f"SELECT product, COUNT(DISTINCT issue_code) AS so_van_de FROM {V} WHERE COUNT(*) > 1 GROUP BY product"
    orchestrator, llm, executor = build(
        fixture, [semantic_plan_json(), sql_json(bad), sql_json(ALL_PRODUCTS_SQL)]
    )
    outcome = orchestrator.handle(turn("sản phẩm nào nhiều vấn đề"))
    assert len(executor.calls) == 2 and outcome.step_results[0].status.ok
    repair_prompt = [p for c, p in llm.calls if c == CALL_TYPE_SQL_REPAIR][0]
    assert "SQL_INVALID" in repair_prompt and "Đèn" not in repair_prompt.split("<sql_truoc>")[0]


def test_unit_out_of_scope_is_refused_without_repair(fixture):
    bad = f"SELECT product, COUNT(DISTINCT issue_code) AS so_van_de FROM {V} WHERE unit_name = 'Nha Trang' GROUP BY product"
    orchestrator, llm, executor = build(fixture, [semantic_plan_json(), sql_json(bad)])
    outcome = orchestrator.handle(turn("sản phẩm nào nhiều vấn đề", TV1))
    assert CALL_TYPE_SQL_REPAIR not in llm_call_types(llm)
    assert outcome.decision is Decision.REFUSE and outcome.reason is Reason.UNAUTHORIZED_SCOPE
    assert executor.calls == []


def test_forbidden_table_stops_as_generation_failed(fixture):
    bad = "SELECT content FROM feedback_records"
    orchestrator, llm, executor = build(fixture, [semantic_plan_json(), sql_json(bad)])
    outcome = orchestrator.handle(turn("sản phẩm nào nhiều vấn đề"))
    assert (
        outcome.decision is Decision.NOT_SUPPORTED
        and outcome.reason is Reason.SQL_GENERATION_FAILED
    )
    assert CALL_TYPE_SQL_REPAIR not in llm_call_types(llm) and executor.calls == []


def test_repairs_exhausted(fixture):
    bad = f"SELECT status AS so_luong FROM {V}"
    orchestrator, llm, executor = build(
        fixture, [semantic_plan_json(), sql_json(bad), sql_json(bad), sql_json(bad)], max_repair=2
    )
    outcome = orchestrator.handle(turn("sản phẩm nào nhiều vấn đề"))
    assert (
        outcome.decision is Decision.NOT_SUPPORTED
        and outcome.reason is Reason.SQL_GENERATION_FAILED
    )
    assert executor.calls == []
    assert llm_call_types(llm) == [CALL_TYPE_SQL, CALL_TYPE_SQL_REPAIR, CALL_TYPE_SQL_REPAIR]


def test_non_json_answer_is_repaired(fixture):
    orchestrator, llm, _ = build(
        fixture, [semantic_plan_json(), "xin lỗi tôi không biết", sql_json(ALL_PRODUCTS_SQL)]
    )
    outcome = orchestrator.handle(turn("sản phẩm nào nhiều vấn đề"))
    assert outcome.step_results[0].status.ok
    assert llm_call_types(llm) == [CALL_TYPE_SQL, CALL_TYPE_SQL_REPAIR]


# ── Kết quả rỗng ──


def test_empty_result_is_no_data_without_repair(fixture):
    empty = f"SELECT product, COUNT(DISTINCT issue_code) AS so_van_de FROM {V} WHERE product LIKE '%XYZ%' GROUP BY product"
    orchestrator, llm, _ = build(
        fixture, [semantic_plan_json({"sentiment": "Tiêu cực"}), sql_json(empty)]
    )
    outcome = orchestrator.handle(turn(QUESTION))
    assert CALL_TYPE_SQL_REPAIR not in llm_call_types(llm)
    assert outcome.reason is Reason.NO_DATA_FILTERED
    sink = ListSink()
    AnswerComposer(llm=llm).compose(outcome, sink)
    assert sink.done.data["status"] == "no_data"
    assert "cảm xúc Tiêu cực" in sink.to_list()[0]["data"]["text"]


# ── Hiển thị ──


def test_ranking_block_title_and_no_sql_in_events(fixture):
    orchestrator, llm, _ = build(
        fixture, [semantic_plan_json({"sentiment": "Tiêu cực"}), sql_json(GOOD_SQL)]
    )
    outcome = orchestrator.handle(turn(QUESTION))
    sink = ListSink()
    AnswerComposer(llm=llm).compose(outcome, sink)
    events = sink.to_list()
    block = next(e["data"] for e in events if e["type"] == "data_block")
    rows = outcome.step_results[0].result.data
    assert block["kind"] == "ranking" and len(block["payload"]["items"]) == len(rows) <= 5
    assert "Số vấn đề" in block["title"] and block["payload"]["value_title"] == "Số vấn đề"
    assert "SELECT" not in json.dumps(events, ensure_ascii=False).upper().replace("SELECTED", "")
    synthesis = [p for c, p in llm.calls if c == "chat_synthesis"]
    if synthesis:
        assert "truy vấn tự động" in synthesis[0] and "sql.r1." in synthesis[0]


def test_block_shapes():
    kpi = build_sql_block([{"so_van_de": 16}])
    assert kpi.kind == "kpi" and kpi.payload["items"][0]["display"] == "16"
    series = build_sql_block(
        [{"thang": "2026-07", "so_van_de": 3}, {"thang": "2026-08", "so_van_de": 16}]
    )
    assert series.kind == "timeseries" and series.payload["aggregate_allowed"] is False
    table = build_sql_block([{"unit_name": TV1, "business_status": "Đã xử lý", "so_van_de": 4}])
    assert table.kind == "table"
    assert [c["header_vi"] for c in table.payload["columns"]] == [
        "Đơn vị",
        "Trạng thái xử lý",
        "Số vấn đề",
    ]
    many = build_sql_block([{"product": f"P{i}", "so_van_de": i} for i in range(25)])
    assert many.kind == "table" and many.payload["truncated"] is True
    odd = build_sql_block(
        [{"product": "A", "so_khach_moi": 2}, {"product": "B", "so_khach_moi": 1}]
    )
    assert odd.payload["value_title"] == "so khach moi"


def test_audit_and_metadata_keep_sql(fixture):
    from dms.chat.ai.types import TurnRequest as TR
    from dms.chat.ws.audit import audit_fields
    from dms.chat.ws.session_service import ChatSessionService
    from dms.chat.ws.turn_runner import TurnRecord

    orchestrator, _, _ = build(
        fixture, [semantic_plan_json({"sentiment": "Tiêu cực"}), sql_json(GOOD_SQL)]
    )
    outcome = orchestrator.handle(turn(QUESTION))
    record = TurnRecord(
        answer_id="a1",
        username="admin",
        session_id="s1",
        client_msg_id="c1",
        turn=TR(question=QUESTION, scope=ADMIN),
        outcome=outcome,
    )
    sql = audit_fields(record)["sql"][0]["sql"]
    assert sql.startswith("SELECT product") and len(sql) <= 2000
    metadata = ChatSessionService(store=None).build_metadata(record)  # type: ignore[arg-type]
    assert metadata["plans"][0]["sql"] == sql
    assert all("SELECT" not in json.dumps(e) for e in metadata["events"])


def test_planner_prompt_v3_only_when_semantic_enabled(fixture):
    _, metadata = fixture
    on = SchemaRetriever(metadata, config=PlannerConfig(enabled_patterns=ON, milestone="M4"))
    off = SchemaRetriever(metadata, config=PlannerConfig(enabled_patterns=ON, milestone="M3"))
    assert on.prompt_name == "planner_v3" and off.prompt_name == "planner_v1"
