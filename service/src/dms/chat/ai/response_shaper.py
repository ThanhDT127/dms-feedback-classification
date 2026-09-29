"""Response Shaper chế độ 3 (spec ``chat-answer-events``, ``chat-grounded-commentary``, b05 D6–D9).

Biến ``TurnOutcome`` thành chuỗi event: khối số liệu do Python dựng đi trước, nhận định do LLM
viết đi sau và chỉ qua được sentence gate mới được phát.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from ..contract import AnswerShape
from ..guardrails.synthesis_guard import SentenceSplitter, SynthesisGuard
from .answer_events import (
    AnswerEvent,
    CommentaryStatus,
    DoneStatus,
    EventSink,
    EventType,
    Stage,
)
from .block_builders import BlockConfig, DataBlock, build_blocks, subtitle_for_range
from .fact_sheet import FactSheet, build_fact_sheet
from .help_content import build_help
from .intents import Intent
from .prompt_loader import load_prompt, sanitize_prompt_data
from .refusal_templates import join_vi, render
from .reports.highlights import SectionData, build_highlights, highlights_block
from .suggestions import build_suggestions
from .types import (
    Decision,
    LLMClient,
    Reason,
    ReportSection,
    SectionStatus,
    StepState,
    TurnMode,
    TurnOutcome,
)
from .vi_format import format_date_range

logger = logging.getLogger("dms-chat-shaper")

CALL_TYPE_SYNTHESIS = "chat_synthesis"
LOOKUP_INTENTS = frozenset({Intent.LOOKUP_FEEDBACK, Intent.LOOKUP_SIMILAR, Intent.LOOKUP_FILE})
LOOKUP_MAX_SENTENCES = 2
SQL_RESULT_RULE = (
    "Luật truy vấn tự động: đây là kết quả truy vấn tự động theo yêu cầu; chỉ mô tả số liệu "
    "trong dữ kiện, không suy ra nguyên nhân."
)
LOOKUP_RULE = (
    "Luật tra cứu: tối đa 2 câu; chỉ mô tả điểm chung của các trích dẫn; "
    "không suy ra số lượng ngoài dữ kiện; không nhắc phản hồi hay mã không có trong trích dẫn."
)
SYNTHESIS_PROMPT_NAME = "synthesis_v1"
REPORT_SYNTHESIS_PROMPT_NAME = "synthesis_report_v1"
REPORT_MAX_SENTENCES_PER_SECTION = 2
SECTION_NO_DATA_TEXT = "Không có dữ liệu"
SYNTHESIS_TEMPERATURE = 0.2
SUMMARY_MAX_CHARS = 300
SUMMARY_MAX_FACTS = 3
QUESTION_MAX_CHARS = 500

# Lỗi hệ thống: phát ``error`` rồi ``done(error)``.
_ERROR_REASONS = frozenset({Reason.TIMEOUT, Reason.INTERNAL})


@dataclass(frozen=True)
class ComposerConfig:
    commentary_enabled: bool = True
    max_sentences: int = 4
    max_output_tokens: int = 400
    milestone: str = "M1"
    blocks: BlockConfig = field(default_factory=BlockConfig)
    # Báo cáo (b10 D6): nhiều câu hơn vì có nhiều phần.
    report_max_sentences: int = 8

    @classmethod
    def from_settings(cls, settings: Any) -> ComposerConfig:
        return cls(
            commentary_enabled=bool(settings.chat_commentary_enabled),
            max_sentences=int(settings.chat_commentary_max_sentences),
            max_output_tokens=int(settings.chat_commentary_max_output_tokens),
            milestone=str(settings.chat_milestone),
            blocks=BlockConfig.from_settings(settings),
            report_max_sentences=int(
                getattr(settings, "chat_report_commentary_max_sentences", 8)
            ),
        )


@dataclass(frozen=True)
class ComposeResult:
    status: DoneStatus
    summary: str
    commentary_status: CommentaryStatus
    dropped_sentences: int
    events: int


@dataclass(frozen=True)
class SynthesisPrompt:
    system_instruction: str
    user_prompt: str
    version: str
    sha256: str


class _Cancelled(Exception):
    """Sink báo huỷ; dừng dựng câu trả lời và phát ``done(cancelled)``."""


class _Emitter:
    """Cấp ``seq`` liên tục và kiểm huỷ trước mỗi event (design D1, D2)."""

    def __init__(self, sink: EventSink, seq_start: int = 0) -> None:
        self.sink = sink
        self.seq = seq_start

    def emit(
        self, event_type: EventType, data: dict[str, Any], *, check_cancel: bool = True
    ) -> None:
        if check_cancel and self.sink.is_cancelled():
            raise _Cancelled
        self.seq += 1
        self.sink.emit(AnswerEvent(seq=self.seq, type=event_type, data=data))

    def check_cancel(self) -> None:
        if self.sink.is_cancelled():
            raise _Cancelled


@dataclass
class _State:
    status: DoneStatus = DoneStatus.OK
    commentary_status: CommentaryStatus = CommentaryStatus.SKIPPED
    dropped_sentences: int = 0
    titles: list[str] = field(default_factory=list)
    sheet: FactSheet = field(default_factory=FactSheet)
    first_commentary: str = ""
    # Báo cáo: số câu đã phát cho từng phần (b10 D6).
    sentences_per_section: dict[str, int] = field(default_factory=dict)
    commentary_lines: list[str] = field(default_factory=list)
    highlights: tuple[str, ...] = ()
    section_refs: tuple[ReportSection, ...] = ()


class AnswerComposer:
    def __init__(
        self,
        *,
        llm: LLMClient | None = None,
        config: ComposerConfig | None = None,
        label_snapshot: dict[str, Any] | None = None,
        exporter: Any | None = None,
        budget_warning: Callable[[TurnOutcome], dict[str, Any] | None] | None = None,
    ) -> None:
        self.llm = llm
        self.config = config or ComposerConfig()
        self.label_snapshot = label_snapshot
        # ``ChatExporter`` (b10 D8); None thì khối ``export`` báo lỗi thay vì tạo file.
        self.exporter = exporter
        # Cảnh báo sắp hết hạn mức token (b11 D9); tính **sau** khi nhận định đã ghi usage.
        self.budget_warning = budget_warning

    # ── API ──

    def compose(
        self,
        outcome: TurnOutcome,
        sink: EventSink,
        *,
        llm: LLMClient | None = None,
        seq_start: int = 0,
    ) -> ComposeResult:
        """Phát event bắt đầu từ ``seq_start + 1``; runner b06 dùng để nối sau các ``status``."""
        if seq_start < 0:
            raise ValueError("seq_start must be >= 0")
        emitter = _Emitter(sink, seq_start)
        state = _State()
        client = llm if llm is not None else self.llm
        try:
            self._compose(outcome, emitter, state, client)
        except _Cancelled:
            state.status = DoneStatus.CANCELLED
        summary = build_summary(state.titles, state.sheet, state.first_commentary)
        done: dict[str, Any] = {
            "status": state.status.value,
            "summary": summary,
            "commentary_status": state.commentary_status.value,
            "dropped_sentences": state.dropped_sentences,
            "request_id": outcome.request_id,
        }
        warning = self._budget_warning(outcome)
        if warning:
            done["budget_warning"] = warning
        emitter.emit(EventType.DONE, done, check_cancel=False)
        return ComposeResult(
            status=state.status,
            summary=summary,
            commentary_status=state.commentary_status,
            dropped_sentences=state.dropped_sentences,
            events=emitter.seq,
        )

    def _budget_warning(self, outcome: TurnOutcome) -> dict[str, Any] | None:
        if self.budget_warning is None or not outcome.username:
            return None
        try:
            return self.budget_warning(outcome)
        except Exception:  # sổ usage lỗi không được làm hỏng câu trả lời
            logger.warning("chat_budget_warning_failed", extra={"request_id": outcome.request_id})
            return None

    # ── Luồng theo decision (design D8) ──

    def _compose(
        self, outcome: TurnOutcome, emitter: _Emitter, state: _State, llm: LLMClient | None
    ) -> None:
        if outcome.reason in _ERROR_REASONS or _first_step_failed(outcome):
            reason = outcome.reason if outcome.reason in _ERROR_REASONS else Reason.INTERNAL
            emitter.emit(EventType.ERROR, {"code": reason.value, "text": render(reason)})
            state.status = DoneStatus.ERROR
            return

        decision = outcome.decision
        if decision is Decision.CLARIFY:
            emitter.emit(
                EventType.CLARIFY,
                {
                    "text": outcome.message or render(Reason.INVALID_PLAN),
                    "options": list(outcome.clarify_options)[:5],
                },
            )
            state.status = DoneStatus.CLARIFY
            return

        if decision is Decision.HELP:
            self._compose_help(outcome, emitter, state)
            return

        if (
            decision in (Decision.REFUSE, Decision.NOT_SUPPORTED)
            or outcome.reason is Reason.UNAUTHORIZED_SCOPE
        ):
            self._compose_refusal(outcome, emitter, state)
            return

        if outcome.mode is TurnMode.REPORT:
            self._compose_report(outcome, emitter, state, llm)
            return
        self._compose_run(outcome, emitter, state, llm)

    def _compose_refusal(self, outcome: TurnOutcome, emitter: _Emitter, state: _State) -> None:
        if outcome.reason is not None:
            reason_code = outcome.reason.value
        elif outcome.guard is not None and outcome.guard.reason_code is not None:
            reason_code = outcome.guard.reason_code.value
        else:
            reason_code = Reason.OUT_OF_DOMAIN.value
        data: dict[str, Any] = {
            "reason": reason_code,
            "text": outcome.message or render(Reason.OUT_OF_DOMAIN),
            "partial": False,
        }
        dropped = [entity.value for entity in outcome.dropped_entities]
        if dropped:
            data["dropped_units"] = dropped
        emitter.emit(EventType.REFUSAL, data)
        self._emit_suggestions(outcome, emitter)
        state.status = (
            DoneStatus.NOT_SUPPORTED
            if outcome.decision is Decision.NOT_SUPPORTED
            else DoneStatus.REFUSED
        )

    def _compose_help(self, outcome: TurnOutcome, emitter: _Emitter, state: _State) -> None:
        planner_output = outcome.validated_plan.planner_output if outcome.validated_plan else None
        answer = build_help(
            getattr(planner_output, "help_topic", None),
            label=getattr(planner_output, "help_label", None),
            milestone=self.config.milestone,
            snapshot=self.label_snapshot,
        )
        if answer.needs_clarify:
            emitter.emit(
                EventType.CLARIFY, {"text": answer.text, "options": list(answer.clarify_options)}
            )
            state.status = DoneStatus.CLARIFY
            return
        text = answer.text
        if outcome.reason is Reason.TOPIC_RESET and outcome.message:
            # "Bắt đầu lại": xác nhận đã đổi chủ đề trước khi nhắc những gì hỏi được (b11 D6).
            text = f"{outcome.message}\n{answer.text}"
        emitter.emit(EventType.COMMENTARY, {"sentence_index": 1, "text": text})
        state.first_commentary = (
            outcome.message or text if outcome.reason is Reason.TOPIC_RESET else text
        )
        self._emit_suggestions(outcome, emitter)
        state.status = DoneStatus.HELP

    def _compose_run(
        self, outcome: TurnOutcome, emitter: _Emitter, state: _State, llm: LLMClient | None
    ) -> None:
        partial = False
        partial_notices = [n for n in outcome.notices if n.kind is Reason.PARTIAL_REFUSAL]
        if partial_notices:
            emitter.emit(
                EventType.REFUSAL,
                {
                    "reason": Reason.PARTIAL_REFUSAL.value,
                    "text": " ".join(notice.message for notice in partial_notices),
                    "partial": True,
                    "dropped_units": [entity.value for entity in outcome.dropped_entities],
                    "allowed_units": list(outcome.scope_units),
                },
            )
            partial = True
        if any(n.kind is Reason.SAMPLE_UNAVAILABLE for n in outcome.notices):
            partial = True
        relaxed = [n for n in outcome.notices if n.kind is Reason.RELAXED_SEARCH]
        if relaxed:
            # Kết quả vẫn đúng yêu cầu đã nới, nên không tính là trả lời một phần (b08 D4).
            emitter.emit(
                EventType.REFUSAL,
                {
                    "reason": Reason.RELAXED_SEARCH.value,
                    "text": " ".join(n.message for n in relaxed),
                    "partial": True,
                    "dropped_terms": list(relaxed[0].details.get("dropped_terms", [])),
                },
            )

        dates = _dates(outcome)
        range_text = format_date_range(dates.get("date_from"), dates.get("date_to"))
        subtitle = _subtitle(dates, outcome.scope_units)
        blocks = build_blocks(outcome.step_results, config=self.config.blocks, subtitle=subtitle)

        if not blocks:
            if outcome.reason in (Reason.NO_MATCH, Reason.NO_DATA_FILTERED) and outcome.message:
                text = outcome.message
            else:
                text = render(Reason.NO_DATA, range=range_text or "đã chọn")
            emitter.emit(EventType.COMMENTARY, {"sentence_index": 1, "text": text})
            state.first_commentary = text
            self._emit_suggestions(outcome, emitter, range_text=range_text)
            state.status = DoneStatus.NO_DATA
            return

        for block in blocks:
            emitter.emit(EventType.DATA_BLOCK, block.to_dict())
            state.titles.append(_title_with_range(block, range_text))

        state.sheet = build_fact_sheet(
            blocks,
            date_from=dates.get("date_from"),
            date_to=dates.get("date_to"),
            compare_from=dates.get("compare_from"),
            compare_to=dates.get("compare_to"),
        )
        self._compose_commentary(outcome, emitter, state, llm, blocks)
        # Xuất lại câu trả lời cũ đi qua nhánh này (b10 D7): khối export nằm sau nhận định.
        self._emit_export(outcome, emitter, state, blocks, range_text)
        self._emit_suggestions(outcome, emitter, range_text=range_text)
        state.status = DoneStatus.PARTIAL if partial else DoneStatus.OK

    # ── Báo cáo (b10 D3, D4) ──

    def _compose_report(
        self, outcome: TurnOutcome, emitter: _Emitter, state: _State, llm: LLMClient | None
    ) -> None:
        partial = self._emit_partial_notices(outcome, emitter)
        dates = _report_dates(outcome)
        range_text = format_date_range(dates.get("date_from"), dates.get("date_to"))
        subtitle = subtitle_for_range(
            dates.get("date_from"), dates.get("date_to"), outcome.scope_units
        )
        by_step = _blocks_by_step(outcome, config=self.config.blocks, subtitle=subtitle)
        rows_by_step = _rows_by_step(outcome)
        state.section_refs = tuple(outcome.sections)

        section_data = [
            SectionData(
                section_id=section.section_id,
                block=next(iter(by_step.get(section.step_index, [])), None),
                row=rows_by_step.get(section.step_index, {}),
            )
            for section in outcome.sections
        ]
        compare_text = format_date_range(dates.get("compare_from"), dates.get("compare_to"))
        state.highlights = build_highlights(
            section_data, compare_label=compare_text or "kỳ trước"
        )
        highlights = highlights_block(state.highlights)
        if highlights is not None:
            emitter.emit(EventType.DATA_BLOCK, highlights.to_dict())

        emitted_blocks: list[DataBlock] = []
        for section in outcome.sections:
            blocks = by_step.get(section.step_index, [])
            if section.status is SectionStatus.OK and blocks:
                for block in blocks:
                    with_section = replace(block, section=section.event_ref())
                    emitter.emit(EventType.DATA_BLOCK, with_section.to_dict())
                    emitted_blocks.append(with_section)
                    state.titles.append(_title_with_range(with_section, range_text))
                continue
            text = (
                SECTION_NO_DATA_TEXT
                if section.status is SectionStatus.NO_DATA
                else render(Reason.SECTION_UNAVAILABLE)
            )
            emitter.emit(EventType.DATA_BLOCK, _empty_section_block(section, text).to_dict())

        state.sheet = build_fact_sheet(
            emitted_blocks,
            date_from=dates.get("date_from"),
            date_to=dates.get("date_to"),
            compare_from=dates.get("compare_from"),
            compare_to=dates.get("compare_to"),
        )
        if emitted_blocks:
            self._compose_report_commentary(outcome, emitter, state, llm)
        self._emit_export(outcome, emitter, state, emitted_blocks, range_text)
        self._emit_suggestions(outcome, emitter, range_text=range_text)
        state.status = _report_status(outcome, partial=partial)

    def _compose_report_commentary(
        self, outcome: TurnOutcome, emitter: _Emitter, state: _State, llm: LLMClient | None
    ) -> None:
        """Một lần gọi LLM cho cả báo cáo; mỗi phần tối đa 2 câu (design D6)."""
        if not self.config.commentary_enabled or llm is None or not len(state.sheet):
            state.commentary_status = CommentaryStatus.SKIPPED
            return
        sections_with_data = tuple(
            section.section_id
            for section in outcome.sections
            if section.status is SectionStatus.OK
        )
        if not sections_with_data:
            state.commentary_status = CommentaryStatus.SKIPPED
            return

        emitter.emit(
            EventType.STATUS, {"stage": Stage.WRITING.value, "text": "Đang viết nhận định"}
        )
        max_sentences = self.config.report_max_sentences
        prompt = render_report_synthesis_prompt(
            outcome, state.sheet, sections_with_data, max_sentences=max_sentences
        )
        guard = SynthesisGuard(
            state.sheet, question=outcome.original_query, sections=sections_with_data
        )
        splitter = SentenceSplitter()
        emitted = 0
        stream = None
        try:
            stream = llm.stream(
                prompt.user_prompt,
                call_type=CALL_TYPE_SYNTHESIS,
                system_instruction=prompt.system_instruction,
                temperature=SYNTHESIS_TEMPERATURE,
                # Báo cáo dài hơn câu trả lời thường nên cho gấp đôi ngân sách token.
                max_output_tokens=self.config.max_output_tokens * 2,
            )
            done = False
            for chunk in stream:
                for sentence in splitter.feed(chunk):
                    emitted = self._emit_section_sentence(sentence, guard, emitter, state, emitted)
                    if emitted >= max_sentences:
                        done = True
                        break
                if done:
                    break
            if not done:
                for sentence in splitter.flush():
                    if emitted >= max_sentences:
                        break
                    emitted = self._emit_section_sentence(sentence, guard, emitter, state, emitted)
            if done:
                stream.close()
            state.commentary_status = (
                CommentaryStatus.FULL if emitted else CommentaryStatus.UNAVAILABLE
            )
        except _Cancelled:
            if stream is not None:
                stream.close()
            state.commentary_status = (
                CommentaryStatus.PARTIAL if emitted else CommentaryStatus.UNAVAILABLE
            )
            raise
        except Exception as exc:  # LLM lỗi: báo cáo vẫn đủ khối và "Điểm chính"
            logger.warning(
                "report_synthesis_failed",
                extra={"request_id": outcome.request_id, "error_type": type(exc).__name__},
            )
            if stream is not None:
                try:
                    stream.close()
                except Exception:
                    logger.debug("synthesis_stream_close_failed")
            state.commentary_status = (
                CommentaryStatus.PARTIAL if emitted else CommentaryStatus.UNAVAILABLE
            )

    def _emit_section_sentence(
        self,
        sentence: str,
        guard: SynthesisGuard,
        emitter: _Emitter,
        state: _State,
        emitted: int,
    ) -> int:
        emitter.check_cancel()
        result = guard.check(sentence)
        if not result.ok:
            state.dropped_sentences += 1
            return emitted
        used = state.sentences_per_section.get(result.section, 0)
        if used >= REPORT_MAX_SENTENCES_PER_SECTION:
            state.dropped_sentences += 1
            return emitted
        state.sentences_per_section[result.section] = used + 1
        emitted += 1
        section = next(
            (s for s in state.section_refs if s.section_id == result.section), None
        )
        data: dict[str, Any] = {"sentence_index": emitted, "text": result.text}
        if section is not None:
            data["section"] = section.event_ref()
        emitter.emit(EventType.COMMENTARY, data)
        state.commentary_lines.append(result.text)
        if emitted == 1:
            state.first_commentary = result.text
        return emitted

    def _emit_partial_notices(self, outcome: TurnOutcome, emitter: _Emitter) -> bool:
        partial_notices = [n for n in outcome.notices if n.kind is Reason.PARTIAL_REFUSAL]
        if partial_notices:
            emitter.emit(
                EventType.REFUSAL,
                {
                    "reason": Reason.PARTIAL_REFUSAL.value,
                    "text": " ".join(notice.message for notice in partial_notices),
                    "partial": True,
                    "dropped_units": [entity.value for entity in outcome.dropped_entities],
                    "allowed_units": list(outcome.scope_units),
                },
            )
        other = [n for n in outcome.notices if n.kind is Reason.TREND_RANGE_TOO_LONG]
        for notice in other:
            emitter.emit(
                EventType.REFUSAL,
                {"reason": notice.kind.value, "text": notice.message, "partial": True},
            )
        return bool(partial_notices)

    def _emit_export(
        self,
        outcome: TurnOutcome,
        emitter: _Emitter,
        state: _State,
        blocks: list[DataBlock],
        range_text: str,
    ) -> None:
        if not outcome.export_request:
            return
        if self.exporter is None or not blocks:
            emitter.emit(
                EventType.DATA_BLOCK,
                _export_block({"error": Reason.EXPORT_FAILED.value}),
            )
            return
        try:
            result = self.exporter.export(
                outcome=outcome,
                blocks=blocks,
                highlights=state.highlights,
                commentary=tuple(state.commentary_lines),
                range_text=range_text,
            )
        except Exception as exc:
            logger.warning(
                "chat_export_failed",
                extra={"request_id": outcome.request_id, "error_type": type(exc).__name__},
            )
            emitter.emit(
                EventType.DATA_BLOCK, _export_block({"error": Reason.EXPORT_FAILED.value})
            )
            return
        emitter.emit(EventType.DATA_BLOCK, _export_block(result))

    # ── Nhận định ──

    def _compose_commentary(
        self,
        outcome: TurnOutcome,
        emitter: _Emitter,
        state: _State,
        llm: LLMClient | None,
        blocks: list[DataBlock],
    ) -> None:
        sheet = state.sheet
        content_facts = [fact for fact in sheet if not _is_fact(fact.key, "range")]
        if not content_facts:
            state.commentary_status = CommentaryStatus.SKIPPED
            return

        single = _single_kpi(outcome, content_facts)
        if single is not None:
            label, value = single
            text = render(Reason.SINGLE_KPI, label=label, value=value)
            emitter.emit(EventType.COMMENTARY, {"sentence_index": 1, "text": text})
            state.first_commentary = text
            state.commentary_status = CommentaryStatus.SKIPPED
            return

        if not self.config.commentary_enabled or llm is None:
            state.commentary_status = CommentaryStatus.SKIPPED
            return

        emitter.emit(
            EventType.STATUS, {"stage": Stage.WRITING.value, "text": "Đang viết nhận định"}
        )
        max_sentences = self.config.max_sentences
        if outcome.intent in LOOKUP_INTENTS:
            max_sentences = min(max_sentences, LOOKUP_MAX_SENTENCES)
        prompt = render_synthesis_prompt(outcome, sheet, max_sentences=max_sentences)
        guard = SynthesisGuard(sheet, question=outcome.original_query)
        splitter = SentenceSplitter()
        emitted = 0
        stream = None
        try:
            stream = llm.stream(
                prompt.user_prompt,
                call_type=CALL_TYPE_SYNTHESIS,
                system_instruction=prompt.system_instruction,
                temperature=SYNTHESIS_TEMPERATURE,
                max_output_tokens=self.config.max_output_tokens,
            )
            done = False
            for chunk in stream:
                for sentence in splitter.feed(chunk):
                    emitted = self._emit_sentence(sentence, guard, emitter, state, emitted)
                    if emitted >= max_sentences:
                        done = True
                        break
                if done:
                    break
            if not done:
                for sentence in splitter.flush():
                    if emitted >= max_sentences:
                        break
                    emitted = self._emit_sentence(sentence, guard, emitter, state, emitted)
            if done:
                stream.close()
            state.commentary_status = (
                CommentaryStatus.FULL if emitted else CommentaryStatus.UNAVAILABLE
            )
        except _Cancelled:
            if stream is not None:
                stream.close()
            state.commentary_status = (
                CommentaryStatus.PARTIAL if emitted else CommentaryStatus.UNAVAILABLE
            )
            raise
        except Exception as exc:  # LLM lỗi, timeout, BLOCKED: dữ liệu vẫn đủ
            logger.warning(
                "synthesis_failed",
                extra={"request_id": outcome.request_id, "error_type": type(exc).__name__},
            )
            if stream is not None:
                try:
                    stream.close()
                except Exception:
                    logger.debug("synthesis_stream_close_failed")
            state.commentary_status = (
                CommentaryStatus.PARTIAL if emitted else CommentaryStatus.UNAVAILABLE
            )

    def _emit_sentence(
        self,
        sentence: str,
        guard: SynthesisGuard,
        emitter: _Emitter,
        state: _State,
        emitted: int,
    ) -> int:
        emitter.check_cancel()
        result = guard.check(sentence)
        if not result.ok:
            state.dropped_sentences += 1
            return emitted
        emitted += 1
        emitter.emit(EventType.COMMENTARY, {"sentence_index": emitted, "text": result.text})
        if emitted == 1:
            state.first_commentary = result.text
        return emitted

    def _emit_suggestions(
        self, outcome: TurnOutcome, emitter: _Emitter, *, range_text: str = ""
    ) -> None:
        unit = outcome.scope_units[0] if len(outcome.scope_units) == 1 else None
        items = build_suggestions(
            outcome.intent,
            milestone=self.config.milestone,
            unit=unit,
            range_text=f"trong {range_text}" if range_text else "",
            asked_question=outcome.original_query,
        )
        if items:
            emitter.emit(EventType.SUGGESTIONS, {"items": items})


# ── Prompt (design D6) ──


def render_synthesis_prompt(
    outcome: TurnOutcome, sheet: FactSheet, *, max_sentences: int = 4
) -> SynthesisPrompt:
    """``system_instruction`` chỉ chứa luật; dữ liệu của lượt nằm trong khối được đánh dấu."""
    system = load_prompt(SYNTHESIS_PROMPT_NAME, {"max_sentences": str(max_sentences)})
    facts = [fact for fact in sheet if not _is_fact(fact.key, "q")]
    quotes = [fact for fact in sheet if _is_fact(fact.key, "q")]

    lines = [
        f"<cau_hoi>{sanitize_prompt_data(outcome.rewritten_query or outcome.original_query, QUESTION_MAX_CHARS)}</cau_hoi>"
    ]
    if outcome.intent is not None:
        lines.append(f"Ý định: {outcome.intent.value}")
    if outcome.assumptions:
        lines.append(
            "Giả định: " + "; ".join(sanitize_prompt_data(a, 200) for a in outcome.assumptions)
        )
    if outcome.intent in LOOKUP_INTENTS:
        lines.append(LOOKUP_RULE)
    if any(step.function_name == "semantic_view" for step in outcome.step_results):
        lines.append(SQL_RESULT_RULE)
    if outcome.notices:
        lines.append(
            "Lưu ý: " + "; ".join(sanitize_prompt_data(n.message, 200) for n in outcome.notices)
        )
    lines.append("DỮ KIỆN")
    lines.append("<du_lieu>")
    lines.extend(_fact_line(fact) for fact in facts)
    lines.append("</du_lieu>")
    if quotes:
        lines.append("<trich_dan>")
        lines.extend(f"{fact.key} → {sanitize_prompt_data(fact.display, 200)}" for fact in quotes)
        lines.append("</trich_dan>")
    lines.append("Viết nhận định:")

    logger.info(
        "synthesis_prompt",
        extra={
            "request_id": outcome.request_id,
            "prompt_version": system.version,
            "prompt_sha256": system.sha256,
            "facts": len(sheet),
        },
    )
    return SynthesisPrompt(
        system_instruction=system.text,
        user_prompt="\n".join(lines),
        version=system.version,
        sha256=system.sha256,
    )


# ── Tóm tắt cho lịch sử (design D9) ──


def _fact_line(fact: Any) -> str:
    """Một dòng dữ kiện; kèm nhãn tiếng Việt để LLM không đoán nhầm đơn vị."""
    text = f"{fact.key} → {sanitize_prompt_data(fact.display, 200)}"
    label = getattr(fact, "label", "")
    if label:
        text += f" ({sanitize_prompt_data(label, 60)})"
    return text


def build_summary(titles: list[str], sheet: FactSheet, first_commentary: str) -> str:
    """Tóm tắt lượt cho lịch sử và trí nhớ phiên; **không** mang nội dung phản hồi (b11 D4)."""
    parts: list[str] = []
    if titles:
        parts.append("; ".join(titles))
    facts = [
        _summary_fact(fact.key, fact.display)
        for fact in sheet
        if not _is_fact(fact.key, "range") and not _is_fact(fact.key, "q")
    ][:SUMMARY_MAX_FACTS]
    if facts:
        parts.append(", ".join(facts))
    if first_commentary:
        parts.append(strip_quoted_content(first_commentary))
    summary = ". ".join(part.rstrip(".") for part in parts if part)
    if len(summary) > SUMMARY_MAX_CHARS:
        summary = summary[: SUMMARY_MAX_CHARS - 1].rstrip() + "…"
    return summary


# Trích dẫn trong câu nhận định: “nội dung” (MÃ) hoặc "nội dung" (MÃ).
_QUOTED_WITH_CODE = re.compile(
    r"[“\"]\s*(?P<content>[^”\"]{1,400}?)\s*[”\"]\s*\(\s*(?P<code>[^)]{1,40}?)\s*\)"
)
_QUOTED_ONLY = re.compile(r"[“\"]\s*(?P<content>[^”\"]{1,400}?)\s*[”\"]")
QUOTE_PLACEHOLDER = "(phản hồi {code})"
QUOTE_PLACEHOLDER_NO_CODE = "(một phản hồi)"


def strip_quoted_content(text: str) -> str:
    """Thay trích dẫn bằng ``(phản hồi <mã>)`` để trí nhớ không giữ nguyên văn phản hồi."""
    replaced = _QUOTED_WITH_CODE.sub(
        lambda match: QUOTE_PLACEHOLDER.format(code=match.group("code").strip()), text or ""
    )
    replaced = _QUOTED_ONLY.sub(QUOTE_PLACEHOLDER_NO_CODE, replaced)
    return " ".join(replaced.split())


# ── Tiện ích ──


def _summary_fact(key: str, display: str) -> str:
    """Kèm nhãn cho KPI để lịch sử đọc được: "Tổng số vấn đề 287" thay vì chỉ "287"."""
    from .function_catalog import KPI_LABELS_VI

    match = re.match(r"^(?:s\d+\.)?kpi\.([a-z_]+)$", key)
    if match and match.group(1) in KPI_LABELS_VI:
        return f"{KPI_LABELS_VI[match.group(1)]} {display}"
    return display


def _is_fact(key: str, family: str) -> bool:
    """``q.1`` và ``s2.q.1`` cùng thuộc họ ``q`` (fact của bước sau có tiền tố ``s<n>.``)."""
    return re.match(rf"^(s\d+\.)?{re.escape(family)}\.", key) is not None


def _first_step_failed(outcome: TurnOutcome) -> bool:
    if outcome.decision is not Decision.RUN or not outcome.step_results:
        return False
    first = outcome.step_results[0]
    return first.status.state is StepState.ERROR


def _dates(outcome: TurnOutcome) -> dict[str, str]:
    plan = outcome.validated_plan
    if plan is None or not plan.steps:
        return {}
    params = plan.steps[0].params
    dates = {
        key: str(params[key])
        for key in ("date_from", "date_to", "compare_from", "compare_to")
        if params.get(key)
    }
    if "compare_from" not in dates:
        # get_comparison tự lùi kỳ; kỳ trước chỉ có trong kết quả, không có trong tham số.
        previous = _comparison_previous_range(outcome)
        if previous is not None:
            dates["compare_from"], dates["compare_to"] = previous
    return dates


def _comparison_previous_range(outcome: TurnOutcome) -> tuple[str, str] | None:
    for step in outcome.step_results:
        if step.function_name != "get_comparison" or step.result is None:
            continue
        row = (step.result.data or [{}])[0]
        previous = row.get("previous_range") if isinstance(row, Mapping) else None
        if isinstance(previous, Mapping) and previous.get("from") and previous.get("to"):
            return str(previous["from"]), str(previous["to"])
    return None


def _subtitle(dates: Mapping[str, str], scope_units: Sequence[str]) -> str:
    """Phụ đề khối; có kỳ so sánh thì ghi cả hai để người đọc biết đang so với kỳ nào."""
    subtitle = subtitle_for_range(dates.get("date_from"), dates.get("date_to"), tuple(scope_units))
    compare_text = format_date_range(dates.get("compare_from"), dates.get("compare_to"))
    if not compare_text:
        return subtitle
    range_text = format_date_range(dates.get("date_from"), dates.get("date_to"))
    if range_text and subtitle.startswith(range_text):
        return f"{range_text} so với {compare_text}{subtitle[len(range_text):]}"
    return f"{subtitle} · so với {compare_text}" if subtitle else f"so với {compare_text}"


def _title_with_range(block: DataBlock, range_text: str) -> str:
    return f"{block.title} {range_text}".strip()


def _single_kpi(outcome: TurnOutcome, content_facts: list[Any]) -> tuple[str, str] | None:
    """Câu hỏi một con số: đúng một fact ``kpi.*`` (không tính vế so sánh) và shape ``number``."""
    plan = outcome.validated_plan
    shape = plan.planner_output.answer_shape if plan is not None else None
    if shape is not AnswerShape.NUMBER:
        return None
    kpis = [
        fact
        for fact in content_facts
        if fact.key.startswith("kpi.") and not fact.key.endswith(".prev")
    ]
    others = [fact for fact in content_facts if not fact.key.startswith("kpi.")]
    if len(kpis) != 1 or others:
        return None
    from .function_catalog import KPI_LABELS_VI

    key = kpis[0].key.split(".", 1)[1]
    return KPI_LABELS_VI.get(key, key), kpis[0].display



# ── Tiện ích của báo cáo (b10 D3, D4, D6) ──


def _report_dates(outcome: TurnOutcome) -> dict[str, str]:
    """Khoảng của báo cáo lấy từ ``TurnOutcome``; xuất lại câu trả lời cũ thì lấy từ bước."""
    if outcome.report_range is None:
        return _dates(outcome)
    dates = {
        "date_from": outcome.report_range.date_from.isoformat(),
        "date_to": outcome.report_range.date_to.isoformat(),
    }
    if outcome.compare_range is not None:
        dates["compare_from"] = outcome.compare_range.date_from.isoformat()
        dates["compare_to"] = outcome.compare_range.date_to.isoformat()
    return dates


def _blocks_by_step(
    outcome: TurnOutcome, *, config: BlockConfig, subtitle: str
) -> dict[int, list[DataBlock]]:
    by_step: dict[int, list[DataBlock]] = {}
    for step in outcome.step_results:
        blocks = build_blocks([step], config=config, subtitle=subtitle)
        if blocks:
            by_step[step.index] = blocks
    return by_step


def _rows_by_step(outcome: TurnOutcome) -> dict[int, dict[str, Any]]:
    rows: dict[int, dict[str, Any]] = {}
    for step in outcome.step_results:
        data = step.result.data if step.result is not None else None
        if data:
            rows[step.index] = dict(data[0])
    return rows


def _empty_section_block(section: ReportSection, text: str) -> DataBlock:
    """Phần không có dữ liệu hoặc lỗi: bảng rỗng kèm lời giải thích, không thêm kiểu event mới."""
    return DataBlock(
        block_id=f"report.{section.section_id}",
        kind="table",
        title=section.title,
        subtitle=text,
        payload={"columns": [], "rows": [], "total_rows": 0, "truncated": False},
        section=section.event_ref(),
    )


def _export_block(result: Mapping[str, Any]) -> dict[str, Any]:
    payload = {
        "export_id": result.get("export_id"),
        "filename": result.get("filename"),
        "size_bytes": result.get("size_bytes"),
        "expires_at": result.get("expires_at"),
        "rows_exported": result.get("rows_exported"),
        "truncated": bool(result.get("truncated")),
    }
    if result.get("error"):
        payload["error"] = str(result["error"])
    return {
        "block_id": "report.export",
        "kind": "export",
        "title": "File Excel",
        "subtitle": "",
        "payload": payload,
        "chart_hint": "none",
    }


def _report_status(outcome: TurnOutcome, *, partial: bool) -> DoneStatus:
    statuses = [section.status for section in outcome.sections]
    if not statuses:
        return DoneStatus.NO_DATA
    if all(status is SectionStatus.NO_DATA for status in statuses):
        return DoneStatus.NO_DATA
    if partial or any(
        status in (SectionStatus.ERROR, SectionStatus.SKIPPED) for status in statuses
    ):
        return DoneStatus.PARTIAL
    return DoneStatus.OK


def render_report_synthesis_prompt(
    outcome: TurnOutcome,
    sheet: FactSheet,
    sections: Sequence[str],
    *,
    max_sentences: int = 8,
) -> SynthesisPrompt:
    """Một prompt cho cả báo cáo; fact mang tiền tố phần (design D6)."""
    system = load_prompt(
        REPORT_SYNTHESIS_PROMPT_NAME,
        {
            "max_sentences": str(max_sentences),
            "max_per_section": str(REPORT_MAX_SENTENCES_PER_SECTION),
        },
    )
    titles = {section.section_id: section.title for section in outcome.sections}
    lines = [
        f"<cau_hoi>{sanitize_prompt_data(outcome.rewritten_query or outcome.original_query, QUESTION_MAX_CHARS)}</cau_hoi>"
    ]
    if outcome.report_type is not None:
        lines.append(f"Loại báo cáo: {outcome.report_type.value}")
    if outcome.assumptions:
        lines.append(
            "Giả định: " + "; ".join(sanitize_prompt_data(a, 200) for a in outcome.assumptions)
        )
    lines.append(
        "DANH SÁCH PHẦN: "
        + "; ".join(f"{section} ({titles.get(section, section)})" for section in sections)
    )
    lines.append("DỮ KIỆN")
    lines.append("<du_lieu>")
    lines.extend(
        f"{fact.key} → {sanitize_prompt_data(fact.display, 200)}"
        for fact in sheet
        if not _is_fact(fact.key.split(".", 1)[-1], "q")
    )
    lines.append("</du_lieu>")
    lines.append("Viết nhận định:")
    logger.info(
        "report_synthesis_prompt",
        extra={
            "request_id": outcome.request_id,
            "prompt_version": system.version,
            "prompt_sha256": system.sha256,
            "facts": len(sheet),
            "sections": list(sections),
        },
    )
    return SynthesisPrompt(
        system_instruction=system.text,
        user_prompt="\n".join(lines),
        version=system.version,
        sha256=system.sha256,
    )

__all__ = [
    "AnswerComposer",
    "render_report_synthesis_prompt",
    "ComposeResult",
    "ComposerConfig",
    "SynthesisPrompt",
    "build_summary",
    "strip_quoted_content",
    "join_vi",
    "render_synthesis_prompt",
]
