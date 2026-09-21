"""Giới hạn số ``ask`` mỗi phút của một user, token bucket trong tiến trình (design b06 D10)."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable


class AskRateLimiter:
    def __init__(self, per_minute: int, *, clock: Callable[[], float] = time.monotonic) -> None:
        if per_minute < 1:
            raise ValueError("per_minute must be >= 1")
        self.capacity = float(per_minute)
        self.refill_per_second = per_minute / 60.0
        self._clock = clock
        self._buckets: dict[str, tuple[float, float]] = {}
        self._lock = threading.Lock()

    def allow(self, username: str) -> bool:
        now = self._clock()
        with self._lock:
            tokens, updated = self._buckets.get(username, (self.capacity, now))
            tokens = min(self.capacity, tokens + (now - updated) * self.refill_per_second)
            if tokens < 1.0:
                self._buckets[username] = (tokens, now)
                return False
            self._buckets[username] = (tokens - 1.0, now)
            return True

    def reset(self) -> None:
        with self._lock:
            self._buckets.clear()
