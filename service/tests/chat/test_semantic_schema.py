"""Spec ``chat-semantic-schema`` (b09 task 2.1–2.5) trên DB fixture thật."""

from __future__ import annotations

import json
import sqlite3
import warnings

import pytest

from dms.analytics import AnalyticsFilter
from dms.chat.ai.semantic_schema import (
    ISSUES_VIEW,
    LABELS_VIEW,
    VIEWS,
    column_title,
    load_column_aliases,
    render_schema_vi,
)
from dms.chat.ai.sql_example_retriever import load_examples, select_examples
from dms.chat.contract import UserScope
from dms.chat.guardrails.sql_guard import SqlGuard, context_for

from .sql_fixture import BH, NT, build_sql_fixture, create_scoped_views, run_scoped_sql

ADMIN = UserScope(username="admin", role="admin", display_name="Admin", unit_ids=[])
AUG = AnalyticsFilter(date_from="2026-08-01", date_to="2026-08-31")
JUL = AnalyticsFilter(date_from="2026-07-01", date_to="2026-07-31")


@pytest.fixture(scope="module")
def fixture_db(tmp_path_factory):
    db = tmp_path_factory.mktemp("sql") / "fixture.db"
    service = build_sql_fixture(db)
    return db, service


def metadata_values(service) -> dict:
    everything = AnalyticsFilter()
    geo = service.filter_options(everything)
    issues = service.issue_filter_options(everything)
    return {
        "units": geo.get("units", []),
        "products": issues.get("products", []),
        "statuses": issues.get("statuses", []),
    }


# ── Không lệch với view ──


def view_columns(db, view: str) -> set[str]:
    conn = sqlite3.connect(db)
    try:
        create_scoped_views(conn, ADMIN)
        return {row[1] for row in conn.execute(f"PRAGMA table_info({view})")}
    finally:
        conn.close()


@pytest.mark.parametrize("view", [ISSUES_VIEW, LABELS_VIEW])
def test_declared_columns_exist_in_fixture_view(fixture_db, view):
    db, _ = fixture_db
    actual = view_columns(db, view)
    missing = set(VIEWS[view].columns) - actual
    assert not missing, f"schema khai báo cột không có trong {view}: {sorted(missing)}"
    extra = actual - set(VIEWS[view].columns) - VIEWS[view].forbidden_columns
    if extra:
        warnings.warn(f"{view} có cột chưa khai báo: {sorted(extra)}", stacklevel=1)


def test_province_and_district_not_declared_yet():
    for spec in VIEWS.values():
        assert "province" not in spec.columns and "district" not in spec.columns


def test_schema_prompt_does_not_mention_base_tables():
    text = render_schema_vi()
    for forbidden in ("feedback_records", "feedback_labels", "sqlite_master", "raw_data_json"):
        assert forbidden not in text
    assert ISSUES_VIEW in text and "COUNT(DISTINCT issue_code)" in text


# ── Tiêu đề cột ──


def test_known_and_unknown_aliases():
    assert column_title("so_van_de") == "Số vấn đề"
    assert column_title("so_khach_moi") == "so khach moi"
    assert all(key == key.lower() for key in load_column_aliases())


# ── Ví dụ SQL ──

EXAMPLES = load_examples()


@pytest.mark.parametrize("example", EXAMPLES, ids=[e.id for e in EXAMPLES])
def test_every_example_passes_guard_and_runs(fixture_db, example):
    db, service = fixture_db
    filters = example.analysis_request.get("filters") or {}
    ctx = context_for(filters, scope_units=None, metadata_values=metadata_values(service))
    guarded = SqlGuard().check(example.sql, ctx)
    result = run_scoped_sql(db, guarded.sql, ADMIN)
    assert result.status.value in {"ok", "no_data"}, (
        example.id,
        result.error_message if hasattr(result, "error_message") else result,
    )


def test_examples_are_about_forty_and_unique():
    assert len(EXAMPLES) >= 35
    assert len({e.question for e in EXAMPLES}) == len(EXAMPLES)


