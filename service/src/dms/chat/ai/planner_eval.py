"""Đánh giá Planner trên golden set (spec ``chat-planner-evaluation``, design b02 D9).

Logic nằm trong package để test gọi được với LLM giả; ``scripts/eval_chat_planner.py`` chỉ
gọi ``main()``.
"""

from __future__ import annotations

import argparse
import json
import math
import time
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from ...settings import SERVICE_DIR
from .intents import milestone_index
from .planner_config import PlannerConfig
from .query_planner import QueryPlanner
from .schema_retriever import SchemaRetriever
from .text_match import normalize_match_text
from .types import HistoryTurn, LLMClient, LLMResult, MetadataProvider, TextStream
from .understanding import understand_query

DEFAULT_CASES_PATH = SERVICE_DIR / "tests" / "chat" / "golden" / "planner_cases.jsonl"
DEFAULT_OUT_DIR = SERVICE_DIR / "work" / "eval"
DEFAULT_ANCHOR_DATE = "2026-09-15"  # ngày neo của expected_params_subset trong golden set

SAMPLE_METADATA: dict[str, list[str]] = {
    "units": [
        "Truyền thống Vùng 1",
        "Truyền thống Vùng 2",
        "Truyền thống Vùng 3",
        "Nha Trang",
        "Biên Hòa",
        "Hồ Chí Minh",
    ],
    "provinces": ["Hà Nội", "Hồ Chí Minh", "Khánh Hòa", "Đồng Nai", "Hà Nam", "Nam Định"],
    "districts": ["Ba Đình", "Hoàn Kiếm", "Biên Hòa"],
    "products": ["Đèn LED Bulb", "Đèn LED Tube", "Bóng đèn huỳnh quang", "Phích nước"],
    "statuses": ["Đã xử lý", "Chờ xử lý"],
}


@dataclass(frozen=True)
class PlannerCase:
    id: str
    question: str
    history: tuple[HistoryTurn, ...] = ()
    expected_intent: str | None = None
    expected_state: str | None = None
    expected_function: str | None = None
    expected_params_subset: Mapping[str, Any] = field(default_factory=dict)
    tags: tuple[str, ...] = ()
    # b08: ca chỉ chạy từ mốc này; bước kỳ vọng là pattern (vd. fts5_search) và từ khoá phải có.
    milestone: str = "M1"
    expected_pattern: str | None = None
    expected_terms: tuple[str, ...] = ()
    # b10: ca báo cáo — Planner không trả bước, chỉ trả các phần được chọn.
    expected_sections: tuple[str, ...] = ()

    @property
    def expected_label(self) -> str:
        return self.expected_intent or self.expected_state or "NONE"


def load_cases(path: Path) -> list[PlannerCase]:
    cases: list[PlannerCase] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        raw = json.loads(line)
        cases.append(
            PlannerCase(
                id=str(raw["id"]),
                question=str(raw["question"]),
                history=tuple(
                    HistoryTurn(str(turn["question"]), str(turn.get("answer", "")))
                    for turn in raw.get("history", [])
                ),
                expected_intent=raw.get("expected_intent"),
                expected_state=raw.get("expected_state"),
                expected_function=raw.get("expected_function"),
                expected_params_subset=raw.get("expected_params_subset") or {},
                tags=tuple(raw.get("tags", [])),
                milestone=str(raw.get("milestone") or "M1"),
                expected_pattern=raw.get("expected_pattern"),
                expected_terms=tuple(raw.get("expected_terms", [])),
                expected_sections=tuple(raw.get("expected_sections", [])),
            )
        )
    return cases


class StaticMetadata:
    def __init__(self, values: Mapping[str, Sequence[str]]) -> None:
        self._values = values

    def valid_values(self) -> Mapping[str, Sequence[str]]:
        return self._values


class _MeteredLLM:
    """Đo độ trễ và token của mọi lần gọi trong một ca."""

    def __init__(self, inner: LLMClient) -> None:
        self.inner = inner
        self.latency_ms = 0
        self.tokens = 0
        # Lời gọi lỗi (429, timeout...) — contextualizer nuốt lỗi nên phải đếm ở đây.
        self.failed_calls = 0

    def generate_json(
        self, prompt: str, *, call_type: str, system_instruction: str | None = None
    ) -> LLMResult:
        started = time.monotonic()
        try:
            extra = {} if system_instruction is None else {"system_instruction": system_instruction}
            result = self.inner.generate_json(prompt, call_type=call_type, **extra)
        except Exception:
            self.failed_calls += 1
            raise
        finally:
            self.latency_ms += int((time.monotonic() - started) * 1000)
        self.tokens += int(result.usage.get("total_tokens", 0))
        return result

    def stream(
        self,
        prompt: str,
        *,
        call_type: str,
        system_instruction: str | None = None,
        temperature: float | None = None,
        max_output_tokens: int | None = None,
    ) -> TextStream:
        """Bộ đánh giá chỉ dùng JSON; giữ cho đủ interface ``LLMClient`` (b04)."""
        return self.inner.stream(
            prompt,
            call_type=call_type,
            system_instruction=system_instruction,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
        )


