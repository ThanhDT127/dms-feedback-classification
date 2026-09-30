"""Đánh giá phần nhận định chế độ 3 (design b05 D12).

Dùng chung cho test offline (``tests/chat/test_synthesis_golden.py``, LLM giả theo
``fake_stream`` của từng ca) và cho ``scripts/eval_chat_synthesis.py`` (Gemini thật).
"""

from __future__ import annotations

import argparse
import json
import logging
import statistics
import time
from collections import Counter
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ...settings import SERVICE_DIR
from ..contract import AnswerShape, QueryPattern, QueryPlan, QueryResult
from .answer_events import EventType, ListSink
from .intents import Intent
from .query_planner import PlannerOutput
from .response_shaper import AnswerComposer, ComposerConfig
from .types import (
    Decision,
    LLMClient,
    StepResult,
    StepState,
    StepStatus,
    TextStream,
    TurnOutcome,
    ValidatedPlan,
)

DEFAULT_CASES_PATH = SERVICE_DIR / "tests" / "chat" / "golden" / "synthesis_cases.jsonl"
DEFAULT_OUT_DIR = SERVICE_DIR / "work" / "eval"
MAX_DROP_RATE = 0.15  # ngưỡng ghép M2


def load_cases(path: Path = DEFAULT_CASES_PATH) -> list[dict[str, Any]]:
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def outcome_from_case(case: Mapping[str, Any]) -> TurnOutcome:
    """Dựng ``TurnOutcome`` đã chạy xong từ một ca golden."""
    shape = AnswerShape(case.get("answer_shape", "table"))
    intent = Intent(case["intent"])
    plan = QueryPlan(
        pattern=QueryPattern.SQL_TEMPLATE,
        answer_shape=shape,
        original_query=case["question"],
        function_name=case["function_name"],
        params=dict(case.get("params") or {}),
    )
    planner_output = PlannerOutput(
        intent=intent, system_state=None, steps=(plan,), answer_shape=shape, confidence=0.9
    )
    step = StepResult(
        index=1,
        function_name=case["function_name"],
        status=StepStatus(StepState.OK),
        result=QueryResult.ok([case["row"]]),
    )
    return TurnOutcome(
        request_id=str(case["id"]),
        original_query=case["question"],
        rewritten_query=case["question"],
        decision=Decision.RUN,
        intent=intent,
        validated_plan=ValidatedPlan(
            planner_output=planner_output, decision=Decision.RUN, intent=intent, steps=(plan,)
        ),
        step_results=(step,),
    )


class _DropCounter(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.INFO)
        self.reasons: Counter[str] = Counter()

    def emit(self, record: logging.LogRecord) -> None:
        if record.getMessage() == "synthesis_sentence_dropped":
            self.reasons[str(getattr(record, "reason", "UNKNOWN"))] += 1


class _TimedLLM:
    """Đo ``first_chunk_ms``/``total_ms`` và gom usage của lời gọi stream."""

    def __init__(self, inner: LLMClient) -> None:
        self.inner = inner
        self.first_chunk_ms: list[int] = []
        self.total_ms: list[int] = []
        self.tokens = 0

    def generate_json(self, prompt: str, *, call_type: str, system_instruction: str | None = None):
        return self.inner.generate_json(
            prompt, call_type=call_type, system_instruction=system_instruction
        )

    def stream(
        self,
        prompt: str,
        *,
        call_type: str,
        system_instruction: str | None = None,
        temperature: float | None = None,
        max_output_tokens: int | None = None,
    ) -> TextStream:
        inner = self.inner.stream(
            prompt,
            call_type=call_type,
            system_instruction=system_instruction,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
        )
        return _TimedStream(inner, self)


@dataclass
class _TimedStream:
    inner: Any
    owner: _TimedLLM
    started: float = field(default_factory=time.monotonic)
    got_first: bool = False
    finished: bool = False

    @property
    def usage(self) -> Mapping[str, int]:
        return self.inner.usage

    @property
    def finish_reason(self) -> str | None:
        return self.inner.finish_reason

    def __iter__(self) -> Iterator[str]:
        return self

    def __next__(self) -> str:
        try:
            chunk = next(self.inner)
        except StopIteration:
            self._finish()
            raise
        if not self.got_first:
            self.got_first = True
            self.owner.first_chunk_ms.append(int((time.monotonic() - self.started) * 1000))
        return chunk

    def close(self) -> None:
        self.inner.close()
        self._finish()

    def _finish(self) -> None:
        if self.finished:
            return
        self.finished = True
        self.owner.total_ms.append(int((time.monotonic() - self.started) * 1000))
        self.owner.tokens += int((self.inner.usage or {}).get("total_tokens", 0))


