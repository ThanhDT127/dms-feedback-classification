"""Persistent storage for file classification jobs."""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

JOB_STATUS_QUEUED = "queued"
JOB_STATUS_RUNNING = "running"
JOB_STATUS_COMPLETED = "completed"
JOB_STATUS_ERROR = "error"
JOB_STATUS_CANCELLED = "cancelled"

TERMINAL_STATUSES = {JOB_STATUS_COMPLETED, JOB_STATUS_ERROR, JOB_STATUS_CANCELLED}
logger = logging.getLogger("dms-web")


def utc_now_iso() -> str:
    """Return an ISO timestamp suitable for persisted job metadata."""
    return datetime.now(UTC).isoformat(timespec="seconds")


def _json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _json_load(value: str | None, default: Any) -> Any:
    if value is None or value == "":
        return default
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _seconds_between(start: str | None, end: str | None) -> float | None:
    started = _parse_iso(start)
    ended = _parse_iso(end)
    if started is None or ended is None:
        return None
    return max(0.0, round((ended - started).total_seconds(), 1))


class ClassificationJobStore:
    """SQLite-backed persistent job store for user-uploaded classification jobs."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._lock = threading.RLock()
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path), timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._lock, self._conn() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS classification_jobs (
                    job_id TEXT PRIMARY KEY,
                    owner_username TEXT NOT NULL,
                    owner_role TEXT NOT NULL DEFAULT 'user',
                    filename TEXT NOT NULL,
                    mode TEXT NOT NULL DEFAULT 'single',
                    status TEXT NOT NULL,
                    input_path TEXT NOT NULL,
                    output_path TEXT NOT NULL,
                    total_rows INTEGER NOT NULL DEFAULT 0,
                    rows_done INTEGER NOT NULL DEFAULT 0,
                    percent INTEGER NOT NULL DEFAULT 0,
                    step INTEGER,
                    step_status TEXT,
                    error TEXT,
                    duration_seconds REAL NOT NULL DEFAULT 0,
                    sp_uploaded INTEGER NOT NULL DEFAULT 0,
                    sp_folder TEXT,
                    sp_web_url TEXT,
                    created_at TEXT NOT NULL,
                    queued_at TEXT,
                    started_at TEXT,
                    completed_at TEXT,
                    retry_count INTEGER NOT NULL DEFAULT 0,
                    last_retry_at TEXT,
                    cancellation_requested INTEGER NOT NULL DEFAULT 0,
                    heartbeat_at TEXT,
                    updated_at TEXT NOT NULL
                )
                """
            )
            self._ensure_column(conn, "queued_at", "TEXT")
            self._ensure_column(conn, "retry_count", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(conn, "last_retry_at", "TEXT")
            self._ensure_column(conn, "cancellation_requested", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(conn, "heartbeat_at", "TEXT")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS classification_job_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(job_id) REFERENCES classification_jobs(job_id) ON DELETE CASCADE
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_classification_jobs_owner_status ON classification_jobs(owner_username, status)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_classification_jobs_created_at ON classification_jobs(created_at)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_classification_job_results_job_id ON classification_job_results(job_id, id)"
            )
            conn.commit()

    @staticmethod
    def _ensure_column(conn: sqlite3.Connection, column_name: str, definition: str) -> None:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(classification_jobs)")}
        if column_name not in columns:
            conn.execute(f"ALTER TABLE classification_jobs ADD COLUMN {column_name} {definition}")

    @staticmethod
    def _row_to_job(row: sqlite3.Row, results: list[dict] | None = None) -> dict:
        job = dict(row)
        job["id"] = job["job_id"]
        job["sp_uploaded"] = bool(job.get("sp_uploaded"))
        job["results"] = results if results is not None else []
        job["retry_count"] = int(job.get("retry_count") or 0)
        job["queued_at"] = job.get("queued_at") or job.get("created_at")
        job["cancellation_requested"] = bool(job.get("cancellation_requested"))
        job["queue_wait_seconds"] = _seconds_between(job.get("queued_at"), job.get("started_at"))
        job["processing_seconds"] = _seconds_between(job.get("started_at"), job.get("completed_at"))
        job["terminal"] = job.get("status") in TERMINAL_STATUSES
        job["can_cancel"] = job.get("status") in {JOB_STATUS_QUEUED, JOB_STATUS_RUNNING} and not job[
            "cancellation_requested"
        ]
        job["can_retry"] = job.get("status") in {JOB_STATUS_ERROR, JOB_STATUS_CANCELLED}
        if job.get("error"):
            job["error_summary"] = str(job["error"])[:180]
        else:
            job["error_summary"] = ""
        return job

    def _load_results(self, conn: sqlite3.Connection, job_id: str, after_id: int = 0) -> list[dict]:
        rows = conn.execute(
            """
            SELECT id, payload
            FROM classification_job_results
            WHERE job_id = ? AND id > ?
            ORDER BY id ASC
            """,
            (job_id, after_id),
        ).fetchall()
        items: list[dict] = []
        for row in rows:
            payload = _json_load(row["payload"], {})
            if isinstance(payload, dict):
                payload.setdefault("_seq", row["id"])
                items.append(payload)
        return items

    def create_job(
        self,
        *,
        job_id: str,
        owner_username: str,
        owner_role: str,
        filename: str,
        mode: str,
        input_path: str | Path,
        output_path: str | Path,
    ) -> dict:
        now = utc_now_iso()
        with self._lock, self._conn() as conn:
            conn.execute(
                """
                INSERT INTO classification_jobs (
                    job_id, owner_username, owner_role, filename, mode, status,
                    input_path, output_path, created_at, queued_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    owner_username,
                    owner_role,
                    filename,
                    mode,
                    JOB_STATUS_QUEUED,
                    str(input_path),
                    str(output_path),
                    now,
                    now,
                    now,
                ),
            )
            conn.commit()
        job = self.get_job(job_id)
        if job is None:
            raise RuntimeError(f"Created job {job_id} could not be loaded")
        return job

    def get_job(self, job_id: str, *, include_results: bool = True) -> dict | None:
        with self._lock, self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM classification_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            if row is None:
                return None
            results = self._load_results(conn, job_id) if include_results else []
            return self._row_to_job(row, results)

    def list_jobs(
        self,
        *,
        owner_username: str | None = None,
        include_results: bool = True,
        active_only: bool = False,
    ) -> list[dict]:
        where: list[str] = []
        params: list[Any] = []
        if owner_username:
            where.append("owner_username = ?")
            params.append(owner_username)
        if active_only:
            where.append("status IN (?, ?)")
            params.extend([JOB_STATUS_QUEUED, JOB_STATUS_RUNNING])
        where_sql = ("WHERE " + " AND ".join(where)) if where else ""

        with self._lock, self._conn() as conn:
            rows = conn.execute(
                f"SELECT * FROM classification_jobs {where_sql} ORDER BY created_at DESC",
                params,
            ).fetchall()
            jobs = []
            for row in rows:
                results = self._load_results(conn, row["job_id"]) if include_results else []
                jobs.append(self._row_to_job(row, results))
            return jobs

    def count_user_jobs(self, owner_username: str, statuses: set[str] | list[str]) -> int:
        if not statuses:
            return 0
        placeholders = ", ".join("?" for _ in statuses)
        params: list[Any] = [owner_username, *list(statuses)]
        with self._lock, self._conn() as conn:
            row = conn.execute(
                f"""
                SELECT COUNT(*) AS count
                FROM classification_jobs
                WHERE owner_username = ? AND status IN ({placeholders})
                """,
                params,
            ).fetchone()
            return int(row["count"] if row else 0)

    def claim_next_job(
        self,
        *,
        worker_id: str,
        global_running_limit: int,
        per_user_running_limit: int,
    ) -> dict | None:
        now = utc_now_iso()
        with self._lock, self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            running_count = conn.execute(
                "SELECT COUNT(*) AS count FROM classification_jobs WHERE status = ?",
                (JOB_STATUS_RUNNING,),
            ).fetchone()["count"]
            if int(running_count) >= global_running_limit:
                conn.rollback()
                return None

            queued_rows = conn.execute(
                """
                SELECT *
                FROM classification_jobs
                WHERE status = ? AND COALESCE(cancellation_requested, 0) = 0
                ORDER BY queued_at ASC, created_at ASC
                """,
                (JOB_STATUS_QUEUED,),
            ).fetchall()
            for row in queued_rows:
                owner_running = conn.execute(
                    """
                    SELECT COUNT(*) AS count
                    FROM classification_jobs
                    WHERE owner_username = ? AND status = ?
                    """,
                    (row["owner_username"], JOB_STATUS_RUNNING),
                ).fetchone()["count"]
                if int(owner_running) >= per_user_running_limit:
                    continue
                cur = conn.execute(
                    """
                    UPDATE classification_jobs
                    SET status = ?, started_at = COALESCE(started_at, ?),
                        heartbeat_at = ?, updated_at = ?
                    WHERE job_id = ? AND status = ? AND COALESCE(cancellation_requested, 0) = 0
                    """,
                    (JOB_STATUS_RUNNING, now, now, now, row["job_id"], JOB_STATUS_QUEUED),
                )
                if cur.rowcount == 1:
                    conn.commit()
                    logger.info("Classification worker %s claimed job %s", worker_id, row["job_id"])
                    return self.get_job(row["job_id"], include_results=False)
            conn.rollback()
            return None

    def mark_running(self, job_id: str) -> None:
        now = utc_now_iso()
        with self._lock, self._conn() as conn:
            conn.execute(
                """
                UPDATE classification_jobs
                SET status = ?, started_at = COALESCE(started_at, ?), updated_at = ?
                WHERE job_id = ?
                """,
                (JOB_STATUS_RUNNING, now, now, job_id),
            )
            conn.commit()

    def heartbeat(self, job_id: str) -> None:
        now = utc_now_iso()
        with self._lock, self._conn() as conn:
            conn.execute(
                """
                UPDATE classification_jobs
                SET heartbeat_at = ?, updated_at = ?
                WHERE job_id = ? AND status = ?
                """,
                (now, now, job_id, JOB_STATUS_RUNNING),
            )
            conn.commit()

    def is_cancellation_requested(self, job_id: str) -> bool:
        with self._lock, self._conn() as conn:
            row = conn.execute(
                """
                SELECT status, cancellation_requested
                FROM classification_jobs
                WHERE job_id = ?
                """,
                (job_id,),
            ).fetchone()
            if row is None:
                return True
            return row["status"] == JOB_STATUS_CANCELLED or bool(row["cancellation_requested"])

    def update_progress(
        self,
        job_id: str,
        *,
        done: int | None = None,
        total: int | None = None,
        step: int | None = None,
        step_status: str | None = None,
    ) -> None:
        assignments = ["updated_at = ?"]
        params: list[Any] = [utc_now_iso()]

        if done is not None:
            total_value = total if total is not None else 0
            percent = int((done / total_value) * 100) if total_value > 0 else 0
            assignments.extend(["rows_done = ?", "total_rows = ?", "percent = ?"])
            params.extend([done, total_value, percent])
        if step is not None:
            assignments.append("step = ?")
            params.append(step)
            assignments.append("step_status = ?")
            params.append(step_status)

        params.append(job_id)
        with self._lock, self._conn() as conn:
            conn.execute(
                f"UPDATE classification_jobs SET {', '.join(assignments)} WHERE job_id = ?",
                params,
            )
            conn.commit()

    def append_results(self, job_id: str, results: list[dict]) -> None:
        if not results:
            return
        now = utc_now_iso()
        with self._lock, self._conn() as conn:
            conn.executemany(
                """
                INSERT INTO classification_job_results (job_id, payload, created_at)
                VALUES (?, ?, ?)
                """,
                [(job_id, _json_dump(result), now) for result in results],
            )
            conn.execute(
                "UPDATE classification_jobs SET updated_at = ? WHERE job_id = ?",
                (now, job_id),
            )
            conn.commit()

    def list_results_after(self, job_id: str, after_id: int = 0) -> list[dict]:
        with self._lock, self._conn() as conn:
            return self._load_results(conn, job_id, after_id)

    def complete_job(
        self,
        job_id: str,
        *,
        total_rows: int,
        rows_done: int,
        output_path: str | Path,
        duration_seconds: float,
        sp_uploaded: bool | None = None,
        sp_folder: str | None = None,
        sp_web_url: str | None = None,
    ) -> None:
        now = utc_now_iso()
        with self._lock, self._conn() as conn:
            conn.execute(
                """
                UPDATE classification_jobs
                SET status = ?, total_rows = ?, rows_done = ?, percent = 100,
                    output_path = ?, duration_seconds = ?, completed_at = ?,
                    updated_at = ?, error = NULL,
                    sp_uploaded = COALESCE(?, sp_uploaded),
                    sp_folder = COALESCE(?, sp_folder),
                    sp_web_url = COALESCE(?, sp_web_url)
                WHERE job_id = ?
                """,
                (
                    JOB_STATUS_COMPLETED,
                    total_rows,
                    rows_done,
                    str(output_path),
                    duration_seconds,
                    now,
                    now,
                    int(sp_uploaded) if sp_uploaded is not None else None,
                    sp_folder,
                    sp_web_url,
                    job_id,
                ),
            )
            conn.commit()

    def fail_job(self, job_id: str, error: str) -> None:
        now = utc_now_iso()
        with self._lock, self._conn() as conn:
            conn.execute(
                """
                UPDATE classification_jobs
                SET status = ?, error = ?, completed_at = COALESCE(completed_at, ?),
                    cancellation_requested = 0, updated_at = ?
                WHERE job_id = ?
                """,
                (JOB_STATUS_ERROR, error, now, now, job_id),
            )
            conn.commit()

    def mark_cancelled(self, job_id: str, message: str | None = None) -> dict | None:
        now = utc_now_iso()
        with self._lock, self._conn() as conn:
            conn.execute(
                """
                UPDATE classification_jobs
                SET status = ?, error = COALESCE(?, error),
                    completed_at = COALESCE(completed_at, ?),
                    cancellation_requested = 0, updated_at = ?
                WHERE job_id = ?
                """,
                (JOB_STATUS_CANCELLED, message, now, now, job_id),
            )
            conn.commit()
            return self.get_job(job_id)

    def cancel_job(self, job_id: str) -> dict | None:
        now = utc_now_iso()
        with self._lock, self._conn() as conn:
            row = conn.execute(
                "SELECT status FROM classification_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            if row is None:
                return None
            if row["status"] in TERMINAL_STATUSES:
                return self.get_job(job_id)
            if row["status"] == JOB_STATUS_QUEUED:
                conn.execute(
                    """
                    UPDATE classification_jobs
                    SET status = ?, completed_at = COALESCE(completed_at, ?),
                        cancellation_requested = 0, updated_at = ?
                    WHERE job_id = ?
                    """,
                    (JOB_STATUS_CANCELLED, now, now, job_id),
                )
            else:
                conn.execute(
                    """
                    UPDATE classification_jobs
                    SET cancellation_requested = 1, updated_at = ?
                    WHERE job_id = ?
                    """,
                    (now, job_id),
                )
            conn.commit()
            return self.get_job(job_id)

    def retry_job(self, job_id: str) -> dict | None:
        return self.requeue_for_retry(job_id, clear_results=True)

    def requeue_for_retry(self, job_id: str, *, clear_results: bool = False) -> dict | None:
        now = utc_now_iso()
        with self._lock, self._conn() as conn:
            row = conn.execute(
                "SELECT status FROM classification_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            if row is None:
                return None
            if row["status"] not in {JOB_STATUS_ERROR, JOB_STATUS_CANCELLED, JOB_STATUS_RUNNING}:
                return self.get_job(job_id)
            conn.execute(
                """
                UPDATE classification_jobs
                SET status = ?, queued_at = ?, started_at = NULL, completed_at = NULL,
                    rows_done = 0, total_rows = 0, percent = 0, step = NULL,
                    step_status = NULL, error = NULL, duration_seconds = 0,
                    retry_count = COALESCE(retry_count, 0) + 1,
                    last_retry_at = ?, cancellation_requested = 0,
                    heartbeat_at = NULL, updated_at = ?
                WHERE job_id = ?
                """,
                (JOB_STATUS_QUEUED, now, now, now, job_id),
            )
            if clear_results:
                conn.execute("DELETE FROM classification_job_results WHERE job_id = ?", (job_id,))
            conn.commit()
            return self.get_job(job_id)

    def maybe_retry_after_failure(self, job_id: str, *, error: str, max_retries: int) -> dict | None:
        with self._lock, self._conn() as conn:
            row = conn.execute(
                "SELECT retry_count FROM classification_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
        if row is None:
            return None
        if int(row["retry_count"] or 0) >= max_retries:
            self.fail_job(job_id, error)
            return self.get_job(job_id, include_results=False)
        return self.requeue_for_retry(job_id, clear_results=False)

    def recover_stale_running_jobs(self, *, stale_after_seconds: int, max_retries: int) -> int:
        cutoff = datetime.now(UTC) - timedelta(seconds=stale_after_seconds)
        recovered = 0
        with self._lock, self._conn() as conn:
            rows = conn.execute(
                """
                SELECT job_id, retry_count, heartbeat_at, updated_at
                FROM classification_jobs
                WHERE status = ?
                """,
                (JOB_STATUS_RUNNING,),
            ).fetchall()
        for row in rows:
            heartbeat = _parse_iso(row["heartbeat_at"] or row["updated_at"])
            if heartbeat is not None and heartbeat > cutoff:
                continue
            if int(row["retry_count"] or 0) >= max_retries:
                self.fail_job(row["job_id"], "Job chạy dở quá lâu sau khi dịch vụ khởi động lại.")
            else:
                self.requeue_for_retry(row["job_id"], clear_results=False)
            recovered += 1
        return recovered

    def queue_metrics(self) -> dict:
        with self._lock, self._conn() as conn:
            rows = conn.execute("SELECT * FROM classification_jobs").fetchall()

        jobs = [self._row_to_job(row, []) for row in rows]
        counts = {
            "queued": 0,
            "running": 0,
            "completed": 0,
            "failed": 0,
            "cancelled": 0,
            "retrying": 0,
            "total": len(jobs),
        }
        wait_times = []
        processing_times = []
        for job in jobs:
            status = job.get("status")
            if status == JOB_STATUS_ERROR:
                counts["failed"] += 1
            elif status in counts:
                counts[status] += 1
            if status == JOB_STATUS_QUEUED and job.get("retry_count", 0) > 0:
                counts["retrying"] += 1
            if job.get("queue_wait_seconds") is not None:
                wait_times.append(float(job["queue_wait_seconds"]))
            if job.get("duration_seconds", 0) > 0:
                processing_times.append(float(job["duration_seconds"]))

        return {
            "counts": counts,
            "avg_queue_wait_seconds": round(sum(wait_times) / len(wait_times), 1)
            if wait_times
            else 0.0,
            "avg_processing_seconds": round(sum(processing_times) / len(processing_times), 1)
            if processing_times
            else 0.0,
        }

    def update_sharepoint(
        self,
        job_id: str,
        *,
        sp_uploaded: bool,
        sp_folder: str | None,
        sp_web_url: str | None,
    ) -> None:
        now = utc_now_iso()
        with self._lock, self._conn() as conn:
            conn.execute(
                """
                UPDATE classification_jobs
                SET sp_uploaded = ?, sp_folder = ?, sp_web_url = ?, updated_at = ?
                WHERE job_id = ?
                """,
                (int(sp_uploaded), sp_folder, sp_web_url, now, job_id),
            )
            conn.commit()
