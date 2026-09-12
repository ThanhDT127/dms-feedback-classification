"""SQL pagination regressions and differential checks against the Python contract."""

from __future__ import annotations

import json
import math
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from analytics_support import seed_classified_records

from dms.analytics import AnalyticsFilter, FeedbackAnalyticsRepository
from dms.analytics.service import FeedbackAnalyticsService


def issue_page(service, analytics_filter=None, **overrides):
    options = dict(
        page=1,
        page_size=2,
        source=None,
        unit_name=None,
        label=None,
        product=None,
        business_status=None,
    )
    options.update(overrides)
    return service.issues(analytics_filter or AnalyticsFilter(), **options)


def test_issues_reads_only_sql_page_and_page_labels(tmp_path, monkeypatch):
    repo = FeedbackAnalyticsRepository(tmp_path / "analytics.db")
    seed_classified_records(
        repo,
        db_path=repo.db_path,
        entries=[
            {"issue_date": None, "labels": ["Website"]},
            {"issue_date": "2026-08-02", "labels": ["Website", "Báo lỗi"]},
            {"issue_date": "2026-08-02", "labels": ["Báo lỗi"]},
            {"issue_date": "2026-08-01", "labels": []},
        ],
    )
    statements = []
    connect = repo._conn

    def traced_connection():
        conn = connect()
        conn.set_trace_callback(statements.append)
        return conn

    def forbid_full_projection():
        raise AssertionError("issues must not load the full analytics projection")

    monkeypatch.setattr(repo, "_conn", traced_connection)
    monkeypatch.setattr(repo, "fetch_analytics_rows", forbid_full_projection)
    result = issue_page(FeedbackAnalyticsService(repo), page=2)

    assert result["total"] == 4
    assert result["total_pages"] == 2
    assert [item["feedback_id"] for item in result["items"]] == [4, 1]
    assert [item["labels"] for item in result["items"]] == [[], ["Website"]]
    sql = [" ".join(statement.upper().split()) for statement in statements]
    assert any("COUNT(*)" in statement for statement in sql)
    assert any("LIMIT 2 OFFSET 2" in statement for statement in sql)
    label_reads = [statement for statement in sql if "FROM FEEDBACK_LABELS" in statement]
    assert len(label_reads) == 1
    assert "WHERE" in label_reads[0] and " IN (" in label_reads[0]


@pytest.fixture(scope="module")
def differential_repo(tmp_path_factory):
    repo = FeedbackAnalyticsRepository(tmp_path_factory.mktemp("sql-pages") / "analytics.db")
    seed_classified_records(
        repo,
        db_path=repo.db_path,
        entries=[
            {
                "issue_code": "same",
                "issue_date": "2026-08-02",
                "source": " ĐIỆN ",
                "unit_name": " Straße ",
                "product": " ĐÈN ",
                "business_status": " ĐÃ XỬ LÝ ",
                "labels": ["Website", "BÁO LỖI", "Straße"],
                "raw_data": {"Tỉnh/TP": " HÀ NỘI ", "Tinh/TP": "wrong", "Quận/huyện": " ĐỐNG ĐA "},
            },
            {
                "issue_code": "same",
                "issue_date": "2026-08-02",
                "source": "điện",
                "unit_name": "STRASSE",
                "product": "đèn",
                "business_status": "đã xử lý",
                "labels": ["Báo lỗi", " báo lỗi "],
                "raw_data": {" TỈNH/TP ": "Hà Nội", " QUẬN   HUYỆN ": "Đống Đa"},
            },
            {
                "issue_code": None,
                "issue_date": None,
                "source": None,
                "unit_name": None,
                "product": None,
                "labels": [],
                "raw_data": {"Tỉnh/TP": 0, "Quận/huyện": False},
            },
            {
                "issue_code": "",
                "issue_date": "",
                "source": "",
                "unit_name": " ",
                "product": "",
                "labels": [""],
                "raw_data": {" Tỉnh/TP ": 0, " Quận/huyện ": False},
            },
            {
                "issue_date": None,
                "source": " ",
                "labels": [" Báo lỗi "],
                "raw_data": {"Tỉnh/TP": " ", "Tinh/TP": "HÀ NỘI", "Quan huyen": "Đống Đa"},
            },
            {
                "issue_date": "2026-08-01",
                "labels": ["__unlabeled__"],
                "raw_data": {" Tỉnh/TP": "ignored", "Tỉnh/TP ": "Hà Nội", "Quan/huyen": "Đống Đa"},
            },
            {"issue_date": "2026-08-03", "labels": [], "source": "Điện"},
        ],
    )
    with repo._conn() as conn:
        conn.execute("UPDATE feedback_records SET is_active = 0 WHERE feedback_id = 7")
        conn.execute(
            "UPDATE feedback_records SET classification_state = 'pending' WHERE feedback_id = 3"
        )
        conn.execute(
            "UPDATE feedback_records SET classification_state = 'failed' WHERE feedback_id = 4"
        )
    return repo


