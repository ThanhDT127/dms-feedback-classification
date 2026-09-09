from __future__ import annotations

import pytest
from analytics_support import seed_classified_records
from conftest import TEST_USER, apply_auth_overrides
from fastapi.testclient import TestClient

from dms.analytics import FeedbackAnalyticsRepository
from dms.web import deps
from dms.web.app import create_app


@pytest.fixture
def analytics_api(settings, monkeypatch):
    repository = FeedbackAnalyticsRepository(settings.classification_jobs_db_path)
    monkeypatch.setattr(deps, "get_feedback_analytics_repository", lambda: repository)
    app = create_app()
    apply_auth_overrides(app, user=TEST_USER)
    return TestClient(app), repository


def test_issue_options_cascade_over_full_scoped_records(analytics_api):
    client, repository = analytics_api
    entries = [
        {"unit_name": "North", "labels": ["Báo lỗi"], "product": "LED", "business_status": "Mới"},
        {
            "unit_name": "North",
            "labels": ["Báo lỗi"],
            "product": "LED",
            "business_status": "Đã xử lý",
        },
        {"unit_name": "North", "labels": ["Báo lỗi"], "product": "Panel", "business_status": "Chờ"},
        {
            "unit_name": "North",
            "labels": ["Website"],
            "product": "Portal",
            "business_status": "Chờ",
        },
        {
            "unit_name": "South",
            "labels": ["Bảo hành"],
            "product": "Other",
            "business_status": "Khác",
        },
    ]
    seed_classified_records(
        repository,
        db_path=repository.db_path,
        entries=[
            {
                "issue_code": str(index),
                "issue_date": "2026-08-15",
                "raw_data": {"Quận/huyện": "D1"},
                **entry,
            }
            for index, entry in enumerate(entries)
        ]
        + [
            {
                "issue_code": "old",
                "issue_date": "2026-07-01",
                "unit_name": "Old",
                "labels": ["Old"],
                "raw_data": {"Quận/huyện": "D1"},
            },
            {
                "issue_code": "away",
                "unit_name": "Away",
                "labels": ["Away"],
                "raw_data": {"Quận/huyện": "D2"},
            },
        ],
    )
    scope = {"from": "2026-08-01", "to": "2026-08-31", "district": " d1 "}
    response = client.get(
        "/api/analytics/issue-filter-options",
        params={
            **scope,
            "issue_unit": " north ",
            "label": " báo LỖI ",
            "product": " led ",
            "status": "Mới",
        },
    )
    assert response.status_code == 200
    assert response.json() == {
        "units": ["North", "South"],
        "labels": ["Báo lỗi", "Website"],
        "products": ["LED", "Panel"],
        "statuses": ["Đã xử lý", "Mới"],
    }
    # Downstream selections never remove upstream alternatives; global unit wins.
    global_unit = client.get(
        "/api/analytics/issue-filter-options",
        params={
            **scope,
            "unit": "South",
            "issue_unit": "North",
            "label": "Báo lỗi",
            "product": "LED",
        },
    )
    assert global_unit.json() == {
        "units": ["South"],
        "labels": ["Bảo hành"],
        "products": [],
        "statuses": [],
    }
    details = client.get(
        "/api/analytics/issues",
        params={
            **scope,
            "unit": "North",
            "label": " báo LỖI ",
            "product": " led ",
            "page_size": 1,
        },
    ).json()
    assert details["total"] == 2
    assert len(details["items"]) == 1


def test_unlabeled_option_matches_detail_rows_including_missing_issue_code(analytics_api):
    client, repository = analytics_api
    seed_classified_records(
        repository,
        db_path=repository.db_path,
        entries=[
            {
                "issue_code": None,
                "labels": [],
                "product": "Unlabeled product",
                "business_status": "Mới",
            },
            {"issue_code": "A", "labels": ["Website"], "product": "Portal"},
        ],
    )
    options = client.get("/api/analytics/issue-filter-options", params={"label": "__unlabeled__"})
    assert options.status_code == 200
    assert "__unlabeled__" in options.json()["labels"]
    assert options.json()["products"] == ["Unlabeled product"]
    assert options.json()["statuses"] == ["Mới"]
    details = client.get("/api/analytics/issues", params={"label": "__unlabeled__"}).json()
    assert details["total"] == 1
    assert details["items"][0]["issue_code"] is None
    assert details["items"][0]["labels"] == []


