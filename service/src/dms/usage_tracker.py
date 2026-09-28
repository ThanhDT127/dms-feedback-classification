"""Gemini API usage tracking and cost estimation."""

from __future__ import annotations

import json
import logging
import math
import re
import sqlite3
import threading
from datetime import timedelta
from pathlib import Path

from .time_utils import utc_day_bounds_iso, utc_now, utc_now_iso

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

_AUDIT_COLUMNS = {
    "event_kind": "TEXT NOT NULL DEFAULT 'legacy'",
    "operation_id": "TEXT",
    "attempt_id": "TEXT",
    "actor": "TEXT",
    "route": "TEXT",
    "response_id": "TEXT",
    "incident_id": "TEXT",
    "model_requested": "TEXT",
    "model_actual": "TEXT",
    "outcome": "TEXT",
    "error_category": "TEXT",
    "usage_known": "INTEGER",
    "actual_prompt_tokens": "INTEGER",
    "actual_completion_tokens": "INTEGER",
    "actual_total_tokens": "INTEGER",
    "actual_cost_usd": "REAL",
    "usage_json": "TEXT",
}

_TOKEN_FIELDS = (
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "thinking_tokens",
    "cached_tokens",
    "reasoning_tokens",
)

# A partially unknown total is unknown, not a misleading subtotal or free usage.
_ACCOUNTING_TOTALS_SQL = ", ".join(
    f"CASE WHEN COUNT(*) = 0 THEN 0 WHEN COUNT({column}) = COUNT(*) "
    f"THEN SUM({column}) END AS {alias}"
    for column, alias in (
        ("prompt_tokens", "total_prompt"),
        ("completion_tokens", "total_completion"),
        ("total_tokens", "total_tokens"),
        ("estimated_cost_usd", "total_cost"),
    )
)


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

    Thread-safe via an RLock â€” follows the same pattern used by
    ``ClassificationJobStore``.
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False, timeout=30.0)
        self._conn.execute("PRAGMA busy_timeout=30000")
        try:
            self._conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.OperationalError:
            pass
        self._init_db()

    def _init_db(self) -> None:
        with self._lock:
            self._conn.executescript(_CREATE_TABLE_SQL)
            # Serialize additive migration across independently starting processes.
            self._conn.execute("BEGIN IMMEDIATE")
            columns = {r[1] for r in self._conn.execute("PRAGMA table_info(gemini_usage_log)")}
            for name, definition in _AUDIT_COLUMNS.items():
                if name not in columns:
                    self._conn.execute(
                        f"ALTER TABLE gemini_usage_log ADD COLUMN {name} {definition}"
                    )
            for idx_sql in _CREATE_INDEXES_SQL:
                self._conn.execute(idx_sql)
            self._conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_usage_attempt_event "
                "ON gemini_usage_log(attempt_id, event_kind)"
            )
            self._conn.execute("""
                CREATE VIEW IF NOT EXISTS gemini_usage_accounting AS
                SELECT timestamp, model, call_type, job_id, duration_ms, success,
                    prompt_tokens, completion_tokens, total_tokens, estimated_cost_usd
                FROM gemini_usage_log WHERE event_kind = 'legacy'
                UNION ALL
                SELECT timestamp, model, call_type, job_id, duration_ms, success,
                    actual_prompt_tokens, actual_completion_tokens, actual_total_tokens,
                    actual_cost_usd
                FROM gemini_usage_log WHERE event_kind = 'attempt_finished'
            """)
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
                    utc_now_iso(),
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

    def record_attempt(
        self,
        *,
        event_kind: str,
        operation_id: str,
        attempt_id: str,
        actor: str,
        job_id: str | None,
        route: str,
        incident_id: str | None,
        model_requested: str,
        model_actual: str | None = None,
        outcome: str,
        error_category: str | None = None,
        response_id: str | None = None,
        usage: dict | None = None,
        estimated_cost_usd: float | None = None,
        call_type: str = "generate",
        duration_ms: int | None = None,
    ) -> None:
        """Persist one immutable event; duplicate attempt/event pairs are no-ops.

        Nullable actual_* columns avoid changing legacy NOT NULL accounting fields.
        Only FINISHED events participate in accounting; callers must not also record().
        """
        if event_kind not in {"attempt_started", "attempt_finished"}:
            raise ValueError("Invalid attempt event_kind")
        for name, value in (
            ("operation_id", operation_id),
            ("attempt_id", attempt_id),
            ("actor", actor),
            ("route", route),
            ("outcome", outcome),
            ("model_requested", model_requested),
        ):
            if not isinstance(value, str) or not value.strip() or any(ord(c) < 32 for c in value):
                raise ValueError(f"Invalid attempt {name}")
        if error_category is not None and not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", error_category):
            raise ValueError("Invalid attempt error_category")
        values = {
            key: usage[key]
            for key in _TOKEN_FIELDS
            if usage is not None and usage.get(key) is not None
        }
        if any(
            type(value) is not int or value < 0 or value > 2**63 - 1 for value in values.values()
        ):
            raise ValueError("Attempt usage must contain nonnegative integer counters")
        if estimated_cost_usd is not None and (
            not math.isfinite(estimated_cost_usd) or estimated_cost_usd < 0
        ):
            raise ValueError("Attempt cost must be finite and nonnegative")
        if event_kind == "attempt_started" and (values or estimated_cost_usd is not None):
            raise ValueError("STARTED cannot contain provider usage or cost")
        if event_kind == "attempt_started":
            outcome = "unknown"
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO gemini_usage_log (
                    timestamp, model, call_type, job_id, duration_ms, success,
                    event_kind, operation_id, attempt_id, actor, route, incident_id,
                    model_requested, model_actual, outcome, error_category, response_id,
                    usage_known, actual_prompt_tokens, actual_completion_tokens,
                    actual_total_tokens, actual_cost_usd, usage_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(attempt_id, event_kind) DO NOTHING
                """,
                (
                    utc_now_iso(),
                    model_actual or model_requested,
                    call_type,
                    job_id,
                    duration_ms,
                    int(outcome == "success"),
                    event_kind,
                    operation_id,
                    attempt_id,
                    actor,
                    route,
                    incident_id,
                    model_requested,
                    model_actual,
                    outcome,
                    error_category,
                    response_id,
                    int(bool(values)),
                    values.get("prompt_tokens"),
                    values.get("completion_tokens"),
                    values.get("total_tokens"),
                    estimated_cost_usd,
                    json.dumps(values) if values else None,
                ),
            )

    def query_attempts(self) -> list[dict]:
        """Return persisted attempt events, retaining nullable provider evidence."""
        with self._lock:
            cursor = self._conn.execute(
                "SELECT * FROM gemini_usage_log WHERE event_kind != 'legacy' ORDER BY id"
            )
            names = [column[0] for column in cursor.description]
            rows = [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]
        for row in rows:
            raw_usage = row.pop("usage_json")
            row["usage"] = json.loads(raw_usage) if raw_usage is not None else None
            for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
                row[key] = row.pop(f"actual_{key}")
            row["estimated_cost_usd"] = row.pop("actual_cost_usd")
        return rows

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
                f"""
                SELECT
                    COUNT(*) AS total_calls,
                    {_ACCOUNTING_TOTALS_SQL},
                    SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) AS success_count,
                    SUM(CASE WHEN success = 0 THEN 1 ELSE 0 END) AS fail_count
                FROM gemini_usage_accounting
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
                "total_prompt_tokens": row[1],
                "total_completion_tokens": row[2],
                "total_tokens": row[3],
                "total_cost_usd": round(row[4], 6) if row[4] is not None else None,
                "success_count": row[5] or 0,
                "fail_count": row[6] or 0,
            }

            # Daily breakdown
            cur.execute(
                f"""
                SELECT
                    SUBSTR(timestamp, 1, 10) AS day,
                    COUNT(*) AS calls,
                    {_ACCOUNTING_TOTALS_SQL}
                FROM gemini_usage_accounting
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
                    "prompt_tokens": r[2],
                    "completion_tokens": r[3],
                    "total_tokens": r[4],
                    "cost_usd": round(r[5], 6) if r[5] is not None else None,
                }
                for r in cur.fetchall()
            ]

            # By call type
            cur.execute(
                f"""
                SELECT
                    call_type,
                    COUNT(*) AS calls,
                    {_ACCOUNTING_TOTALS_SQL}
                FROM gemini_usage_accounting
                WHERE timestamp >= ? AND timestamp <= ?
                GROUP BY call_type
                ORDER BY calls DESC
                """,
                (start, end),
            )
            by_type = {
                r[0]: {
                    "calls": r[1],
                    "prompt_tokens": r[2],
                    "completion_tokens": r[3],
                    "total_tokens": r[4],
                    "cost_usd": round(r[5], 6) if r[5] is not None else None,
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
        start, end = self._resolve_date_range(
            "custom" if from_date else "month", from_date, to_date
        )

        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                f"""
                SELECT
                    job_id,
                    COUNT(*) AS calls,
                    {_ACCOUNTING_TOTALS_SQL}
                FROM gemini_usage_accounting
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
                    "prompt_tokens": r[2],
                    "completion_tokens": r[3],
                    "total_tokens": r[4],
                    "cost_usd": round(r[5], 6) if r[5] is not None else None,
                }
                for r in cur.fetchall()
            ]

    # ------------------------------------------------------------------
    # cleanup
    # ------------------------------------------------------------------

    def cleanup(self, retention_days: int = 90) -> int:
        """Delete usage records older than *retention_days*. Returns rows deleted."""
        cutoff = (utc_now() - timedelta(days=retention_days)).isoformat(timespec="seconds")
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

    def close(self) -> None:
        """Close the SQLite connection."""
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None  # type: ignore[assignment]

    def __enter__(self) -> UsageTracker:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
        self.close()

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_date_range(
        period: str,
        from_date: str | None,
        to_date: str | None,
    ) -> tuple[str, str]:
        """Return UTC-aware ``(start_iso, end_iso)`` strings for the requested period."""
        now = utc_now()
        if from_date:
            fallback_start = None
        elif period == "day":
            fallback_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        elif period == "week":
            fallback_start = (now - timedelta(days=7)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
        elif period == "month":
            fallback_start = (now - timedelta(days=30)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
        else:
            fallback_start = (now - timedelta(days=30)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )

        return utc_day_bounds_iso(
            date_from=from_date,
            date_to=to_date,
            fallback_start=fallback_start,
            fallback_end=now,
        )
