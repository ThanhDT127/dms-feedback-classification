"""Label history storage using SQLite."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .time_utils import utc_day_bounds_iso, utc_now_iso


class LabelHistoryStore:
    """Lightweight SQLite-backed label change history."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._init_db()

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS label_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    action TEXT NOT NULL,
                    label_name TEXT NOT NULL,
                    field TEXT NOT NULL,
                    old_value TEXT,
                    new_value TEXT,
                    user TEXT NOT NULL DEFAULT 'Admin',
                    timestamp TEXT NOT NULL
                )
            """)
            conn.commit()

    def _conn(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        return sqlite3.connect(str(self.db_path))

    def record(
        self,
        action: str,
        label_name: str,
        field: str,
        old_value: Any = None,
        new_value: Any = None,
        user: str = "Admin",
    ) -> None:
        """Record a single label change."""
        ts = utc_now_iso()
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO label_history (action, label_name, field, old_value, new_value, user, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    action,
                    label_name,
                    field,
                    json.dumps(old_value, ensure_ascii=False) if old_value is not None else None,
                    json.dumps(new_value, ensure_ascii=False) if new_value is not None else None,
                    user,
                    ts,
                ),
            )
            conn.commit()

    def get_history(
        self,
        limit: int = 20,
        offset: int = 0,
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> dict:
        """Query history with pagination and optional date filter."""
        where_clauses: list[str] = []
        params: list[Any] = []

        if date_from:
            start = utc_day_bounds_iso(date_from=date_from, date_to=date_from)[0]
            where_clauses.append("timestamp >= ?")
            params.append(start)
        if date_to:
            end = utc_day_bounds_iso(date_from=date_to, date_to=date_to)[1]
            where_clauses.append("timestamp <= ?")
            params.append(end)

        where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

        with self._conn() as conn:
            conn.row_factory = sqlite3.Row
            # Total count
            total = conn.execute(f"SELECT COUNT(*) FROM label_history {where_sql}", params).fetchone()[0]

            # Items
            rows = conn.execute(
                f"SELECT * FROM label_history {where_sql} ORDER BY timestamp DESC LIMIT ? OFFSET ?",
                params + [limit, offset],
            ).fetchall()

            items = []
            for row in rows:
                item = dict(row)
                # Parse JSON values
                if item.get("old_value"):
                    try:
                        item["old_value"] = json.loads(item["old_value"])
                    except (json.JSONDecodeError, TypeError):
                        pass
                if item.get("new_value"):
                    try:
                        item["new_value"] = json.loads(item["new_value"])
                    except (json.JSONDecodeError, TypeError):
                        pass
                items.append(item)

        return {
            "items": items,
            "total": total,
            "has_more": (offset + limit) < total,
        }

    def record_diff(
        self,
        old_labels: dict,
        new_labels: dict,
        user: str = "Admin",
    ) -> int:
        """Compare old and new label data and record all changes. Returns count of changes."""
        changes = 0
        ts = utc_now_iso()

        old_defs = old_labels.get("label_definitions", {})
        new_defs = new_labels.get("label_definitions", {})
        old_order = old_labels.get("minor_order", [])
        new_order = new_labels.get("minor_order", [])
        old_mapping = old_labels.get("minor_to_major", {})
        new_mapping = new_labels.get("minor_to_major", {})

        with self._conn() as conn:
            # Detect definition changes
            all_keys = set(list(old_defs.keys()) + list(new_defs.keys()))
            for key in all_keys:
                if key not in old_defs:
                    conn.execute(
                        "INSERT INTO label_history (action, label_name, field, old_value, new_value, user, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        ("add", key, "definition", None, json.dumps(new_defs[key], ensure_ascii=False), user, ts),
                    )
                    changes += 1
                elif key not in new_defs:
                    conn.execute(
                        "INSERT INTO label_history (action, label_name, field, old_value, new_value, user, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        ("delete", key, "definition", json.dumps(old_defs[key], ensure_ascii=False), None, user, ts),
                    )
                    changes += 1
                elif old_defs[key] != new_defs[key]:
                    conn.execute(
                        "INSERT INTO label_history (action, label_name, field, old_value, new_value, user, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        ("edit", key, "definition", json.dumps(old_defs[key], ensure_ascii=False), json.dumps(new_defs[key], ensure_ascii=False), user, ts),
                    )
                    changes += 1

            # Detect minor_order changes
            if old_order != new_order:
                conn.execute(
                    "INSERT INTO label_history (action, label_name, field, old_value, new_value, user, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    ("edit", "_system", "minor_order", json.dumps(old_order, ensure_ascii=False), json.dumps(new_order, ensure_ascii=False), user, ts),
                )
                changes += 1

            # Detect minor_to_major changes
            if old_mapping != new_mapping:
                conn.execute(
                    "INSERT INTO label_history (action, label_name, field, old_value, new_value, user, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    ("edit", "_system", "minor_to_major", json.dumps(old_mapping, ensure_ascii=False), json.dumps(new_mapping, ensure_ascii=False), user, ts),
                )
                changes += 1

            conn.commit()

        return changes
