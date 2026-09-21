"""Context của một lượt chat để gắn user vào mọi lời gọi LLM (design b11 D8).

``ThreadPoolExecutor`` không tự chép ``contextvars``, nên runner phải chạy công việc bằng
``contextvars.copy_context().run(...)`` — xem ``run_in_context``.
"""

from __future__ import annotations

import contextvars
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class ChatRequestContext:
    username: str
    request_id: str
    session_id: str | None = None


_CURRENT: contextvars.ContextVar[ChatRequestContext | None] = contextvars.ContextVar(
    "chat_request_context", default=None
)


def current() -> ChatRequestContext | None:
    return _CURRENT.get()


@contextmanager
def use_context(context: ChatRequestContext | None) -> Iterator[None]:
    token = _CURRENT.set(context)
    try:
        yield
    finally:
        _CURRENT.reset(token)


def bind(context: ChatRequestContext | None, func: Callable[..., T]) -> Callable[..., T]:
    """Gói ``func`` để khi chạy ở thread khác vẫn thấy ``context`` (và các contextvar khác)."""
    snapshot = contextvars.copy_context()

    def runner(*args, **kwargs) -> T:
        def call() -> T:
            with use_context(context):
                return func(*args, **kwargs)

        return snapshot.run(call)

    return runner


__all__ = ["ChatRequestContext", "bind", "current", "use_context"]
