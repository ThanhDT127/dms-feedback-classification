from __future__ import annotations

import json
from pathlib import Path

from dms.chat.ai.intents import (
    INTENT_SPECS,
    REPORT_INTENTS,
    Intent,
    milestone_index,
    supported_functions,
)
from dms.chat.ai.planner_config import PlannerConfig
from dms.chat.ai.planner_eval import PlannerCase, load_cases, main
from dms.chat.ai.text_match import normalize_match_text
from dms.chat.ai.types import LLMResult

GOLDEN = Path(__file__).resolve().parent / "golden" / "planner_cases.jsonl"


def oracle_output(case: PlannerCase, milestone: str = "M1") -> dict:
    base = {"answer_shape": "table", "confidence": 0.9, "steps": [], "intent": None}
    if case.expected_state:
        return {**base, "system_state": case.expected_state}
    intent = Intent(case.expected_intent)
    if not INTENT_SPECS[intent].needs_plan:
        return {**base, "intent": intent.value, "help_topic": "usage"}
    if intent in REPORT_INTENTS:
        return {
            **base,
            "intent": intent.value,
            "report_sections": list(case.expected_sections),
        }
    if case.expected_pattern == "fts5_search":
        step = {"pattern": "fts5_search", "search_terms": list(case.expected_terms), "filters": {}}
        return {**base, "intent": intent.value, "steps": [step]}
    functions = supported_functions(intent, "M1")
    fts_at = INTENT_SPECS[intent].fts_enabled_at
    if not functions and fts_at and milestone_index(milestone) >= milestone_index(fts_at):
        step = {"pattern": "fts5_search", "search_terms": [], "filters": {}}
        return {**base, "intent": intent.value, "steps": [step]}
    if not functions:
        return {**base, "intent": intent.value}
    step = {
        "pattern": "sql_template",
        "function_name": case.expected_function or functions[0],
        "params": dict(case.expected_params_subset),
    }
    return {**base, "intent": intent.value, "steps": [step]}


def question_in_prompt(prompt: str) -> str:
    start = prompt.rfind("<cau_hoi>\n") + len("<cau_hoi>\n")
    return prompt[start : prompt.find("\n</cau_hoi>", start)]


class OracleLLM:
    def __init__(self, cases: list[PlannerCase], milestone: str = "M1") -> None:
        self.by_question = {normalize_match_text(c.question): c for c in cases}
        self.milestone = milestone

    def generate_json(self, prompt: str, *, call_type: str) -> LLMResult:
        case = self.by_question[normalize_match_text(question_in_prompt(prompt))]
        return LLMResult(
            text=json.dumps(oracle_output(case, self.milestone)), usage={"total_tokens": 100}
        )


class AlwaysOverviewLLM:
    def generate_json(self, prompt: str, *, call_type: str) -> LLMResult:
        body = {
            "intent": "OVERVIEW",
            "answer_shape": "number",
            "confidence": 0.9,
            "steps": [{"pattern": "sql_template", "function_name": "get_overview", "params": {}}],
        }
        return LLMResult(text=json.dumps(body), usage={"total_tokens": 50})


def test_oracle_reaches_perfect_scores(tmp_path):
    cases = load_cases(GOLDEN)
    out = tmp_path / "report.json"
    code = main(["--out", str(out)], llm=OracleLLM(cases), config=PlannerConfig())
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["failures"] == []
    assert code == 0
    assert report["intent_accuracy"] == 1.0
    assert report["function_accuracy"] == 1.0
    assert report["params_match_rate"] == 1.0
    m1_overview = sum(
        case.expected_intent == "OVERVIEW" and case.milestone == "M1" for case in cases
    )
    assert report["confusion_matrix"]["OVERVIEW"] == {"OVERVIEW": m1_overview}
    assert report["total_tokens"] == 100 * sum(case.milestone == "M1" for case in cases)
    assert set(report["latency_ms"]) == {"p50", "p95"}


def test_below_threshold_exits_non_zero(tmp_path, capsys):
    out = tmp_path / "report.json"
    code = main(
        ["--out", str(out), "--repeat", "2"], llm=AlwaysOverviewLLM(), config=PlannerConfig()
    )
    report = json.loads(out.read_text(encoding="utf-8"))
    assert code == 1
    assert report["intent_accuracy"] < 0.85
    assert len(report["runs"]) == 2
    assert report["failures"]
    assert "DƯỚI NGƯỠNG" in capsys.readouterr().out


class QuotaExhaustedLLM:
    def generate_json(self, prompt: str, *, call_type: str) -> LLMResult:
        raise RuntimeError("429 RESOURCE_EXHAUSTED")


def test_llm_failures_make_run_invalid(tmp_path, capsys):
    cases = tmp_path / "cases.jsonl"
    cases.write_text(GOLDEN.read_text(encoding="utf-8").splitlines()[0] + "\n", encoding="utf-8")
    out = tmp_path / "report.json"

    code = main(
        [
            "--cases",
            str(cases),
            "--out",
            str(out),
            "--min-intent-accuracy",
            "0",
            "--min-function-accuracy",
            "0",
        ],
        llm=QuotaExhaustedLLM(),
        config=PlannerConfig(),
    )

    report = json.loads(out.read_text(encoding="utf-8"))
    assert code == 1
    assert report["valid_run"] is False
    assert len(report["unavailable_cases"]) == 1
    assert "không hợp lệ" in capsys.readouterr().out


def _cases_up_to(milestone: str) -> list[PlannerCase]:
    rank = milestone_index(milestone)
    return [case for case in load_cases(GOLDEN) if milestone_index(case.milestone) <= rank]


def test_oracle_reaches_perfect_scores_at_m3_with_fts(tmp_path):
    cases = load_cases(GOLDEN)
    out = tmp_path / "report.json"
    config = PlannerConfig(
        enabled_patterns=frozenset({"sql_template", "fts5_search"}), milestone="M3"
    )
    code = main(["--out", str(out)], llm=OracleLLM(cases, "M3"), config=config)
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["case_count"] == len(_cases_up_to("M3"))
    assert report["failures"] == [] and code == 0
    assert report["params_match_rate"] == 1.0


def test_oracle_reaches_perfect_scores_at_m5_with_reports(tmp_path):
    cases = load_cases(GOLDEN)
    out = tmp_path / "report.json"
    config = PlannerConfig(
        enabled_patterns=frozenset({"sql_template", "fts5_search", "semantic_view"}),
        milestone="M5",
    )
    code = main(["--out", str(out)], llm=OracleLLM(cases, "M5"), config=config)
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["case_count"] == len(cases)
    assert report["failures"] == [] and code == 0
    assert report["params_match_rate"] == 1.0


def test_golden_set_has_report_section_cases():
    report_cases = [case for case in load_cases(GOLDEN) if case.milestone == "M5"]
    assert len(report_cases) >= 12
    assert {case.expected_intent for case in report_cases} == {
        "REPORT_DAILY",
        "REPORT_WEEKLY",
        "REPORT_CUSTOM",
        "REPORT_EXPORT",
    }
