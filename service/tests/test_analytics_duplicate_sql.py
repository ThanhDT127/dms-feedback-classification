"""Duplicate SQL pagination budgets and differential legacy contract checks."""

from __future__ import annotations

import math
import sqlite3
from collections import defaultdict

import pytest
from analytics_support import seed_classified_records

from dms.analytics import AnalyticsFilter, FeedbackAnalyticsRepository
from dms.analytics.service import FeedbackAnalyticsService


def legacy_duplicates(repo, scope, *, page=1, page_size=2):
    """Frozen grouping/output algorithm; _rows supplies the unchanged global scope."""
    service = FeedbackAnalyticsService(repo)
    groups = defaultdict(list)
    for row in service._rows(scope):
        normalized = str(row.get("normalized_content") or "").strip()
        if normalized:
            groups[normalized].append(row)
    items = []
    for rows in groups.values():
        if len(rows) < 2:
            continue
        codes = sorted({str(row.get("issue_code") or "").strip() for row in rows} - {""})
        units = sorted({str(row.get("unit_name") or "").strip() for row in rows} - {""})
        contents = sorted({" ".join(str(row["content"]).split()) for row in rows})
        items.append(
            dict(
                content=contents[0],
                record_count=len(rows),
                duplicate_rows=len(rows) - 1,
                issue_count=len(codes),
                issue_codes=codes,
                units=units,
            )
        )
    items.sort(key=lambda item: (-item["record_count"], item["content"]))
    total = len(items)
    start = (page - 1) * page_size
    return dict(
        items=items[start : start + page_size],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=math.ceil(total / page_size) if total else 0,
    )


def test_duplicate_page_reads_only_selected_members_without_projection(tmp_path, monkeypatch):
    repo = FeedbackAnalyticsRepository(tmp_path / "duplicates.db")
    seed_classified_records(
        repo,
        db_path=repo.db_path,
        entries=[
            {
                "content": f"Group {group}",
                "issue_code": f"{group}-{member}",
                "labels": ["Website", "Báo lỗi"],
                "raw_data": {"large": "x" * 10000},
            }
            for group in range(12)
            for member in range(2)
        ],
    )
    scope = AnalyticsFilter()
    expected = legacy_duplicates(repo, scope, page=2, page_size=1)
    statements = []
    returned_rows = []
    connect = repo._conn

    def traced_connection():
        conn = connect()
        conn.set_trace_callback(statements.append)

        def record_row(cursor, values):
            returned_rows.append(tuple(column[0] for column in cursor.description))
            return sqlite3.Row(cursor, values)

        conn.row_factory = record_row
        return conn

    def forbid_projection():
        raise AssertionError("duplicates must not hydrate the complete analytics projection")

    monkeypatch.setattr(repo, "_conn", traced_connection)
    monkeypatch.setattr(repo, "fetch_analytics_rows", forbid_projection)
    actual = FeedbackAnalyticsService(repo).duplicate_details(scope, page=2, page_size=1)

    assert actual == expected
    reads = [
        " ".join(sql.upper().split())
        for sql in statements
        if sql.lstrip().upper().startswith(("SELECT", "WITH"))
    ]
    assert len(reads) <= 2
    assert any("GROUP BY" in sql and "LIMIT 1 OFFSET 1" in sql for sql in reads)
    assert all("FEEDBACK_LABELS" not in sql and "RAW_DATA_JSON" not in sql for sql in reads)
    # One count row plus only the two requested duplicate members cross into Python.
    assert len(returned_rows) <= 3
    assert all("raw_data_json" not in columns for columns in returned_rows)


def test_display_content_collapses_unicode_whitespace_before_minimum_and_sort(tmp_path):
    repo = FeedbackAnalyticsRepository(tmp_path / "display.db")
    contents = ["\tZulu\u2003text", "Alpha\ntext", "\u00a0Beta\ttext", "Beta text"]
    seed_classified_records(
        repo,
        db_path=repo.db_path,
        entries=[
            {"content": content, "issue_code": str(index)} for index, content in enumerate(contents)
        ],
    )
    with repo._conn() as conn:
        conn.executemany(
            "UPDATE feedback_records SET normalized_content = ? WHERE feedback_id = ?",
            [("first" if index < 2 else "second", index + 1) for index in range(4)],
        )
    scope = AnalyticsFilter()
    expected = legacy_duplicates(repo, scope, page_size=1)
    assert expected["items"][0]["content"] == "Alpha text"
    assert FeedbackAnalyticsService(repo).duplicate_details(scope, page=1, page_size=1) == expected


@pytest.mark.parametrize("page", [99, 10**30])
def test_out_of_range_page_keeps_total_and_returns_no_items(tmp_path, page):
    repo = FeedbackAnalyticsRepository(tmp_path / "out-of-range.db")
    seed_classified_records(
        repo,
        db_path=repo.db_path,
        entries=[
            {"content": f"Group {group}", "issue_code": f"{group}-{member}"}
            for group in range(3)
            for member in range(2)
        ],
    )
    expected = legacy_duplicates(repo, AnalyticsFilter(), page=page, page_size=1)
    assert expected["total"] == 3
    assert (
        FeedbackAnalyticsService(repo).duplicate_details(AnalyticsFilter(), page=page, page_size=1)
        == expected
    )


def test_normalized_keys_use_unicode_strip_without_casefold(tmp_path):
    repo = FeedbackAnalyticsRepository(tmp_path / "unicode.db")
    keys = ["\u2003Same\u00a0", "Same", " same ", "\t same\n", "\u2003\u00a0", "\t\n"]
    seed_classified_records(
        repo,
        db_path=repo.db_path,
        entries=[
            {"content": "identical display", "issue_code": str(index)} for index in range(len(keys))
        ],
    )
    with repo._conn() as conn:
        conn.executemany(
            "UPDATE feedback_records SET normalized_content = ? WHERE feedback_id = ?",
            [(key, index + 1) for index, key in enumerate(keys)],
        )
    scope = AnalyticsFilter()
    expected = legacy_duplicates(repo, scope)
    assert expected["total"] == 2
    assert [item["issue_codes"] for item in expected["items"]] == [["0", "1"], ["2", "3"]]
    assert FeedbackAnalyticsService(repo).duplicate_details(scope, page=1, page_size=2) == expected
