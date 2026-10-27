"""SQLite persistence for the feedback analytics projection and its history."""

from __future__ import annotations

import json
import re
import sqlite3
import threading
from collections.abc import Iterable
from pathlib import Path

from dms.classification_jobs import utc_now_iso

from .models import AnalyticsFilter, BatchClassificationResult, FeedbackInputRecord
from .result_cache import init_cache_revision
from .sql_queries import issues_where, register_issue_functions

_PENDING = "pending"
_COMPLETED = "completed"
_FAILED = "failed"


_UUID_PREFIX_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}_(?:output_)?(.+)$"
)


def _expand_candidate_filenames(filenames: Iterable[str]) -> set[str]:
    """Expand raw input/output/UUID-prefixed filenames into canonical source_file_name candidates."""
    candidates: set[str] = set()
    for raw in filenames:
        name = Path(str(raw or "").strip()).name
        if not name:
            continue
        candidates.add(name)
        match = _UUID_PREFIX_RE.match(name)
        if match:
            stripped = match.group(1).strip()
            if stripped:
                candidates.add(stripped)
        for item in list(candidates):
            if item.lower().endswith("_output.xlsx") and len(item) > len("_output.xlsx"):
                stem = item[: -len("_output.xlsx")].strip()
                if stem:
                    candidates.add(f"{stem}.xlsx")
    return candidates


