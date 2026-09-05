from __future__ import annotations

import pytest
from analytics_support import seed_classified_records
from conftest import apply_auth_overrides
from fastapi.testclient import TestClient

from dms.analytics import FeedbackAnalyticsRepository
from dms.web import deps
from dms.web.app import create_app


@pytest.fixture
def analytics_api(settings, monkeypatch):
    repository = FeedbackAnalyticsRepository(settings.classification_jobs_db_path)
    monkeypatch.setattr(deps, "get_feedback_analytics_repository", lambda: repository)
    app = create_app()
    apply_auth_overrides(app)
    return TestClient(app), repository


@pytest.mark.parametrize(
    "path",
    [
        "/api/analytics/overview",
        "/api/analytics/sources",
        "/api/analytics/units",
        "/api/analytics/groups",
        "/api/analytics/products",
        "/api/analytics/issues",
        "/api/analytics/data-quality",
        "/api/analytics/trends/daily",
        "/api/analytics/issue-types",
        "/api/analytics/duplicates",
        "/api/analytics/unit-issue-type-matrix",
        "/api/analytics/geography",
        "/api/analytics/status-backlog",
    ],
)
def test_analytics_routes_require_authentication(path):
    response = TestClient(create_app()).get(path)

    assert response.status_code == 401


def test_analytics_overview_returns_metric_contract(analytics_api):
    client, _ = analytics_api

    response = client.get("/api/analytics/overview?from=2026-08-01&to=2026-08-31")

    assert response.status_code == 200
    assert set(response.json()["total_issues"]) >= {
        "available",
        "value",
        "denominator",
        "excluded_missing_issue_code",
        "reason",
    }


@pytest.mark.parametrize(
    "query",
    [
        "from=2026-99-01&to=2026-08-31",
        "from=20260801&to=20260831",
        "from=2026-W01-1&to=2026-W01-2",
        "from=2026-08-01",
        "to=2026-08-31",
        "from=2026-08-31&to=2026-08-01",
        "compare_from=2026-08-01",
        "compare_to=2026-08-31",
        "compare_from=2026-08-31&compare_to=2026-08-01",
    ],
)
def test_analytics_rejects_invalid_partial_or_reversed_date_ranges(analytics_api, query):
    client, _ = analytics_api

    response = client.get(f"/api/analytics/overview?{query}")

    assert response.status_code == 422
    assert response.json()["detail"] == "Invalid date range"


@pytest.mark.parametrize(
    "query",
    ["page=0", "page_size=0", "page_size=201"],
)
def test_analytics_issues_rejects_invalid_pagination(analytics_api, query):
    client, _ = analytics_api

    response = client.get(f"/api/analytics/issues?{query}")

    assert response.status_code == 422


def test_analytics_empty_state_and_detail_filter_aliases(analytics_api):
    client, repository = analytics_api
    seed_classified_records(
        repository,
        db_path=repository.db_path,
        entries=[
            {
                "issue_code": "A",
                "issue_date": "2026-08-15",
                "source": "CRM",
                "unit_name": "North",
                "business_status": "Đã xử lý",
                "labels": ["Báo lỗi"],
                "product": "LED",
            },
            {
                "issue_code": "B",
                "issue_date": "2026-08-16",
                "source": "Email",
                "unit_name": "South",
                "business_status": "Mới",
                "labels": ["Website"],
                "product": "Portal",
            },
        ],
    )

    response = client.get(
        "/api/analytics/issues"
        "?from=2026-08-01&to=2026-08-31"
        "&source=CRM&unit=North&label=Báo%20lỗi&product=LED"
        "&status=Đã%20xử%20lý&page=1&page_size=25"
    )

    assert response.status_code == 200
    assert response.json()["page_size"] == 25
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["issue_code"] == "A"


def test_analytics_empty_repository_returns_stable_payloads(settings, monkeypatch):
    repository = FeedbackAnalyticsRepository(settings.classification_jobs_db_path)
    monkeypatch.setattr(deps, "get_feedback_analytics_repository", lambda: repository)
    app = create_app()
    apply_auth_overrides(app)
    client = TestClient(app)

    assert client.get("/api/analytics/data-quality").json()["total_records"] == 0
    issues = client.get("/api/analytics/issues?page=1&page_size=25")
    assert issues.status_code == 200
    assert issues.json() == {
        "items": [],
        "total": 0,
        "page": 1,
        "page_size": 25,
        "total_pages": 0,
    }


def test_analytics_daily_trend_route_returns_stable_contract(analytics_api):
    client, repository = analytics_api
    seed_classified_records(
        repository,
        db_path=repository.db_path,
        entries=[{"issue_code": "A", "issue_date": "2026-08-15", "labels": []}],
    )

    response = client.get("/api/analytics/trends/daily?from=2026-08-01&to=2026-08-31")

    assert response.status_code == 200
    assert response.json()["items"] == [{"date": "2026-08-15", "issue_count": 1}]


def test_analytics_issue_types_route_returns_stable_contract(analytics_api):
    client, repository = analytics_api
    seed_classified_records(
        repository,
        db_path=repository.db_path,
        entries=[{"issue_code": "A", "raw_data": {"Loại vấn đề": "Vấn đề khác"}}],
    )

    response = client.get("/api/analytics/issue-types")

    assert response.status_code == 200
    assert response.json()["items"][0]["label"] == "Vấn đề khác"


def test_analytics_duplicate_details_route_validates_pagination(analytics_api):
    client, repository = analytics_api
    seed_classified_records(
        repository,
        db_path=repository.db_path,
        entries=[
            {"issue_code": "A", "content": "Đèn lỗi"},
            {"issue_code": "B", "content": " đèn  lỗi "},
        ],
    )

    response = client.get("/api/analytics/duplicates?page=1&page_size=10")

    assert response.status_code == 200
    assert response.json()["items"][0]["duplicate_rows"] == 1
    assert client.get("/api/analytics/duplicates?page=0").status_code == 422


def test_analytics_unit_issue_type_matrix_route(analytics_api):
    client, repository = analytics_api
    seed_classified_records(
        repository,
        db_path=repository.db_path,
        entries=[{"issue_code": "A", "unit_name": "North", "raw_data": {"Loại vấn đề": "Báo lỗi"}}],
    )

    response = client.get("/api/analytics/unit-issue-type-matrix")

    assert response.status_code == 200
    assert response.json()["rows"][0]["counts"] == {"Báo lỗi": 1}


def test_analytics_geography_route_and_filters(analytics_api):
    client, repository = analytics_api
    seed_classified_records(
        repository,
        db_path=repository.db_path,
        entries=[{"issue_code": "A", "raw_data": {"Tỉnh/TP": "Hà Nội", "Quận/huyện": "Hoàng Mai"}}],
    )

    response = client.get("/api/analytics/geography?province=Hà%20Nội&district=Hoàng%20Mai")

    assert response.status_code == 200
    assert response.json()["provinces"][0]["label"] == "Hà Nội"
    assert (
        client.get("/api/analytics/overview?province=Hà%20Nội").json()["total_issues"]["value"] == 1
    )


def test_analytics_status_backlog_route(analytics_api):
    client, repository = analytics_api
    seed_classified_records(
        repository,
        db_path=repository.db_path,
        entries=[{"issue_code": "A", "issue_date": "2026-08-31", "business_status": "Chờ xử lý"}],
    )

    response = client.get("/api/analytics/status-backlog")

    assert response.status_code == 200
    assert response.json()["backlog_count"] == 1
