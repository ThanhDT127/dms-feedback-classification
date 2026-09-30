"""Test ``ChatTurnRunner`` với orchestrator/composer giả (b06 task 2.5)."""

from __future__ import annotations

import threading
import time

import pytest

from dms.chat.ai.answer_events import AnswerEvent, CommentaryStatus, DoneStatus, EventType
from dms.chat.ai.response_shaper import AnswerComposer, ComposeResult
from dms.chat.ai.types import Decision, TurnCancelled, TurnOutcome, TurnRequest
from dms.chat.contract import UserScope
from dms.chat.ws.answer_buffer import InMemoryAnswerBuffer
from dms.chat.ws.turn_runner import (
    INTERNAL_ERROR_TEXT,
    ChatTurnRunner,
    RunnerBusy,
    RunnerConfig,
    TurnRecord,
)

SCOPE = UserScope(username="alice", role="user", display_name="Alice", unit_ids=["TV1"])


def turn_request(question: str = "Tổng quan tháng 8") -> TurnRequest:
    return TurnRequest(question=question, scope=SCOPE, session_id="s1", request_id="req-1")


def outcome() -> TurnOutcome:
    return TurnOutcome(
        request_id="req-1",
        original_query="Tổng quan tháng 8",
        rewritten_query="Tổng quan tháng 8",
        decision=Decision.HELP,
    )


class FakeOrchestrator:
    def __init__(self, *, stages=("understanding", "planning", "querying"), error=None, gate=None):
        self.stages = stages
        self.error = error
        self.gate = gate

    def handle(self, turn, *, on_stage=None, should_cancel=None):
        for stage in self.stages:
            if should_cancel and should_cancel():
                raise TurnCancelled(stage)
            if on_stage:
                on_stage(stage)
            if self.gate is not None:
                self.gate.wait(timeout=5)
        if self.error:
            raise self.error
        return outcome()


class FakeComposer:
    def __init__(self, *, fail_after_first: bool = False) -> None:
        self.fail_after_first = fail_after_first
        self.seq_start = None

    def compose(self, outcome, sink, *, seq_start=0):
        self.seq_start = seq_start
        seq = seq_start + 1
        sink.emit(AnswerEvent(seq, EventType.COMMENTARY, {"text": "Xin chào."}))
        if self.fail_after_first:
            raise RuntimeError("boom")
        sink.emit(AnswerEvent(seq + 1, EventType.DONE, {"status": "ok", "summary": "tóm tắt"}))
        return ComposeResult(DoneStatus.OK, "tóm tắt", CommentaryStatus.FULL, 0, seq + 1)


def make_runner(orchestrator=None, composer=None, *, finalizers=(), **config):
    buffer = InMemoryAnswerBuffer()
    runner = ChatTurnRunner(
        buffer=buffer,
        orchestrator=orchestrator or FakeOrchestrator(),
        composer=composer or FakeComposer(),
        config=RunnerConfig(**config),
        finalizers=finalizers,
    )
    return runner, buffer


def submit(runner, answer_id="a1", username="alice"):
    return runner.submit(
        answer_id=answer_id,
        username=username,
        session_id="s1",
        client_msg_id="c1",
        turn=turn_request(),
    )


def events_of(buffer, answer_id="a1"):
    read = buffer.read(answer_id, 0, timeout_ms=0)
    assert read.complete, "answer phải được mark_complete"
    seqs = [e["seq"] for e in read.events]
    assert seqs == list(range(1, len(seqs) + 1))
    assert read.events[-1]["type"] == "done"
    return read.events


def test_status_events_then_composer_events_with_contiguous_seq():
    composer = FakeComposer()
    runner, buffer = make_runner(composer=composer)
    record = submit(runner).result(timeout=5)

    events = events_of(buffer)
    assert [e["type"] for e in events] == ["status", "status", "status", "commentary", "done"]
    assert [e["data"]["stage"] for e in events[:3]] == ["understanding", "planning", "querying"]
    assert all(e["data"]["text"] for e in events[:3])
    assert composer.seq_start == 3
    assert record.done_status is DoneStatus.OK
    assert record.summary == "tóm tắt"
    assert record.events == list(events)
    assert record.queue_ms >= 0 and record.total_ms >= record.queue_ms


def test_real_composer_continues_after_status():
    runner, buffer = make_runner(composer=AnswerComposer())
    submit(runner).result(timeout=5)

    events = events_of(buffer)
    assert events[0]["type"] == "status"
    assert events[-1]["data"]["status"] == "help"


