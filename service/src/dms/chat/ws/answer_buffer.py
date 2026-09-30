"""Answer Buffer: nơi runner ghi event và WS handler đọc lại (design b06 D3–D5).

``AnswerBuffer`` là Protocol để Dev A cài bản dùng chung giữa các worker (Redis);
``InMemoryAnswerBuffer`` chỉ đúng khi chạy **một** worker. Bộ test contract nằm ở
``tests/chat/test_answer_buffer.py`` và được tham số hoá theo bản cài.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class BufferRead:
    events: tuple[dict[str, Any], ...] = ()
    complete: bool = False
    expired: bool = False


@dataclass(frozen=True)
class AnswerMeta:
    owner: str
    session_id: str
    complete: bool
    created_at: float
    # True khi answer đã hết hạn nhưng buffer còn nhớ chủ sở hữu: resume trả ``answer_expired``
    # thay vì ``ANSWER_NOT_FOUND`` (spec chat-answer-resume).
    expired: bool = False


class AnswerBuffer(Protocol):
    def create(self, answer_id: str, *, owner: str, session_id: str, ttl_seconds: int) -> None: ...

    def append(self, answer_id: str, event: Mapping[str, Any]) -> None: ...

    def read(self, answer_id: str, after_seq: int, *, timeout_ms: int) -> BufferRead: ...

    def mark_complete(self, answer_id: str) -> None: ...

    def request_cancel(self, answer_id: str) -> None: ...

    def is_cancel_requested(self, answer_id: str) -> bool: ...

    def meta(self, answer_id: str) -> AnswerMeta | None: ...

    def active_count(self, owner: str) -> int: ...


@dataclass
class _Entry:
    owner: str
    session_id: str
    ttl_seconds: int
    created_at: float
    created_mono: float
    events: list[dict[str, Any]] = field(default_factory=list)
    complete: bool = False
    completed_mono: float | None = None
    cancel_requested: bool = False


class InMemoryAnswerBuffer:
    """Buffer trong tiến trình dùng ``threading.Condition``.

    - TTL tính từ ``mark_complete``.
    - Answer chưa hoàn tất quá ``stale_after_seconds`` (mặc định ``2 × CHAT_TURN_TIMEOUT``)
      được coi là hết hạn để không giữ ``active_count`` mãi khi runner chết.
    - Answer hết hạn để lại "bia mộ" (chủ, phiên) trong ``tombstone_seconds``, tối đa
      ``max_tombstones`` bản, để ``meta`` báo ``expired``.
    """

    def __init__(
        self,
        *,
        stale_after_seconds: float = 120.0,
        tombstone_seconds: float = 86400.0,
        max_tombstones: int = 10000,
        monotonic: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
    ) -> None:
        self._stale_after = stale_after_seconds
        self._monotonic = monotonic
        self._wall_clock = wall_clock
        self._entries: dict[str, _Entry] = {}
        self._tombstone_seconds = tombstone_seconds
        self._max_tombstones = max_tombstones
        # answer_id → (meta đã hết hạn, thời điểm hết hạn theo monotonic); giữ thứ tự chèn.
        self._tombstones: dict[str, tuple[AnswerMeta, float]] = {}
        self._cond = threading.Condition()

    # -- ghi ---------------------------------------------------------------------------------

    def create(self, answer_id: str, *, owner: str, session_id: str, ttl_seconds: int) -> None:
        with self._cond:
            self._purge_locked()
            if answer_id in self._entries or answer_id in self._tombstones:
                raise ValueError(f"answer_id đã tồn tại: {answer_id}")
            self._entries[answer_id] = _Entry(
                owner=owner,
                session_id=session_id,
                ttl_seconds=int(ttl_seconds),
                created_at=self._wall_clock(),
                created_mono=self._monotonic(),
            )

    def append(self, answer_id: str, event: Mapping[str, Any]) -> None:
        with self._cond:
            entry = self._live_locked(answer_id)
            if entry is None:
                raise KeyError(answer_id)
            if entry.complete:
                raise ValueError(f"answer đã hoàn tất: {answer_id}")
            expected = len(entry.events) + 1
            seq = event.get("seq")
            if seq != expected:
                raise ValueError(f"seq không liên tục: nhận {seq}, cần {expected}")
            entry.events.append(dict(event))
            self._cond.notify_all()

    def mark_complete(self, answer_id: str) -> None:
        with self._cond:
            entry = self._entries.get(answer_id)
            if entry is None or entry.complete:
                return
            entry.complete = True
            entry.completed_mono = self._monotonic()
            self._cond.notify_all()

    def request_cancel(self, answer_id: str) -> None:
        with self._cond:
            entry = self._live_locked(answer_id)
            if entry is not None:
                entry.cancel_requested = True

    # -- đọc ---------------------------------------------------------------------------------

    def read(self, answer_id: str, after_seq: int, *, timeout_ms: int) -> BufferRead:
        deadline = self._monotonic() + max(0, timeout_ms) / 1000
        with self._cond:
            while True:
                entry = self._live_locked(answer_id)
                if entry is None:
                    return BufferRead(expired=True)
                if len(entry.events) > after_seq or entry.complete:
                    events = tuple(dict(e) for e in entry.events[max(0, after_seq) :])
                    return BufferRead(events=events, complete=entry.complete)
                remaining = deadline - self._monotonic()
                if remaining <= 0:
                    return BufferRead()
                self._cond.wait(timeout=remaining)

    def is_cancel_requested(self, answer_id: str) -> bool:
        with self._cond:
            entry = self._live_locked(answer_id)
            return bool(entry and entry.cancel_requested)

    def meta(self, answer_id: str) -> AnswerMeta | None:
        with self._cond:
            entry = self._live_locked(answer_id)
            if entry is None:
                tombstone = self._tombstones.get(answer_id)
                return tombstone[0] if tombstone else None
            return AnswerMeta(
                owner=entry.owner,
                session_id=entry.session_id,
                complete=entry.complete,
                created_at=entry.created_at,
            )

    def active_count(self, owner: str) -> int:
        with self._cond:
            self._purge_locked()
            return sum(1 for e in self._entries.values() if e.owner == owner and not e.complete)

    # -- nội bộ ------------------------------------------------------------------------------

    def _expired(self, entry: _Entry, now: float) -> bool:
        if entry.complete:
            assert entry.completed_mono is not None
            return now - entry.completed_mono >= entry.ttl_seconds
        return now - entry.created_mono >= self._stale_after

    def _live_locked(self, answer_id: str) -> _Entry | None:
        entry = self._entries.get(answer_id)
        now = self._monotonic()
        if entry is not None and self._expired(entry, now):
            self._bury_locked(answer_id, entry, now)
            return None
        return entry

    def _purge_locked(self) -> None:
        now = self._monotonic()
        for answer_id in [k for k, e in self._entries.items() if self._expired(e, now)]:
            self._bury_locked(answer_id, self._entries[answer_id], now)
        for answer_id in [
            k for k, (_, at) in self._tombstones.items() if now - at >= self._tombstone_seconds
        ]:
            del self._tombstones[answer_id]

    def _bury_locked(self, answer_id: str, entry: _Entry, now: float) -> None:
        del self._entries[answer_id]
        self._tombstones[answer_id] = (
            AnswerMeta(
                owner=entry.owner,
                session_id=entry.session_id,
                complete=entry.complete,
                created_at=entry.created_at,
                expired=True,
            ),
            now,
        )
        while len(self._tombstones) > self._max_tombstones:
            del self._tombstones[next(iter(self._tombstones))]
