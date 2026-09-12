"""Cross-worker JSON aggregate cache stored separately from the source database."""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Callable
from contextlib import closing
from pathlib import Path
from typing import Protocol


class _Repository(Protocol):
    db_path: Path


def cache_path(repository: _Repository) -> Path:
    """Use one sidecar for all repository instances targeting the same source."""
    source = Path(repository.db_path).resolve()
    return source.with_name(source.name + ".analytics-cache.sqlite3")


def init_cache_revision(conn: sqlite3.Connection) -> None:
    """Install invalidation in the caller's source-schema transaction; do not commit."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS analytics_cache_revision "
        "(singleton INTEGER PRIMARY KEY CHECK(singleton = 1), revision INTEGER NOT NULL)"
    )
    conn.execute("INSERT OR IGNORE INTO analytics_cache_revision VALUES (1, 0)")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS analytics_cache_identity "
        "(singleton INTEGER PRIMARY KEY CHECK(singleton = 1), database_id TEXT NOT NULL)"
    )
    conn.execute(
        "INSERT OR IGNORE INTO analytics_cache_identity VALUES (1, ?)",
        (uuid.uuid4().hex,),
    )
    for table in ("feedback_records", "feedback_labels"):
        for operation in ("INSERT", "UPDATE", "DELETE"):
            conn.execute(
                f"CREATE TRIGGER IF NOT EXISTS cache_revision_{table}_{operation.lower()} "
                f"AFTER {operation} ON {table} BEGIN "
                "UPDATE analytics_cache_revision SET revision = revision + 1 WHERE singleton = 1; "
                "END"
            )


def _source_version(source: sqlite3.Connection) -> tuple[str, int]:
    row = source.execute(
        "SELECT i.database_id, r.revision "
        "FROM analytics_cache_identity i JOIN analytics_cache_revision r "
        "ON i.singleton = r.singleton WHERE i.singleton = 1"
    ).fetchone()
    return str(row[0]), int(row[1])


def cached_result(repository: _Repository, key: str, compute: Callable[[], dict]) -> dict:
    """Return a detached JSON aggregate shared by repository instances."""
    with (
        closing(sqlite3.connect(repository.db_path, isolation_level=None)) as source,
        closing(sqlite3.connect(cache_path(repository), timeout=30)) as conn,
    ):
        conn.execute("PRAGMA journal_mode=WAL")
        database_id, revision = _source_version(source)
        conn.execute(
            "CREATE TABLE IF NOT EXISTS results "
            "(cache_key TEXT PRIMARY KEY, revision INTEGER NOT NULL, payload TEXT NOT NULL, "
            "database_id TEXT NOT NULL DEFAULT '')"
        )
        columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(results)")}
        if "database_id" not in columns:
            try:
                conn.execute("ALTER TABLE results ADD COLUMN database_id TEXT NOT NULL DEFAULT ''")
            except sqlite3.OperationalError as exc:
                if "duplicate column name" not in str(exc).lower():
                    raise
        row = conn.execute(
            "SELECT payload FROM results WHERE cache_key = ? AND database_id = ? AND revision = ?",
            (key, database_id, revision),
        ).fetchone()
        if row is not None:
            return json.loads(row[0])
        conn.execute("BEGIN IMMEDIATE")
        database_id, revision = _source_version(source)
        row = conn.execute(
            "SELECT payload FROM results WHERE cache_key = ? AND database_id = ? AND revision = ?",
            (key, database_id, revision),
        ).fetchone()
        if row is not None:
            conn.rollback()
            return json.loads(row[0])
        value = compute()
        # The source is deliberately not locked while computing: ingestion must
        # continue. Only publish when the source snapshot is still current.
        if _source_version(source) != (database_id, revision):
            conn.rollback()
            return value
        conn.execute(
            "INSERT OR REPLACE INTO results(cache_key, revision, payload, database_id) "
            "VALUES (?, ?, ?, ?)",
            (key, revision, json.dumps(value), database_id),
        )
        # Keep the sidecar bounded without extending the source write path.
        conn.execute(
            "DELETE FROM results WHERE cache_key NOT IN "
            "(SELECT cache_key FROM results ORDER BY rowid DESC LIMIT 128)"
        )
        conn.commit()
        return value