class FeedbackAnalyticsRepository:
    """Maintain the current feedback projection and immutable per-job snapshots."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._lock = threading.RLock()
        self._cached_analytics_rows: list[dict] | None = None
        self._cache_timestamp: float = 0.0
        self._cache_source_version: tuple[str, int] | None = None
        self._conn_cached: sqlite3.Connection | None = None
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        # ponytail: one connection per repository, serialised by self._lock (16 of 19 call sites
        # hold it; the other 3 are single-statement reads). The old per-call connection was never
        # closed -> fd growth on every request and read locks that blocked WAL checkpointing
        # behind the writer. Connection pool only if this lock shows up in profiles.
        with self._lock:
            if self._conn_cached is None:
                self.db_path.parent.mkdir(parents=True, exist_ok=True)
                conn = sqlite3.connect(str(self.db_path), timeout=30, check_same_thread=False)
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA busy_timeout=30000")
                conn.execute("PRAGMA foreign_keys=ON")
                self._conn_cached = conn
            return self._conn_cached

    def _init_db(self) -> None:
        with self._lock, self._conn() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS analytics_schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL
                )
                """
            )
            applied = {
                int(row["version"])
                for row in conn.execute("SELECT version FROM analytics_schema_migrations")
            }
            if 1 not in applied:
                self._apply_migration_1(conn)
                conn.execute(
                    "INSERT INTO analytics_schema_migrations (version, applied_at) VALUES (?, ?)",
                    (1, utc_now_iso()),
                )
            if 2 not in applied:
                conn.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_feedback_records_active_page_order
                    ON feedback_records(is_active, COALESCE(issue_date, '') DESC, feedback_id DESC)
                    """
                )
                conn.execute(
                    "INSERT INTO analytics_schema_migrations (version, applied_at) VALUES (?, ?)",
                    (2, utc_now_iso()),
                )
            init_cache_revision(conn)
            self._reconcile_startup_state(conn)
            conn.commit()
            self._check_and_sync_fts(conn)

    def sync_fts(self) -> int:
        """Đồng bộ toàn bộ active feedback_records sang FTS5 dual-index."""
        from dms.chat.db.migrations import apply_chat_migrations, sync_fts_index

        with self._lock, self._conn() as conn:
            apply_chat_migrations(conn)
            return sync_fts_index(conn)

    def _check_and_sync_fts(self, conn: sqlite3.Connection | None = None) -> None:
        """Đồng bộ FTS5 nếu có sự lệch số lượng bản ghi hoặc chưa có FTS."""
        from dms.chat.db.migrations import apply_chat_migrations, sync_fts_index

        def _do(c: sqlite3.Connection) -> None:
            has_records = c.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='feedback_records'"
            ).fetchone()
            if not has_records:
                return
            apply_chat_migrations(c)
            active_cnt = c.execute(
                "SELECT COUNT(*) FROM feedback_records WHERE is_active = 1"
            ).fetchone()[0]
            fts_cnt = c.execute(
                "SELECT COUNT(*) FROM feedback_fts_raw"
            ).fetchone()[0]
            if active_cnt != fts_cnt:
                sync_fts_index(c)

        if conn is not None:
            _do(conn)
        else:
            with self._lock, self._conn() as c:
                _do(c)

    @staticmethod
    def _apply_migration_1(conn: sqlite3.Connection) -> None:
        statements = (
            """
            CREATE TABLE feedback_records (
                feedback_id INTEGER PRIMARY KEY,
                source_file_key TEXT NOT NULL,
                source_file_name TEXT NOT NULL,
                source_row_number INTEGER NOT NULL,
                last_job_id TEXT NOT NULL REFERENCES classification_jobs(job_id),
                raw_data_json TEXT NOT NULL,
                content TEXT NOT NULL,
                normalized_content TEXT NOT NULL,
                issue_code TEXT,
                issue_date TEXT,
                source TEXT,
                unit_name TEXT,
                business_status TEXT,
                product TEXT,
                product_line TEXT,
                model TEXT,
                sentiment TEXT,
                brand TEXT,
                bm25_score REAL,
                classification_state TEXT NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                classified_at TEXT,
                UNIQUE(source_file_key, source_row_number)
            )
            """,
            """
            CREATE TABLE feedback_record_versions (
                feedback_record_version_id INTEGER PRIMARY KEY,
                feedback_id INTEGER NOT NULL REFERENCES feedback_records(feedback_id),
                job_id TEXT NOT NULL REFERENCES classification_jobs(job_id),
                source_file_key TEXT NOT NULL,
                source_file_name TEXT NOT NULL,
                source_row_number INTEGER NOT NULL,
                raw_data_json TEXT NOT NULL,
                content TEXT NOT NULL,
                normalized_content TEXT NOT NULL,
                issue_code TEXT,
                issue_date TEXT,
                source TEXT,
                unit_name TEXT,
                business_status TEXT,
                product TEXT,
                product_line TEXT,
                model TEXT,
                sentiment TEXT,
                brand TEXT,
                bm25_score REAL,
                labels_json TEXT NOT NULL DEFAULT '[]',
                classification_state TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                classified_at TEXT,
                UNIQUE(feedback_id, job_id)
            )
            """,
            """
            CREATE TABLE feedback_labels (
                feedback_id INTEGER NOT NULL REFERENCES feedback_records(feedback_id),
                label TEXT NOT NULL,
                major_group TEXT,
                created_at TEXT NOT NULL,
                UNIQUE(feedback_id, label)
            )
            """,
            "CREATE INDEX idx_feedback_records_active_issue_date ON feedback_records(is_active, issue_date)",
            "CREATE INDEX idx_feedback_records_active_issue_code ON feedback_records(is_active, issue_code)",
            "CREATE INDEX idx_feedback_records_source ON feedback_records(source)",
            "CREATE INDEX idx_feedback_records_unit_name ON feedback_records(unit_name)",
            "CREATE INDEX idx_feedback_record_versions_job_id ON feedback_record_versions(job_id, feedback_id)",
            "CREATE INDEX idx_feedback_labels_major_group ON feedback_labels(major_group, feedback_id)",
        )
        for statement in statements:
            conn.execute(statement)

    @staticmethod
    def _record_values(record: FeedbackInputRecord) -> tuple[object, ...]:
        return (
            json.dumps(record.raw_data, ensure_ascii=False, default=str),
            record.content,
            record.normalized_content,
            record.issue_code,
            record.issue_date,
            record.source,
            record.unit_name,
            record.business_status,
        )

    @staticmethod
    def _snapshot_values(
        *,
        job_id: str,
        source_file_key: str,
        source_file_name: str,
        record: FeedbackInputRecord,
        now: str,
    ) -> tuple[object, ...]:
        return (
            job_id,
            source_file_key,
            source_file_name,
            record.source_row_number,
            *FeedbackAnalyticsRepository._record_values(record),
            _PENDING,
            now,
            now,
        )

    def get_job_source_file_key(self, job_id: str) -> str | None:
        """Keep the original input identity when a job is retried by another worker."""
        with self._lock, self._conn() as conn:
            rows = conn.execute(
                "SELECT DISTINCT source_file_key FROM feedback_record_versions WHERE job_id = ?",
                (job_id,),
            ).fetchall()
        if len(rows) > 1:
            raise ValueError(f"Ambiguous input source keys for job_id={job_id!r}")
        return str(rows[0]["source_file_key"]) if rows else None

    def persist_input_snapshot(
        self,
        *,
        job_id: str,
        source_file_key: str,
        source_file_name: str,
        records: Iterable[FeedbackInputRecord],
        deactivate_absent: bool,
    ) -> None:
        """Upsert current inputs and create/retry the matching job snapshots."""
        snapshot_records = list(records)
        now = utc_now_iso()
        with self._lock, self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            job = conn.execute(
                "SELECT mode FROM classification_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            if job is None:
                raise ValueError(f"Unknown classification job: {job_id!r}")
            is_analytics_ingest = job["mode"] == "analytics_ingest"
            seen_rows = {record.source_row_number for record in snapshot_records}
            for record in snapshot_records:
                existing = conn.execute(
                    """
                    SELECT feedback_id, last_job_id, classification_state
                    FROM feedback_records
                    WHERE source_file_key = ? AND source_row_number = ?
                    """,
                    (source_file_key, record.source_row_number),
                ).fetchone()
                if existing is None:
                    cursor = conn.execute(
                        """
                        INSERT INTO feedback_records (
                            source_file_key, source_file_name, source_row_number, last_job_id,
                            raw_data_json, content, normalized_content, issue_code, issue_date,
                            source, unit_name, business_status, classification_state, is_active,
                            created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                        """,
                        (
                            source_file_key,
                            source_file_name,
                            record.source_row_number,
                            job_id,
                            *self._record_values(record),
                            _PENDING,
                            now,
                            now,
                        ),
                    )
                    if cursor.lastrowid is None:
                        raise RuntimeError("Inserted feedback record did not return an id")
                    feedback_id = cursor.lastrowid
                else:
                    feedback_id = int(existing["feedback_id"])
                    keep_completed_current = existing["classification_state"] == _COMPLETED and (
                        existing["last_job_id"] == job_id or is_analytics_ingest
                    )
                    if keep_completed_current:
                        conn.execute(
                            """
                            UPDATE feedback_records
                            SET source_file_name = ?, raw_data_json = ?, content = ?, normalized_content = ?,
                                issue_code = ?, issue_date = ?, source = ?, unit_name = ?,
                                business_status = ?, is_active = 1, updated_at = ?
                            WHERE feedback_id = ?
                            """,
                            (source_file_name, *self._record_values(record), now, feedback_id),
                        )
                    else:
                        conn.execute(
                            """
                            UPDATE feedback_records
                            SET source_file_name = ?, last_job_id = ?, raw_data_json = ?, content = ?,
                                normalized_content = ?, issue_code = ?, issue_date = ?, source = ?,
                                unit_name = ?, business_status = ?, product = NULL, product_line = NULL,
                                model = NULL, sentiment = NULL, brand = NULL, bm25_score = NULL,
                                classification_state = ?, is_active = 1, classified_at = NULL, updated_at = ?
                            WHERE feedback_id = ?
                            """,
                            (
                                source_file_name,
                                job_id,
                                *self._record_values(record),
                                _PENDING,
                                now,
                                feedback_id,
                            ),
                        )
                        conn.execute(
                            "DELETE FROM feedback_labels WHERE feedback_id = ?", (feedback_id,)
                        )

                version = conn.execute(
                    """
                    SELECT feedback_record_version_id, classification_state,
                           product, product_line, model, sentiment, brand, bm25_score,
                           labels_json, classified_at
                    FROM feedback_record_versions
                    WHERE feedback_id = ? AND job_id = ?
                    """,
                    (feedback_id, job_id),
                ).fetchone()
                if version is None:
                    conn.execute(
                        """
                        INSERT INTO feedback_record_versions (
                            feedback_id, job_id, source_file_key, source_file_name, source_row_number,
                            raw_data_json, content, normalized_content, issue_code, issue_date, source,
                            unit_name, business_status, classification_state, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            feedback_id,
                            *self._snapshot_values(
                                job_id=job_id,
                                source_file_key=source_file_key,
                                source_file_name=source_file_name,
                                record=record,
                                now=now,
                            ),
                        ),
                    )
                elif version["classification_state"] != _COMPLETED:
                    conn.execute(
                        """
                        UPDATE feedback_record_versions
                        SET source_file_key = ?, source_file_name = ?, source_row_number = ?,
                            raw_data_json = ?, content = ?, normalized_content = ?, issue_code = ?,
                            issue_date = ?, source = ?, unit_name = ?, business_status = ?,
                            product = NULL, product_line = NULL, model = NULL, sentiment = NULL,
                            brand = NULL, bm25_score = NULL, labels_json = '[]',
                            classification_state = ?, classified_at = NULL, updated_at = ?
                        WHERE feedback_record_version_id = ?
                        """,
                        (
                            source_file_key,
                            source_file_name,
                            record.source_row_number,
                            *self._record_values(record),
                            _PENDING,
                            now,
                            version["feedback_record_version_id"],
                        ),
                    )
                elif existing is not None and not keep_completed_current:
                    conn.execute(
                        """
                        UPDATE feedback_records
                        SET last_job_id = ?, product = ?, product_line = ?, model = ?,
                            sentiment = ?, brand = ?, bm25_score = ?,
                            classification_state = ?, is_active = 1,
                            classified_at = ?, updated_at = ?
                        WHERE feedback_id = ?
                        """,
                        (
                            job_id,
                            version["product"],
                            version["product_line"],
                            version["model"],
                            version["sentiment"],
                            version["brand"],
                            version["bm25_score"],
                            _COMPLETED,
                            version["classified_at"] or now,
                            now,
                            feedback_id,
                        ),
                    )
                    conn.execute(
                        "DELETE FROM feedback_labels WHERE feedback_id = ?",
                        (feedback_id,),
                    )
                    try:
                        saved_labels = json.loads(version["labels_json"] or "[]")
                    except Exception:
                        saved_labels = []
                    if isinstance(saved_labels, list) and saved_labels:
                        conn.executemany(
                            """
                            INSERT OR IGNORE INTO feedback_labels (feedback_id, label, major_group, created_at)
                            VALUES (?, ?, ?, ?)
                            """,
                            [
                                (
                                    feedback_id,
                                    str(item.get("label")),
                                    item.get("major_group"),
                                    now,
                                )
                                for item in saved_labels
                                if isinstance(item, dict) and item.get("label")
                            ],
                        )

            if deactivate_absent:
                if seen_rows:
                    placeholders = ", ".join("?" for _ in seen_rows)
                    conn.execute(
                        f"""
                        UPDATE feedback_records
                        SET is_active = 0, updated_at = ?
                        WHERE source_file_key = ? AND source_row_number NOT IN ({placeholders})
                        """,
                        (now, source_file_key, *sorted(seen_rows)),
                    )
                else:
                    conn.execute(
                        """
                        UPDATE feedback_records
                        SET is_active = 0, updated_at = ?
                        WHERE source_file_key = ?
                        """,
                        (now, source_file_key),
                    )
            conn.commit()
            self._check_and_sync_fts(conn)
        self._cached_analytics_rows = None
        self._cache_source_version = None

    def apply_batch_results(
        self,
        *,
        job_id: str,
        results: Iterable[BatchClassificationResult],
        minor_to_major: dict[str, str],
    ) -> None:
        """Persist completed versions and update only the current job's projection."""
        now = utc_now_iso()
        with self._lock, self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            for result in results:
                label_snapshot = [
                    {"label": label, "major_group": minor_to_major.get(label)}
                    for label in result.labels
                ]
                labels_json = json.dumps(label_snapshot, ensure_ascii=False)
                matching_versions = conn.execute(
                    """
                    SELECT v.feedback_record_version_id, v.feedback_id, v.classification_state
                    FROM feedback_record_versions v
                    WHERE v.job_id = ? AND v.source_row_number = ?
                    """,
                    (job_id, result.source_row_number),
                ).fetchall()
                if not matching_versions:
                    raise ValueError(
                        "No input snapshot for "
                        f"job_id={job_id!r}, source_row_number={result.source_row_number}"
                    )
                if len(matching_versions) > 1:
                    raise ValueError(
                        "Ambiguous input snapshots for "
                        f"job_id={job_id!r}, source_row_number={result.source_row_number}"
                    )
                version_rows = [
                    version
                    for version in matching_versions
                    if version["classification_state"] == _PENDING
                ]
                for version in version_rows:
                    feedback_id = int(version["feedback_id"])
                    conn.execute(
                        """
                        UPDATE feedback_record_versions
                        SET product = ?, product_line = ?, model = ?, sentiment = ?, brand = ?,
                            bm25_score = ?, labels_json = ?, classification_state = ?,
                            classified_at = ?, updated_at = ?
                        WHERE feedback_record_version_id = ?
                        """,
                        (
                            result.product,
                            result.product_line,
                            result.model,
                            result.sentiment,
                            result.brand,
                            result.bm25_score,
                            labels_json,
                            _COMPLETED,
                            now,
                            now,
                            version["feedback_record_version_id"],
                        ),
                    )
                    current = conn.execute(
                        """
                        SELECT feedback_id FROM feedback_records
                        WHERE feedback_id = ? AND last_job_id = ?
                        """,
                        (feedback_id, job_id),
                    ).fetchone()
                    if current is None:
                        continue
                    conn.execute(
                        """
                        UPDATE feedback_records
                        SET product = ?, product_line = ?, model = ?, sentiment = ?, brand = ?,
                            bm25_score = ?, classification_state = ?, classified_at = ?, updated_at = ?
                        WHERE feedback_id = ? AND last_job_id = ?
                        """,
                        (
                            result.product,
                            result.product_line,
                            result.model,
                            result.sentiment,
                            result.brand,
                            result.bm25_score,
                            _COMPLETED,
                            now,
                            now,
                            feedback_id,
                            job_id,
                        ),
                    )
                    conn.execute(
                        "DELETE FROM feedback_labels WHERE feedback_id = ?", (feedback_id,)
                    )
                    conn.executemany(
                        """
                        INSERT INTO feedback_labels (feedback_id, label, major_group, created_at)
                        VALUES (?, ?, ?, ?)
                        """,
                        [
                            (feedback_id, label, minor_to_major.get(label), now)
                            for label in result.labels
                        ],
                    )
            conn.commit()
            self._check_and_sync_fts(conn)
        self._cached_analytics_rows = None
        self._cache_source_version = None

    def mark_job_unfinished_failed(self, job_id: str) -> None:
        """Mark the job's pending snapshots and matching current rows as failed."""
        now = utc_now_iso()
        with self._lock, self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                UPDATE feedback_record_versions
                SET classification_state = ?, updated_at = ?
                WHERE job_id = ? AND classification_state = ?
                """,
                (_FAILED, now, job_id, _PENDING),
            )
            conn.execute(
                """
                UPDATE feedback_records
                SET classification_state = ?, updated_at = ?
                WHERE last_job_id = ? AND classification_state = ?
                """,
                (_FAILED, now, job_id, _PENDING),
            )
            conn.commit()
        self._cached_analytics_rows = None
        self._cache_source_version = None

    def has_active_records_for_source_key(self, source_file_key: str) -> bool:
        """Return True if at least one active feedback_records row exists for source_file_key."""
        with self._lock, self._conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM feedback_records WHERE source_file_key = ? AND is_active = 1 LIMIT 1",
                (source_file_key,),
            ).fetchone()
        return row is not None

    def _rollback_cancelled_job_locked(
        self,
        conn: sqlite3.Connection,
        job_id: str,
        now: str,
    ) -> int:
        """Roll back or deactivate current records belonging to a cancelled job inside an open transaction."""
        conn.execute(
            """
            UPDATE feedback_record_versions
            SET classification_state = ?, updated_at = ?
            WHERE job_id = ? AND classification_state = ?
            """,
            (_FAILED, now, job_id, _PENDING),
        )

        job_row = conn.execute(
            "SELECT filename FROM classification_jobs WHERE job_id = ?",
            (job_id,),
        ).fetchone()
        filename = str(job_row["filename"]).strip() if job_row and job_row["filename"] else ""

        impacted_rows = conn.execute(
            """
            SELECT DISTINCT r.feedback_id
            FROM feedback_records r
            LEFT JOIN feedback_record_versions v ON v.feedback_id = r.feedback_id AND v.job_id = ?
            WHERE r.last_job_id = ? OR v.feedback_id IS NOT NULL
            """,
            (job_id, job_id),
        ).fetchall()
        impacted_ids = {int(row["feedback_id"]) for row in impacted_rows}

        if filename:
            has_completed_classify_job = conn.execute(
                """
                SELECT 1 FROM classification_jobs
                WHERE filename = ?
                  AND job_id != ?
                  AND status = 'completed'
                  AND COALESCE(cancellation_requested, 0) = 0
                  AND mode != 'analytics_ingest'
                LIMIT 1
                """,
                (filename, job_id),
            ).fetchone()
            has_other_active_job = conn.execute(
                """
                SELECT 1 FROM classification_jobs
                WHERE filename = ?
                  AND job_id != ?
                  AND status IN ('queued', 'running')
                  AND COALESCE(cancellation_requested, 0) = 0
                  AND mode != 'analytics_ingest'
                LIMIT 1
                """,
                (filename, job_id),
            ).fetchone()
            if not has_completed_classify_job and not has_other_active_job:
                extra_rows = conn.execute(
                    "SELECT feedback_id FROM feedback_records WHERE source_file_name = ?",
                    (filename,),
                ).fetchall()
                impacted_ids.update(int(r["feedback_id"]) for r in extra_rows)

        changed = 0
        for feedback_id in sorted(impacted_ids):
            prior = conn.execute(
                """
                SELECT v.*
                FROM feedback_record_versions v
                JOIN classification_jobs j ON j.job_id = v.job_id
                WHERE v.feedback_id = ?
                  AND v.job_id != ?
                  AND v.classification_state = ?
                  AND j.status = 'completed'
                  AND COALESCE(j.cancellation_requested, 0) = 0
                  AND j.mode != 'analytics_ingest'
                ORDER BY v.feedback_record_version_id DESC
                LIMIT 1
                """,
                (feedback_id, job_id, _COMPLETED),
            ).fetchone()
            if prior is not None:
                conn.execute(
                    """
                    UPDATE feedback_records
                    SET last_job_id = ?, product = ?, product_line = ?, model = ?,
                        sentiment = ?, brand = ?, bm25_score = ?,
                        classification_state = ?, is_active = 1,
                        classified_at = ?, updated_at = ?
                    WHERE feedback_id = ?
                    """,
                    (
                        prior["job_id"],
                        prior["product"],
                        prior["product_line"],
                        prior["model"],
                        prior["sentiment"],
                        prior["brand"],
                        prior["bm25_score"],
                        _COMPLETED,
                        prior["classified_at"],
                        now,
                        feedback_id,
                    ),
                )
                conn.execute(
                    "DELETE FROM feedback_labels WHERE feedback_id = ?",
                    (feedback_id,),
                )
                try:
                    prior_labels = json.loads(prior["labels_json"] or "[]")
                except Exception:
                    prior_labels = []
                if isinstance(prior_labels, list) and prior_labels:
                    conn.executemany(
                        """
                        INSERT OR IGNORE INTO feedback_labels (feedback_id, label, major_group, created_at)
                        VALUES (?, ?, ?, ?)
                        """,
                        [
                            (
                                feedback_id,
                                str(item.get("label")),
                                item.get("major_group"),
                                now,
                            )
                            for item in prior_labels
                            if isinstance(item, dict) and item.get("label")
                        ],
                    )
            else:
                conn.execute(
                    """
                    UPDATE feedback_records
                    SET product = NULL, product_line = NULL, model = NULL,
                        sentiment = NULL, brand = NULL, bm25_score = NULL,
                        classification_state = ?, is_active = 0,
                        classified_at = NULL, updated_at = ?
                    WHERE feedback_id = ?
                    """,
                    (_FAILED, now, feedback_id),
                )
                conn.execute(
                    "DELETE FROM feedback_labels WHERE feedback_id = ?",
                    (feedback_id,),
                )
            changed += 1
        return changed

    def rollback_cancelled_job(self, job_id: str) -> int:
        """Deactivate or restore feedback_records affected by a cancelled classification job."""
        now = utc_now_iso()
        with self._lock, self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            changed = self._rollback_cancelled_job_locked(conn, job_id, now)
            conn.commit()
            self._check_and_sync_fts(conn)
        self._cached_analytics_rows = None
        self._cache_source_version = None
        return changed

    def _purge_file_records_locked(
        self,
        conn: sqlite3.Connection,
        filenames: Iterable[str],
    ) -> int:
        """Delete analytics records, labels, versions, and ingest jobs for deleted files."""
        candidates = _expand_candidate_filenames(filenames)
        if not candidates:
            return 0

        now = utc_now_iso()
        placeholders = ", ".join("?" for _ in candidates)
        candidate_list = sorted(candidates)

        matched_jobs = conn.execute(
            f"""
            SELECT job_id, filename, mode, status, input_path, output_path
            FROM classification_jobs
            WHERE filename IN ({placeholders})
            """,
            candidate_list,
        ).fetchall()

        all_jobs = conn.execute(
            "SELECT job_id, filename, mode, status, input_path, output_path FROM classification_jobs"
        ).fetchall()
        matched_job_ids: set[str] = {str(r["job_id"]) for r in matched_jobs}
        ingest_job_ids: set[str] = {
            str(r["job_id"]) for r in matched_jobs if r["mode"] == "analytics_ingest"
        }

        for j in all_jobs:
            in_name = Path(str(j["input_path"] or "")).name
            out_name = Path(str(j["output_path"] or "")).name
            j_expanded = _expand_candidate_filenames([j["filename"] or "", in_name, out_name])
            if j_expanded & candidates:
                matched_job_ids.add(str(j["job_id"]))
                if j["filename"]:
                    candidates.add(str(j["filename"]))
                if j["mode"] == "analytics_ingest":
                    ingest_job_ids.add(str(j["job_id"]))

        placeholders = ", ".join("?" for _ in candidates)
        candidate_list = sorted(candidates)

        feedback_ids: set[int] = set()
        rows = conn.execute(
            f"SELECT feedback_id FROM feedback_records WHERE source_file_name IN ({placeholders})",
            candidate_list,
        ).fetchall()
        feedback_ids.update(int(r["feedback_id"]) for r in rows)

        v_rows = conn.execute(
            f"SELECT DISTINCT feedback_id, job_id FROM feedback_record_versions WHERE source_file_name IN ({placeholders})",
            candidate_list,
        ).fetchall()
        for r in v_rows:
            feedback_ids.add(int(r["feedback_id"]))
            matched_job_ids.add(str(r["job_id"]))

        if matched_job_ids:
            j_placeholders = ", ".join("?" for _ in matched_job_ids)
            j_list = sorted(matched_job_ids)
            r_by_job = conn.execute(
                f"SELECT feedback_id FROM feedback_records WHERE last_job_id IN ({j_placeholders})",
                j_list,
            ).fetchall()
            feedback_ids.update(int(r["feedback_id"]) for r in r_by_job)
            v_by_job = conn.execute(
                f"SELECT DISTINCT feedback_id FROM feedback_record_versions WHERE job_id IN ({j_placeholders})",
                j_list,
            ).fetchall()
            feedback_ids.update(int(r["feedback_id"]) for r in v_by_job)

            conn.execute(
                f"""
                UPDATE classification_jobs
                SET status = 'cancelled',
                    cancellation_requested = 0,
                    completed_at = COALESCE(completed_at, ?),
                    updated_at = ?
                WHERE job_id IN ({j_placeholders}) AND status IN ('queued', 'running')
                """,
                (now, now, *j_list),
            )

        deleted_count = 0
        if feedback_ids:
            f_placeholders = ", ".join("?" for _ in feedback_ids)
            f_list = sorted(feedback_ids)
            conn.execute(
                f"DELETE FROM feedback_labels WHERE feedback_id IN ({f_placeholders})",
                f_list,
            )
            conn.execute(
                f"DELETE FROM feedback_record_versions WHERE feedback_id IN ({f_placeholders})",
                f_list,
            )
            cur = conn.execute(
                f"DELETE FROM feedback_records WHERE feedback_id IN ({f_placeholders})",
                f_list,
            )
            deleted_count = int(cur.rowcount or len(f_list))

        if ingest_job_ids:
            i_placeholders = ", ".join("?" for _ in ingest_job_ids)
            i_list = sorted(ingest_job_ids)
            conn.execute(
                f"DELETE FROM feedback_record_versions WHERE job_id IN ({i_placeholders})",
                i_list,
            )
            conn.execute(
                f"DELETE FROM classification_job_results WHERE job_id IN ({i_placeholders})",
                i_list,
            )
            conn.execute(
                f"DELETE FROM classification_jobs WHERE job_id IN ({i_placeholders}) AND NOT EXISTS (SELECT 1 FROM feedback_records r WHERE r.last_job_id = classification_jobs.job_id)",
                i_list,
            )

        return deleted_count

    def purge_file_records(self, filenames: Iterable[str]) -> int:
        """Purge all analytics records, versions, and labels associated with deleted files."""
        with self._lock, self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            deleted = self._purge_file_records_locked(conn, filenames)
            conn.commit()
            self._check_and_sync_fts(conn)
        self._cached_analytics_rows = None
        self._cache_source_version = None
        return deleted

    def reconcile_remote_input_files(
        self,
        remote_filenames: Iterable[str],
        *,
        seen_filenames: Iterable[str] = (),
    ) -> int:
        """Purge active records for files that were previously tracked on SharePoint/web and deleted from SharePoint Input."""
        remote_set = {Path(str(n or "").strip()).name for n in remote_filenames if str(n or "").strip()}
        seen_set = {Path(str(n or "").strip()).name for n in seen_filenames if str(n or "").strip()}
        with self._lock, self._conn() as conn:
            has_tables = conn.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name IN ('feedback_records', 'classification_jobs')"
            ).fetchone()[0]
            if int(has_tables) < 2:
                return 0
            active_files = [
                str(r["source_file_name"])
                for r in conn.execute(
                    "SELECT DISTINCT source_file_name FROM feedback_records WHERE is_active = 1"
                ).fetchall()
                if r["source_file_name"]
            ]
            to_purge: list[str] = []
            for fname in active_files:
                if fname in remote_set:
                    continue
                active_job = conn.execute(
                    """
                    SELECT 1 FROM classification_jobs
                    WHERE filename = ? AND status IN ('queued', 'running') AND COALESCE(cancellation_requested, 0) = 0
                    LIMIT 1
                    """,
                    (fname,),
                ).fetchone()
                if active_job is not None:
                    continue
                was_tracked = fname in seen_set or (
                    conn.execute(
                        """
                        SELECT 1 FROM classification_jobs
                        WHERE filename = ? AND mode IN ('watcher', 'single', 'reclassify')
                        LIMIT 1
                        """,
                        (fname,),
                    ).fetchone()
                    is not None
                )
                if was_tracked:
                    to_purge.append(fname)

            if not to_purge:
                return 0
            conn.execute("BEGIN IMMEDIATE")
            deleted = self._purge_file_records_locked(conn, to_purge)
            conn.commit()
            self._check_and_sync_fts(conn)
        if deleted:
            self._cached_analytics_rows = None
            self._cache_source_version = None
        return deleted

    def _reconcile_startup_state(self, conn: sqlite3.Connection) -> None:
        """Self-heal cancelled jobs and deleted work/input files on repository startup."""
        has_jobs = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='classification_jobs'"
        ).fetchone()
        has_records = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='feedback_records'"
        ).fetchone()
        if not has_jobs or not has_records:
            return

        now = utc_now_iso()
        cancelled_jobs = conn.execute(
            """
            SELECT job_id, status, cancellation_requested
            FROM classification_jobs
            WHERE status = 'cancelled' OR COALESCE(cancellation_requested, 0) = 1
            """
        ).fetchall()
        for row in cancelled_jobs:
            jid = str(row["job_id"])
            if row["status"] != "cancelled" or int(row["cancellation_requested"] or 0) == 1:
                conn.execute(
                    """
                    UPDATE classification_jobs
                    SET status = 'cancelled',
                        cancellation_requested = 0,
                        completed_at = COALESCE(completed_at, ?),
                        updated_at = ?
                    WHERE job_id = ?
                    """,
                    (now, now, jid),
                )
            self._rollback_cancelled_job_locked(conn, jid, now)

        input_dir = self.db_path.parent / "input"
        seen_path = self.db_path.parent / "seen_files.json"
        if input_dir.is_dir() and seen_path.is_file():
            try:
                local_input_names = {p.name for p in input_dir.iterdir() if p.is_file()}
            except Exception:
                local_input_names = set()
            active_files = [
                str(r["source_file_name"])
                for r in conn.execute(
                    "SELECT DISTINCT source_file_name FROM feedback_records WHERE is_active = 1"
                ).fetchall()
                if r["source_file_name"]
            ]
            missing_web_files: list[str] = []
            for fname in active_files:
                web_job = conn.execute(
                    """
                    SELECT 1 FROM classification_jobs
                    WHERE filename = ? AND mode IN ('single', 'analytics_ingest')
                    LIMIT 1
                    """,
                    (fname,),
                ).fetchone()
                if web_job is None:
                    continue
                active_job = conn.execute(
                    """
                    SELECT 1 FROM classification_jobs
                    WHERE filename = ? AND status IN ('queued', 'running')
                    LIMIT 1
                    """,
                    (fname,),
                ).fetchone()
                if active_job is not None:
                    continue
                has_local = fname in local_input_names or any(
                    n.endswith(f"_{fname}") for n in local_input_names
                )
                if not has_local:
                    missing_web_files.append(fname)
            if missing_web_files:
                self._purge_file_records_locked(conn, missing_web_files)

    def fetch_current_records(self, *, row: int | None = None) -> list[dict]:
        where = "WHERE source_row_number = ?" if row is not None else ""
        params: tuple[object, ...] = (row,) if row is not None else ()
        with self._lock, self._conn() as conn:
            rows = conn.execute(
                f"SELECT * FROM feedback_records {where} ORDER BY feedback_id", params
            ).fetchall()
        return [dict(item) for item in rows]

    def fetch_issues_page(
        self,
        analytics_filter: AnalyticsFilter,
        *,
        page: int,
        page_size: int,
        source: str | None,
        unit_name: str | None,
        label: str | None,
        product: str | None,
        business_status: str | None,
    ) -> tuple[list[dict], int]:
        """Read an issues page and its memberships in one consistent snapshot."""
        where, params = issues_where(
            analytics_filter,
            source=source,
            unit_name=unit_name,
            label=label,
            product=product,
            business_status=business_status,
        )
        with self._lock, self._conn() as conn:
            register_issue_functions(conn)
            conn.execute("BEGIN")
            total = int(
                conn.execute(
                    f"SELECT COUNT(*) FROM feedback_records r WHERE {where}", params
                ).fetchone()[0]
            )
            # API pages have no upper bound; avoid binding an oversized SQLite integer.
            offset = (page - 1) * page_size
            if offset >= total:
                return [], total
            # Python sorted missing dates as ''; SQL NULL must tie with empty dates.
            records = conn.execute(
                f"""
                SELECT r.* FROM feedback_records r WHERE {where}
                ORDER BY COALESCE(r.issue_date, '') DESC, r.feedback_id DESC LIMIT ? OFFSET ?
                """,
                (*params, page_size, offset),
            ).fetchall()
            labels_by_feedback: dict[int, list[dict[str, str | None]]] = {}
            if records:
                placeholders = ", ".join("?" for _ in records)
                labels = conn.execute(
                    f"""
                    SELECT feedback_id, label, major_group FROM feedback_labels
                    WHERE feedback_id IN ({placeholders}) ORDER BY feedback_id, rowid
                    """,
                    [record["feedback_id"] for record in records],
                ).fetchall()
                for membership in labels:
                    labels_by_feedback.setdefault(int(membership["feedback_id"]), []).append(
                        {
                            "label": str(membership["label"]),
                            "major_group": membership["major_group"],
                        }
                    )
        return [
            {**dict(record), "labels": labels_by_feedback.get(int(record["feedback_id"]), [])}
            for record in records
        ], total

    def fetch_analytics_rows(self) -> list[dict]:
        """Return current records with their current label memberships (cached with TTL)."""
        import time

        with self._lock:
            now_mono = time.monotonic()
            with self._conn() as revision_conn:
                version_row = revision_conn.execute(
                    "SELECT i.database_id, r.revision "
                    "FROM analytics_cache_identity i JOIN analytics_cache_revision r "
                    "ON i.singleton = r.singleton WHERE i.singleton = 1"
                ).fetchone()
                source_version = (str(version_row[0]), int(version_row[1]))
            if (
                self._cached_analytics_rows is not None
                and self._cache_source_version == source_version
                and (now_mono - self._cache_timestamp) < 60.0
            ):
                return self._cached_analytics_rows

            # Keep check, cold load, construction and publication singleflight.
            with self._conn() as conn:
                conn.execute("BEGIN")
                version_row = conn.execute(
                    "SELECT i.database_id, r.revision "
                    "FROM analytics_cache_identity i JOIN analytics_cache_revision r "
                    "ON i.singleton = r.singleton WHERE i.singleton = 1"
                ).fetchone()
                source_version = (str(version_row[0]), int(version_row[1]))
                records = conn.execute(
                    "SELECT * FROM feedback_records ORDER BY feedback_id"
                ).fetchall()
                labels = conn.execute(
                    """
                    SELECT feedback_id, label, major_group
                    FROM feedback_labels
                    ORDER BY feedback_id, rowid
                    """
                ).fetchall()

            labels_by_feedback: dict[int, list[dict[str, str | None]]] = {}
            for label in labels:
                labels_by_feedback.setdefault(int(label["feedback_id"]), []).append(
                    {"label": str(label["label"]), "major_group": label["major_group"]}
                )
            result = [
                {
                    **dict(record),
                    "labels": labels_by_feedback.get(int(record["feedback_id"]), []),
                }
                for record in records
            ]
            self._cached_analytics_rows = result
            self._cache_timestamp = time.monotonic()
            self._cache_source_version = source_version
            return result

    def fetch_versions(self, *, job_id: str | None = None, row: int | None = None) -> list[dict]:
        conditions: list[str] = []
        params: list[object] = []
        if job_id is not None:
            conditions.append("job_id = ?")
            params.append(job_id)
        if row is not None:
            conditions.append("source_row_number = ?")
            params.append(row)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        with self._lock, self._conn() as conn:
            rows = conn.execute(
                f"SELECT * FROM feedback_record_versions {where} ORDER BY feedback_record_version_id",
                params,
            ).fetchall()
        return [dict(item) for item in rows]

    def fetch_current_labels(self, *, row: int) -> list[str]:
        with self._lock, self._conn() as conn:
            rows = conn.execute(
                """
                SELECT label
                FROM feedback_labels
                WHERE feedback_id = (
                    SELECT feedback_id FROM feedback_records
                    WHERE source_row_number = ?
                    ORDER BY feedback_id DESC LIMIT 1
                )
                ORDER BY rowid
                """,
                (row,),
            ).fetchall()
        return [str(item["label"]) for item in rows]

    def get_label_distribution(self) -> dict[str, int]:
        """Return label counts aggregated directly via SQL without loading JSON payloads."""
        with self._lock, self._conn() as conn:
            table_check = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='feedback_labels'"
            ).fetchone()
            if not table_check:
                return {}
            rows = conn.execute(
                """
                SELECT label, COUNT(*) AS cnt
                FROM feedback_labels
                GROUP BY label
                HAVING cnt > 0
                ORDER BY cnt DESC
                """
            ).fetchall()
            return {row["label"]: int(row["cnt"]) for row in rows if row["label"]}

    def get_cache_revision(self) -> int:
        """Đọc revision hiện tại từ analytics_cache_revision."""
        try:
            with self._conn() as conn:
                row = conn.execute(
                    "SELECT revision FROM analytics_cache_revision WHERE singleton = 1"
                ).fetchone()
                return int(row[0]) if row else 0
        except Exception:
            return 0