def test_comparison_reuses_overview_metrics_and_scope(analytics_api):
    client, repository = analytics_api
    seed_classified_records(
        repository,
        db_path=repository.db_path,
        entries=[
            {
                "issue_code": "A",
                "issue_date": "2026-08-01",
                "product": "LED",
                "sentiment": "Tiêu cực",
                "content": "Duplicate",
            },
            {
                "issue_code": "A",
                "issue_date": "2026-08-02",
                "product": "LED",
                "content": "Duplicate",
            },
            {"issue_code": "B", "issue_date": "2026-08-31", "content": "Unique"},
            {"issue_code": None, "issue_date": "2026-08-15", "content": "Missing code"},
            {
                "issue_code": "P",
                "issue_date": "2026-07-01",
                "sentiment": "Tích cực",
                "content": "Prior",
            },
            {"issue_code": "excluded-unit", "issue_date": "2026-08-15", "unit_name": "South"},
            {"issue_code": "excluded-date", "issue_date": "2026-06-30"},
            {"issue_code": "excluded-missing-date", "issue_date": None},
        ],
    )
    response = client.get(
        "/api/analytics/comparison",
        params={
            "from": "2026-08-01",
            "to": "2026-08-31",
            "unit": " north ",
            "period": "month",
            # Explicit old-style comparison must not affect this new comparison.
            "compare_from": "2026-06-01",
            "compare_to": "2026-06-30",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["period"] == "month"
    assert body["current_range"] == {"from": "2026-08-01", "to": "2026-08-31"}
    assert body["previous_range"] == {"from": "2026-07-01", "to": "2026-07-31"}
    metrics = body["metrics"]
    assert set(metrics) == {
        "total_issues",
        "sentiment_coverage",
        "product_coverage",
        "duplicate_issue_rate",
        "model_accuracy",
    }
    assert {
        key: metrics["total_issues"][key]
        for key in ("current", "previous", "change", "change_percent", "unit", "available")
    } == {
        "current": 2,
        "previous": 1,
        "change": 1,
        "change_percent": 100.0,
        "unit": "count",
        "available": True,
    }
    assert metrics["sentiment_coverage"]["current"] == 50.0
    assert metrics["sentiment_coverage"]["previous"] == 100.0
    assert metrics["sentiment_coverage"]["change"] == -50.0
    assert metrics["sentiment_coverage"]["change_percent"] == -50.0
    assert metrics["sentiment_coverage"]["unit"] == "percentage_points"
    assert metrics["product_coverage"]["change"] == 50.0
    assert metrics["product_coverage"]["change_percent"] is None
    assert metrics["product_coverage"]["available"] is True
    assert metrics["duplicate_issue_rate"]["current"] == 50.0
    assert metrics["duplicate_issue_rate"]["previous"] == 0.0
    assert metrics["model_accuracy"]["available"] is False
    for field in ("current", "previous", "change", "change_percent"):
        assert metrics["model_accuracy"][field] is None
    for range_key, value_key in (("current_range", "current"), ("previous_range", "previous")):
        overview = client.get(
            "/api/analytics/overview", params={**body[range_key], "unit": "North"}
        ).json()
        for key, metric in metrics.items():
            assert metric[value_key] == overview[key]["value"]


@pytest.mark.parametrize(
    "period,start,end,previous_start,previous_end",
    [
        ("month", "2024-03-01", "2024-03-31", "2024-02-01", "2024-02-29"),
        ("month", "2025-03-30", "2025-03-31", "2025-02-28", "2025-02-28"),
        ("month", "2026-01-01", "2026-01-31", "2025-12-01", "2025-12-31"),
        ("quarter", "2026-05-31", "2026-08-31", "2026-02-28", "2026-05-31"),
        ("quarter", "2024-01-31", "2024-03-31", "2023-10-31", "2023-12-31"),
        ("year", "2024-02-29", "2024-03-31", "2023-02-28", "2023-03-31"),
        ("year", "2025-02-28", "2025-02-28", "2024-02-28", "2024-02-28"),
    ],
)
def test_comparison_shifts_each_boundary_by_calendar_months(
    analytics_api, period, start, end, previous_start, previous_end
):
    client, _ = analytics_api
    response = client.get(
        "/api/analytics/comparison", params={"from": start, "to": end, "period": period}
    )
    assert response.status_code == 200
    assert response.json()["current_range"] == {"from": start, "to": end}
    assert response.json()["previous_range"] == {"from": previous_start, "to": previous_end}


@pytest.mark.parametrize(
    "query",
    [
        "",
        "period=month",
        "from=2026-08-01",
        "to=2026-08-31",
        "from=2026-08-31&to=2026-08-01",
        "from=2026-02-30&to=2026-03-01",
        "from=20260801&to=20260831",
        "from=2026-08-01&to=2026-08-31&period=week",
        "from=2026-08-01&to=2026-08-31&period=",
        "from=0001-01-01&to=0001-01-31&period=month",
        "from=0001-03-01&to=0001-04-30&period=quarter",
        "from=0001-12-31&to=0002-01-31&period=year",
    ],
)
def test_comparison_rejects_incomplete_invalid_or_underflow_ranges(analytics_api, query):
    client, _ = analytics_api
    assert client.get(f"/api/analytics/comparison?{query}").status_code == 422


@pytest.mark.parametrize(
    "path",
    [
        "/api/analytics/issue-filter-options",
        "/api/analytics/comparison?from=2026-08-01&to=2026-08-31&period=month",
    ],
)
def test_new_endpoints_require_authentication(path):
    assert TestClient(create_app()).get(path).status_code == 401


def test_issue_options_validate_global_date_range(analytics_api):
    client, _ = analytics_api
    assert client.get("/api/analytics/issue-filter-options?from=2026-08-01").status_code == 422


def test_empty_scope_keeps_metrics_unavailable_and_options_empty(analytics_api):
    client, repository = analytics_api
    seed_classified_records(
        repository,
        db_path=repository.db_path,
        entries=[
            {"issue_code": "A", "issue_date": "2026-08-01"},
        ],
    )
    response = client.get("/api/analytics/comparison?from=2026-08-01&to=2026-08-31")
    assert response.status_code == 200
    assert response.json()["period"] == "month"
    for metric in response.json()["metrics"].values():
        assert metric["available"] is False
        assert metric["change"] is None
        assert metric["change_percent"] is None
        assert metric["reason"]
    assert response.json()["metrics"]["total_issues"]["previous"] == 0
    assert client.get("/api/analytics/issue-filter-options?unit=Absent").json() == {
        "units": [],
        "labels": [],
        "products": [],
        "statuses": [],
    }


def test_options_use_all_records_beyond_maximum_issue_page(analytics_api):
    client, repository = analytics_api
    seed_classified_records(
        repository,
        db_path=repository.db_path,
        entries=[
            {
                "issue_code": str(index),
                "labels": [f"Label {index:03d}"],
                "product": f"Product {index:03d}",
            }
            for index in range(205)
        ],
    )
    page = client.get("/api/analytics/issues?page_size=200").json()
    options = client.get("/api/analytics/issue-filter-options").json()
    assert page["total"] == 205
    assert len(page["items"]) == 200
    assert options["labels"] == [f"Label {index:03d}" for index in range(205)]
    assert options["products"] == [f"Product {index:03d}" for index in range(205)]


def test_comparison_preserves_district_province_scope_and_legacy_comparison(analytics_api):
    client, repository = analytics_api
    seed_classified_records(
        repository,
        db_path=repository.db_path,
        entries=[
            {
                "issue_code": f"{month}-{index}",
                "issue_date": f"2026-{month}-15",
                "raw_data": geography,
            }
            for month in ("07", "08")
            for index, geography in enumerate(
                [
                    {"Tỉnh/TP": "P1", "Quận/huyện": "D1"},
                    {"Tỉnh/TP": "P1", "Quận/huyện": "D2"},
                    {"Tỉnh/TP": "P2", "Quận/huyện": "D1"},
                ]
            )
        ],
    )
    scope = {
        "from": "2026-08-01",
        "to": "2026-08-31",
        "unit": "North",
        "province": " p1 ",
        "district": "d1",
    }
    metrics = client.get("/api/analytics/comparison", params=scope).json()["metrics"]
    assert metrics["total_issues"]["current"] == 1
    assert metrics["total_issues"]["previous"] == 1
    assert metrics["total_issues"]["change"] == 0
    legacy = client.get(
        "/api/analytics/overview",
        params={
            **scope,
            "compare_from": "2026-07-01",
            "compare_to": "2026-07-31",
        },
    ).json()["total_issues"]["comparison"]
    assert legacy == {
        "available": True,
        "value": 1,
        "change_percent": 0.0,
        "direction": "unchanged",
        "reason": None,
    }


def test_inactive_and_superseded_values_never_leak_into_options_or_comparison(analytics_api):
    client, repository = analytics_api
    seed_classified_records(
        repository,
        db_path=repository.db_path,
        entries=[
            {
                "issue_code": "A",
                "issue_date": "2026-08-01",
                "labels": ["Old label"],
                "product": "Old product",
            },
            {
                "issue_code": "B",
                "issue_date": "2026-07-01",
                "unit_name": "Inactive",
                "labels": ["Inactive"],
            },
        ],
    )
    # Exercise actual repository projection/versioning, not fabricated analytics rows.
    from analytics_support import create_job, make_record, make_result

    from dms.classification_jobs import ClassificationJobStore

    jobs = ClassificationJobStore(repository.db_path)
    create_job(jobs, "replace")
    repository.persist_input_snapshot(
        job_id="replace",
        source_file_key="seed-0",
        source_file_name="seed-0.xlsx",
        records=[make_record(issue_code="A", content="Updated content")],
        deactivate_absent=True,
    )
    repository.apply_batch_results(
        job_id="replace",
        results=[make_result(labels=["Current"], product="Current product")],
        minor_to_major={},
    )
    create_job(jobs, "deactivate")
    repository.persist_input_snapshot(
        job_id="deactivate",
        source_file_key="seed-1",
        source_file_name="seed-1.xlsx",
        records=[],
        deactivate_absent=True,
    )
    options = client.get("/api/analytics/issue-filter-options").json()
    assert options == {
        "units": ["North"],
        "labels": ["Current"],
        "products": ["Current product"],
        "statuses": [],
    }
    metrics = client.get("/api/analytics/comparison?from=2026-08-01&to=2026-08-31").json()[
        "metrics"
    ]
    assert metrics["total_issues"]["current"] == 1
    assert metrics["total_issues"]["previous"] == 0
    assert metrics["total_issues"]["available"] is False
