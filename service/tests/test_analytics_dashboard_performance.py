"""Regression budgets for the grouped analytics response (not production timings)."""

from __future__ import annotations

import sqlite3

from analytics_support import seed_classified_records
from conftest import apply_auth_overrides
from fastapi.testclient import TestClient

from dms.analytics import AnalyticsFilter, FeedbackAnalyticsRepository, FeedbackAnalyticsService
from dms.web import deps
from dms.web.app import create_app

PANELS = {
    "overview": "overview",
    "dailyTrend": "daily_trend",
    "issueTypes": "issue_types",
    "geography": "geography",
    "sources": "sources",
    "units": "units",
    "groups": "groups",
    "products": "products",
    "status": "status_backlog",
}


def test_dashboard_matches_individual_panels_with_one_projection_read(settings, monkeypatch):
    repo = FeedbackAnalyticsRepository(settings.classification_jobs_db_path)
    seed_classified_records(
        repo,
        db_path=repo.db_path,
        entries=[
            {
                "issue_code": "A",
                "labels": ["Báo lỗi"],
                "product": "LED",
                "sentiment": "Tiêu cực",
                "raw_data": {"Loại vấn đề": "Báo lỗi", "Quận/huyện": "Hoàng Mai"},
            },
            {"issue_code": "B", "labels": ["Website"], "unit_name": "South"},
        ],
    )
    scope = AnalyticsFilter(district="Hoàng Mai")
    service = FeedbackAnalyticsService(repo)
    expected = {key: getattr(service, name)(scope) for key, name in PANELS.items()}
    reads = 0
    original = repo.fetch_analytics_rows

    def counted():
        nonlocal reads
        reads += 1
        return original()

    monkeypatch.setattr(repo, "fetch_analytics_rows", counted)
    assert service.dashboard(scope) == expected
    assert reads == 1
    # A second scope must never reuse the filtered rows from the first request.
    assert service.dashboard(AnalyticsFilter())["overview"]["total_issues"]["value"] == 2


def test_dashboard_route_auth_and_validation(settings, monkeypatch):
    assert TestClient(create_app()).get("/api/analytics/dashboard").status_code == 401
    repo = FeedbackAnalyticsRepository(settings.classification_jobs_db_path)
    monkeypatch.setattr(deps, "get_feedback_analytics_repository", lambda: repo)
    app = create_app()
    apply_auth_overrides(app)
    client = TestClient(app)
    result = client.get("/api/analytics/dashboard")
    assert result.status_code == 200
    assert set(result.json()) == set(PANELS)
    assert client.get("/api/analytics/dashboard?from=2026-08-01").status_code == 422


def test_dashboard_shared_cache_invalidates_other_repository_after_write(settings, monkeypatch):
    first = FeedbackAnalyticsRepository(settings.classification_jobs_db_path)
    seed_classified_records(first, db_path=first.db_path, entries=[{"issue_code": "A"}])
    second = FeedbackAnalyticsRepository(first.db_path)
    active = [first]
    monkeypatch.setattr(deps, "get_feedback_analytics_repository", lambda: active[0])
    app = create_app()
    apply_auth_overrides(app)
    client = TestClient(app)
    expected = client.get("/api/analytics/dashboard").json()
    active[0] = second
    reads = 0
    original = second.fetch_analytics_rows

    def counted():
        nonlocal reads
        reads += 1
        return original()

    monkeypatch.setattr(second, "fetch_analytics_rows", counted)
    assert client.get("/api/analytics/dashboard").json() == expected
    assert reads == 0, "A different worker must reuse the aggregate, not hydrate the projection"
    with sqlite3.connect(first.db_path) as conn:
        conn.execute("UPDATE feedback_records SET is_active = 0")
    active[0] = first  # this worker still has the OLD full-projection cache
    assert client.get("/api/analytics/dashboard").json()["overview"]["total_issues"]["value"] == 0


def test_heavy_aggregates_are_shared_across_workers(settings, monkeypatch):
    first = FeedbackAnalyticsRepository(settings.classification_jobs_db_path)
    seed_classified_records(
        first,
        db_path=first.db_path,
        entries=[
            {"issue_code": "A", "sentiment": "Tiêu cực", "raw_data": {"Loại vấn đề": "Báo lỗi"}},
            {"issue_code": "B", "sentiment": "Tích cực", "raw_data": {"Loại vấn đề": "Website"}},
        ],
    )
    second = FeedbackAnalyticsRepository(first.db_path)
    active = [first]
    monkeypatch.setattr(deps, "get_feedback_analytics_repository", lambda: active[0])
    app = create_app()
    apply_auth_overrides(app)
    client = TestClient(app)
    expected_matrix = client.get("/api/analytics/unit-issue-type-matrix").json()
    expected_priority = client.get("/api/analytics/priority-issues?limit=1").json()
    active[0] = second
    monkeypatch.setattr(
        second,
        "fetch_analytics_rows",
        lambda: (_ for _ in ()).throw(AssertionError("aggregate cache miss in another worker")),
    )
    assert client.get("/api/analytics/unit-issue-type-matrix").json() == expected_matrix
    assert client.get("/api/analytics/priority-issues?limit=1").json() == expected_priority
