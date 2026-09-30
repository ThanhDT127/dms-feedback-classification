"""Đánh giá Pattern 2 theo kết quả thực thi (design b09 D10).

Mỗi ca: ``analysis_request`` → SqlGenerator (LLM) → SQL Guard → vòng sửa → chạy trên DB fixture →
so kết quả với ``expected_rows`` của SQL tham chiếu (so theo tập giá trị, bỏ thứ tự và tên cột).
Hàm chạy SQL được truyền vào để test offline và script dùng chung.
"""

from __future__ import annotations

import json
import statistics
import time
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from ...settings import SERVICE_DIR
from ..guardrails.sql_guard import SqlGuard, SqlGuardError, context_for
from .sql_generator import CALL_TYPE_SQL, CALL_TYPE_SQL_REPAIR, SqlGenerator, SqlGeneratorConfig
from .types import LLMClient, LLMResult, TextStream

DEFAULT_CASES_PATH = SERVICE_DIR / "tests" / "chat" / "golden" / "sql_cases.jsonl"
MIN_EXECUTION_ACCURACY = 0.75

RunSql = Callable[[str], tuple[str, list[dict[str, Any]], str]]  # (status, rows, error_message)


def load_cases(path: Path = DEFAULT_CASES_PATH) -> list[dict[str, Any]]:
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def normalize_rows(rows: Sequence[Sequence[Any] | Mapping[str, Any]]) -> Counter[tuple[str, ...]]:
    def cell(value: Any) -> str:
        if isinstance(value, float):
            return f"{value:.1f}" if not value.is_integer() else str(int(value))
        return "" if value is None else str(value)

    counter: Counter[tuple[str, ...]] = Counter()
    for row in rows:
        values = row.values() if isinstance(row, Mapping) else row
        counter[tuple(sorted(cell(v) for v in values))] += 1
    return counter


class _CountingLLM:
    def __init__(self, inner: LLMClient) -> None:
        self.inner = inner
        self.tokens: Counter[str] = Counter()
        self.calls: Counter[str] = Counter()
        self.failed = 0

    def generate_json(
        self, prompt: str, *, call_type: str, system_instruction: str | None = None
    ) -> LLMResult:
        try:
            extra = {} if system_instruction is None else {"system_instruction": system_instruction}
            result = self.inner.generate_json(prompt, call_type=call_type, **extra)
        except Exception:
            self.failed += 1
            raise
        self.calls[call_type] += 1
        self.tokens[call_type] += int((result.usage or {}).get("total_tokens", 0))
        return result

    def stream(self, prompt: str, **kwargs: Any) -> TextStream:  # pragma: no cover - không dùng
        return self.inner.stream(prompt, **kwargs)


def evaluate(
    cases: Sequence[Mapping[str, Any]],
    *,
    llm: LLMClient,
    run_sql: RunSql,
    metadata_values: Mapping[str, Sequence[str]],
    max_repair: int = 2,
    guard: SqlGuard | None = None,
    config: SqlGeneratorConfig | None = None,
    pause_seconds: float = 0.0,
) -> dict[str, Any]:
    guard = guard or SqlGuard()
    counting = _CountingLLM(llm)
    generator = SqlGenerator(counting, config=config)
    per_case: list[dict[str, Any]] = []
    guard_codes: Counter[str] = Counter()
    for number, case in enumerate(cases):
        if pause_seconds and number:
            time.sleep(pause_seconds)
        request = case["analysis_request"]
        filters = dict(request.get("filters") or {})
        context = context_for(filters, scope_units=None, metadata_values=metadata_values)
        failed_before = counting.failed
        outcome, repairs, sql = "error", 0, ""
        try:
            draft = generator.generate(request, filters, question=case["question"])
            while True:
                try:
                    guarded = guard.check(draft.sql, context)
                except SqlGuardError as error:
                    guard_codes[error.code.value] += 1
                    if not error.repairable or repairs >= max_repair:
                        outcome = f"guard:{error.code.value}"
                        break
                    repairs += 1
                    draft = generator.repair(
                        draft, error_code=error.code.value, error_detail=error.detail
                    )
                    continue
                sql = guarded.sql
                status, rows, message = run_sql(guarded.sql)
                if status == "error":
                    guard_codes["SQL_INVALID"] += 1
                    if repairs >= max_repair:
                        outcome = "sql_error"
                        break
                    repairs += 1
                    draft = generator.repair(draft, error_code="SQL_INVALID", error_detail=message)
                    continue
                expected = normalize_rows(case["expected_rows"])
                outcome = "match" if normalize_rows(rows) == expected else "mismatch"
                break
        except Exception as exc:  # LLM lỗi/429: ghi nhận, không dừng bộ đánh giá
            outcome = f"exception:{type(exc).__name__}"
        per_case.append(
            {
                "id": case["id"],
                "outcome": outcome,
                "repairs": repairs,
                "llm_unavailable": counting.failed > failed_before,
                "sql": sql[:500],
            }
        )

    matches = sum(item["outcome"] == "match" for item in per_case)
    unavailable = [item["id"] for item in per_case if item["llm_unavailable"]]
    return {
        "cases": len(per_case),
        "execution_accuracy": round(matches / len(per_case), 4) if per_case else 0.0,
        "avg_repairs": round(statistics.mean(i["repairs"] for i in per_case), 3)
        if per_case
        else 0.0,
        "guard_codes": dict(guard_codes),
        "tokens": {k: counting.tokens[k] for k in (CALL_TYPE_SQL, CALL_TYPE_SQL_REPAIR)},
        "calls": {k: counting.calls[k] for k in (CALL_TYPE_SQL, CALL_TYPE_SQL_REPAIR)},
        "unavailable_cases": unavailable,
        "valid_run": not unavailable,
        "accuracy_ok": matches / max(len(per_case), 1) >= MIN_EXECUTION_ACCURACY
        and not unavailable,
        "failures": [item for item in per_case if item["outcome"] != "match"],
    }