def legacy_issues(repo, scope, **overrides):
    """Frozen pre-SQL algorithm, including its output fields and subtle label rules."""
    options = dict(
        page=1,
        page_size=2,
        source=None,
        unit_name=None,
        label=None,
        product=None,
        business_status=None,
    )
    options.update(overrides)
    service = FeedbackAnalyticsService(repo)
    rows = [dict(row) for row in repo.fetch_analytics_rows() if row["is_active"] == 1]
    if scope.date_from is not None or scope.date_to is not None:
        rows = [
            row
            for row in rows
            if row.get("issue_date")
            and (scope.date_from is None or row["issue_date"] >= scope.date_from)
            and (scope.date_to is None or row["issue_date"] <= scope.date_to)
        ]
    for expected, aliases in (
        (scope.province, ("Tỉnh/TP", "Tinh/TP", "Tỉnh thành", "Tinh thanh")),
        (scope.district, ("Quận/huyện", "Quan/huyen", "Quận huyện", "Quan huyen")),
    ):
        if expected:
            rows = [
                row for row in rows if service._matches(service._raw_value(row, *aliases), expected)
            ]
    if scope.unit_name:
        rows = [row for row in rows if service._matches(row.get("unit_name"), scope.unit_name)]
    rows = [
        row
        for row in rows
        if all(
            service._matches(row.get(field), options[field])
            for field in ("source", "unit_name", "product", "business_status")
        )
        and service._matches_label(row, options["label"])
    ]
    rows.sort(
        key=lambda row: (str(row.get("issue_date") or ""), int(row["feedback_id"])), reverse=True
    )
    total = len(rows)
    start = (options["page"] - 1) * options["page_size"]
    fields = (
        "feedback_id",
        "source_file_key",
        "source_file_name",
        "source_row_number",
        "issue_code",
        "issue_date",
        "source",
        "unit_name",
        "business_status",
        "content",
        "product",
        "product_line",
        "model",
        "sentiment",
        "brand",
        "bm25_score",
        "classification_state",
    )
    items = [
        {
            **{field: row[field] for field in fields},
            "job_id": row["last_job_id"],
            "labels": [item["label"] for item in row["labels"]],
            "raw_data": json.loads(row["raw_data_json"]),
        }
        for row in rows[start : start + options["page_size"]]
    ]
    return dict(
        items=items,
        total=total,
        page=options["page"],
        page_size=options["page_size"],
        total_pages=math.ceil(total / options["page_size"]) if total else 0,
    )


