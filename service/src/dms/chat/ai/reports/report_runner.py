"""Chạy các bước của một báo cáo (spec ``chat-report-composition``, design b10 D3).

Khác chế độ thường: nhiều bước hơn, timeout lượt riêng, và **một phần lỗi không làm hỏng cả
báo cáo** — trừ phần tổng quan, vì không có số chính thì báo cáo vô nghĩa.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Sequence
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass, field, replace

from ...contract import QueryPlan, QueryResult, UserScope
from ..answer_events import Stage
from ..types import (
    ErrorCode,
    ReportSection,
    SectionStatus,
    StepResult,
    StepState,
    StepStatus,
    TurnCancelled,
    ValidatedPlan,
)
from .templates import SECTION_OVERVIEW

logger = logging.getLogger("dms-chat-report")

RunStep = Callable[[QueryPlan, UserScope, float], QueryResult]
Interpret = Callable[[QueryResult, int], StepStatus]

_STATE_TO_SECTION = {
    StepState.OK: SectionStatus.OK,
    StepState.NO_DATA: SectionStatus.NO_DATA,
    StepState.ERROR: SectionStatus.ERROR,
    StepState.FORBIDDEN: SectionStatus.ERROR,
}


@dataclass(frozen=True)
class ReportRunConfig:
    max_steps: int = 8
    step_timeout_seconds: float = 20.0
    turn_timeout_seconds: float = 90.0


@dataclass
class ReportRun:
    """Kết quả chạy báo cáo: bước, trạng thái từng phần và thời gian."""

    step_results: tuple[StepResult, ...] = ()
    sections: tuple[ReportSection, ...] = ()
    timings_ms: dict[str, int] = field(default_factory=dict)

    @property
    def overview_failed(self) -> bool:
        return any(
            section.section_id == SECTION_OVERVIEW and section.status is SectionStatus.ERROR
            for section in self.sections
        )

    @property
    def has_data(self) -> bool:
        return any(section.status is SectionStatus.OK for section in self.sections)

    @property
    def all_no_data(self) -> bool:
        return bool(self.sections) and all(
            section.status is SectionStatus.NO_DATA for section in self.sections
        )

    @property
    def has_failed_section(self) -> bool:
        return any(section.status is SectionStatus.ERROR for section in self.sections)


class ReportRunner:
    def __init__(
        self,
        *,
        run_step: RunStep,
        interpret: Interpret,
        config: ReportRunConfig | None = None,
    ) -> None:
        self.run_step = run_step
        self.interpret = interpret
        self.config = config or ReportRunConfig()

    def run(
        self,
        validated: ValidatedPlan,
        scope: UserScope,
        *,
        started: float | None = None,
        should_cancel: Callable[[], bool] | None = None,
        request_id: str = "",
    ) -> ReportRun:
        """Chạy tuần tự từng phần; phần lỗi được đánh dấu và các phần sau vẫn chạy."""
        began = time.monotonic() if started is None else started
        sections = list(validated.report_sections)
        steps = list(validated.steps)
        limit = self.config.max_steps
        results: list[StepResult] = []
        timings: dict[str, int] = {}
        exhausted = False

        for index, (section, step) in enumerate(zip(sections, steps, strict=False), start=1):
            if index > limit:
                sections[index - 1] = replace(section, status=SectionStatus.SKIPPED)
                continue
            if index > 1 and should_cancel is not None and should_cancel():
                raise TurnCancelled(Stage.QUERYING.value)
            remaining = self.config.turn_timeout_seconds - (time.monotonic() - began)
            if exhausted or remaining <= 0:
                exhausted = True
                sections[index - 1] = replace(section, status=SectionStatus.ERROR)
                results.append(_timeout_step(index, step))
                continue

            mark = time.monotonic()
            try:
                result = self.run_step(
                    step, scope, min(self.config.step_timeout_seconds, remaining)
                )
            except FutureTimeout:
                timings[f"section_{section.section_id}"] = _ms_since(mark)
                logger.warning(
                    "report_section_timeout",
                    extra={"request_id": request_id, "section": section.section_id},
                )
                sections[index - 1] = replace(section, status=SectionStatus.ERROR)
                results.append(_timeout_step(index, step))
                continue

            duration = _ms_since(mark)
            timings[f"section_{section.section_id}"] = duration
            status = self.interpret(result, index)
            sections[index - 1] = replace(
                section, status=_STATE_TO_SECTION.get(status.state, SectionStatus.ERROR)
            )
            results.append(
                StepResult(
                    index=index,
                    function_name=step.function_name,
                    status=status,
                    result=result if status.ok else None,
                    duration_ms=duration,
                )
            )
            logger.info(
                "report_section_done",
                extra={
                    "request_id": request_id,
                    "section": section.section_id,
                    "status": status.state.value,
                    "duration_ms": duration,
                },
            )

        return ReportRun(step_results=tuple(results), sections=tuple(sections), timings_ms=timings)


def sections_with_data(run: ReportRun) -> tuple[str, ...]:
    return tuple(
        section.section_id for section in run.sections if section.status is SectionStatus.OK
    )


def section_of_step(sections: Sequence[ReportSection], step_index: int) -> ReportSection | None:
    for section in sections:
        if section.step_index == step_index:
            return section
    return None


def _timeout_step(index: int, step: QueryPlan) -> StepResult:
    return StepResult(
        index=index,
        function_name=step.function_name,
        status=StepStatus(StepState.ERROR, ErrorCode.TIMEOUT),
    )


def _ms_since(mark: float) -> int:
    return int((time.monotonic() - mark) * 1000)


__all__ = [
    "ReportRun",
    "ReportRunConfig",
    "ReportRunner",
    "section_of_step",
    "sections_with_data",
]
