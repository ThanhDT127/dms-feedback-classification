"""Nơi lưu trí nhớ phiên (spec ``chat-conversation-memory``, design b11 D2).

Dev A sở hữu phần lưu trữ thật (cột ``chat_sessions.memory_json`` hoặc bảng riêng); Dev B chỉ
khai báo Protocol và một bản in-memory để chạy test. Ghi có ``if_version`` để hai lượt chạy
song song không đè tóm tắt của nhau.
"""

from __future__ import annotations

import threading
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any, Protocol

SUMMARY_METHOD_LLM = "llm"
SUMMARY_METHOD_EXTRACTIVE = "extractive"


@dataclass(frozen=True)
class SessionMemory:
    """Nội dung ``memory_json`` của một phiên."""

    summary: str = ""
    topics: tuple[str, ...] = ()
    summarized_until_message_id: int | None = None
    method: str = SUMMARY_METHOD_EXTRACTIVE
    version: int = 0
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary,
            "topics": list(self.topics),
            "summarized_until_message_id": self.summarized_until_message_id,
            "method": self.method,
            "version": self.version,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> SessionMemory:
        if not data:
            return cls()
        until = data.get("summarized_until_message_id")
        return cls(
            summary=str(data.get("summary") or ""),
            topics=tuple(str(topic) for topic in data.get("topics") or ()),
            summarized_until_message_id=int(until) if until is not None else None,
            method=str(data.get("method") or SUMMARY_METHOD_EXTRACTIVE),
            version=int(data.get("version") or 0),
            updated_at=str(data.get("updated_at") or ""),
        )

    def at_version(self, version: int, *, now: datetime | None = None) -> SessionMemory:
        """Bản ghi sẽ được lưu: phiên bản do store cấp, không lấy từ bản gửi vào."""
        moment = now or datetime.now(UTC)
        return replace(self, version=version, updated_at=moment.isoformat())


class MemoryVersionConflict(Exception):
    """Phiên đã có bản tóm tắt mới hơn; lần tóm tắt này bị bỏ (design D2)."""


class SessionMemoryStore(Protocol):
    def load(self, session_id: str) -> SessionMemory | None: ...

    def save(self, session_id: str, memory: SessionMemory, *, if_version: int) -> SessionMemory:
        """Lưu khi phiên bản hiện tại đúng bằng ``if_version``; lệch thì ném
        ``MemoryVersionConflict``."""
        ...


@dataclass
class InMemorySessionMemoryStore:
    """Bản in-memory cho test và môi trường chưa có store của Dev A."""

    _data: dict[str, SessionMemory] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def load(self, session_id: str) -> SessionMemory | None:
        with self._lock:
            return self._data.get(session_id)

    def save(self, session_id: str, memory: SessionMemory, *, if_version: int) -> SessionMemory:
        with self._lock:
            current = self._data.get(session_id)
            current_version = current.version if current is not None else 0
            if current_version != if_version:
                raise MemoryVersionConflict(
                    f"session={session_id} version={current_version} expected={if_version}"
                )
            saved = memory.at_version(if_version + 1)
            self._data[session_id] = saved
            return saved


__all__ = [
    "SUMMARY_METHOD_EXTRACTIVE",
    "SUMMARY_METHOD_LLM",
    "InMemorySessionMemoryStore",
    "MemoryVersionConflict",
    "SessionMemory",
    "SessionMemoryStore",
]