@pytest.mark.parametrize(
    "scope, options",
    [
        (AnalyticsFilter(), {}),
        (AnalyticsFilter(), {"page": 2}),
        (AnalyticsFilter(), {"page": 3}),
        (AnalyticsFilter(), {"page": 99}),
        (AnalyticsFilter(), {"page": 10**30}),
        (AnalyticsFilter(), {"source": " ĐIỆN "}),
        (AnalyticsFilter(), {"source": "dien"}),
        (AnalyticsFilter(), {"source": ""}),
        (AnalyticsFilter(), {"unit_name": "strasse"}),
        (AnalyticsFilter(), {"product": " ĐÈN "}),
        (AnalyticsFilter(), {"business_status": " ĐÃ XỬ LÝ "}),
        (AnalyticsFilter(), {"business_status": " "}),
        (AnalyticsFilter(), {"label": " báo lỗi "}),
        (AnalyticsFilter(), {"label": "STRASSE"}),
        (AnalyticsFilter(), {"label": " __UNLABELED__ "}),
        (AnalyticsFilter(), {"label": ""}),
        (AnalyticsFilter(), {"source": "' OR 1=1 --"}),
        (AnalyticsFilter(date_from="2026-08-02", date_to="2026-08-02"), {}),
        (AnalyticsFilter(date_from="2026-08-02"), {}),
        (AnalyticsFilter(date_to="2026-08-01"), {}),
        (AnalyticsFilter(date_from=""), {}),
        (AnalyticsFilter(province=" hà nội ", district="đống đa"), {}),
        (AnalyticsFilter(province="wrong"), {}),
        (AnalyticsFilter(province="0", district="false"), {}),
        (AnalyticsFilter(province=" ", district=" "), {}),
        (AnalyticsFilter(province="", district="", unit_name=""), {}),
        (AnalyticsFilter(unit_name="strasse"), {"unit_name": "North"}),
        (
            AnalyticsFilter(unit_name="strasse", province="hà nội", district="đống đa"),
            {"source": "điện", "product": "đèn", "label": "báo lỗi", "business_status": "đã xử lý"},
        ),
        (AnalyticsFilter(compare_from="1900-01-01", compare_to="1900-01-02"), {}),
    ],
)
def test_sql_page_matches_legacy_contract(differential_repo, scope, options):
    expected = legacy_issues(differential_repo, scope, **options)
    assert issue_page(FeedbackAnalyticsService(differential_repo), scope, **options) == expected


def test_page_order_uses_index_without_sorting_full_projection(differential_repo, monkeypatch):
    statements = []
    connect = differential_repo._conn

    def traced_connection():
        conn = connect()
        conn.set_trace_callback(statements.append)
        return conn

    monkeypatch.setattr(differential_repo, "_conn", traced_connection)
    issue_page(FeedbackAnalyticsService(differential_repo))
    page_query = next(sql for sql in statements if "LIMIT" in sql.upper())
    with connect() as conn:
        plan = [row[3] for row in conn.execute("EXPLAIN QUERY PLAN " + page_query)]
    assert not any("TEMP B-TREE" in step for step in plan), plan


def test_projection_cold_fill_is_singleflight(tmp_path, monkeypatch):
    repo = FeedbackAnalyticsRepository(tmp_path / "singleflight.db")
    seed_classified_records(repo, db_path=repo.db_path, entries=[{"labels": ["Báo lỗi"]}])
    # Rendezvous immediately after each thread's first critical section. The old
    # check/load gap deterministically admits every caller with a cold cache.
    barrier = threading.Barrier(4)
    underlying = threading.RLock()
    local = threading.local()

    class RendezvousLock:
        def __enter__(self):
            underlying.acquire()
            return self

        def __exit__(self, *exc):
            underlying.release()
            if not getattr(local, "exited", False):
                local.exited = True
                barrier.wait(timeout=10)

    reads = []
    connect = repo._conn

    def traced_connection():
        conn = connect()
        conn.set_trace_callback(reads.append)
        return conn

    monkeypatch.setattr(repo, "_lock", RendezvousLock())
    monkeypatch.setattr(repo, "_conn", traced_connection)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: repo.fetch_analytics_rows(), range(4)))
    assert sum("SELECT * FROM feedback_records" in sql for sql in reads) == 1
    assert all(result is results[0] for result in results)
