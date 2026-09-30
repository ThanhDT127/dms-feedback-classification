from __future__ import annotations

from collections import Counter
from pathlib import Path

from unidecode import unidecode

from dms.chat.ai.intents import Intent, PlannerState, supported_functions
from dms.chat.ai.planner_eval import load_cases
from dms.chat.ai.schema_retriever import load_fewshot
from dms.chat.ai.text_match import normalize_match_text

GOLDEN = Path(__file__).resolve().parent / "golden" / "planner_cases.jsonl"
CASES = load_cases(GOLDEN)


def test_ids_are_unique():
    duplicates = [key for key, count in Counter(c.id for c in CASES).items() if count > 1]
    assert not duplicates


def test_each_case_has_exactly_one_expected_label():
    for case in CASES:
        assert (case.expected_intent is None) != (case.expected_state is None), case.id
        if case.expected_intent:
            Intent(case.expected_intent)
        else:
            PlannerState(case.expected_state)


def test_coverage():
    assert len(CASES) >= 90
    per_intent = Counter(c.expected_intent for c in CASES if c.expected_intent)
    missing = {
        i.value: per_intent.get(i.value, 0) for i in Intent if per_intent.get(i.value, 0) < 5
    }
    assert not missing, f"Intent thiếu ca: {missing}"
    assert sum(1 for c in CASES if c.expected_state) >= 10
    assert sum(1 for c in CASES if "mixed_units" in c.tags) >= 3
    unaccented = sum(1 for c in CASES if unidecode(c.question) == c.question)
    assert unaccented / len(CASES) >= 0.15


def test_expected_functions_are_allowed_at_m1():
    for case in CASES:
        if case.expected_function:
            assert case.expected_intent is not None
            allowed = supported_functions(Intent(case.expected_intent), "M1")
            assert case.expected_function in allowed, case.id


def test_fewshot_does_not_overlap_evaluation_set():
    golden = {normalize_match_text(c.question): c.id for c in CASES}
    overlap = [
        (example.id, golden[normalize_match_text(example.question)])
        for example in load_fewshot()
        if normalize_match_text(example.question) in golden
    ]
    assert not overlap, f"Câu trùng giữa few-shot và tập đánh giá: {overlap}"


def test_m3_lookup_cases_use_fts_intents():
    from dms.chat.ai.intents import supports_fts

    m3 = [case for case in CASES if case.milestone == "M3"]
    assert len(m3) >= 15
    for case in m3:
        assert case.expected_pattern == "fts5_search", case.id
        assert supports_fts(Intent(case.expected_intent), "M3", {"fts5_search"}), case.id
    assert sum("no_diacritics" in case.tags for case in m3) >= 3
