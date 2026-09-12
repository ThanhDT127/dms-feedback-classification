"""SQLite duplicate grouping: paginate groups before reading their members."""

from __future__ import annotations

import math
from contextlib import closing
from typing import Any

from .models import AnalyticsFilter
from .repository import FeedbackAnalyticsRepository
from .sql_queries import issues_where, register_issue_functions


def duplicate_details(
    repository: FeedbackAnalyticsRepository,
    scope: AnalyticsFilter,
    *,
    page: int,
    page_size: int,
) -> dict[str, Any]:
    where, params = issues_where(
        scope, source=None, unit_name=None, label=None, product=None, business_status=None
    )
    key = "analytics_duplicate_key(r.normalized_content)"
    grouped = f"""
        SELECT {key} AS duplicate_key, COUNT(*) AS record_count,
               MIN(analytics_display_content(r.content)) AS content,
               MIN(r.feedback_id) AS first_feedback_id
        FROM feedback_records r
        WHERE {where} AND {key} != ''
        GROUP BY {key}
        HAVING COUNT(*) >= 2
    """
    items: dict[str, dict[str, Any]] = {}
    total = 0
    with closing(repository._conn()) as conn, conn:
        register_issue_functions(conn)
        # SQLite trim only removes ASCII spaces; preserve Python Unicode semantics.
        conn.create_function(
            "analytics_duplicate_key",
            1,
            lambda value: str(value or "").strip(),
            deterministic=True,
        )
        conn.create_function(
            "analytics_display_content",
            1,
            lambda value: " ".join(str(value or "").split()),
            deterministic=True,
        )
        # Keep total and page membership on the same read snapshot during ingestion.
        conn.execute("BEGIN")
        offset = (page - 1) * page_size
        page_groups = []
        if offset <= 9_223_372_036_854_775_807:
            page_groups = conn.execute(
                f"""
                WITH grouped AS ({grouped})
                SELECT duplicate_key, record_count, content, first_feedback_id,
                       COUNT(*) OVER () AS group_total
                FROM grouped
                ORDER BY record_count DESC, content, first_feedback_id
                LIMIT ? OFFSET ?
                """,
                [*params, page_size, offset],
            ).fetchall()
        if page_groups:
            total = int(page_groups[0]["group_total"])
            for row in page_groups:
                items[row["duplicate_key"]] = {
                    "content": row["content"],
                    "record_count": row["record_count"],
                    "duplicate_rows": row["record_count"] - 1,
                    "issue_codes": set(),
                    "units": set(),
                }
            placeholders = ", ".join("?" for _ in page_groups)
            members = conn.execute(
                f"""
                SELECT {key} AS duplicate_key, r.issue_code, r.unit_name
                FROM feedback_records r
                WHERE {where} AND {key} IN ({placeholders})
                """,
                [*params, *(row["duplicate_key"] for row in page_groups)],
            )
            for row in members:
                item = items[row["duplicate_key"]]
                code = str(row["issue_code"] or "").strip()
                unit = str(row["unit_name"] or "").strip()
                if code:
                    item["issue_codes"].add(code)
                if unit:
                    item["units"].add(unit)
        elif page > 1:
            total = int(conn.execute(f"SELECT COUNT(*) FROM ({grouped})", params).fetchone()[0])
    for item in items.values():
        item["issue_codes"] = sorted(item["issue_codes"])
        item["units"] = sorted(item["units"])
        item["issue_count"] = len(item["issue_codes"])
    return {
        "items": list(items.values()),
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": math.ceil(total / page_size) if total else 0,
    }
