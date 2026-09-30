"""Spec ``chat-fts-query-building`` + ``chat-feedback-lookup`` đầu-cuối (b08 task 2.3, 3.x, 4.x, 5.3).

Planner giả (ScriptedLLM) → Plan Guard → Orchestrator → ``SqliteFtsFakeExecutor`` (index FTS thật
theo migration của Dev A) → AnswerComposer. Không dùng ``MockQueryExecutor`` (M01).
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from dms.chat.ai.answer_events import EventType, ListSink
from dms.chat.ai.fts_query_builder import DocumentFrequencies, FtsQueryBuilder
from dms.chat.ai.orchestrator import ChatOrchestrator, OrchestratorConfig
from dms.chat.ai.planner_config import PlannerConfig
from dms.chat.ai.query_planner import CALL_TYPE_PLAN, CALL_TYPE_REPAIR, QueryPlanner
from dms.chat.ai.response_shaper import AnswerComposer
from dms.chat.ai.schema_retriever import SchemaRetriever
from dms.chat.ai.types import Decision, Intent, Reason, TurnRequest
from dms.chat.contract import UserScope
from dms.chat.guardrails.plan_guard import PlanGuard, PlanGuardConfig

from .ai_fakes import (
    FixedClock,
    ScriptedLLM,
    SqliteFtsFakeExecutor,
    StaticMetadataProvider,
    load_fts_records,
)

CLOCK = FixedClock(datetime(2026, 9, 15, 3, 0, tzinfo=UTC))
TV1, TV2, NT = "Truyền thống Vùng 1", "Truyền thống Vùng 2", "Nha Trang"
FTS_PATTERNS = frozenset({"sql_template", "fts5_search"})
ADMIN = UserScope(username="admin", role="admin", display_name="Admin", unit_ids=[])
RECORDS = {r["feedback_id"]: r for r in load_fts_records()}


def plan_json(intent="LOOKUP_FEEDBACK", terms=(), filters=None, limit=None, shape="list"):
    step = {"pattern": "fts5_search", "search_terms": list(terms), "filters": filters or {}}
    if limit is not None:
        step["limit"] = limit
    return json.dumps({"intent": intent, "answer_shape": shape, "confidence": 0.9, "steps": [step]})


def build(
    responses,
    *,
    executor=None,
    patterns=FTS_PATTERNS,
    builder=None,
    max_relax=2,
    code_lookup=False,
    stream=("Các phản hồi đều nói về đèn.",),
):
    executor = executor or SqliteFtsFakeExecutor()
    llm = ScriptedLLM(list(responses), stream_chunks=[list(stream)] * 3)
    metadata = StaticMetadataProvider()
    planner_config = PlannerConfig(enabled_patterns=patterns, milestone="M3")
    guard_config = PlanGuardConfig(
        enabled_patterns=patterns, milestone="M3", fts_max_relax=max_relax
    )
    orchestrator = ChatOrchestrator(
        llm=llm,
        planner=QueryPlanner(
            llm, SchemaRetriever(metadata, config=planner_config), config=planner_config
        ),
        plan_guard=PlanGuard(
            metadata,
            config=guard_config,
            fts_builder=builder or FtsQueryBuilder(stats=executor.document_frequencies()),
        ),
        executor=executor,
        metadata=metadata,
        config=OrchestratorConfig(fts_max_relax=max_relax, code_lookup_available=code_lookup),
        clock=CLOCK,
    )
    return orchestrator, llm, executor


def turn(question, *units, quotes=()):
    scope = (
        UserScope(username="nv", role="user", display_name="NV", unit_ids=list(units))
        if units
        else ADMIN
    )
    return TurnRequest(
        question=question, scope=scope, session_id="s1", previous_quotes=tuple(quotes)
    )


def compose(outcome, llm):
    sink = ListSink()
    AnswerComposer(llm=llm).compose(outcome, sink)
    return sink


# ── Từ khoá phải có trong câu hỏi ──


def test_invented_term_is_dropped_and_logged(caplog):
    orchestrator, _, executor = build([plan_json(terms=["chập chờn", "bảo hành"])])
    with caplog.at_level(logging.INFO, logger="dms-chat-fts"):
        outcome = orchestrator.handle(turn("tìm phản hồi đèn chập chờn"))
    assert outcome.decision is Decision.RUN
    assert executor.calls[0].fts_query == "chập chờn"
    assert any(r.getMessage() == "fts_term_not_in_question" for r in caplog.records)


# ── Token an toàn ──


def test_operator_words_do_not_break_syntax():
    question = "tìm đèn OR quạt NOT sáng"
    orchestrator, _, executor = build([plan_json(terms=["đèn OR quạt NOT sáng"])])
    orchestrator.handle(turn(question))
    for plan in executor.calls:
        assert not any(word in plan.fts_query.split() for word in ("OR", "NOT"))
    assert executor.syntax_errors == []


def test_only_stopwords_clarify_without_querying():
    orchestrator, _, executor = build([plan_json(terms=["các phản hồi"])])
    outcome = orchestrator.handle(turn("tìm các phản hồi"))
    assert outcome.decision is Decision.CLARIFY
    assert outcome.message == "Bạn muốn tìm phản hồi có nội dung gì?"
    assert executor.calls == []


# ── Nới điều kiện ──


def test_relax_one_level_drops_least_specific_term():
    executor = SqliteFtsFakeExecutor()
    stats = DocumentFrequencies(
        total_docs=100, df={"den": 60, "led": 20, "chap": 14, "chon": 14, "ban": 10, "dem": 95}
    )
    builder = FtsQueryBuilder(stats=stats, synonyms={})
    orchestrator, llm, executor = build(
        [plan_json(terms=["đèn led chập chờn ban đêm"])], executor=executor, builder=builder
    )
    outcome = orchestrator.handle(turn("tìm đèn led chập chờn ban đêm"))
    assert len(executor.calls) == 2
    relaxed = [n for n in outcome.notices if n.kind is Reason.RELAXED_SEARCH]
    assert relaxed and relaxed[0].details["dropped_terms"] == ["đêm"]
    events = compose(outcome, llm).to_list()
    refusal = next(e for e in events if e["type"] == "refusal")
    assert refusal["data"]["partial"] is True and "đêm" in refusal["data"]["text"]


def test_all_relax_levels_empty_gives_no_match():
    builder = FtsQueryBuilder(synonyms={})
    orchestrator, llm, executor = build(
        [plan_json(terms=["tủ lạnh đóng tuyết"])], builder=builder, max_relax=2
    )
    outcome = orchestrator.handle(turn("tìm tủ lạnh đóng tuyết tháng 8"))
    assert len(executor.calls) == 3
    assert outcome.reason is Reason.NO_MATCH
    sink = compose(outcome, llm)
    text = sink.of_type(EventType.COMMENTARY)[0].data["text"]
    assert "tủ, lạnh, đóng và tuyết" in text or "tủ" in text
    assert "01/08/2026" in text
    assert sink.done.data["status"] == "no_data"


# ── Plan Guard ──


def test_province_filter_is_not_supported():
    orchestrator, _, executor = build(
        [plan_json(terms=["chập chờn"], filters={"province": "Khánh Hòa"})]
    )
    outcome = orchestrator.handle(turn("tìm phản hồi chập chờn ở Khánh Hòa"))
    assert outcome.decision is Decision.NOT_SUPPORTED
    assert outcome.reason is Reason.FILTER_NOT_SUPPORTED
    assert executor.calls == []


def test_fts_step_when_pattern_disabled_goes_through_repair():
    orchestrator, llm, executor = build(
        [plan_json(terms=["chập chờn"]), plan_json(terms=["chập chờn"])],
        patterns=frozenset({"sql_template"}),
    )
    outcome = orchestrator.handle(turn("tìm phản hồi chập chờn"))
    assert [c for c, _ in llm.calls if c in (CALL_TYPE_PLAN, CALL_TYPE_REPAIR)] == [
        CALL_TYPE_PLAN,
        CALL_TYPE_REPAIR,
    ]
    assert outcome.decision is not Decision.RUN
    assert executor.calls == []


def test_multi_unit_user_scope_without_unit_filter():
    orchestrator, _, executor = build([plan_json(terms=["chập chờn"])])
    outcome = orchestrator.handle(turn("tìm phản hồi chập chờn", TV1, TV2))
    assert outcome.decision is Decision.RUN
    assert sorted(executor.scopes[0].unit_ids) == sorted([TV1, TV2])
    assert "unit_name" not in executor.calls[0].fts_filters
    assert all(r["unit_name"] in (TV1, TV2) for r in outcome.step_results[0].result.data)


def test_single_unit_user_gets_unit_filter():
    orchestrator, _, executor = build([plan_json(terms=["chập chờn"])])
    orchestrator.handle(turn("tìm phản hồi chập chờn", TV1))
    assert executor.calls[0].fts_filters.get("unit_name") == TV1


# ── Tra cứu theo nội dung ──


def test_fourteen_matches_give_kpi_and_ten_quotes():
    orchestrator, llm, executor = build([plan_json(terms=["chập chờn"])])
    outcome = orchestrator.handle(turn("tìm phản hồi chập chờn"))
    rows = outcome.step_results[0].result.data
    expected = sum("chập chờn" in r["content"].lower() for r in RECORDS.values())
    assert len(rows) == expected > 10
    events = compose(outcome, llm).to_list()
    blocks = [e["data"] for e in events if e["type"] == "data_block"]
    assert [b["kind"] for b in blocks] == ["kpi", "quote"]
    assert blocks[0]["payload"]["items"][0]["value"] == expected
    quotes = blocks[1]["payload"]["quotes"]
    assert len(quotes) == 10
    codes = {r["issue_code"] for r in rows}
    assert {q["issue_code"] for q in quotes} <= codes
    commentary = " ".join(e["data"]["text"] for e in events if e["type"] == "commentary")
    for code in [r["issue_code"] for r in RECORDS.values()]:
        if code in commentary:
            assert code in codes


def test_lookup_commentary_is_limited_to_two_sentences():
    orchestrator, llm, _ = build(
        [plan_json(terms=["chập chờn"])],
        stream=("Câu một nói về đèn. ", "Câu hai nói về đèn. ", "Câu ba nói về đèn. "),
    )
    outcome = orchestrator.handle(turn("tìm phản hồi chập chờn"))
    sink = compose(outcome, llm)
    assert len(sink.of_type(EventType.COMMENTARY)) <= 2
    prompt = [p for c, p in llm.calls if c == "chat_synthesis"][0]
    assert "Luật tra cứu" in prompt


def test_unaccented_query_with_d_finds_accented_feedback():
    question = "tim phan hoi doi tra den rang dong"
    orchestrator, _, executor = build([plan_json(terms=["doi tra den rang dong"])])
    outcome = orchestrator.handle(turn(question))
    contents = [r["content"] for r in outcome.step_results[0].result.data]
    assert "Khách muốn đổi trả đèn Rạng Đông vì không sáng" in contents
    assert executor.calls[0].fts_query == "doi tra den rang dong"  # builder không tự bỏ dấu


# ── Tương tự ──


def _previous_quotes():
    orchestrator, llm, _ = build([plan_json(terms=["chập chờn"])])
    outcome = orchestrator.handle(turn("tìm phản hồi chập chờn"))
    events = compose(outcome, llm).to_list()
    return next(e for e in events if e["type"] == "data_block" and e["data"]["kind"] == "quote")[
        "data"
    ]["payload"]["quotes"]


def test_similar_to_second_quote_excludes_it_without_llm_extraction():
    quotes = _previous_quotes()
    reference = quotes[1]
    orchestrator, llm, executor = build([plan_json(intent="LOOKUP_SIMILAR")])
    outcome = orchestrator.handle(turn("có phản hồi nào giống cái thứ 2 không", quotes=quotes))
    assert outcome.decision is Decision.RUN
    ids = {r["feedback_id"] for r in outcome.step_results[0].result.data}
    assert ids and reference["feedback_id"] not in ids
    assert {c for c, _ in llm.calls} == {CALL_TYPE_PLAN}  # không có lời gọi trích từ khoá


def test_similar_code_outside_scope_clarifies_without_leaking():
    nt_code = next(r["issue_code"] for r in RECORDS.values() if r["unit_name"] == NT)
    executor = SqliteFtsFakeExecutor(code_lookup=True)
    orchestrator, _, _ = build(
        [plan_json(intent="LOOKUP_SIMILAR")], executor=executor, code_lookup=True
    )
    outcome = orchestrator.handle(turn(f"tìm phản hồi giống mã {nt_code}", TV1))
    assert outcome.decision is Decision.CLARIFY
    assert "không có quyền" not in outcome.message and NT not in outcome.message


def test_similar_code_inside_scope_runs():
    tv1 = next(r for r in RECORDS.values() if r["unit_name"] == TV1 and "chập chờn" in r["content"])
    executor = SqliteFtsFakeExecutor(code_lookup=True)
    orchestrator, _, _ = build(
        [plan_json(intent="LOOKUP_SIMILAR")], executor=executor, code_lookup=True
    )
    outcome = orchestrator.handle(turn(f"tìm phản hồi giống mã {tv1['issue_code']}", TV1))
    assert outcome.decision is Decision.RUN
    assert tv1["feedback_id"] not in {r["feedback_id"] for r in outcome.step_results[0].result.data}


def test_similar_by_code_without_code_filter_is_not_supported():
    orchestrator, _, executor = build([plan_json(intent="LOOKUP_SIMILAR")])
    outcome = orchestrator.handle(turn("tìm phản hồi giống mã TV1-0101"))
    assert outcome.decision is Decision.NOT_SUPPORTED
    assert outcome.reason is Reason.LOOKUP_BY_CODE_UNAVAILABLE
    assert executor.calls == []


def test_similar_without_reference_clarifies():
    orchestrator, _, _ = build([plan_json(intent="LOOKUP_SIMILAR")])
    outcome = orchestrator.handle(turn("có phản hồi nào giống cái thứ 2 không"))
    assert (
        outcome.decision is Decision.CLARIFY and outcome.reason is Reason.SIMILAR_REFERENCE_UNKNOWN
    )


def test_similar_reference_from_quote_outside_current_scope_clarifies():
    quotes = [q for q in _previous_quotes() if q["unit_name"] == NT][:1]
    orchestrator, _, _ = build([plan_json(intent="LOOKUP_SIMILAR")])
    outcome = orchestrator.handle(turn("có phản hồi nào giống cái đó không", TV1, quotes=quotes))
    assert outcome.decision is Decision.CLARIFY


# ── Vị trí file ──


def test_file_lookup_with_citation_columns_gives_table():
    orchestrator, llm, _ = build(
        [plan_json(intent="LOOKUP_FILE", terms=["chập chờn"], filters={"unit_name": TV1})]
    )
    outcome = orchestrator.handle(turn("phản hồi đèn chập chờn ở TV1 nằm ở file nào"))
    events = compose(outcome, llm).to_list()
    table = next(e["data"] for e in events if e["type"] == "data_block")
    assert table["kind"] == "table"
    keys = [c["key"] for c in table["payload"]["columns"]]
    assert "source_file_name" in keys and "source_row_number" in keys
    assert table["payload"]["rows"][0]["source_file_name"].endswith(".xlsx")


def test_file_lookup_without_citation_columns_not_supported():
    executor = SqliteFtsFakeExecutor(with_citation_columns=False)
    orchestrator, _, _ = build(
        [plan_json(intent="LOOKUP_FILE", terms=["chập chờn"])], executor=executor
    )
    outcome = orchestrator.handle(turn("phản hồi đèn chập chờn nằm ở file nào"))
    assert outcome.decision is Decision.NOT_SUPPORTED
    assert outcome.reason is Reason.FILE_LOOKUP_UNAVAILABLE


def test_listing_by_file_name_not_supported():
    orchestrator, _, executor = build([plan_json(intent="LOOKUP_FILE", terms=["DMS_T8"])])
    outcome = orchestrator.handle(turn("liệt kê phản hồi trong file DMS_T8.xlsx"))
    assert outcome.decision is Decision.NOT_SUPPORTED
    assert outcome.reason is Reason.FILTER_NOT_SUPPORTED
    assert executor.calls == []


def test_intent_value_used_by_lookup():
    assert Intent.LOOKUP_SIMILAR.value == "LOOKUP_SIMILAR"
