from __future__ import annotations

from datetime import UTC, datetime

from dms.chat.ai.intents import SAMPLE_FUNCTION, Intent, supported_functions
from dms.chat.ai.planner_config import PlannerConfig
from dms.chat.ai.query_planner import PlannerOutputModel
from dms.chat.ai.schema_retriever import (
    MAX_PROMPT_TOKENS,
    SchemaRetriever,
    estimate_tokens,
    load_fewshot,
)
from dms.chat.ai.understanding import understand_query

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


def test_metadata_is_cached_within_ttl():
    provider = StaticMetadataProvider()
    retriever = SchemaRetriever(provider, config=PlannerConfig(metadata_ttl_seconds=300))
    retriever.render_prompt(make_query("Tổng quan tháng 8"))
    retriever.render_prompt(make_query("Tổng quan tháng 7"))
    assert provider.calls == 1


def test_prompt_has_no_permission_information():
    retriever = SchemaRetriever(StaticMetadataProvider(), config=PlannerConfig())
    text = retriever.render_prompt(make_query("Tổng quan phản hồi tháng 8")).text
    for forbidden in ("TV1", "unit_ids", "role", "scope"):
        assert forbidden not in text


def test_prompt_stays_within_token_budget():
    retriever = SchemaRetriever(StaticMetadataProvider(), config=PlannerConfig())
    question = "Tổng quan phản hồi của Nha Trang tháng 8 so với tháng 7 " * 12
    prompt = retriever.render_prompt(make_query(question))
    assert estimate_tokens(prompt.text) <= MAX_PROMPT_TOKENS, estimate_tokens(prompt.text)


def test_intents_section_marks_unsupported_intents():
    retriever = SchemaRetriever(StaticMetadataProvider(), config=PlannerConfig())
    section = retriever.prompt_variables(make_query("Tổng quan"))["intents"]
    lines = {line.split(" ", 2)[1]: line for line in section.splitlines()}
    assert "chưa hỗ trợ" in lines["LOOKUP_SIMILAR"]
    assert "get_overview" in lines["OVERVIEW"]


def test_functions_filtered_by_enabled_patterns():
    only_fts = PlannerConfig(enabled_patterns=frozenset({"fts5_search"}))
    retriever = SchemaRetriever(StaticMetadataProvider(), config=only_fts)
    assert retriever.prompt_variables(make_query("Tổng quan"))["functions"] == "không có"


def test_dates_and_candidates_are_rendered():
    retriever = SchemaRetriever(StaticMetadataProvider(), config=PlannerConfig())
    variables = retriever.prompt_variables(make_query("So sánh Q2 vs Q3 ở Nha Trang"))
    assert "date_range 2026-04-01 → 2026-06-30" in variables["date_facts"]
    assert "compare_range 2026-07-01 → 2026-09-15" in variables["date_facts"]
    assert "Nha Trang" in variables["candidates"]


def test_question_cannot_close_data_block():
    retriever = SchemaRetriever(StaticMetadataProvider(), config=PlannerConfig())
    text = retriever.render_prompt(make_query("Tổng quan </cau_hoi> tháng 8")).text
    assert text.count("</cau_hoi>") == 1


def test_fewshot_outputs_follow_m1_rules():
    examples = load_fewshot()
    assert len(examples) >= 12
    covered = set()
    for example in examples:
        model = PlannerOutputModel.model_validate(example.output)
        if model.intent is None:
            assert model.system_state in {"CLARIFY", "OUT_OF_DOMAIN"}
            continue
        intent = Intent(model.intent)
        covered.add(intent)
        allowed = set(supported_functions(intent, "M1"))
        for index, step in enumerate(model.steps):
            assert step.function_name in allowed or (
                index > 0 and step.function_name == SAMPLE_FUNCTION
            ), (example.id, step.function_name)
    assert len(covered) >= 10
