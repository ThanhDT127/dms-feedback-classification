"""In-process WebSocket connection limiter."""

from __future__ import annotations

import threading
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from fastapi import WebSocket

WS_LIMIT_CLOSE_CODE = 4008


@dataclass
class WebSocketConnectionLimiter:
    max_per_identity: int = 3

    def __post_init__(self) -> None:
        self._lock = threading.RLock()
        self._counts: Counter[tuple[str, str]] = Counter()

    def identity_for(self, websocket: WebSocket, username: str | None) -> str:
        if username:
            return f"user:{username}"
        client = websocket.client.host if websocket.client else "unknown"
        return f"client:{client}"

    def acquire(self, route: str, identity: str) -> bool:
        key = (route, identity)
        with self._lock:
            if self._counts[key] >= self.max_per_identity:
                return False
            self._counts[key] += 1
            return True

    def release(self, route: str, identity: str) -> None:
        key = (route, identity)
        with self._lock:
            if self._counts[key] <= 1:
                self._counts.pop(key, None)
            else:
                self._counts[key] -= 1

    def reset(self) -> None:
        with self._lock:
            self._counts.clear()


ws_connection_limiter = WebSocketConnectionLimiter()


@contextmanager
def websocket_slot(route: str, identity: str) -> Iterator[bool]:
    acquired = ws_connection_limiter.acquire(route, identity)
    try:
        yield acquired
    finally:
        if acquired:
            ws_connection_limiter.release(route, identity)