def test_selection_prefers_similar_question_and_distinct_shapes():
    chosen = select_examples(
        "top 5 sản phẩm bị phản hồi tiêu cực tháng 8",
        {"measures": ["so_van_de"], "dimensions": ["product"]},
        top_k=4,
    )
    assert chosen[0].id == "ex01"
    assert len({e.shape for e in chosen}) == len(chosen) == 4
    assert select_examples("x", {}, top_k=0) == []


# ── Nhất quán với dashboard (2.5) ──


def scalar(db, sql):
    return run_scoped_sql(db, sql, ADMIN).data[0]


def by_label(db, sql):
    return {row["nhan"]: row["so_van_de"] for row in (run_scoped_sql(db, sql, ADMIN).data or [])}


def items(panel):
    return {item["label"]: item["issue_count"] for item in panel["items"]}


V = ISSUES_VIEW
AUG_WHERE = "issue_code IS NOT NULL AND issue_date BETWEEN '2026-08-01' AND '2026-08-31'"
JUL_WHERE = "issue_code IS NOT NULL AND issue_date BETWEEN '2026-07-01' AND '2026-07-31'"


def test_total_issues_august_matches_overview(fixture_db):
    db, service = fixture_db
    sql = f"SELECT COUNT(DISTINCT issue_code) AS so_van_de FROM {V} WHERE {AUG_WHERE}"
    assert scalar(db, sql)["so_van_de"] == service.overview(AUG)["total_issues"]["value"]


def test_total_issues_july_matches_overview(fixture_db):
    db, service = fixture_db
    sql = f"SELECT COUNT(DISTINCT issue_code) AS so_van_de FROM {V} WHERE {JUL_WHERE}"
    assert scalar(db, sql)["so_van_de"] == service.overview(JUL)["total_issues"]["value"]


@pytest.mark.parametrize("unit", [NT, BH])
def test_unit_filtered_total_matches_overview(fixture_db, unit):
    db, service = fixture_db
    sql = f"SELECT COUNT(DISTINCT issue_code) AS so_van_de FROM {V} WHERE {AUG_WHERE} AND unit_name = '{unit}'"
    expected = service.overview(
        AnalyticsFilter(date_from="2026-08-01", date_to="2026-08-31", unit_name=unit)
    )
    assert scalar(db, sql)["so_van_de"] == expected["total_issues"]["value"]


@pytest.mark.parametrize(
    ("column", "panel", "filter_"),
    [
        ("unit_name", "units", AUG),
        ("source", "sources", AUG),
        ("product", "products", AUG),
        ("product", "products", JUL),
        ("source", "sources", JUL),
    ],
)
def test_grouped_counts_match_panels(fixture_db, column, panel, filter_):
    db, service = fixture_db
    where = AUG_WHERE if filter_ is AUG else JUL_WHERE
    sql = f"SELECT {column} AS nhan, COUNT(DISTINCT issue_code) AS so_van_de FROM {V} WHERE {where} GROUP BY {column}"
    assert by_label(db, sql) == items(getattr(service, panel)(filter_))


def test_daily_counts_match_daily_trend(fixture_db):
    db, service = fixture_db
    sql = f"SELECT issue_date AS nhan, COUNT(DISTINCT issue_code) AS so_van_de FROM {V} WHERE {AUG_WHERE} GROUP BY issue_date"
    expected = {
        i["date"]: i["issue_count"] for i in service.daily_trend(AUG)["items"] if i["issue_count"]
    }
    assert by_label(db, sql) == expected


def test_example_file_is_valid_jsonl():
    from dms.chat.ai.sql_example_retriever import EXAMPLES_PATH

    for line in EXAMPLES_PATH.read_text(encoding="utf-8").splitlines():
        json.loads(line)


def test_raw_key_catalog_reads_real_keys(fixture_db):
    from dms.chat.ai.metadata_adapter import AnalyticsMetadataAdapter
    from dms.chat.ai.query_normalizer import CachedMetadataProvider

    _, service = fixture_db
    keys = AnalyticsMetadataAdapter(service).raw_key_catalog()
    assert "Nội dung phản hồi" in keys
    assert CachedMetadataProvider(AnalyticsMetadataAdapter(service)).raw_key_catalog() == keys
