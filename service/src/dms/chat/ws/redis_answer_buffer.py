"""Answer Buffer dùng chung giữa các worker, lưu trên Redis (design b06 D4).

``InMemoryAnswerBuffer`` chỉ đúng khi chạy một worker; bản này để nhiều worker cùng đọc một
câu trả lời, nên client rớt mạng rồi ``resume`` vào worker khác vẫn nhận tiếp được.

Mỗi answer dùng ba khoá:

- ``<prefix><id>``      hash: chủ sở hữu, phiên, mốc thời gian, cờ hoàn tất/huỷ, ``next_seq``
- ``<prefix><id>:ev``   stream: mỗi event một entry, id chính là ``<seq>-0``
- ``<prefix>own:<user>`` set: các answer đang chạy của một user, cho ``active_count``

Hạn dùng được tính bằng đồng hồ tường (mọi worker đọc cùng một mốc) chứ không phải
``time.monotonic`` của từng tiến trình. Hash được giữ lại sau khi hết hạn để làm "bia mộ":
``resume`` một answer cũ trả ``answer_expired`` thay vì ``ANSWER_NOT_FOUND``.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from .answer_buffer import AnswerMeta, BufferRead

logger = logging.getLogger("dms-chat")

DEFAULT_PREFIX = "chat:ab:"
DEFAULT_STALE_AFTER_SECONDS = 120.0
DEFAULT_TOMBSTONE_SECONDS = 86400.0
# Mỗi lần chờ chỉ chặn tối đa ngần này rồi đọc lại, để không phụ thuộc hoàn toàn vào việc
# Redis đánh thức đúng lúc (và để bản giả trong test cũng chạy đúng).
MAX_BLOCK_MS = 500
EVENT_FIELD = "json"
SENTINEL_FIELD = "done"


@dataclass(frozen=True)
class _Meta:
    owner: str
    session_id: str
    ttl_seconds: float
    created_at: float
    complete: bool
    completed_at: float
    cancel: bool
    next_seq: int


class RedisAnswerBuffer:
    """Cài đặt ``AnswerBuffer`` trên Redis Stream."""

    def __init__(
        self,
        client: Any,
        *,
        prefix: str = DEFAULT_PREFIX,
        stale_after_seconds: float = DEFAULT_STALE_AFTER_SECONDS,
        tombstone_seconds: float = DEFAULT_TOMBSTONE_SECONDS,
        clock: Callable[[], float] = time.time,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.client = client
        self.prefix = prefix
        self._stale_after = float(stale_after_seconds)
        self._tombstone_seconds = int(tombstone_seconds)
        self._clock = clock
        self._monotonic = monotonic

    # ── Khoá ──

    def _meta_key(self, answer_id: str) -> str:
        return f"{self.prefix}{answer_id}"

    def _stream_key(self, answer_id: str) -> str:
        return f"{self.prefix}{answer_id}:ev"

    def _owner_key(self, owner: str) -> str:
        return f"{self.prefix}own:{owner}"

    # ── Ghi ──

    def create(self, answer_id: str, *, owner: str, session_id: str, ttl_seconds: int) -> None:
        key = self._meta_key(answer_id)
        now = self._clock()
        created = self.client.hsetnx(key, "owner", owner)
        if not created:
            # Còn sống hoặc mới là bia mộ: dùng lại id đều không được (spec chat-answer-resume).
            raise ValueError(f"answer_id đã tồn tại: {answer_id}")
        self.client.hset(
            key,
            mapping={
                "session_id": session_id,
                "ttl": int(ttl_seconds),
                "created_at": now,
                "complete": 0,
                "completed_at": 0,
                "cancel": 0,
                "next_seq": 1,
            },
        )
        self.client.expire(key, self._tombstone_seconds)
        self.client.sadd(self._owner_key(owner), answer_id)
        self.client.expire(self._owner_key(owner), self._tombstone_seconds)

    def append(self, answer_id: str, event: Mapping[str, Any]) -> None:
        meta = self._live(answer_id)
        if meta is None:
            raise KeyError(answer_id)
        if meta.complete:
            raise ValueError(f"answer đã hoàn tất: {answer_id}")
        seq = event.get("seq")
        if seq != meta.next_seq:
            raise ValueError(f"seq không liên tục: nhận {seq}, cần {meta.next_seq}")
        stream = self._stream_key(answer_id)
        self.client.xadd(
            stream,
            {EVENT_FIELD: json.dumps(dict(event), ensure_ascii=False)},
            id=f"{seq}-0",
        )
        self.client.expire(stream, self._tombstone_seconds)
        self.client.hset(self._meta_key(answer_id), "next_seq", int(seq) + 1)

    def mark_complete(self, answer_id: str) -> None:
        meta = self._live(answer_id)
        if meta is None or meta.complete:
            return
        self.client.hset(
            self._meta_key(answer_id),
            mapping={"complete": 1, "completed_at": self._clock()},
        )
        # Đánh thức mọi reader đang chặn: id sau event cuối nhưng trước event kế tiếp.
        self.client.xadd(
            self._stream_key(answer_id), {SENTINEL_FIELD: 1}, id=f"{meta.next_seq - 1}-1"
        )
        self.client.srem(self._owner_key(meta.owner), answer_id)

    def request_cancel(self, answer_id: str) -> None:
        if self._live(answer_id) is not None:
            self.client.hset(self._meta_key(answer_id), "cancel", 1)

    # ── Đọc ──

    def read(self, answer_id: str, after_seq: int, *, timeout_ms: int) -> BufferRead:
        deadline = self._monotonic() + max(0, timeout_ms) / 1000
        stream = self._stream_key(answer_id)
        while True:
            meta = self._live(answer_id)
            if meta is None:
                return BufferRead(expired=True)
            events = self._events_after(stream, after_seq)
            if events or meta.complete:
                return BufferRead(events=events, complete=meta.complete)
            remaining = deadline - self._monotonic()
            if remaining <= 0:
                return BufferRead()
            block_ms = max(1, min(int(remaining * 1000), MAX_BLOCK_MS))
            try:
                self.client.xread({stream: f"{after_seq}-0"}, count=1, block=block_ms)
            except Exception:  # mất kết nối giữa chừng: đọc lại ở vòng sau
                logger.warning("chat_answer_buffer_read_failed", extra={"answer_id": answer_id})
                time.sleep(min(remaining, MAX_BLOCK_MS / 1000))

    def is_cancel_requested(self, answer_id: str) -> bool:
        meta = self._live(answer_id)
        return bool(meta and meta.cancel)

    def meta(self, answer_id: str) -> AnswerMeta | None:
        raw = self._meta_raw(answer_id)
        if raw is None:
            return None
        return AnswerMeta(
            owner=raw.owner,
            session_id=raw.session_id,
            complete=raw.complete,
            created_at=raw.created_at,
            expired=self._expired(raw),
        )

    def active_count(self, owner: str) -> int:
        key = self._owner_key(owner)
        count = 0
        for answer_id in self.client.smembers(key) or ():
            answer_id = _text(answer_id)
            meta = self._meta_raw(answer_id)
            if meta is None or meta.complete or self._expired(meta):
                self.client.srem(key, answer_id)
                continue
            count += 1
        return count

    # ── Nội bộ ──

    def _events_after(self, stream: str, after_seq: int) -> tuple[dict[str, Any], ...]:
        entries = self.client.xrange(stream, min=f"{max(0, after_seq) + 1}-0", max="+")
        events = []
        for _entry_id, fields in entries or ():
            payload = _field(fields, EVENT_FIELD)
            if payload is None:  # entry đánh thức của mark_complete
                continue
            events.append(json.loads(payload))
        return tuple(events)

    def _meta_raw(self, answer_id: str) -> _Meta | None:
        raw = self.client.hgetall(self._meta_key(answer_id))
        if not raw:
            return None
        data = {_text(k): _text(v) for k, v in raw.items()}
        return _Meta(
            owner=data.get("owner", ""),
            session_id=data.get("session_id", ""),
            ttl_seconds=float(data.get("ttl", 0) or 0),
            created_at=float(data.get("created_at", 0) or 0),
            complete=data.get("complete") == "1",
            completed_at=float(data.get("completed_at", 0) or 0),
            cancel=data.get("cancel") == "1",
            next_seq=int(data.get("next_seq", 1) or 1),
        )

    def _live(self, answer_id: str) -> _Meta | None:
        meta = self._meta_raw(answer_id)
        if meta is None:
            return None
        if self._expired(meta):
            # Xoá event cho nhẹ Redis; hash ở lại làm bia mộ tới khi hết hạn vật lý.
            self.client.delete(self._stream_key(answer_id))
            self.client.srem(self._owner_key(meta.owner), answer_id)
            return None
        return meta

    def _expired(self, meta: _Meta) -> bool:
        now = self._clock()
        if meta.complete:
            return now - meta.completed_at >= meta.ttl_seconds
        return now - meta.created_at >= self._stale_after


def _text(value: Any) -> str:
    return value.decode("utf-8") if isinstance(value, bytes) else str(value)


def _field(fields: Mapping[Any, Any], name: str) -> str | None:
    for key, value in fields.items():
        if _text(key) == name:
            return _text(value)
    return None


def build_redis_client(url: str) -> Any:
    """Client đồng bộ, tự giải mã chuỗi; import muộn để không cần redis khi không bật."""
    import redis

    return redis.Redis.from_url(url, decode_responses=True)


__all__ = ["DEFAULT_PREFIX", "RedisAnswerBuffer", "build_redis_client"]
