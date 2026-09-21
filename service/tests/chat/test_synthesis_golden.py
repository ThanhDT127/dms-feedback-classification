"""Task 6.3: quét output cuối của mọi ca golden — không lọt chữ số ngoài fact và câu hỏi."""

from __future__ import annotations

import re

import pytest

from dms.chat.ai.answer_events import EventType, ListSink
from dms.chat.ai.block_builders import build_blocks
from dms.chat.ai.fact_sheet import build_fact_sheet, number_tokens
from dms.chat.ai.response_shaper import AnswerComposer
from dms.chat.ai.synthesis_eval import evaluate, load_cases, outcome_from_case

from .ai_fakes import ScriptedLLM

CASES = load_cases()
_NUMBER_RUN = re.compile(r"\d[\d.,]*")


def test_golden_set_has_about_thirty_cases_covering_block_kinds():
    assert 25 <= len(CASES) <= 40
    assert len({case["function_name"] for case in CASES}) >= 8


@pytest.mark.parametrize("case", CASES, ids=[case["id"] for case in CASES])
def test_commentary_never_contains_ungrounded_digits(case):
    outcome = outcome_from_case(case)
    llm = ScriptedLLM(stream_chunks=[case["fake_stream"]])
    sink = ListSink()

    AnswerComposer(llm=llm).compose(outcome, sink)

    blocks = build_blocks(outcome.step_results)
    sheet = build_fact_sheet(blocks, **case["params"])
    allowed = set(number_tokens(case["question"]))
    for fact in sheet:
        allowed |= fact.tokens
    for name in sheet.entity_names():
        allowed |= number_tokens(name)

    for event in sink.of_type(EventType.COMMENTARY):
        digits = {run.rstrip(".,") for run in _NUMBER_RUN.findall(event.data["text"])}
        assert digits <= allowed, f"{case['id']}: lọt số {digits - allowed}"
        assert "get_" not in event.data["text"]
        assert "feedback_records" not in event.data["text"]

    # Mỗi ca có ít nhất một câu đối kháng nên phải có câu bị loại.
    assert sink.done.data["dropped_sentences"] >= 1


def test_offline_evaluation_report_shape():
    class GoldenLLM(ScriptedLLM):
        def __init__(self) -> None:
            super().__init__(stream_chunks=[case["fake_stream"] for case in CASES])

    report = evaluate(CASES, llm=GoldenLLM())

    assert report["cases"] == len(CASES)
    assert report["sentences_dropped"] >= len(CASES)
    assert set(report["drops_by_reason"]) <= {
        "UNKNOWN_FACT",
        "UNGROUNDED_NUMBER",
        "UNGROUNDED_CODE",
        "LEAK",
        "BAD_LENGTH",
    }
    assert report["first_chunk_ms"]["p50"] is not None
    assert report["valid_run"] is True
    assert report["unavailable_cases"] == []
    assert len(report["per_case"]) == len(CASES)


def test_run_with_unavailable_commentary_is_not_ok():
    class FailingLLM(ScriptedLLM):
        def __init__(self) -> None:
            super().__init__(stream_chunks=[[RuntimeError("429 RESOURCE_EXHAUSTED")]] * 2)

    report = evaluate(CASES[:2], llm=FailingLLM())

    assert report["valid_run"] is False
    assert report["drop_rate_ok"] is False
    assert len(report["unavailable_cases"]) == 2
