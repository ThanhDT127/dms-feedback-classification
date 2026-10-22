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

# Dashboard / UI mode: sum known non-null records so failed/unknown attempts do not zero/null out period totals.
_PARTIAL_ACCOUNTING_TOTALS_SQL = ", ".join(
    f"COALESCE(SUM({column}), 0) AS {alias}"
    for column, alias in (
        ("prompt_tokens", "total_prompt"),
        ("completion_tokens", "total_completion"),
        ("total_tokens", "total_tokens"),
        ("estimated_cost_usd", "total_cost"),
    )
)

DEFAULT_MODEL_PRICING: dict[str, dict[str, float]] = {
    "gemini-2.5-flash": {"input": 0.30, "output": 2.50},
    "gemini-flash": {"input": 0.30, "output": 2.50},
    "gemini-2.0-flash": {"input": 0.10, "output": 0.40},
    "gemini-2.5-flash-lite": {"input": 0.025, "output": 0.30},
    "gemini-flash-lite": {"input": 0.025, "output": 0.30},
    "gemini-3.5-flash": {"input": 1.50, "output": 9.00},
    "gemini-3.1-flash-lite": {"input": 0.025, "output": 0.10},
}

_MODEL_ALIAS_MAP: dict[str, str] = {
    "gemini-flash-lite": "gemini-2.5-flash-lite",
    "gemini-flash": "gemini-2.5-flash",
}


def resolve_model_pricing(model: str | None, pricing_config: dict | None) -> dict | None:
    """Resolve model pricing entry from *pricing_config*, handling common aliases and provider prefixes."""
    if (
        not model
        or not isinstance(model, str)
        or not pricing_config
        or not isinstance(pricing_config, dict)
    ):
        return None
    model_key = model.strip().lower()
    for prefix in ("models/", "gemini/", "vertex_ai/", "google/"):
        if model_key.startswith(prefix):
            model_key = model_key[len(prefix) :]
            break
    if not model_key:
        return None

    norm_pricing = {
        str(k).strip().lower(): v for k, v in pricing_config.items() if isinstance(v, dict)
    }
    candidates = [model_key]
    if model_key in _MODEL_ALIAS_MAP:
        candidates.append(_MODEL_ALIAS_MAP[model_key])

    for cand in candidates:
        if cand in norm_pricing:
            return norm_pricing[cand]

    # Sort keys longest-first so "gemini-2.5-flash-lite" matches before "gemini-2.5-flash"
    sorted_keys = sorted(norm_pricing.keys(), key=len, reverse=True)
    for cand in candidates:
        for key in sorted_keys:
            if cand.startswith(key) or key.startswith(cand):
                return norm_pricing[key]
    return None


def calculate_cost(
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    pricing_config: dict,
) -> float:
    """Calculate estimated cost in USD for a Gemini API call.

    Prices in *pricing_config* are expressed as USD per **1 million tokens**.
    """
    prices = resolve_model_pricing(model, pricing_config)
    if not prices:
        return 0.0
    input_price = prices.get("input", 0)  # per 1M tokens
    output_price = prices.get("output", 0)
    return (prompt_tokens * input_price + completion_tokens * output_price) / 1_000_000