def evaluate(
    cases: Sequence[Mapping[str, Any]],
    *,
    llm: LLMClient,
    config: ComposerConfig | None = None,
    pause_seconds: float = 0.0,
) -> dict[str, Any]:
    timed = _TimedLLM(llm)
    counter = _DropCounter()
    guard_logger = logging.getLogger("dms-chat-synthesis")
    guard_logger.addHandler(counter)
    previous_level = guard_logger.level
    guard_logger.setLevel(logging.INFO)
    composer = AnswerComposer(llm=timed, config=config or ComposerConfig())
    per_case = []
    try:
        for index, case in enumerate(cases):
            if pause_seconds > 0 and index > 0:
                time.sleep(pause_seconds)  # tránh 429 khi quota của project thấp
            sink = ListSink()
            composer.compose(outcome_from_case(case), sink)
            done = sink.done.data if sink.done else {}
            per_case.append(
                {
                    "id": case["id"],
                    "sentences": len(sink.of_type(EventType.COMMENTARY)),
                    "dropped": int(done.get("dropped_sentences", 0)),
                    "commentary_status": done.get("commentary_status"),
                }
            )
    finally:
        guard_logger.removeHandler(counter)
        guard_logger.setLevel(previous_level)

    kept = sum(item["sentences"] for item in per_case)
    dropped = sum(item["dropped"] for item in per_case)
    total = kept + dropped
    drop_rate = dropped / total if total else 0.0
    # Ca không có nhận định (LLM lỗi/429) không được tính là "đạt": tỉ lệ loại khi đó vô nghĩa.
    unavailable = [item["id"] for item in per_case if item["commentary_status"] == "unavailable"]
    return {
        "cases": len(per_case),
        "sentences_kept": kept,
        "sentences_dropped": dropped,
        "drop_rate": round(drop_rate, 4),
        "drop_rate_ok": drop_rate < MAX_DROP_RATE and not unavailable,
        "unavailable_cases": unavailable,
        "valid_run": not unavailable,
        "drops_by_reason": dict(counter.reasons),
        "avg_sentences": round(kept / len(per_case), 2) if per_case else 0.0,
        "first_chunk_ms": _percentiles(timed.first_chunk_ms),
        "total_ms": _percentiles(timed.total_ms),
        "tokens": timed.tokens,
        "per_case": per_case,
    }


def _percentiles(values: list[int]) -> dict[str, int | None]:
    if not values:
        return {"p50": None, "p95": None}
    ordered = sorted(values)
    p95_index = max(0, int(round(0.95 * (len(ordered) - 1))))
    return {"p50": int(statistics.median(ordered)), "p95": ordered[p95_index]}


def main(argv: Sequence[str] | None = None, *, llm: LLMClient | None = None) -> int:
    parser = argparse.ArgumentParser(description="Đánh giá nhận định chế độ 3 với Gemini thật.")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES_PATH)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--pause", type=float, default=0.0, help="Giây nghỉ giữa các ca")
    args = parser.parse_args(argv)

    from ...settings import get_settings

    settings = get_settings()
    if llm is None:
        from .llm_gateway import GeminiChatGateway

        llm = GeminiChatGateway(settings)
    cases = load_cases(args.cases)
    if args.limit > 0:
        cases = cases[: args.limit]

    report = evaluate(
        cases, llm=llm, config=ComposerConfig.from_settings(settings), pause_seconds=args.pause
    )
    out = args.out or DEFAULT_OUT_DIR / f"synthesis-{datetime.now(UTC):%Y%m%dT%H%M%SZ}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"{report['cases']} ca · giữ {report['sentences_kept']} câu · loại {report['drop_rate']:.1%} "
        f"{report['drops_by_reason']} · first_chunk p50/p95 {report['first_chunk_ms']} → {out}"
    )
    return 0 if report["drop_rate_ok"] else 1
