"""Golden ``sql_cases.jsonl`` và bộ đánh giá Pattern 2 (b09 task 7.1–7.2)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from dms.chat.ai.sql_eval import load_cases, normalize_rows
from dms.chat.ai.sql_example_retriever import load_examples
from dms.chat.ai.types import LLMResult
from dms.chat.contract import UserScope
from dms.chat.guardrails.sql_guard import SqlGuard, context_for

from .sql_fixture import BH, NT, TV1, TV2, build_sql_fixture, run_scoped_sql

CASES = load_cases()
ADMIN = UserScope(username="a", role="admin", display_name="a", unit_ids=[])
SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "eval_chat_sql.py"


@pytest.fixture(scope="module")
def db(tmp_path_factory):
    path = tmp_path_factory.mktemp("sqleval") / "fixture.db"
    build_sql_fixture(path)
    return path


def test_golden_has_fifty_cases_disjoint_from_examples():
    assert len(CASES) >= 50
    assert not {c["question"] for c in CASES} & {e.question for e in load_examples()}


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_reference_sql_passes_guard_and_matches_expected_rows(db, case):
    filters = case["analysis_request"].get("filters") or {}
    ctx = context_for(filters, scope_units=None, metadata_values={"units": [TV1, TV2, NT, BH]})
    guarded = SqlGuard().check(case["reference_sql"], ctx)
    result = run_scoped_sql(db, guarded.sql, ADMIN)
    assert normalize_rows(result.data or []) == normalize_rows(case["expected_rows"])


class OracleSqlLLM:
    """Trả SQL tham chiếu của đúng ca (nhận diện qua câu hỏi trong prompt)."""

    def __init__(self, cases, *, broken_ids=()):
        self.cases = cases
        self.broken_ids = set(broken_ids)
        self.seen: dict[str, int] = {}

    def generate_json(self, prompt, *, call_type, system_instruction=None):
        case = next(c for c in self.cases if f"<cau_hoi>{c['question']}</cau_hoi>" in prompt)
        count = self.seen.get(case["id"], 0)
        self.seen[case["id"]] = count + 1
        sql = case["reference_sql"]
        if case["id"] in self.broken_ids and count == 0:
            sql = sql.replace("COUNT(DISTINCT issue_code) AS so_van_de", "COUNT(*) AS so_van_de")
        return LLMResult(
            text=json.dumps({"sql": sql, "output_columns": []}), usage={"total_tokens": 7}
        )


def test_oracle_run_reaches_full_accuracy_with_one_repair(db):
    sys.path.insert(0, str(SCRIPT.parent))
    import eval_chat_sql

    out = db.parent / "report.json"
    broken = [CASES[0]["id"]]
    code = eval_chat_sql.main(["--out", str(out)], llm=OracleSqlLLM(CASES, broken_ids=broken))
    report = json.loads(out.read_text(encoding="utf-8"))
    assert code == 0
    assert report["execution_accuracy"] == 1.0 and report["valid_run"] is True
    assert report["guard_codes"] == {"SQL_MEASURE_MISMATCH": 1}
    assert report["calls"]["chat_sql_repair"] == 1
    assert set(report) >= {"avg_repairs", "tokens", "failures", "unavailable_cases"}
