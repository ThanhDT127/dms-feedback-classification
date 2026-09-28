"""Bản SQLite của ``ChatUsageLedger`` (spec ``chat-token-budget``, design b11 D8).

``at`` lưu dạng ISO UTC **luôn có microseconds** nên chuỗi dài bằng nhau và so sánh chuỗi
trùng với so sánh thời gian — nhờ đó khoảng ``[start, end)`` chạy thẳng bằng ``BETWEEN``/``<``.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, date, datetime
from typing import Any

from ..ai.budget.usage_ledger import UsageRow


def _iso(moment: datetime) -> str:
    aware = moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)
    return aware.astimezone(UTC).isoformat(timespec="microseconds")


class SqliteChatUsageLedger:
    """Ghi và cộng usage LLM của chat theo user trong bảng ``chat_usage_log``."""

    def __init__(self, conn_factory: Any) -> None:
        self._conn_factory = conn_factory

    def _conn(self) -> sqlite3.Connection:
        # sqlite3.Connection cũng callable (biên dịch câu lệnh) nên phải xét kiểu trước.
        if isinstance(self._conn_factory, sqlite3.Connection):
            return self._conn_factory
        return self._conn_factory()

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
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO chat_usage_log (
                    username, request_id, session_id, call_type, model,
                    prompt_tokens, completion_tokens, total_tokens, cost_usd, success, at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    username,
                    request_id,
                    session_id,
                    call_type,
                    model,
                    int(prompt_tokens),
                    int(completion_tokens),
                    int(total_tokens),
                    float(cost_usd),
                    1 if success else 0,
                    _iso(at),
                ),
            )

    def total_tokens(self, username: str, start: datetime, end: datetime) -> int:
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT COALESCE(SUM(total_tokens), 0)
                FROM chat_usage_log
                WHERE username = ? AND at >= ? AND at < ?
                """,
                (username, _iso(start), _iso(end)),
            ).fetchone()
        return int(row[0])

    def daily_summary(self, start_date: date, end_date: date) -> list[UsageRow]:
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT
                    username,
                    substr(at, 1, 10) AS day,
                    SUM(total_tokens) AS total_tokens,
                    SUM(cost_usd) AS cost_usd,
                    COUNT(DISTINCT NULLIF(request_id, '')) AS turns
                FROM chat_usage_log
                WHERE substr(at, 1, 10) BETWEEN ? AND ?
                GROUP BY username, day
                ORDER BY day, username
                """,
                (start_date.isoformat(), end_date.isoformat()),
            ).fetchall()
        return [
            UsageRow(
                username=str(row[0]),
                date=str(row[1]),
                total_tokens=int(row[2] or 0),
                cost_usd=float(row[3] or 0.0),
                turns=int(row[4] or 0),
            )
            for row in rows
        ]

    def cleanup(self, before: datetime) -> int:
        """Xoá bản ghi cũ hơn ``before``; dùng cho housekeeping."""
        with self._conn() as conn:
            cursor = conn.execute("DELETE FROM chat_usage_log WHERE at < ?", (_iso(before),))
            return cursor.rowcount


__all__ = ["SqliteChatUsageLedger"]
