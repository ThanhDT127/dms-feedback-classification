"""Chạy một lượt chat trong thread pool, tách "sinh" khỏi "gửi" (design b06 D3, D6, D7).

``submit`` tạo answer trong buffer rồi trả về ngay; WS handler đọc buffer để gửi. Mọi lỗi
trong lượt đều thành ``error`` + ``done(error)`` và answer luôn được ``mark_complete``.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Protocol

from ...settings import Settings
from ..ai.answer_events import AnswerEvent, CommentaryStatus, DoneStatus, EventSink, EventType
from ..ai.budget.request_context import ChatRequestContext, bind
from ..ai.response_shaper import ComposeResult
from ..ai.types import TurnCancelled, TurnOutcome, TurnRequest
from .answer_buffer import AnswerBuffer
from .buffer_sink import BufferSink

logger = logging.getLogger("dms-chat")

STAGE_TEXTS: dict[str, str] = {
    "understanding": "Đang đọc câu hỏi",
    "planning": "Đang xác định số liệu cần lấy",
    "querying": "Đang truy vấn dữ liệu",
}
INTERNAL_ERROR_TEXT = "Hệ thống đang gặp sự cố khi trả lời, vui lòng thử lại."


class TurnHandler(Protocol):
    def handle(
        self,
        turn: TurnRequest,
        *,
        on_stage: Callable[[str], None] | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> TurnOutcome: ...


class Composer(Protocol):
    def compose(
        self, outcome: TurnOutcome, sink: EventSink, *, seq_start: int = 0
    ) -> ComposeResult: ...


class RunnerBusy(Exception):
    """User đã có đủ lượt đang chạy (``error(BUSY)``)."""


@dataclass(frozen=True)
class RunnerConfig:
    max_concurrent_turns: int = 4
    max_active_turns_per_user: int = 1
    answer_ttl_seconds: int = 600

    @classmethod
    def from_settings(cls, settings: Settings) -> RunnerConfig:
        return cls(
            max_concurrent_turns=int(settings.chat_max_concurrent_turns),
            max_active_turns_per_user=int(settings.chat_max_active_turns_per_user),
            answer_ttl_seconds=int(settings.chat_answer_buffer_ttl_seconds),
        )


@dataclass
class TurnRecord:
    """Mọi thứ về một lượt đã xong, đưa cho các finalizer (lưu phiên, audit)."""

    answer_id: str
    username: str
    session_id: str
    client_msg_id: str
    turn: TurnRequest
    outcome: TurnOutcome | None = None
    compose: ComposeResult | None = None
    done_status: DoneStatus = DoneStatus.ERROR
    summary: str = ""
    events: list[dict[str, Any]] = field(default_factory=list)
    queue_ms: int = 0
    total_ms: int = 0
    error: str | None = None


Finalizer = Callable[[TurnRecord], None]


class ChatTurnRunner:
    def __init__(
        self,
        *,
        buffer: AnswerBuffer,
        orchestrator: TurnHandler,
        composer: Composer,
        config: RunnerConfig | None = None,
        starters: Sequence[Finalizer] = (),
        finalizers: Sequence[Finalizer] = (),
        background: Sequence[Finalizer] = (),
    ) -> None:
        """``starters`` chạy trong thread của lượt trước orchestrator (vd. ghi tin nhắn user),
        ``finalizers`` chạy sau ``mark_complete``; lỗi của cả hai chỉ được ghi log.

        ``background`` (vd. tóm tắt phiên, b11 D2) được gửi vào pool **sau** khi lượt xong và
        chỉ khi pool còn rảnh, nên không bao giờ làm chậm ``done`` của lượt nào.
        """
        self.buffer = buffer
        self.orchestrator = orchestrator
        self.composer = composer
        self.config = config or RunnerConfig()
        self.starters = list(starters)
        self.finalizers = list(finalizers)
        self.background = list(background)
        self._pool = ThreadPoolExecutor(
            max_workers=self.config.max_concurrent_turns, thread_name_prefix="chat-turn"
        )
        self._admit_lock = threading.Lock()
        self._inflight = 0
        self._inflight_lock = threading.Lock()

    def submit(
        self,
        *,
        answer_id: str,
        username: str,
        session_id: str,
        client_msg_id: str,
        turn: TurnRequest,
    ) -> Future[TurnRecord]:
        """Tạo answer và đưa lượt vào hàng đợi; ném ``RunnerBusy`` nếu vượt giới hạn user."""
        with self._admit_lock:
            if self.buffer.active_count(username) >= self.config.max_active_turns_per_user:
                raise RunnerBusy(username)
            self.buffer.create(
                answer_id,
                owner=username,
                session_id=session_id,
                ttl_seconds=self.config.answer_ttl_seconds,
            )
        record = TurnRecord(
            answer_id=answer_id,
            username=username,
            session_id=session_id,
            client_msg_id=client_msg_id,
            turn=turn,
        )
        submitted = time.monotonic()
        # Mọi lời gọi LLM của lượt (kể cả tóm tắt nền) được ghi sổ usage theo user (b11 D8).
        context = ChatRequestContext(
            username=username,
            request_id=turn.request_id or answer_id,
            session_id=session_id,
        )
        self._track(+1)
        try:
            return self._pool.submit(bind(context, self._run), record, submitted, context)
        except RuntimeError:  # pool đã shutdown
            self._track(-1)
            self.buffer.mark_complete(answer_id)
            raise

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)

    # ── Một lượt ──

    def _run(
        self,
        record: TurnRecord,
        submitted: float,
        context: ChatRequestContext | None = None,
    ) -> TurnRecord:
        try:
            return self._run_turn(record, submitted)
        finally:
            self._track(-1)
            self._submit_background(record, context)

    def _run_turn(self, record: TurnRecord, submitted: float) -> TurnRecord:
        started = time.monotonic()
        record.queue_ms = int((started - submitted) * 1000)
        sink = BufferSink(self.buffer, record.answer_id)
        self._call_hooks(self.starters, record, "chat_turn_starter_failed")
        try:
            self._produce(record, sink)
        except Exception as exc:  # buffer hỏng/hết hạn: không còn nơi để báo lỗi
            record.done_status = DoneStatus.ERROR
            record.error = f"{type(exc).__name__}: {exc}"[:300]
            logger.exception(
                "chat_turn_buffer_failed",
                extra={"answer_id": record.answer_id, "request_id": record.turn.request_id},
            )
        finally:
            record.events = list(sink.events)
            record.total_ms = int((time.monotonic() - submitted) * 1000)
            self.buffer.mark_complete(record.answer_id)
            self._call_hooks(self.finalizers, record, "chat_turn_finalizer_failed")
        return record

    def _produce(self, record: TurnRecord, sink: BufferSink) -> None:
        def on_stage(stage: str) -> None:
            sink.emit(
                AnswerEvent(
                    seq=sink.last_seq + 1,
                    type=EventType.STATUS,
                    data={"stage": stage, "text": STAGE_TEXTS.get(stage, stage)},
                )
            )

        try:
            outcome = self.orchestrator.handle(
                record.turn, on_stage=on_stage, should_cancel=sink.is_cancelled
            )
            record.outcome = outcome
            result = self.composer.compose(outcome, sink, seq_start=sink.last_seq)
            record.compose = result
            record.done_status = result.status
            record.summary = result.summary
        except TurnCancelled:
            record.done_status = DoneStatus.CANCELLED
            self._emit_done(sink, record, DoneStatus.CANCELLED)
        except Exception as exc:
            record.error = f"{type(exc).__name__}: {exc}"[:300]
            logger.exception(
                "chat_turn_failed",
                extra={"answer_id": record.answer_id, "request_id": record.turn.request_id},
            )
            record.done_status = DoneStatus.ERROR
            if not _has_done(sink):
                sink.emit(
                    AnswerEvent(
                        seq=sink.last_seq + 1,
                        type=EventType.ERROR,
                        data={"code": "INTERNAL", "text": INTERNAL_ERROR_TEXT},
                    )
                )
                self._emit_done(sink, record, DoneStatus.ERROR)

    # ── Tác vụ nền ưu tiên thấp (b11 D2) ──

    def _submit_background(
        self, record: TurnRecord, context: ChatRequestContext | None
    ) -> None:
        if not self.background or record.done_status is DoneStatus.CANCELLED:
            return
        if self.inflight >= self.config.max_concurrent_turns:
            # Pool đang bận: bỏ lần này, lượt sau sẽ gộp luôn phần còn thiếu.
            logger.info("chat_background_skipped_busy", extra={"answer_id": record.answer_id})
            return
        for hook in self.background:
            self._track(+1)
            try:
                self._pool.submit(bind(context, self._run_background), hook, record)
            except RuntimeError:  # pool đã shutdown
                self._track(-1)
                return

    def _run_background(self, hook: Finalizer, record: TurnRecord) -> None:
        try:
            self._call_hooks([hook], record, "chat_background_failed")
        finally:
            self._track(-1)

    @property
    def inflight(self) -> int:
        """Số lượt và tác vụ nền đang chạy hoặc đang chờ trong pool."""
        with self._inflight_lock:
            return self._inflight

    def _track(self, delta: int) -> None:
        with self._inflight_lock:
            self._inflight = max(0, self._inflight + delta)

    @staticmethod
    def _emit_done(sink: BufferSink, record: TurnRecord, status: DoneStatus) -> None:
        sink.emit(
            AnswerEvent(
                seq=sink.last_seq + 1,
                type=EventType.DONE,
                data={
                    "status": status.value,
                    "summary": "",
                    "commentary_status": CommentaryStatus.SKIPPED.value,
                    "dropped_sentences": 0,
                    "request_id": record.turn.request_id,
                },
            )
        )

    @staticmethod
    def _call_hooks(hooks: Sequence[Finalizer], record: TurnRecord, event: str) -> None:
        for hook in hooks:
            try:
                hook(record)
            except Exception:
                logger.exception(event, extra={"answer_id": record.answer_id, "hook": _name(hook)})


def _has_done(sink: BufferSink) -> bool:
    return bool(sink.events) and sink.events[-1]["type"] == EventType.DONE.value


def _name(func: object) -> str:
    return getattr(func, "__qualname__", None) or type(func).__name__
