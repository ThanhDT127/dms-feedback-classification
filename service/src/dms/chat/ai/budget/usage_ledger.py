"""Sổ usage LLM theo user (spec ``chat-token-budget``, design b11 D8).

Không dùng ``gemini_usage_log`` của pipeline: bảng đó không có ``username`` và đang phục vụ
trang thống kê phân loại. Dev A cài bản thật (đề xuất bảng ``chat_usage_log``); ở đây chỉ có
Protocol và bản in-memory.
"""

from __future__ import annotations

import threading
from bisect import bisect_left
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Protocol


@dataclass(frozen=True)
class UsageEntry:
    """Một lần gọi LLM của chat."""

    username: str
    request_id: str
    session_id: str | None
    call_type: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cost_usd: float
    success: bool
    at: datetime


@dataclass(frozen=True)
class UsageRow:
    """Một dòng thống kê theo user theo ngày."""

    username: str
    date: str
    total_tokens: int
    cost_usd: float
    turns: int

    def to_dict(self) -> dict[str, object]:
        return {
            "username": self.username,
            "date": self.date,
            "total_tokens": self.total_tokens,
            "cost_usd": round(self.cost_usd, 6),
            "turns": self.turns,
        }


class ChatUsageLedger(Protocol):
    def record(
        self,
        *,
        username: str,
        request_id: str,
        session_id: str | None,
        call_type: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        total_tokens: int,
        cost_usd: float,
        success: bool,
        at: datetime,
    ) -> None: ...

    def total_tokens(self, username: str, start: datetime, end: datetime) -> int: ...

    def daily_summary(self, start_date: date, end_date: date) -> list[UsageRow]: ...


@dataclass
class InMemoryChatUsageLedger:
    """Bản in-memory; giữ bản ghi theo thứ tự thời gian để cộng nhanh theo khoảng."""

    entries: list[UsageEntry] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _keys: list[datetime] = field(default_factory=list)

    def record(
        self,
        *,
        username: str,
        request_id: str,
        session_id: str | None,
        call_type: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        total_tokens: int,
        cost_usd: float,
        success: bool,
        at: datetime,
    ) -> None:
        entry = UsageEntry(
            username=username,
            request_id=request_id,
            session_id=session_id,
            call_type=call_type,
            model=model,
            prompt_tokens=int(prompt_tokens),
            completion_tokens=int(completion_tokens),
            total_tokens=int(total_tokens),
            cost_usd=float(cost_usd),
            success=bool(success),
            at=_aware(at),
        )
        with self._lock:
            index = bisect_left(self._keys, entry.at)
            self._keys.insert(index, entry.at)
            self.entries.insert(index, entry)

    def total_tokens(self, username: str, start: datetime, end: datetime) -> int:
        """Tổng token của đúng user này trong ``[start, end)``."""
        window = self._window(start, end)
        return sum(entry.total_tokens for entry in window if entry.username == username)

    def daily_summary(self, start_date: date, end_date: date) -> list[UsageRow]:
        buckets: dict[tuple[str, str], list[UsageEntry]] = {}
        with self._lock:
            entries = list(self.entries)
        for entry in entries:
            day = entry.at.date()
            if day < start_date or day > end_date:
                continue
            buckets.setdefault((entry.username, day.isoformat()), []).append(entry)
        rows = [
            UsageRow(
                username=username,
                date=day,
                total_tokens=sum(item.total_tokens for item in items),
                cost_usd=sum(item.cost_usd for item in items),
                turns=len({item.request_id for item in items if item.request_id}),
            )
            for (username, day), items in buckets.items()
        ]
        rows.sort(key=lambda row: (row.date, row.username))
        return rows

    # ── Nội bộ ──

    def _window(self, start: datetime, end: datetime) -> Sequence[UsageEntry]:
        low, high = _aware(start), _aware(end)
        with self._lock:
            left = bisect_left(self._keys, low)
            right = bisect_left(self._keys, high)
            return list(self.entries[left:right])


def _aware(moment: datetime) -> datetime:
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


__all__ = [
    "ChatUsageLedger",
    "InMemoryChatUsageLedger",
    "UsageEntry",
    "UsageRow",
]
