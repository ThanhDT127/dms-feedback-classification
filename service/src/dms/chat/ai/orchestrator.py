"""Plan Orchestrator (spec ``chat-plan-orchestration``, design b03 D7, D9, D11).

Nối IG1 → CTX → NRM → IG2 → Planner → Plan Guard → Executor và trả ``TurnOutcome`` có cấu trúc.
**Không sinh văn bản trả lời** — việc đó thuộc Response Shaper (b05).
"""

from __future__ import annotations

import logging
import re
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

from ...settings import Settings
from ..contract import AnswerShape, QueryPattern, QueryPlan, QueryResult, UserScope
from ..guardrails.plan_guard import PlanGuard
from ..guardrails.sql_guard import SqlErrorCode, SqlGuard, SqlGuardError, context_for
from . import similar_terms
from .answer_events import Stage
from .function_catalog import FUNCTION_CATALOG
from .intents import Intent
from .query_planner import ANALYSIS_REQUEST_PARAM, QueryPlanner
from .refusal_templates import join_vi, render
from .reports.report_runner import ReportRunConfig, ReportRunner
from .result_interpreter import ResultInterpreter
from .sql_generator import SqlGenerator, SqlGeneratorConfig
from .types import (
    FLAG_EXPORT_REPLAY,
    Decision,
    ErrorCode,
    LLMClient,
    LLMResult,
    MetadataProvider,
    Notice,
    QueryExecutor,
    Reason,
    ReportSection,
    StepResult,
    StepState,
    StepStatus,
    TextStream,
    TurnCancelled,
    TurnMode,
    TurnOutcome,
    TurnRequest,
    ValidatedPlan,
)
from .understanding import UnderstandingConfig, understand_query

logger = logging.getLogger("dms-chat-orchestrator")

MAX_STEPS = 3
SEMANTIC_FUNCTION = "semantic_view"
MAX_AUDIT_SQL_CHARS = 2000
FTS_FUNCTION = "fts5_search"
FTS_FILE_FUNCTION = "fts5_file_lookup"
CITATION_COLUMNS = ("source_file_name", "source_row_number")
_ISSUE_CODE = re.compile(r"\b[A-Za-z][A-Za-z0-9]{1,5}-\d{3,}\b")
_ORDINAL_WORDS = {
    "nhat": 1,
    "dau tien": 1,
    "hai": 2,
    "ba": 3,
    "tu": 4,
    "bon": 4,
    "nam": 5,
    "sau": 6,
    "bay": 7,
    "tam": 8,
    "chin": 9,
    "muoi": 10,
}
_ORDINAL = re.compile(r"\b(?:thu|so)\s+(\d{1,2}|nhat|hai|ba|tu|bon|nam|sau|bay|tam|chin|muoi)\b")


@dataclass(frozen=True)
class OrchestratorConfig:
    step_timeout_seconds: float = 20.0
    turn_timeout_seconds: float = 60.0
    max_steps: int = MAX_STEPS
    similar_top_terms: int = 8
    fts_max_relax: int = 2
    # Executor lọc được get_issues theo issue_code (Dev A chưa có ở v1.0 — b08 D6).
    code_lookup_available: bool = False
    sql_max_repair: int = 2
    # Chế độ báo cáo (b10 D3): nhiều bước hơn và timeout lượt riêng.
    report_max_steps: int = 8
    report_turn_timeout_seconds: float = 90.0

    @classmethod
    def from_settings(cls, settings: Settings) -> OrchestratorConfig:
        return cls(
            step_timeout_seconds=settings.chat_step_timeout_seconds,
            turn_timeout_seconds=settings.chat_turn_timeout_seconds,
            similar_top_terms=settings.chat_similar_top_terms,
            fts_max_relax=settings.chat_fts_max_relax,
            sql_max_repair=settings.chat_sql_max_repair,
            report_max_steps=int(settings.chat_report_max_steps),
            report_turn_timeout_seconds=float(settings.chat_report_turn_timeout_seconds),
        )


class _UsageRecordingLLM:
    """Bọc ``LLMClient`` để gom usage theo ``call_type`` cho một lượt (design D9)."""

    def __init__(self, inner: LLMClient) -> None:
        self._inner = inner
        self.usage: dict[str, dict[str, int]] = {}

    def generate_json(
        self, prompt: str, *, call_type: str, system_instruction: str | None = None
    ) -> LLMResult:
        result = self._inner.generate_json(
            prompt, call_type=call_type, system_instruction=system_instruction
        )
        bucket = self.usage.setdefault(call_type, {})
        bucket["calls"] = bucket.get("calls", 0) + 1
        for key, value in result.usage.items():
            bucket[key] = bucket.get(key, 0) + int(value)
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
        """Chuyển thẳng để gateway (b04) tự ghi usage của nó."""
        return self._inner.stream(
            prompt,
            call_type=call_type,
            system_instruction=system_instruction,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
        )


