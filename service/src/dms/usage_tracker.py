"""Gemini API usage tracking and cost estimation."""

from __future__ import annotations

import logging
import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger("dms-watcher")

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS gemini_usage_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    model TEXT NOT NULL,
    call_type TEXT NOT NULL,
    job_id TEXT,
    prompt_tokens INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    total_tokens INTEGER NOT NULL DEFAULT 0,
    estimated_cost_usd REAL NOT NULL DEFAULT 0,
    duration_ms INTEGER,
    success INTEGER NOT NULL DEFAULT 1
);
"""

_CREATE_INDEXES_SQL = [
    "CREATE INDEX IF NOT EXISTS idx_usage_timestamp ON gemini_usage_log(timestamp);",
    "CREATE INDEX IF NOT EXISTS idx_usage_job_id ON gemini_usage_log(job_id);",
]


def calculate_cost(
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    pricing_config: dict,
) -> float:
    """Calculate estimated cost in USD for a Gemini API call.

    Prices in *pricing_config* are expressed as USD per **1 million tokens**.
    """
    model_key = model.lower()
    # Try exact match first, then prefix match
    prices = pricing_config.get(model_key)
    if not prices:
        for key in pricing_config:
            if model_key.startswith(key) or key.startswith(model_key):
                prices = pricing_config[key]
                break
    if not prices:
        return 0.0
    input_price = prices.get("input", 0)  # per 1M tokens
    output_price = prices.get("output", 0)
    return (prompt_tokens * input_price + completion_tokens * output_price) / 1_000_000


class UsageTracker:
    """Persistent Gemini API usage tracker backed by SQLite.

    Thread-safe via an RLock — follows the same pattern used by
    ``ClassificationJobStore``.
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._init_db()

    def _init_db(self) -> None:
        with self._lock:
            self._conn.executescript(_CREATE_TABLE_SQL)
            for idx_sql in _CREATE_INDEXES_SQL:
                self._conn.execute(idx_sql)
            self._conn.commit()

    # ------------------------------------------------------------------
    # record
    # ------------------------------------------------------------------

    def record(
        self,
        *,
        model: str,
        call_type: str,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        total_tokens: int = 0,
        estimated_cost_usd: float = 0.0,
        duration_ms: int | None = None,
        success: bool = True,
        job_id: str | None = None,
    ) -> None:
        """Insert a single usage record."""
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO gemini_usage_log
                    (timestamp, model, call_type, job_id,
                     prompt_tokens, completion_tokens, total_tokens,
                     estimated_cost_usd, duration_ms, success)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    datetime.now().isoformat(timespec="seconds"),
                    model,
                    call_type,
                    job_id,
                    prompt_tokens,
                    completion_tokens,
                    total_tokens,
                    estimated_cost_usd,
                    duration_ms,
                    1 if success else 0,
                ),
            )
            self._conn.commit()

    # ------------------------------------------------------------------
    # query_usage
    # ------------------------------------------------------------------

    def query_usage(
        self,
        period: str = "week",
        from_date: str | None = None,
        to_date: str | None = None,
    ) -> dict:
        """Return aggregated usage statistics.

        Parameters
        ----------
        period : str
            One of ``'day'``, ``'week'``, ``'month'``, ``'custom'``.
        from_date, to_date : str | None
            ISO-format date strings (``YYYY-MM-DD``).  Required when
            *period* is ``'custom'``.

        Returns
        -------
        dict
            Keys: ``summary``, ``daily``, ``by_type``.
        """
        start, end = self._resolve_date_range(period, from_date, to_date)

        with self._lock:
            cur = self._conn.cursor()

            # Summary
            cur.execute(
                """
                SELECT
                    COUNT(*) AS total_calls,
                    SUM(prompt_tokens) AS total_prompt,
                    SUM(completion_tokens) AS total_completion,
                    SUM(total_tokens) AS total_tokens,
                    SUM(estimated_cost_usd) AS total_cost,
                    SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) AS success_count,
                    SUM(CASE WHEN success = 0 THEN 1 ELSE 0 END) AS fail_count
                FROM gemini_usage_log
                WHERE timestamp >= ? AND timestamp <= ?
                """,
                (start, end),
            )
            row = cur.fetchone()
            summary = {
                "period": period,
                "from_date": start[:10],
                "to_date": end[:10],
                "total_calls": row[0] or 0,
                "total_prompt_tokens": row[1] or 0,
                "total_completion_tokens": row[2] or 0,
                "total_tokens": row[3] or 0,
                "total_cost_usd": round(row[4] or 0.0, 6),
                "success_count": row[5] or 0,
                "fail_count": row[6] or 0,
            }

            # Daily breakdown
            cur.execute(
                """
                SELECT
                    SUBSTR(timestamp, 1, 10) AS day,
                    COUNT(*) AS calls,
                    SUM(prompt_tokens),
                    SUM(completion_tokens),
                    SUM(total_tokens),
                    SUM(estimated_cost_usd)
                FROM gemini_usage_log
                WHERE timestamp >= ? AND timestamp <= ?
                GROUP BY day
                ORDER BY day
                """,
                (start, end),
            )
            daily = [
                {
                    "date": r[0],
                    "calls": r[1],
                    "prompt_tokens": r[2] or 0,
                    "completion_tokens": r[3] or 0,
                    "total_tokens": r[4] or 0,
                    "cost_usd": round(r[5] or 0.0, 6),
                }
                for r in cur.fetchall()
            ]

            # By call type
            cur.execute(
                """
                SELECT
                    call_type,
                    COUNT(*) AS calls,
                    SUM(prompt_tokens),
                    SUM(completion_tokens),
                    SUM(total_tokens),
                    SUM(estimated_cost_usd)
                FROM gemini_usage_log
                WHERE timestamp >= ? AND timestamp <= ?
                GROUP BY call_type
                ORDER BY calls DESC
                """,
                (start, end),
            )
            by_type = {
                r[0]: {
                    "calls": r[1],
                    "prompt_tokens": r[2] or 0,
                    "completion_tokens": r[3] or 0,
                    "total_tokens": r[4] or 0,
                    "cost_usd": round(r[5] or 0.0, 6),
                }
                for r in cur.fetchall()
            }

        return {"summary": summary, "daily": daily, "by_type": by_type}

    # ------------------------------------------------------------------
    # get_top_jobs
    # ------------------------------------------------------------------

    def get_top_jobs(
        self,
        limit: int = 10,
        from_date: str | None = None,
        to_date: str | None = None,
    ) -> list[dict]:
        """Return the jobs with the highest total token usage."""
        start, end = self._resolve_date_range("custom" if from_date else "month", from_date, to_date)

        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """
                SELECT
                    job_id,
                    COUNT(*) AS calls,
                    SUM(prompt_tokens) AS total_prompt,
                    SUM(completion_tokens) AS total_completion,
                    SUM(total_tokens) AS total_tokens,
                    SUM(estimated_cost_usd) AS total_cost
                FROM gemini_usage_log
                WHERE job_id IS NOT NULL
                  AND timestamp >= ? AND timestamp <= ?
                GROUP BY job_id
                ORDER BY total_tokens DESC
                LIMIT ?
                """,
                (start, end, limit),
            )
            return [
                {
                    "job_id": r[0],
                    "calls": r[1],
                    "prompt_tokens": r[2] or 0,
                    "completion_tokens": r[3] or 0,
                    "total_tokens": r[4] or 0,
                    "cost_usd": round(r[5] or 0.0, 6),
                }
                for r in cur.fetchall()
            ]

    # ------------------------------------------------------------------
    # cleanup
    # ------------------------------------------------------------------

    def cleanup(self, retention_days: int = 90) -> int:
        """Delete usage records older than *retention_days*. Returns rows deleted."""
        cutoff = (datetime.now() - timedelta(days=retention_days)).isoformat(timespec="seconds")
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM gemini_usage_log WHERE timestamp < ?",
                (cutoff,),
            )
            self._conn.commit()
            deleted = cur.rowcount
        if deleted:
            logger.info("Cleaned up %d usage records older than %d days", deleted, retention_days)
        return deleted

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_date_range(
        period: str,
        from_date: str | None,
        to_date: str | None,
    ) -> tuple[str, str]:
        """Return ``(start_iso, end_iso)`` strings for the requested period."""
        now = datetime.now()
        end = to_date + "T23:59:59" if to_date else now.isoformat(timespec="seconds")

        if from_date:
            start = from_date + "T00:00:00"
        elif period == "day":
            start = now.strftime("%Y-%m-%dT00:00:00")
        elif period == "week":
            start = (now - timedelta(days=7)).strftime("%Y-%m-%dT00:00:00")
        elif period == "month":
            start = (now - timedelta(days=30)).strftime("%Y-%m-%dT00:00:00")
        else:
            # custom with no explicit from → fall back to 30 days
            start = (now - timedelta(days=30)).strftime("%Y-%m-%dT00:00:00")

        return start, end
