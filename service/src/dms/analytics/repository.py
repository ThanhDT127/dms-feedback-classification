"""SQLite persistence for the feedback analytics projection and its history."""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterable
from pathlib import Path

from dms.classification_jobs import utc_now_iso

from .models import BatchClassificationResult, FeedbackInputRecord

_PENDING = "pending"
_COMPLETED = "completed"
_FAILED = "failed"


class FeedbackAnalyticsRepository:
    """Maintain the current feedback projection and immutable per-job snapshots."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._lock = threading.RLock()
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path), timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

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
            conn.commit()

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
                    keep_completed_current = (
                        existing["last_job_id"] == job_id
                        and existing["classification_state"] == _COMPLETED
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
                    SELECT feedback_record_version_id, classification_state
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

    def fetch_current_records(self, *, row: int | None = None) -> list[dict]:
        where = "WHERE source_row_number = ?" if row is not None else ""
        params: tuple[object, ...] = (row,) if row is not None else ()
        with self._lock, self._conn() as conn:
            rows = conn.execute(
                f"SELECT * FROM feedback_records {where} ORDER BY feedback_id", params
            ).fetchall()
        return [dict(item) for item in rows]

    def fetch_analytics_rows(self) -> list[dict]:
        """Return current records with their current label memberships."""
        with self._lock, self._conn() as conn:
            records = conn.execute("SELECT * FROM feedback_records ORDER BY feedback_id").fetchall()
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
        return [
            {
                **dict(record),
                "labels": labels_by_feedback.get(int(record["feedback_id"]), []),
            }
            for record in records
        ]

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
