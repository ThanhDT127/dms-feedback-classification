"""Đánh giá truy hồi FTS trên DB fixture (spec ``chat-feedback-lookup``, b08 task 6.1–6.2).

Mỗi ca đi đúng đường của hệ thống: kiểm chéo từ khoá → token → các lần thử nới → executor.
Ngưỡng M3: recall@10 ≥ 0.80 và 0 lỗi cú pháp FTS. Không dùng ``MockQueryExecutor``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dms.chat.ai.fts_query_builder import FtsQueryBuilder
from dms.chat.contract import AnswerShape, QueryPattern, QueryPlan, UserScope

from .ai_fakes import SqliteFtsFakeExecutor

CASES_PATH = Path(__file__).resolve().parent / "golden" / "fts_cases.jsonl"
CASES = [
    json.loads(line) for line in CASES_PATH.read_text(encoding="utf-8").splitlines() if line.strip()
]
ADMIN = UserScope(username="admin", role="admin", display_name="Admin", unit_ids=[])
MIN_RECALL_AT_10 = 0.80
MAX_RELAX = 2


def search(
    case: dict, executor: SqliteFtsFakeExecutor, builder: FtsQueryBuilder
) -> tuple[list[int], int]:
    spec = builder.build(case["search_terms"], case["question"], filters=case["filters"])
    calls = 0
    for attempt in builder.attempts(spec.tokens, max_relax=MAX_RELAX):
        calls += 1
        plan = QueryPlan(
            pattern=QueryPattern.FTS5_SEARCH,
            answer_shape=AnswerShape.LIST,
            original_query=case["question"],
            fts_query=attempt.fts_query,
            fts_filters=dict(spec.filters),
            fts_limit=spec.limit,
        )
        result = executor.execute(plan, ADMIN)
        if result.data:
            return [row["feedback_id"] for row in result.data], calls
    return [], calls


def evaluate(executor: SqliteFtsFakeExecutor) -> dict:
    builder = FtsQueryBuilder(stats=executor.document_frequencies())
    per_case = []
    for case in CASES:
        found, calls = search(case, executor, builder)
        expected = set(case["expected_feedback_ids"])
        top = set(found[:10])
        recall = len(expected & top) / min(len(expected), 10)
        per_case.append(
            {"id": case["id"], "recall": round(recall, 3), "calls": calls, "tags": case["tags"]}
        )
    recall_at_10 = sum(item["recall"] for item in per_case) / len(per_case)
    return {
        "cases": len(per_case),
        "recall_at_10": round(recall_at_10, 4),
        "syntax_errors": len(executor.syntax_errors),
        "misses": [item for item in per_case if item["recall"] < 1],
    }


def test_golden_set_shape():
    assert len(CASES) >= 40
    n = len(CASES)
    assert sum("no_diacritics" in c["tags"] for c in CASES) / n >= 0.25
    assert sum("d_letter" in c["tags"] for c in CASES) / n >= 0.10
    assert sum("special" in c["tags"] for c in CASES) / n >= 0.10
    assert any("relax" in c["tags"] for c in CASES)
    assert all(c["expected_feedback_ids"] for c in CASES)
    assert len({c["id"] for c in CASES}) == n


def test_recall_at_10_meets_m3_threshold_with_zero_syntax_errors():
    report = evaluate(SqliteFtsFakeExecutor())
    assert report["syntax_errors"] == 0, report
    assert report["recall_at_10"] >= MIN_RECALL_AT_10, report


@pytest.mark.skip(
    reason="Chờ Dev A merge R08 + áp fts_filters trong SecureQueryExecutor (b08 task 6.2)"
)
def test_recall_with_secure_query_executor():  # pragma: no cover
    raise AssertionError
