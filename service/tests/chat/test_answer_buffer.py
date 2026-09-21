"""Bộ test contract của ``AnswerBuffer`` (design b06 D4).

Tham số hoá theo bản cài: Dev A thêm factory của bản Redis vào ``BUFFER_FACTORIES`` và
chạy lại đúng bộ này. Factory nhận ``clock`` giả để test TTL không phải ngủ thật.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

import pytest

from dms.chat.ws.answer_buffer import AnswerBuffer, InMemoryAnswerBuffer


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


BufferFactory = Callable[[FakeClock], AnswerBuffer]

BUFFER_FACTORIES: dict[str, BufferFactory] = {
    "in_memory": lambda clock: InMemoryAnswerBuffer(stale_after_seconds=120, wall_clock=clock),
}

# Bản in-memory dùng đồng hồ thật cho monotonic để read có timeout; TTL test dùng factory riêng.
TTL_FACTORIES: dict[str, BufferFactory] = {
    "in_memory": lambda clock: InMemoryAnswerBuffer(
        stale_after_seconds=120, monotonic=clock, wall_clock=clock
    ),
}


@pytest.fixture(params=sorted(BUFFER_FACTORIES))
def buffer(request) -> AnswerBuffer:
    return BUFFER_FACTORIES[request.param](FakeClock())


@pytest.fixture(params=sorted(TTL_FACTORIES))
def timed(request) -> tuple[AnswerBuffer, FakeClock]:
    clock = FakeClock()
    return TTL_FACTORIES[request.param](clock), clock


def ev(seq: int, type_: str = "status") -> dict:
    return {"seq": seq, "type": type_, "data": {"n": seq}}


def test_append_and_read_from_start(buffer):
    buffer.create("a1", owner="alice", session_id="s1", ttl_seconds=600)
    buffer.append("a1", ev(1))
    buffer.append("a1", ev(2))

    read = buffer.read("a1", 0, timeout_ms=0)

    assert [e["seq"] for e in read.events] == [1, 2]
    assert read.events[1] == ev(2)
    assert read.complete is False and read.expired is False


def test_read_after_seq_returns_only_newer_events(buffer):
    buffer.create("a1", owner="alice", session_id="s1", ttl_seconds=600)
    for seq in range(1, 5):
        buffer.append("a1", ev(seq))

    assert [e["seq"] for e in buffer.read("a1", 2, timeout_ms=0).events] == [3, 4]
    assert buffer.read("a1", 4, timeout_ms=0).events == ()


@pytest.mark.parametrize("bad_seq", [0, 2, None])
def test_append_rejects_non_contiguous_seq(buffer, bad_seq):
    buffer.create("a1", owner="alice", session_id="s1", ttl_seconds=600)
    with pytest.raises(ValueError):
        buffer.append("a1", {"seq": bad_seq, "type": "status", "data": {}})


def test_append_after_seq_gap_is_rejected(buffer):
    buffer.create("a1", owner="alice", session_id="s1", ttl_seconds=600)
    buffer.append("a1", ev(1))
    with pytest.raises(ValueError):
        buffer.append("a1", ev(3))
    buffer.append("a1", ev(2))


def test_create_twice_is_rejected(buffer):
    buffer.create("a1", owner="alice", session_id="s1", ttl_seconds=600)
    with pytest.raises(ValueError):
        buffer.create("a1", owner="alice", session_id="s1", ttl_seconds=600)


def test_read_times_out_without_events(buffer):
    buffer.create("a1", owner="alice", session_id="s1", ttl_seconds=600)
    started = time.monotonic()

    read = buffer.read("a1", 0, timeout_ms=150)

    assert read.events == () and read.complete is False and read.expired is False
    assert 0.1 <= time.monotonic() - started < 2


def test_blocking_read_wakes_on_append(buffer):
    buffer.create("a1", owner="alice", session_id="s1", ttl_seconds=600)
    timer = threading.Timer(0.05, lambda: buffer.append("a1", ev(1)))
    timer.start()
    started = time.monotonic()
    try:
        read = buffer.read("a1", 0, timeout_ms=3000)
    finally:
        timer.join()

    assert [e["seq"] for e in read.events] == [1]
    assert time.monotonic() - started < 2


def test_blocking_read_wakes_on_complete(buffer):
    buffer.create("a1", owner="alice", session_id="s1", ttl_seconds=600)
    buffer.append("a1", ev(1))
    timer = threading.Timer(0.05, lambda: buffer.mark_complete("a1"))
    timer.start()
    try:
        read = buffer.read("a1", 1, timeout_ms=3000)
    finally:
        timer.join()

    assert read.events == () and read.complete is True


def test_complete_answer_is_still_readable_and_rejects_append(buffer):
    buffer.create("a1", owner="alice", session_id="s1", ttl_seconds=600)
    buffer.append("a1", ev(1))
    buffer.mark_complete("a1")
    buffer.mark_complete("a1")  # idempotent

    read = buffer.read("a1", 0, timeout_ms=0)
    assert [e["seq"] for e in read.events] == [1] and read.complete is True
    with pytest.raises(ValueError):
        buffer.append("a1", ev(2))


def test_unknown_answer_reads_as_expired(buffer):
    assert buffer.read("missing", 0, timeout_ms=0).expired is True
    assert buffer.meta("missing") is None  # không tồn tại ≠ hết hạn
    assert buffer.is_cancel_requested("missing") is False
    buffer.request_cancel("missing")
    buffer.mark_complete("missing")


def test_meta_reports_owner_session_and_completion(buffer):
    buffer.create("a1", owner="alice", session_id="s1", ttl_seconds=600)
    meta = buffer.meta("a1")
    assert meta is not None
    assert (meta.owner, meta.session_id, meta.complete) == ("alice", "s1", False)
    assert meta.created_at > 0

    buffer.mark_complete("a1")
    assert buffer.meta("a1").complete is True


def test_cancel_flag(buffer):
    buffer.create("a1", owner="alice", session_id="s1", ttl_seconds=600)
    assert buffer.is_cancel_requested("a1") is False
    buffer.request_cancel("a1")
    assert buffer.is_cancel_requested("a1") is True


def test_active_count_counts_only_incomplete_answers_of_owner(buffer):
    buffer.create("a1", owner="alice", session_id="s1", ttl_seconds=600)
    buffer.create("a2", owner="alice", session_id="s1", ttl_seconds=600)
    buffer.create("b1", owner="bob", session_id="s2", ttl_seconds=600)
    buffer.mark_complete("a2")

    assert buffer.active_count("alice") == 1
    assert buffer.active_count("bob") == 1
    assert buffer.active_count("carol") == 0


def test_ttl_starts_at_completion(timed):
    buffer, clock = timed
    buffer.create("a1", owner="alice", session_id="s1", ttl_seconds=600)
    buffer.append("a1", ev(1))
    clock.advance(100)  # chưa hoàn tất: không tính TTL
    buffer.mark_complete("a1")
    clock.advance(599)
    assert buffer.read("a1", 0, timeout_ms=0).complete is True

    clock.advance(2)
    assert buffer.read("a1", 0, timeout_ms=0).expired is True
    meta = buffer.meta("a1")
    assert meta is not None and meta.expired is True and meta.owner == "alice"


def test_stale_incomplete_answer_expires_and_frees_active_slot(timed):
    buffer, clock = timed
    buffer.create("a1", owner="alice", session_id="s1", ttl_seconds=600)
    assert buffer.active_count("alice") == 1

    clock.advance(121)

    assert buffer.active_count("alice") == 0
    assert buffer.read("a1", 0, timeout_ms=0).expired is True


def test_concurrent_writer_and_reader_see_every_event_in_order(buffer):
    buffer.create("a1", owner="alice", session_id="s1", ttl_seconds=600)
    total = 200

    def writer() -> None:
        for seq in range(1, total + 1):
            buffer.append("a1", ev(seq))
        buffer.mark_complete("a1")

    thread = threading.Thread(target=writer)
    thread.start()
    seen: list[int] = []
    while True:
        read = buffer.read("a1", len(seen), timeout_ms=1000)
        seen.extend(e["seq"] for e in read.events)
        if read.complete and len(seen) == total:
            break
    thread.join()

    assert seen == list(range(1, total + 1))


def test_expired_answer_id_cannot_be_reused(timed):
    buffer, clock = timed
    buffer.create("a1", owner="alice", session_id="s1", ttl_seconds=10)
    buffer.mark_complete("a1")
    clock.advance(11)
    assert buffer.meta("a1").expired is True
    with pytest.raises(ValueError):
        buffer.create("a1", owner="bob", session_id="s2", ttl_seconds=10)


def test_live_meta_is_not_expired(buffer):
    buffer.create("a1", owner="alice", session_id="s1", ttl_seconds=600)
    assert buffer.meta("a1").expired is False
