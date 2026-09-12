"""Priority must select bounded top-k without sorting the entire projection."""

from __future__ import annotations

import builtins

from analytics_support import seed_classified_records

from dms.analytics import AnalyticsFilter, FeedbackAnalyticsRepository, FeedbackAnalyticsService
from dms.analytics import service as service_module


def test_priority_does_not_sort_all_candidates(settings, monkeypatch):
    repo = FeedbackAnalyticsRepository(settings.classification_jobs_db_path)
    seed_classified_records(
        repo,
        db_path=repo.db_path,
        entries=[
            {
                "issue_code": f"A-{index}",
                "sentiment": "Tiêu cực" if index % 2 else "Tích cực",
                "issue_date": f"2026-08-{index % 28 + 1:02d}",
                "business_status": "Đã xử lý" if index % 3 else "Chờ xử lý",
            }
            for index in range(60)
        ],
    )
    service = FeedbackAnalyticsService(repo)
    expected = service.priority_issues(AnalyticsFilter(), limit=5)

    def bounded_sorted(values, *args, **kwargs):
        values = list(values)
        assert len(values) <= 5, "Full candidate sort instead of bounded top-k"
        return builtins.sorted(values, *args, **kwargs)

    monkeypatch.setattr(service_module, "sorted", bounded_sorted, raising=False)
    assert service.priority_issues(AnalyticsFilter(), limit=5) == expected
    assert service.priority_issues(AnalyticsFilter(), limit=0)["items"] == []