def test_unexpected_orchestrator_error_becomes_error_and_done():
    runner, buffer = make_runner(FakeOrchestrator(error=RuntimeError("sqlite secret path")))
    record = submit(runner).result(timeout=5)

    events = events_of(buffer)
    assert events[-2]["type"] == "error"
    assert events[-2]["data"] == {"code": "INTERNAL", "text": INTERNAL_ERROR_TEXT}
    assert events[-1]["data"]["status"] == "error"
    assert "secret" not in str(events)
    assert record.done_status is DoneStatus.ERROR and "sqlite" in record.error


def test_composer_failure_mid_stream_still_ends_with_done():
    runner, buffer = make_runner(composer=FakeComposer(fail_after_first=True))
    submit(runner).result(timeout=5)

    events = events_of(buffer)
    assert [e["type"] for e in events[-3:]] == ["commentary", "error", "done"]


def test_cancel_during_turn_ends_with_done_cancelled():
    gate = threading.Event()
    runner, buffer = make_runner(FakeOrchestrator(gate=gate))
    future = submit(runner)
    buffer.read("a1", 0, timeout_ms=2000)  # đợi status đầu tiên
    buffer.request_cancel("a1")
    gate.set()

    record = future.result(timeout=5)

    events = events_of(buffer)
    assert events[-1]["data"]["status"] == "cancelled"
    assert record.done_status is DoneStatus.CANCELLED
    assert not any(e["type"] == "commentary" for e in events)


def test_client_disconnect_does_not_cancel():
    """Runner không biết gì về kết nối: không ai đọc buffer thì lượt vẫn chạy xong."""
    runner, buffer = make_runner()
    record = submit(runner).result(timeout=5)
    assert record.done_status is DoneStatus.OK
    assert buffer.meta("a1").complete is True


def test_second_active_turn_of_same_user_is_busy():
    gate = threading.Event()
    runner, buffer = make_runner(FakeOrchestrator(gate=gate))
    first = submit(runner, "a1")
    try:
        with pytest.raises(RunnerBusy):
            submit(runner, "a2")
        assert buffer.meta("a2") is None
        other = submit(runner, "b1", username="bob")  # user khác không bị chặn
    finally:
        gate.set()
    first.result(timeout=5)
    other.result(timeout=5)
    submit(runner, "a3").result(timeout=5)  # xong lượt trước thì hỏi tiếp được


def test_pool_limits_concurrency_and_measures_queue_time():
    gate = threading.Event()
    runner, _ = make_runner(FakeOrchestrator(gate=gate), max_concurrent_turns=1)
    first = submit(runner, "a1", username="u1")
    second = submit(runner, "a2", username="u2")
    time.sleep(0.1)
    gate.set()
    first.result(timeout=5)
    record = second.result(timeout=5)
    assert record.queue_ms >= 80


def test_finalizer_failure_is_isolated_and_all_finalizers_run():
    seen: list[TurnRecord] = []

    def broken(record):
        raise RuntimeError("db down")

    runner, buffer = make_runner(finalizers=[broken, seen.append])
    submit(runner).result(timeout=5)

    assert len(seen) == 1 and seen[0].answer_id == "a1"
    assert buffer.meta("a1").complete is True


def test_buffer_failure_still_completes_and_finalizes():
    seen: list[TurnRecord] = []
    runner, buffer = make_runner(finalizers=[seen.append])

    def broken_append(answer_id, event):
        raise ConnectionError("redis gone")

    buffer.append = broken_append  # type: ignore[method-assign]
    record = submit(runner).result(timeout=5)

    assert record.done_status is DoneStatus.ERROR
    assert buffer.meta("a1").complete is True
    assert seen == [record]


def test_shutdown_rejects_new_turns_without_leaving_active_answers():
    runner, buffer = make_runner()
    runner.shutdown()
    with pytest.raises(RuntimeError):
        submit(runner)
    assert buffer.active_count("alice") == 0


def test_starters_run_before_orchestrator_and_failures_are_isolated():
    order: list[str] = []

    class RecordingOrchestrator(FakeOrchestrator):
        def handle(self, turn, **kwargs):
            order.append("handle")
            return super().handle(turn, **kwargs)

    def broken(record):
        raise RuntimeError("db down")

    runner, _ = make_runner(
        RecordingOrchestrator(), finalizers=[lambda r: order.append("finalize")]
    )
    runner.starters = [broken, lambda r: order.append("start")]
    record = submit(runner).result(timeout=5)

    assert order == ["start", "handle", "finalize"]
    assert record.done_status is DoneStatus.OK
