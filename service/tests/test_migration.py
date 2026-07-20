"""Unit tests for one-time migration of seen_files.json into SQLite.

Tests idempotency and correct mapping of done/failed entries (task 2.2).
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def tmp_seen_files(tmp_path):
    """Write a seen_files.json with mix of done/failed/retry entries."""
    seen = {
        "sp-file-id-001": {
            "name": "file_done_1.xlsx",
            "status": "done",
            "lastModifiedDateTime": "2026-05-10T08:00:00Z",
            "processed_at": "2026-05-10T08:05:00Z",
            "total_rows": 50,
            "duration_seconds": 3.5,
            "label_distribution": {"Sản phẩm": 40, "Giá/cơ chế": 10},
        },
        "sp-file-id-002": {
            "name": "file_done_2.xlsx",
            "status": "done",
            "lastModifiedDateTime": "2026-05-15T09:00:00Z",
            "processed_at": "2026-05-15T09:10:00Z",
            "total_rows": 30,
            "duration_seconds": 2.0,
            "label_distribution": {"Dịch vụ": 30},
        },
        "sp-file-id-003": {
            "name": "file_failed.xlsx",
            "status": "failed",
            "lastModifiedDateTime": "2026-05-20T10:00:00Z",
            "last_attempt": "2026-05-20T10:05:00Z",
            "failures": 3,
            "last_error": "GeminiError: timeout",
        },
        "sp-file-id-004": {
            "name": "file_retry.xlsx",
            "status": "retry",
            "lastModifiedDateTime": "2026-06-01T10:00:00Z",
            "last_attempt": "2026-06-01T10:02:00Z",
            "failures": 1,
            "last_error": "NetworkError: connection refused",
        },
    }
    seen_path = tmp_path / "seen_files.json"
    seen_path.write_text(json.dumps(seen), encoding="utf-8")
    return seen_path, seen


@pytest.fixture
def job_store(tmp_path):
    from dms.classification_jobs import ClassificationJobStore

    db_path = tmp_path / "classification_jobs.db"
    return ClassificationJobStore(db_path)


@pytest.fixture
def watcher_with_store(tmp_path, job_store, tmp_seen_files):
    """Watcher with real job_store and seen_files.json."""
    from dms.metrics import MetricsCollector
    from dms.watcher import Watcher

    seen_path, _ = tmp_seen_files

    settings = MagicMock()
    settings.work_dir = tmp_path
    settings.seen_files_path = seen_path
    settings.metrics_path = tmp_path / "metrics.json"

    (tmp_path / "input").mkdir(parents=True, exist_ok=True)

    metrics = MagicMock(spec=MetricsCollector)

    return Watcher(
        sharepoint_client=MagicMock(),
        pipeline_runner=MagicMock(),
        notification_service=MagicMock(),
        metrics=metrics,
        settings=settings,
        job_store=job_store,
    )


def test_migration_creates_correct_records(watcher_with_store, tmp_seen_files, job_store):
    """Migration creates completed/error records for done/failed/retry entries."""
    _, seen = tmp_seen_files
    watcher_with_store._migrate_seen_files_to_sqlite(seen)

    jobs = job_store.list_jobs(owner_username="system_watcher", include_results=False)

    # Should have 4 jobs (done x2, failed x1, retry x1)
    assert len(jobs) == 4

    completed = [j for j in jobs if j["status"] == "completed"]
    errors = [j for j in jobs if j["status"] == "error"]

    assert len(completed) == 2
    assert len(errors) == 2  # failed + retry both become error

    # Verify dates were preserved from lastModifiedDateTime
    completed_dates = {j["completed_at"][:10] for j in completed}
    assert "2026-05-10" in completed_dates
    assert "2026-05-15" in completed_dates


def test_migration_label_distribution_saved(watcher_with_store, tmp_seen_files, job_store):
    """Migration saves label_distribution as job results."""
    _, seen = tmp_seen_files
    watcher_with_store._migrate_seen_files_to_sqlite(seen)

    jobs = job_store.list_jobs(owner_username="system_watcher", include_results=True)
    completed = [j for j in jobs if j["status"] == "completed"]

    # Both completed jobs should have results with label_distribution
    jobs_with_results = [j for j in completed if j.get("results")]
    assert len(jobs_with_results) >= 1

    # Check first completed job's labels
    results = job_store.list_results_after(completed[0]["job_id"], after_id=0)
    assert len(results) >= 1
    assert "label_distribution" in results[0]


def test_migration_is_idempotent(watcher_with_store, tmp_seen_files, job_store):
    """Calling migration twice does NOT create duplicate records."""
    _, seen = tmp_seen_files

    watcher_with_store._migrate_seen_files_to_sqlite(seen)
    first_count = len(job_store.list_jobs(owner_username="system_watcher", include_results=False))

    # Run again
    watcher_with_store._migrate_seen_files_to_sqlite(seen)
    second_count = len(job_store.list_jobs(owner_username="system_watcher", include_results=False))

    assert first_count == second_count  # Idempotent!


def test_migration_skips_empty_seen(watcher_with_store, job_store):
    """Migration does nothing if seen_files is empty."""
    watcher_with_store._migrate_seen_files_to_sqlite({})

    jobs = job_store.list_jobs(owner_username="system_watcher", include_results=False)
    assert len(jobs) == 0


def test_migration_skips_when_watcher_records_exist(watcher_with_store, tmp_seen_files, job_store):
    """Migration skips if system_watcher records already exist (idempotency check)."""
    import uuid

    # Pre-create a watcher record to simulate already migrated
    job_store.create_job(
        job_id=str(uuid.uuid4()),
        owner_username="system_watcher",
        owner_role="system",
        filename="existing.xlsx",
        mode="watcher",
        input_path="/app/data/work/input/existing.xlsx",
        output_path="/app/data/work/output/existing_output.xlsx",
    )

    _, seen = tmp_seen_files
    watcher_with_store._migrate_seen_files_to_sqlite(seen)

    # Should still only have 1 record (the pre-existing one)
    jobs = job_store.list_jobs(owner_username="system_watcher", include_results=False)
    assert len(jobs) == 1