class ChatOrchestrator:
    def __init__(
        self,
        *,
        llm: LLMClient,
        planner: QueryPlanner,
        plan_guard: PlanGuard,
        executor: QueryExecutor,
        metadata: MetadataProvider,
        config: OrchestratorConfig | None = None,
        understanding: UnderstandingConfig | None = None,
        clock: Callable[[], datetime] | None = None,
        interpreter: ResultInterpreter | None = None,
        sql_generator_config: SqlGeneratorConfig | None = None,
        sql_guard: SqlGuard | None = None,
    ) -> None:
        self.llm = llm
        self.planner = planner
        self.plan_guard = plan_guard
        self.executor = executor
        self.metadata = metadata
        self.config = config or OrchestratorConfig()
        self.understanding = understanding or UnderstandingConfig()
        self.clock = clock
        self.interpreter = interpreter or ResultInterpreter()
        self.sql_generator_config = sql_generator_config or SqlGeneratorConfig()
        self.sql_guard = sql_guard or SqlGuard()

    # ── API ──

    def handle(
        self,
        turn: TurnRequest,
        *,
        on_stage: Callable[[str], None] | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> TurnOutcome:
        """Chạy một lượt; ném ``TurnCancelled`` nếu ``should_cancel`` bật giữa các giai đoạn."""
        outcome = self._handle(turn, on_stage=on_stage, should_cancel=should_cancel)
        # Danh tính người hỏi gắn ở một chỗ cho mọi nhánh kết thúc (xuất file b10, hạn mức b11).
        return replace(
            outcome,
            username=turn.scope.username,
            display_name=turn.display_name or turn.scope.display_name or turn.scope.username,
            is_admin=turn.scope.is_admin,
        )

    def _handle(
        self,
        turn: TurnRequest,
        *,
        on_stage: Callable[[str], None] | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> TurnOutcome:
        request_id = turn.request_id or uuid.uuid4().hex
        started = time.monotonic()
        timings: dict[str, int] = {}
        llm = _UsageRecordingLLM(self.llm)

        def enter(stage: Stage) -> None:
            if should_cancel is not None and should_cancel():
                self._log(request_id, "turn_cancelled", stage=stage.value)
                raise TurnCancelled(stage.value)
            if on_stage is not None:
                on_stage(stage.value)

        # IG1 → CTX → NRM → IG2
        enter(Stage.UNDERSTANDING)
        mark = time.monotonic()
        query = understand_query(
            turn.question,
            session_history=turn.history,
            previous_slots=turn.previous_slots,
            llm=llm,
            metadata=self.metadata,
            clock=self.clock,
            config=self.understanding,
            request_id=request_id,
            # Slot kế thừa được giao với phạm vi hiện tại (b11 D7); admin không giới hạn.
            scope_units=None if turn.scope.is_admin else tuple(turn.scope.unit_ids),
        )
        timings["understand"] = _ms_since(mark)

        if not query.guard.allowed:
            self._log(request_id, "turn_refused_by_input_guard", decision=Decision.REFUSE.value)
            return self._finish(
                TurnOutcome(
                    request_id=request_id,
                    original_query=turn.question,
                    rewritten_query=query.rewritten_query,
                    decision=Decision.REFUSE,
                    message=query.guard.message,
                    guard=query.guard,
                    slots=query.slots,
                    assumptions=query.assumptions,
                ),
                started,
                timings,
                llm,
            )

        # "Bắt đầu lại" không kèm câu hỏi: không có gì để truy vấn (b11 D6).
        if query.reset_only:
            self._log(request_id, "topic_reset_only")
            return self._finish(
                TurnOutcome(
                    request_id=request_id,
                    original_query=turn.question,
                    rewritten_query=query.rewritten_query,
                    decision=Decision.HELP,
                    reason=Reason.TOPIC_RESET,
                    message=render(Reason.TOPIC_RESET),
                    guard=query.guard,
                    slots=query.slots,
                    assumptions=query.assumptions,
                ),
                started,
                timings,
                llm,
            )

        # Planner
        enter(Stage.PLANNING)
        mark = time.monotonic()
        try:
            planner = QueryPlanner(llm, self.planner.retriever, config=self.planner.config)
            output = planner.plan(query)
        except Exception:  # lỗi LLM/SDK — không để lộ chi tiết ra ngoài
            logger.exception("planner_failed", extra={"request_id": request_id})
            return self._finish(
                self._terminal_outcome(request_id, turn, query, Decision.REFUSE, Reason.INTERNAL),
                started,
                timings,
                llm,
            )
        timings["plan"] = _ms_since(mark)

        # Plan Guard
        mark = time.monotonic()
        validated = self.plan_guard.check(output, query, turn.scope)
        timings["plan_guard"] = _ms_since(mark)
        self._log(
            request_id,
            "plan_guard_decision",
            decision=validated.decision.value,
            intent=validated.intent.value if validated.intent else None,
        )

        if validated.is_terminal:
            return self._finish(
                self._outcome_from(request_id, turn, query, validated), started, timings, llm
            )

        # Executor
        enter(Stage.QUERYING)
        if validated.report_type is not None or validated.export_request:
            outcome = self._execute_report(
                request_id, turn, query, validated, started, timings, should_cancel=should_cancel
            )
            return self._finish(outcome, started, timings, llm)
        exclude_ids: frozenset[Any] = frozenset()
        if validated.intent is Intent.LOOKUP_SIMILAR:
            prepared = self._prepare_similar(request_id, turn, query, validated)
            if isinstance(prepared, TurnOutcome):
                return self._finish(prepared, started, timings, llm)
            validated, exclude_ids = prepared
        outcome = self._execute(
            request_id,
            turn,
            query,
            validated,
            started,
            timings,
            should_cancel=should_cancel,
            exclude_ids=exclude_ids,
            llm=llm,
        )
        return self._finish(outcome, started, timings, llm)

    # ── Thực thi từng bước ──

    def _execute(
        self,
        request_id: str,
        turn: TurnRequest,
        query,
        validated: ValidatedPlan,
        started: float,
        timings: dict[str, int],
        *,
        should_cancel: Callable[[], bool] | None = None,
        exclude_ids: frozenset[Any] = frozenset(),
        llm: LLMClient | None = None,
    ) -> TurnOutcome:
        scope = self._execution_scope(turn.scope, validated)
        notices = list(validated.notices)
        results: list[StepResult] = []

        for index, step in enumerate(validated.steps[: self.config.max_steps], start=1):
            is_sample = index > 1
            if index > 1 and should_cancel is not None and should_cancel():
                self._log(request_id, "turn_cancelled", stage=Stage.QUERYING.value, step=index)
                raise TurnCancelled(Stage.QUERYING.value)
            remaining = self.config.turn_timeout_seconds - (time.monotonic() - started)
            if remaining <= 0:
                return self._timeout_outcome(
                    request_id, turn, query, validated, results, notices, is_sample
                )

            if step.pattern is QueryPattern.SEMANTIC_VIEW:
                mark = time.monotonic()
                try:
                    semantic = self._run_semantic(
                        request_id,
                        turn,
                        query,
                        validated,
                        step,
                        scope,
                        started,
                        index,
                        llm or self.llm,
                        notices,
                    )
                except FutureTimeout:
                    timings[f"execute_{index}"] = _ms_since(mark)
                    return self._timeout_outcome(
                        request_id, turn, query, validated, results, notices, is_sample
                    )
                timings[f"execute_{index}"] = _ms_since(mark)
                if isinstance(semantic, TurnOutcome):
                    return semantic
                results.append(semantic)
                continue

            mark = time.monotonic()
            attempts = (
                validated.fts_attempts[index - 1]
                if step.pattern is QueryPattern.FTS5_SEARCH and index <= len(validated.fts_attempts)
                else ()
            )
            try:
                if attempts:
                    fts = self._run_fts(
                        request_id, step, attempts, scope, started, exclude_ids, should_cancel
                    )
                    result = fts.result
                else:
                    result = self._run_step(
                        step, scope, min(self.config.step_timeout_seconds, remaining)
                    )
            except FutureTimeout:
                timings[f"execute_{index}"] = _ms_since(mark)
                logger.warning(
                    "step_timeout_orphaned",
                    extra={
                        "request_id": request_id,
                        "step": index,
                        "function_name": step.function_name,
                    },
                )
                if not is_sample:
                    return self._timeout_outcome(
                        request_id, turn, query, validated, results, notices, is_sample
                    )
                notices.append(Notice(Reason.SAMPLE_UNAVAILABLE, render(Reason.SAMPLE_UNAVAILABLE)))
                results.append(
                    StepResult(
                        index=index,
                        function_name=step.function_name,
                        status=StepStatus(StepState.ERROR, code=None),
                        duration_ms=_ms_since(mark),
                        is_sample=True,
                    )
                )
                continue

            duration = _ms_since(mark)
            timings[f"execute_{index}"] = duration
            status = self.interpreter.interpret(result, request_id=request_id, step=index)
            if attempts and not is_sample:
                terminal = self._fts_outcome(
                    request_id, turn, query, validated, step, fts, status, duration, notices, index
                )
                if isinstance(terminal, TurnOutcome):
                    return terminal
                results.append(terminal)
                continue
            results.append(
                StepResult(
                    index=index,
                    function_name=step.function_name,
                    status=status,
                    result=result,
                    duration_ms=duration,
                    is_sample=is_sample,
                )
            )
            self._log(
                request_id,
                "step_done",
                decision=validated.decision.value,
                intent=validated.intent.value if validated.intent else None,
                function_name=step.function_name,
                step=index,
                duration_ms=duration,
                total_rows=result.metadata.total_rows,
                status=status.state.value,
            )

            if status.state is StepState.FORBIDDEN:
                # Plan Guard đã chặn phần lớn; tới đây là executor tự từ chối.
                notices.append(
                    Notice(
                        Reason.UNAUTHORIZED_SCOPE,
                        render(
                            Reason.UNAUTHORIZED_SCOPE,
                            dropped_units="dữ liệu ngoài phạm vi",
                            allowed_units=join_vi(validated.scope_units) or "đơn vị của bạn",
                        ),
                    )
                )
                if not is_sample:
                    return self._outcome_from(
                        request_id,
                        turn,
                        query,
                        validated,
                        step_results=results,
                        notices=notices,
                        reason=Reason.UNAUTHORIZED_SCOPE,
                        message=notices[-1].message,
                    )
            elif status.state is StepState.ERROR:
                reason = self.interpreter.reason_for(status) or Reason.INTERNAL
                if not is_sample:
                    return self._outcome_from(
                        request_id,
                        turn,
                        query,
                        validated,
                        step_results=results,
                        notices=notices,
                        reason=reason,
                        message=render(reason),
                    )
                notices.append(Notice(Reason.SAMPLE_UNAVAILABLE, render(Reason.SAMPLE_UNAVAILABLE)))

        return self._outcome_from(
            request_id, turn, query, validated, step_results=results, notices=notices
        )

    # ── Chế độ báo cáo (b10 D3) ──

    def _execute_report(
        self,
        request_id: str,
        turn: TurnRequest,
        query,
        validated: ValidatedPlan,
        started: float,
        timings: dict[str, int],
        *,
        should_cancel: Callable[[], bool] | None = None,
    ) -> TurnOutcome:
        if FLAG_EXPORT_REPLAY in validated.flags:
            replayed = self._replay_previous_plans(request_id, turn, query, validated)
            if isinstance(replayed, TurnOutcome):
                return replayed
            validated = replayed
        # Phạm vi lấy sau khi chạy lại plan, để dùng đúng quyền của lượt hiện tại.
        scope = self._execution_scope(turn.scope, validated)
        runner = ReportRunner(
            run_step=self._run_step,
            interpret=lambda result, index: self.interpreter.interpret(
                result, request_id=request_id, step=index
            ),
            config=ReportRunConfig(
                max_steps=self.config.report_max_steps,
                step_timeout_seconds=self.config.step_timeout_seconds,
                turn_timeout_seconds=self.config.report_turn_timeout_seconds,
            ),
        )
        run = runner.run(
            validated,
            scope,
            started=started,
            should_cancel=should_cancel,
            request_id=request_id,
        )
        timings.update(run.timings_ms)
        outcome = self._outcome_from(
            request_id,
            turn,
            query,
            validated,
            step_results=list(run.step_results),
            notices=list(validated.notices),
        )
        # Xuất lại câu trả lời cũ không phải báo cáo: không gắn `section` vào event (design D4).
        is_report = validated.report_type is not None
        outcome = replace(
            outcome,
            mode=TurnMode.REPORT if is_report else TurnMode.NORMAL,
            report_type=validated.report_type,
            report_range=validated.report_range,
            compare_range=validated.report_compare,
            sections=run.sections if is_report else (),
            export_request=validated.export_request,
            assumptions=tuple(query.assumptions) + tuple(validated.assumptions),
        )
        if is_report and run.overview_failed:
            # Không có số chính thì báo cáo vô nghĩa: cả lượt là lỗi (design D3).
            self._log(request_id, "report_overview_failed")
            return replace(outcome, reason=Reason.INTERNAL, message=render(Reason.INTERNAL))
        return outcome

    def _replay_previous_plans(
        self, request_id: str, turn: TurnRequest, query, validated: ValidatedPlan
    ) -> ValidatedPlan | TurnOutcome:
        """Xuất câu trả lời trước: chạy lại plan đã lưu qua Plan Guard với phạm vi hiện tại."""
        steps = _plans_to_steps(turn.previous_plans, query.rewritten_query)
        if not steps:
            self._log(request_id, "export_without_source")
            return self._outcome_from(
                request_id,
                turn,
                query,
                replace(validated, decision=Decision.CLARIFY),
                reason=Reason.EXPORT_NO_SOURCE,
                message=render(Reason.EXPORT_NO_SOURCE),
            )
        rechecked = self.plan_guard.recheck_steps(
            validated.planner_output, steps, query, turn.scope
        )
        if rechecked.decision is not Decision.RUN:
            return self._outcome_from(request_id, turn, query, rechecked)
        sections = tuple(
            ReportSection(
                section_id=f"data_{index}",
                title="Kết quả",
                index=index,
                step_index=index,
            )
            for index in range(1, len(rechecked.steps) + 1)
        )
        return replace(rechecked, report_sections=sections, export_request=True)

    # ── FTS: các lần thử nới (b08 D4) ──

    def _run_fts(
        self,
        request_id: str,
        step: QueryPlan,
        attempts: Sequence[Any],
        scope: UserScope,
        started: float,
        exclude_ids: frozenset[Any],
        should_cancel: Callable[[], bool] | None,
    ) -> _FtsRun:
        """Chạy tuần tự, dừng ở lần đầu có kết quả; không tính vào giới hạn số bước."""
        last = QueryResult.no_data()
        for number, attempt in enumerate(attempts):
            if number and should_cancel is not None and should_cancel():
                raise TurnCancelled(Stage.QUERYING.value)
            remaining = self.config.turn_timeout_seconds - (time.monotonic() - started)
            if remaining <= 0:
                raise FutureTimeout()
            plan = replace(step, fts_query=attempt.fts_query)
            result = self._run_step(plan, scope, min(self.config.step_timeout_seconds, remaining))
            if exclude_ids and result.data:
                kept = [row for row in result.data if row.get("feedback_id") not in exclude_ids]
                result = QueryResult.ok(kept) if kept else QueryResult.no_data()
            self._log(
                request_id,
                "fts_attempt",
                attempt=number,
                kind=attempt.kind,
                tokens=len(attempt.tokens),
                rows=len(result.data or []),
                status=getattr(result.status, "value", str(result.status)),
            )
            last = result
            if result.data or getattr(result.status, "value", "") == "error":
                return _FtsRun(result=result, attempt=attempt, number=number)
        return _FtsRun(result=last, attempt=attempts[-1], number=len(attempts) - 1, exhausted=True)

    def _fts_outcome(
        self,
        request_id: str,
        turn: TurnRequest,
        query,
        validated: ValidatedPlan,
        step: QueryPlan,
        fts: _FtsRun,
        status: StepStatus,
        duration: int,
        notices: list[Notice],
        index: int,
    ) -> StepResult | TurnOutcome:
        if status.state is StepState.NO_DATA:
            first = validated.fts_attempts[index - 1][0]
            message = render(
                Reason.NO_MATCH,
                terms=join_vi(first.tokens),
                filters=_describe_fts_filters(step.fts_filters),
            )
            return self._outcome_from(
                request_id,
                turn,
                query,
                validated,
                step_results=[
                    StepResult(index, FTS_FUNCTION, status, fts.result, duration_ms=duration)
                ],
                notices=notices,
                reason=Reason.NO_MATCH,
                message=message,
            )
        name = FTS_FUNCTION
        if status.ok and validated.intent is Intent.LOOKUP_FILE:
            rows = fts.result.data or []
            if not all(column in rows[0] for column in CITATION_COLUMNS):
                self._log(request_id, "file_lookup_unavailable")
                return self._outcome_from(
                    request_id,
                    turn,
                    query,
                    replace(validated, decision=Decision.NOT_SUPPORTED),
                    notices=notices,
                    reason=Reason.FILE_LOOKUP_UNAVAILABLE,
                    message=render(Reason.FILE_LOOKUP_UNAVAILABLE),
                )
            name = FTS_FILE_FUNCTION
        if status.ok and fts.number > 0:
            dropped = list(fts.attempt.dropped_terms)
            notices.append(
                Notice(
                    Reason.RELAXED_SEARCH,
                    render(
                        Reason.RELAXED_SEARCH,
                        dropped=join_vi(dropped) if dropped else "dùng từ đồng nghĩa",
                    ),
                    details={"dropped_terms": dropped, "attempt": fts.attempt.kind},
                )
            )
        return StepResult(
            index=index, function_name=name, status=status, result=fts.result, duration_ms=duration
        )

    # ── Pattern 2: sinh → guard → thực thi → sửa (b09 D6) ──

    def _run_semantic(
        self,
        request_id: str,
        turn: TurnRequest,
        query,
        validated: ValidatedPlan,
        step: QueryPlan,
        scope: UserScope,
        started: float,
        index: int,
        llm: LLMClient,
        notices: list[Notice],
    ) -> StepResult | TurnOutcome:
        params = dict(step.params)
        request = dict(params.pop(ANALYSIS_REQUEST_PARAM, None) or {})
        filters = params
        scope_units = (
            None if turn.scope.is_admin else tuple(validated.scope_units or turn.scope.unit_ids)
        )
        raw_keys: tuple[str, ...] = ()
        if self.sql_guard.config.json_extract_enabled:
            catalog = getattr(self.metadata, "raw_key_catalog", None)
            raw_keys = tuple(catalog()) if callable(catalog) else ()
        context = context_for(
            filters,
            scope_units=scope_units,
            metadata_values=self.metadata.valid_values(),
            raw_keys=raw_keys,
        )
        generator = SqlGenerator(llm, config=self.sql_generator_config)
        repairs = 0

        def stop(decision: Decision, reason: Reason, **values: object) -> TurnOutcome:
            self._log(request_id, "sql_stopped", reason=reason.value, repairs=repairs)
            return self._outcome_from(
                request_id,
                turn,
                query,
                replace(validated, decision=decision, steps=()),
                notices=notices,
                reason=reason,
                message=render(reason, **values),
            )

        mark = time.monotonic()
        draft = generator.generate(request, filters, question=query.rewritten_query)
        while True:
            try:
                guarded = self.sql_guard.check(draft.sql, context)
            except SqlGuardError as error:
                if error.code is SqlErrorCode.SQL_UNIT_OUT_OF_SCOPE:
                    return stop(
                        Decision.REFUSE,
                        Reason.UNAUTHORIZED_SCOPE,
                        dropped_units="đơn vị bạn hỏi",
                        allowed_units=join_vi(scope_units or ()) or "đơn vị của bạn",
                    )
                if not error.repairable or repairs >= self.config.sql_max_repair:
                    return stop(Decision.NOT_SUPPORTED, Reason.SQL_GENERATION_FAILED)
                repairs += 1
                draft = generator.repair(
                    draft, error_code=error.code.value, error_detail=error.detail
                )
                continue

            remaining = self.config.turn_timeout_seconds - (time.monotonic() - started)
            if remaining <= 0:
                raise FutureTimeout()
            plan = replace(step, sql=guarded.sql)
            result = self._run_step(plan, scope, min(self.config.step_timeout_seconds, remaining))
            status = self.interpreter.interpret(
                result, request_id=request_id, step=index, sql_step=True
            )
            if status.code is ErrorCode.SQL_INVALID:
                if repairs >= self.config.sql_max_repair:
                    return stop(Decision.NOT_SUPPORTED, Reason.SQL_GENERATION_FAILED)
                repairs += 1
                draft = generator.repair(
                    draft,
                    error_code=ErrorCode.SQL_INVALID.value,
                    error_detail=result.error_message or "",
                )
                continue
            break

        self._log(
            request_id,
            "sql_executed",
            repairs=repairs,
            rows=len(result.data or []),
            status=status.state.value,
        )
        step_result = StepResult(
            index=index,
            function_name=SEMANTIC_FUNCTION,
            status=status,
            result=result,
            duration_ms=_ms_since(mark),
            sql=guarded.sql[:MAX_AUDIT_SQL_CHARS],
            output_columns=tuple(draft.output_columns),
            sql_repairs=repairs,
        )
        if status.state is StepState.NO_DATA:
            # Kết quả rỗng không phải lỗi: không sửa SQL, nêu rõ bộ lọc đã áp (D6).
            return self._outcome_from(
                request_id,
                turn,
                query,
                validated,
                step_results=[step_result],
                notices=notices,
                reason=Reason.NO_DATA_FILTERED,
                message=render(Reason.NO_DATA_FILTERED, filters=_describe_fts_filters(filters)),
            )
        return step_result

    # ── LOOKUP_SIMILAR (b08 D6) ──

    def _prepare_similar(
        self, request_id: str, turn: TurnRequest, query, validated: ValidatedPlan
    ) -> tuple[ValidatedPlan, frozenset[Any]] | TurnOutcome:
        def stop(decision: Decision, reason: Reason) -> TurnOutcome:
            self._log(request_id, "similar_reference_unresolved", reason=reason.value)
            return self._outcome_from(
                request_id,
                turn,
                query,
                replace(validated, decision=decision, steps=(), fts_attempts=()),
                reason=reason,
                message=render(reason),
            )

        reference: Mapping[str, Any] | None = None
        code = _ISSUE_CODE.search(turn.question)
        if code:
            if not self.config.code_lookup_available:
                return stop(Decision.NOT_SUPPORTED, Reason.LOOKUP_BY_CODE_UNAVAILABLE)
            lookup = QueryPlan(
                pattern=QueryPattern.SQL_TEMPLATE,
                answer_shape=AnswerShape.LIST,
                original_query=query.rewritten_query,
                function_name="get_issues",
                params={"issue_code": code.group(0).upper()},
            )
            found = self._run_step(
                lookup,
                self._execution_scope(turn.scope, validated),
                self.config.step_timeout_seconds,
            )
            reference = found.data[0] if found.data else None
        else:
            reference = _quote_by_ordinal(turn.previous_quotes, query.match_text)
            if reference is not None and not _in_scope(reference, turn.scope, validated):
                reference = None
        content = str((reference or {}).get("content") or "").strip()
        if not content:
            # Không nói "không có quyền": tránh lộ sự tồn tại của phản hồi ngoài phạm vi.
            return stop(Decision.CLARIFY, Reason.SIMILAR_REFERENCE_UNKNOWN)

        builder = self.plan_guard.fts_builder
        terms = similar_terms.extract(content, builder, top=self.config.similar_top_terms)
        tokens = similar_terms.search_tokens(terms, builder)
        if not tokens:
            return stop(Decision.CLARIFY, Reason.SIMILAR_REFERENCE_UNKNOWN)
        base = next(
            (s for s in validated.steps if s.pattern is QueryPattern.FTS5_SEARCH),
            QueryPlan(
                pattern=QueryPattern.FTS5_SEARCH,
                answer_shape=AnswerShape.LIST,
                original_query=query.rewritten_query,
                fts_query=" ".join(tokens),
            ),
        )
        # Lấy dư 1 để vẫn đủ kết quả sau khi bỏ chính phản hồi gốc.
        step = replace(
            base,
            fts_query=" ".join(tokens),
            fts_limit=min(base.fts_limit + 1, 50),
            params={**base.params, "search_terms": terms[:3]},
        )
        attempts = tuple(builder.attempts(tokens, max_relax=self.config.fts_max_relax))
        exclude = (
            {reference.get("feedback_id")}
            if reference and reference.get("feedback_id") is not None
            else set()
        )
        self._log(request_id, "similar_terms", terms=len(terms), tokens=len(tokens))
        return replace(validated, steps=(step,), fts_attempts=(attempts,)), frozenset(exclude)

    def _run_step(self, plan: QueryPlan, scope: UserScope, timeout: float) -> QueryResult:
        """Chạy một bước có hạn giờ; thread quá giờ vẫn chạy nốt (design D7)."""
        pool = ThreadPoolExecutor(max_workers=1)
        try:
            future = pool.submit(self.executor.execute, plan, scope)
            return future.result(timeout=timeout)
        finally:
            pool.shutdown(wait=False)

    @staticmethod
    def _execution_scope(scope: UserScope, validated: ValidatedPlan) -> UserScope:
        """Phạm vi thực thi không bao giờ rộng hơn phạm vi gốc."""
        if scope.is_admin:
            return scope
        allowed = set(scope.unit_ids)
        units = [unit for unit in validated.scope_units if unit in allowed] or list(scope.unit_ids)
        return UserScope(
            username=scope.username,
            role=scope.role,
            display_name=scope.display_name,
            unit_ids=units,
        )

    # ── Dựng TurnOutcome ──

    def _outcome_from(
        self,
        request_id: str,
        turn: TurnRequest,
        query,
        validated: ValidatedPlan,
        *,
        step_results: list[StepResult] | None = None,
        notices: list[Notice] | None = None,
        reason: Reason | None = None,
        message: str | None = None,
    ) -> TurnOutcome:
        return TurnOutcome(
            request_id=request_id,
            original_query=turn.question,
            rewritten_query=query.rewritten_query,
            decision=validated.decision,
            intent=validated.intent,
            intent_downgraded_from=validated.intent_downgraded_from,
            reason=reason if reason is not None else validated.reason,
            validated_plan=validated,
            step_results=tuple(step_results or ()),
            scope_units=validated.scope_units,
            dropped_entities=validated.dropped_entities,
            notices=tuple(notices if notices is not None else validated.notices),
            assumptions=query.assumptions,
            clarify_options=validated.clarify_options,
            message=message if message is not None else validated.message,
            guard=query.guard,
            slots=query.slots,
        )

    def _terminal_outcome(
        self,
        request_id: str,
        turn: TurnRequest,
        query,
        decision: Decision,
        reason: Reason,
    ) -> TurnOutcome:
        return TurnOutcome(
            request_id=request_id,
            original_query=turn.question,
            rewritten_query=query.rewritten_query,
            decision=decision,
            reason=reason,
            message=render(reason),
            guard=query.guard,
            slots=query.slots,
            assumptions=query.assumptions,
        )

    def _timeout_outcome(
        self,
        request_id: str,
        turn: TurnRequest,
        query,
        validated: ValidatedPlan,
        results: list[StepResult],
        notices: list[Notice],
        is_sample: bool,
    ) -> TurnOutcome:
        results = [
            *results,
            StepResult(
                index=len(results) + 1,
                function_name=None,
                status=StepStatus(StepState.ERROR, _timeout_code()),
                is_sample=is_sample,
            ),
        ]
        return self._outcome_from(
            request_id,
            turn,
            query,
            validated,
            step_results=results,
            notices=notices,
            reason=Reason.TIMEOUT,
            message=render(Reason.TIMEOUT),
        )

    def _finish(
        self,
        outcome: TurnOutcome,
        started: float,
        timings: dict[str, int],
        llm: _UsageRecordingLLM,
    ) -> TurnOutcome:
        timings["total"] = _ms_since(started)
        from dataclasses import replace as _replace

        return _replace(outcome, timings_ms=dict(timings), llm_usage=dict(llm.usage))

    @staticmethod
    def _log(request_id: str, event: str, **fields: object) -> None:
        logger.info(event, extra={"request_id": request_id, **fields})


def _ms_since(mark: float) -> int:
    return int((time.monotonic() - mark) * 1000)


def _timeout_code():
    from .types import ErrorCode

    return ErrorCode.TIMEOUT


@dataclass(frozen=True)
class _FtsRun:
    result: QueryResult
    attempt: Any
    number: int
    exhausted: bool = False


_FILTER_LABELS_VI = {
    "unit_name": "đơn vị",
    "sentiment": "cảm xúc",
    "source": "nguồn",
    "label": "nhãn",
    "product": "sản phẩm",
    "business_status": "trạng thái",
}


def _describe_fts_filters(filters: Mapping[str, Any]) -> str:
    from .vi_format import format_date_range

    parts = []
    range_text = format_date_range(filters.get("date_from"), filters.get("date_to"))
    parts.append(f"trong {range_text}" if range_text else "mọi thời gian")
    parts.extend(
        f"{label} {filters[key]}" for key, label in _FILTER_LABELS_VI.items() if filters.get(key)
    )
    return ", ".join(parts)


def _quote_by_ordinal(
    quotes: Sequence[Mapping[str, Any]], match_text: str
) -> Mapping[str, Any] | None:
    """ "cái thứ 2" → trích dẫn thứ 2 của câu trả lời trước; chỉ một trích dẫn thì dùng luôn."""
    if not quotes:
        return None
    found = _ORDINAL.search(match_text)
    if found:
        raw = found.group(1)
        number = int(raw) if raw.isdigit() else _ORDINAL_WORDS.get(raw, 0)
        return quotes[number - 1] if 1 <= number <= len(quotes) else None
    if "dau tien" in match_text:
        return quotes[0]
    if "cuoi" in match_text.split():
        return quotes[-1]
    return quotes[0] if len(quotes) == 1 else None


def _in_scope(reference: Mapping[str, Any], scope: UserScope, validated: ValidatedPlan) -> bool:
    if scope.is_admin:
        return True
    from .text_match import normalize_match_text

    allowed = {normalize_match_text(u) for u in (validated.scope_units or scope.unit_ids)}
    return normalize_match_text(str(reference.get("unit_name") or "")) in allowed


def _plans_to_steps(
    plans: Sequence[Mapping[str, Any]], original_query: str
) -> tuple[QueryPlan, ...]:
    """Dựng lại bước từ ``metadata.plans`` đã lưu; bỏ mục không còn hợp lệ (b10 D7)."""
    steps: list[QueryPlan] = []
    for plan in plans or ():
        name = str(plan.get("function_name") or "")
        if not name or name not in FUNCTION_CATALOG:
            continue
        params = plan.get("params") or {}
        if not isinstance(params, Mapping):
            continue
        steps.append(
            QueryPlan(
                pattern=QueryPattern.SQL_TEMPLATE,
                answer_shape=AnswerShape.TABLE,
                original_query=original_query or "Xuất dữ liệu",
                confidence=1.0,
                function_name=name,
                params=dict(params),
            )
        )
    return tuple(steps)