def estimate_attempt_cost(
    model: str | None,
    usage: dict | None,
    pricing_config: dict | None,
) -> float | None:
    """Estimate USD cost for a gateway/direct attempt when token usage and model pricing are known.

    Returns ``None`` if usage is incomplete or the model is not recognized in *pricing_config*.
    """
    if not isinstance(usage, dict):
        return None
    prompt_tokens = usage.get("prompt_tokens")
    completion_tokens = usage.get("completion_tokens")
    if (
        type(prompt_tokens) is not int
        or type(completion_tokens) is not int
        or prompt_tokens < 0
        or completion_tokens < 0
    ):
        return None
    prices = resolve_model_pricing(model, pricing_config)
    if not prices:
        return None
    input_price = float(prices.get("input", 0))
    output_price = float(prices.get("output", 0))
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

    def backfill_missing_costs(self, pricing_config: dict | None = None) -> int:
        """Backfill cost for historical ``attempt_finished`` and ``legacy`` rows with known tokens."""
        pricing = pricing_config or DEFAULT_MODEL_PRICING
        if not pricing:
            return 0
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT id, event_kind, model, model_actual, model_requested,
                       actual_prompt_tokens, actual_completion_tokens,
                       prompt_tokens, completion_tokens
                FROM gemini_usage_log
                WHERE (
                    event_kind = 'attempt_finished'
                    AND (actual_cost_usd IS NULL OR actual_cost_usd = 0)
                    AND actual_prompt_tokens IS NOT NULL
                    AND actual_completion_tokens IS NOT NULL
                ) OR (
                    event_kind = 'legacy'
                    AND (estimated_cost_usd IS NULL OR estimated_cost_usd = 0)
                    AND (prompt_tokens > 0 OR completion_tokens > 0)
                )
                """
            ).fetchall()
            if not rows:
                return 0
            attempt_updates: list[tuple[float, int]] = []
            legacy_updates: list[tuple[float, int]] = []
            for (
                row_id,
                event_kind,
                model,
                model_actual,
                model_requested,
                act_p,
                act_c,
                leg_p,
                leg_c,
            ) in rows:
                if event_kind == "attempt_finished":
                    usage = {"prompt_tokens": int(act_p), "completion_tokens": int(act_c)}
                else:
                    usage = {"prompt_tokens": int(leg_p or 0), "completion_tokens": int(leg_c or 0)}
                cost = None
                for cand in (model_actual, model, model_requested):
                    cost = estimate_attempt_cost(cand, usage, pricing)
                    if cost is not None:
                        break
                if cost is not None and cost > 0:
                    if event_kind == "attempt_finished":
                        attempt_updates.append((cost, int(row_id)))
                    else:
                        legacy_updates.append((cost, int(row_id)))
            if attempt_updates:
                self._conn.executemany(
                    "UPDATE gemini_usage_log SET actual_cost_usd = ? WHERE id = ?",
                    attempt_updates,
                )
            if legacy_updates:
                self._conn.executemany(
                    "UPDATE gemini_usage_log SET estimated_cost_usd = ? WHERE id = ?",
                    legacy_updates,
                )
            if attempt_updates or legacy_updates:
                self._conn.commit()
            return len(attempt_updates) + len(legacy_updates)

    # ------------------------------------------------------------------
    # query_usage
    # ------------------------------------------------------------------

    def query_usage(
        self,
        period: str = "week",
        from_date: str | None = None,
        to_date: str | None = None,
        *,
        include_partial: bool = False,
    ) -> dict:
        """Return aggregated usage statistics.

        Parameters
        ----------
        period : str
            One of ``'day'``, ``'today'``, ``'week'``, ``'month'``, ``'custom'``.
        from_date, to_date : str | None
            ISO-format date strings (``YYYY-MM-DD``).  Required when
            *period* is ``'custom'``.
        include_partial : bool
            When ``True``, sums known non-null records via ``COALESCE(SUM(...), 0)``
            instead of returning ``None`` when any attempt has unknown usage/cost.

        Returns
        -------
        dict
            Keys: ``summary``, ``daily``, ``by_type``.
        """
        start, end = self._resolve_date_range(period, from_date, to_date)
        totals_sql = _PARTIAL_ACCOUNTING_TOTALS_SQL if include_partial else _ACCOUNTING_TOTALS_SQL

        with self._lock:
            cur = self._conn.cursor()

            # Summary
            cur.execute(
                f"""
                SELECT
                    COUNT(*) AS total_calls,
                    {totals_sql},
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
                    {totals_sql}
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
                    {totals_sql}
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
        *,
        include_partial: bool = False,
    ) -> list[dict]:
        """Return the jobs with the highest total token usage."""
        start, end = self._resolve_date_range(
            "custom" if from_date else "month", from_date, to_date
        )
        totals_sql = _PARTIAL_ACCOUNTING_TOTALS_SQL if include_partial else _ACCOUNTING_TOTALS_SQL

        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                f"""
                SELECT
                    job_id,
                    COUNT(*) AS calls,
                    {totals_sql}
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
        elif period in ("day", "today"):
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
