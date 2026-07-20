"""Unit tests for ClassificationJobStore.daily_stats() method.

Tests that daily_stats() aggregates both Watcher and Web Upload jobs
correctly grouped by date (task 6.3).
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest


@pytest.fixture
def job_store(tmp_path):
    from dms.classification_jobs import ClassificationJobStore

    return ClassificationJobStore(tmp_path / "jobs.db")


def _create_completed_job(job_store, *, filename: str, completed_at: str, total_rows: int = 10, owner: str = "system_watcher"):
    """Helper to insert a completed job with a specific completed_at date."""
    job_id = str(uuid.uuid4())
    job_store.create_job(
        job_id=job_id,
        owner_username=owner,
        owner_role="system" if owner == "system_watcher" else "user",
        filename=filename,
        mode="watcher" if owner == "system_watcher" else "single",
        input_path=f"/input/{filename}",
        output_path=f"/output/{filename}",
    )
    # Directly update completed_at to historical date
    with job_store._lock, job_store._conn() as conn:
        conn.execute(
            """UPDATE classification_jobs
               SET status = 'completed', total_rows = ?, rows_done = ?, percent = 100,
                   completed_at = ?, updated_at = ?
               WHERE job_id = ?""",
            (total_rows, total_rows, completed_at, completed_at, job_id),
        )
        conn.commit()
    return job_id


def _create_failed_job(job_store, *, filename: str, completed_at: str, owner: str = "system_watcher"):
    """Helper to insert a failed job with a specific completed_at date."""
    job_id = str(uuid.uuid4())
    job_store.create_job(
        job_id=job_id,
        owner_username=owner,
        owner_role="system" if owner == "system_watcher" else "user",
        filename=filename,
        mode="watcher" if owner == "system_watcher" else "single",
        input_path=f"/input/{filename}",
        output_path=f"/output/{filename}",
    )
    with job_store._lock, job_store._conn() as conn:
        conn.execute(
            """UPDATE classification_jobs
               SET status = 'error', error = 'Test error',
                   completed_at = ?, updated_at = ?
               WHERE job_id = ?""",
            (completed_at, completed_at, job_id),
        )
        conn.commit()
    return job_id


def test_daily_stats_empty_db(job_store):
    """Empty DB returns empty arrays."""
    result = job_store.daily_stats()
    assert result == {"dates": [], "success_counts": [], "failed_counts": [], "counts": []}


def test_daily_stats_single_date(job_store):
    """Single date with 3 completed and 1 failed."""
    _create_completed_job(job_store, filename="a.xlsx", completed_at="2026-06-10T08:00:00Z")
    _create_completed_job(job_store, filename="b.xlsx", completed_at="2026-06-10T09:00:00Z")
    _create_completed_job(job_store, filename="c.xlsx", completed_at="2026-06-10T10:00:00Z")
    _create_failed_job(job_store, filename="d.xlsx", completed_at="2026-06-10T11:00:00Z")

    result = job_store.daily_stats()

    assert result["dates"] == ["2026-06-10"]
    assert result["success_counts"] == [3]
    assert result["failed_counts"] == [1]
    assert result["counts"] == [4]


def test_daily_stats_multiple_dates(job_store):
    """Multiple dates are correctly grouped and sorted."""
    _create_completed_job(job_store, filename="a.xlsx", completed_at="2026-05-01T08:00:00Z")
    _create_completed_job(job_store, filename="b.xlsx", completed_at="2026-05-01T09:00:00Z")
    _create_failed_job(job_store, filename="c.xlsx", completed_at="2026-05-02T10:00:00Z")
    _create_completed_job(job_store, filename="d.xlsx", completed_at="2026-05-03T08:00:00Z")

    result = job_store.daily_stats()

    assert result["dates"] == ["2026-05-01", "2026-05-02", "2026-05-03"]
    assert result["success_counts"] == [2, 0, 1]
    assert result["failed_counts"] == [0, 1, 0]
    assert result["counts"] == [2, 1, 1]


def test_daily_stats_combines_watcher_and_web(job_store):
    """Jobs from both system_watcher and regular users are combined."""
    _create_completed_job(
        job_store, filename="watcher.xlsx", completed_at="2026-06-15T08:00:00Z",
        owner="system_watcher"
    )
    _create_completed_job(
        job_store, filename="web_upload.xlsx", completed_at="2026-06-15T10:00:00Z",
        owner="alice"
    )
    _create_failed_job(
        job_store, filename="web_failed.xlsx", completed_at="2026-06-15T11:00:00Z",
        owner="bob"
    )

    result = job_store.daily_stats()

    assert result["dates"] == ["2026-06-15"]
    assert result["success_counts"] == [2]  # 1 watcher + 1 web
    assert result["failed_counts"] == [1]   # 1 web failed
    assert result["counts"] == [3]


def test_daily_stats_date_range_filter(job_store):
    """from_date/to_date filters work correctly."""
    _create_completed_job(job_store, filename="old.xlsx", completed_at="2026-04-01T08:00:00Z")
    _create_completed_job(job_store, filename="in_range.xlsx", completed_at="2026-06-10T08:00:00Z")
    _create_completed_job(job_store, filename="new.xlsx", completed_at="2026-07-01T08:00:00Z")

    result = job_store.daily_stats(from_date="2026-06-01", to_date="2026-06-30")

    assert result["dates"] == ["2026-06-10"]
    assert result["success_counts"] == [1]


def test_daily_stats_queued_jobs_excluded(job_store):
    """Jobs still in queued/running state should NOT appear in daily stats (no completed_at)."""
    job_id = str(uuid.uuid4())
    job_store.create_job(
        job_id=job_id,
        owner_username="system_watcher",
        owner_role="system",
        filename="pending.xlsx",
        mode="watcher",
        input_path="/input/pending.xlsx",
        output_path="/output/pending.xlsx",
    )
    # completed_at is NULL for queued jobs

    result = job_store.daily_stats()
    assert result["dates"] == []  # Queued job excluded