@dataclass
class CaseResult:
    case_id: str
    run: int
    expected: str
    predicted: str
    intent_ok: bool
    function_ok: bool | None
    params_ok: bool | None
    latency_ms: int
    tokens: int
    error: str | None = None
    llm_failed_calls: int = 0
    used_semantic: bool = False


def _percentile(values: Sequence[int], pct: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    return ordered[max(0, math.ceil(pct / 100 * len(ordered)) - 1)]


def _rate(flags: Sequence[bool]) -> float | None:
    return round(sum(flags) / len(flags), 4) if flags else None


def evaluate(
    cases: Sequence[PlannerCase],
    *,
    llm: LLMClient,
    metadata: MetadataProvider,
    config: PlannerConfig,
    anchor: date,
    repeat: int = 1,
    pause_seconds: float = 0.0,
) -> dict[str, Any]:
    clock_value = datetime(anchor.year, anchor.month, anchor.day, 3, 0, tzinfo=UTC)
    rank = milestone_index(config.milestone)
    cases = [case for case in cases if milestone_index(case.milestone) <= rank]
    retriever = SchemaRetriever(metadata, config=config)
    results: list[CaseResult] = []
    for run in range(1, repeat + 1):
        for case in cases:
            if pause_seconds > 0 and results:
                time.sleep(pause_seconds)  # tránh 429 khi quota của project thấp
            metered = _MeteredLLM(llm)
            planner = QueryPlanner(metered, retriever, config=config)
            predicted, function_ok, params_ok, error = "ERROR", None, None, None
            used_semantic = False
            try:
                query = understand_query(
                    case.question,
                    session_history=case.history,
                    previous_slots=None,
                    llm=metered,
                    metadata=metadata,
                    clock=lambda: clock_value,
                )
                if not query.guard.allowed:
                    predicted = f"GUARD_{query.guard.reason_code}"
                else:
                    output = planner.plan(query)
                    if output.system_state is not None:
                        predicted = output.system_state.value
                    else:
                        predicted = output.intent.value if output.intent else "NONE"
                    first = output.steps[0] if output.steps else None
                    used_semantic = any(s.pattern.value == "semantic_view" for s in output.steps)
                    if case.expected_pattern:
                        function_ok = bool(first and first.pattern.value == case.expected_pattern)
                        if case.expected_terms:
                            found = normalize_match_text(
                                " ".join(str(t) for t in (first.params.get("search_terms") or []))
                                if first
                                else ""
                            )
                            params_ok = all(
                                normalize_match_text(t) in found for t in case.expected_terms
                            )
                    elif case.expected_function:
                        function_ok = bool(first and first.function_name == case.expected_function)
                    if case.expected_sections:
                        # Báo cáo: bước do template dựng nên chỉ chấm các phần được chọn.
                        function_ok = not output.steps
                        params_ok = tuple(output.report_sections) == case.expected_sections
                    if case.expected_params_subset:
                        params_ok = bool(first) and all(
                            first is not None and first.params.get(key) == value
                            for key, value in case.expected_params_subset.items()
                        )
            except Exception as exc:  # một ca lỗi không dừng cả bộ đánh giá
                error = f"{type(exc).__name__}: {exc}"[:300]
            results.append(
                CaseResult(
                    case_id=case.id,
                    run=run,
                    expected=case.expected_label,
                    predicted=predicted,
                    intent_ok=predicted == case.expected_label,
                    function_ok=function_ok,
                    params_ok=params_ok,
                    latency_ms=metered.latency_ms,
                    tokens=metered.tokens,
                    error=error,
                    llm_failed_calls=metered.failed_calls,
                    used_semantic=used_semantic,
                )
            )

    confusion: dict[str, Counter[str]] = defaultdict(Counter)
    for result in results:
        confusion[result.expected][result.predicted] += 1
    per_run = []
    for run in range(1, repeat + 1):
        run_results = [r for r in results if r.run == run]
        per_run.append(
            {
                "run": run,
                "intent_accuracy": _rate([r.intent_ok for r in run_results]),
                "function_accuracy": _rate(
                    [bool(r.function_ok) for r in run_results if r.function_ok is not None]
                ),
            }
        )
    latencies = [r.latency_ms for r in results]
    expected_functions = {case.id: case.expected_function for case in cases}
    # Ca có lời gọi LLM lỗi không phản ánh chất lượng prompt: báo riêng và coi lần chạy không hợp lệ.
    unavailable = [f"{r.case_id}#{r.run}" for r in results if r.llm_failed_calls]
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "anchor_date": anchor.isoformat(),
        "milestone": config.milestone,
        "case_count": len(cases),
        "repeat": repeat,
        "intent_accuracy": _rate([r.intent_ok for r in results]) or 0.0,
        "function_accuracy": _rate(
            [bool(r.function_ok) for r in results if r.function_ok is not None]
        )
        or 0.0,
        "params_match_rate": _rate([bool(r.params_ok) for r in results if r.params_ok is not None]),
        "runs": per_run,
        "confusion_matrix": {expected: dict(row) for expected, row in sorted(confusion.items())},
        "failures": [
            asdict(r)
            for r in results
            if not r.intent_ok or r.function_ok is False or r.params_ok is False or r.error
        ],
        "unavailable_cases": unavailable,
        # b09 D7: ca Pattern 1 trả lời được (có expected_function) mà Planner lại chọn semantic_view.
        "semantic_usage_on_pattern1_cases": _rate(
            [r.used_semantic for r in results if expected_functions.get(r.case_id)]
        ),
        "valid_run": not unavailable,
        "latency_ms": {"p50": _percentile(latencies, 50), "p95": _percentile(latencies, 95)},
        "total_tokens": sum(r.tokens for r in results),
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Đánh giá Query Planner của chatbot")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES_PATH)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--min-intent-accuracy", type=float, default=0.85)
    parser.add_argument("--min-function-accuracy", type=float, default=0.80)
    parser.add_argument("--anchor-date", default=DEFAULT_ANCHOR_DATE)
    parser.add_argument("--metadata-source", choices=("static", "db"), default="static")
    parser.add_argument("--pause", type=float, default=0.0, help="Giây nghỉ giữa các ca")
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    llm: LLMClient | None = None,
    metadata: MetadataProvider | None = None,
    config: PlannerConfig | None = None,
) -> int:
    args = _build_parser().parse_args(argv)
    if llm is None or config is None or (metadata is None and args.metadata_source == "db"):
        from ...settings import get_settings

        settings = get_settings()
        config = config or PlannerConfig.from_settings(settings)
        if llm is None:
            from .llm_gateway import GeminiJsonClient

            llm = GeminiJsonClient(settings)
        if metadata is None and args.metadata_source == "db":
            from ...analytics import FeedbackAnalyticsRepository, FeedbackAnalyticsService
            from .metadata_adapter import AnalyticsMetadataAdapter

            repository = FeedbackAnalyticsRepository(settings.classification_jobs_db_path)
            metadata = AnalyticsMetadataAdapter(FeedbackAnalyticsService(repository))
    metadata = metadata or StaticMetadata(SAMPLE_METADATA)
    if llm is None:
        raise RuntimeError("Không dựng được LLM client cho bộ đánh giá Planner")

    report = evaluate(
        load_cases(args.cases),
        llm=llm,
        metadata=metadata,
        config=config,
        anchor=date.fromisoformat(args.anchor_date),
        repeat=max(1, args.repeat),
        pause_seconds=max(0.0, args.pause),
    )
    out = args.out or DEFAULT_OUT_DIR / f"planner-{datetime.now(UTC):%Y%m%dT%H%M%SZ}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    violations = []
    if not report["valid_run"]:
        violations.append(f"LLM lỗi ở {len(report['unavailable_cases'])} ca, kết quả không hợp lệ")
    if report["intent_accuracy"] < args.min_intent_accuracy:
        violations.append(
            f"intent_accuracy {report['intent_accuracy']} < {args.min_intent_accuracy}"
        )
    if report["function_accuracy"] < args.min_function_accuracy:
        violations.append(
            f"function_accuracy {report['function_accuracy']} < {args.min_function_accuracy}"
        )
    print(
        f"intent={report['intent_accuracy']} function={report['function_accuracy']} "
        f"params={report['params_match_rate']} p50={report['latency_ms']['p50']}ms "
        f"p95={report['latency_ms']['p95']}ms tokens={report['total_tokens']} "
        f"failures={len(report['failures'])} → {out}"
    )
    for violation in violations:
        print(f"DƯỚI NGƯỠNG: {violation}")
    return 1 if violations else 0
