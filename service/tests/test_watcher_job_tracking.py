"""Unit tests for Watcher SQLite job tracking integration.

Tests that _process_file() correctly creates job records in SQLite
for both success and failure paths (tasks 1.3, 1.4, 1.5).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def mock_settings(tmp_path):
    """Minimal Settings mock for Watcher."""
    s = MagicMock()
    s.work_dir = tmp_path / "work"
    s.seen_files_path = tmp_path / "work" / "seen_files.json"
    s.metrics_path = tmp_path / "work" / "metrics.json"
    s.log_dir = tmp_path / "logs"
    s.poll_interval_seconds = 30
    s.classification_jobs_db_path = tmp_path / "work" / "classification_jobs.db"
    s.sp_checkpoint_folder = "Check_Point"
    s.sp_output_folder = "Output"
    s.notify_on_success = False
    s.notify_on_error = False
    (tmp_path / "work" / "input").mkdir(parents=True, exist_ok=True)
    (tmp_path / "work" / "output").mkdir(parents=True, exist_ok=True)
    (tmp_path / "work" / "checkpoint").mkdir(parents=True, exist_ok=True)
    return s


@pytest.fixture
def job_store(mock_settings):
    from dms.classification_jobs import ClassificationJobStore

    return ClassificationJobStore(mock_settings.classification_jobs_db_path)


@pytest.fixture
def watcher(mock_settings, job_store):
    from dms.metrics import MetricsCollector
    from dms.watcher import Watcher

    metrics = MagicMock(spec=MetricsCollector)
    metrics.get_pending_retry_count.return_value = 0

    w = Watcher(
        sharepoint_client=MagicMock(),
        pipeline_runner=MagicMock(),
        notification_service=MagicMock(),
        metrics=metrics,
        settings=mock_settings,
        job_store=job_store,
    )
    return w


def test_process_file_success_creates_completed_job(watcher, job_store):
    """Success path: _process_file creates a 'completed' job in SQLite."""
    file_info = {
        "id": "test-file-id-001",
        "name": "feedback.xlsx",
        "lastModifiedDateTime": "2026-06-10T10:00:00Z",
    }

    # Mock pipeline to return success
    watcher.pipeline_runner.run_pipeline.return_value = {
        "total_rows": 42,
        "duration_seconds": 5.5,
        "label_distribution": {"Sản phẩm": 30, "Giá/cơ chế": 12},
    }
    watcher.sharepoint_client.download_file.return_value = None
    watcher.sharepoint_client.upload_output.return_value = None
    watcher.sharepoint_client.upload_checkpoint.return_value = None
    watcher.sharepoint_client.upload_checkpoint.return_value = None

    seen = {}
    result = watcher._process_file(file_info, seen)

    assert result is True

    # Verify SQLite job was created
    jobs = job_store.list_jobs(owner_username="system_watcher", include_results=False)
    assert len(jobs) == 1
    job = jobs[0]
    assert job["status"] == "completed"
    assert job["filename"] == "feedback.xlsx"
    assert job["total_rows"] == 42

    # Verify label_distribution was saved as result
    results = job_store.list_results_after(job["job_id"], after_id=0)
    assert len(results) == 1
    assert results[0]["label_distribution"] == {"Sản phẩm": 30, "Giá/cơ chế": 12}


def test_process_file_failure_creates_error_job(watcher, job_store):
    """Failure path: _process_file creates an 'error' job in SQLite."""
    file_info = {
        "id": "test-file-id-002",
        "name": "bad_file.xlsx",
        "lastModifiedDateTime": "2026-06-11T10:00:00Z",
    }

    watcher.sharepoint_client.download_file.side_effect = RuntimeError("Network error")

    seen = {}
    result = watcher._process_file(file_info, seen)

    assert result is False

    # Verify SQLite error job was created
    jobs = job_store.list_jobs(owner_username="system_watcher", include_results=False)
    assert len(jobs) == 1
    job = jobs[0]
    assert job["status"] == "error"
    assert job["filename"] == "bad_file.xlsx"
    assert "RuntimeError" in (job["error"] or "")


def test_process_file_final_failure_marks_final(watcher, job_store):
    """Final failure: error message is annotated with FINAL prefix."""
    from dms.watcher import MAX_FILE_RETRIES

    file_info = {
        "id": "test-file-id-003",
        "name": "failing.xlsx",
        "lastModifiedDateTime": "2026-06-12T10:00:00Z",
    }

    watcher.sharepoint_client.download_file.side_effect = RuntimeError("Persistent error")

    # Simulate already at max retries - 1 to hit final on this call
    seen = {
        "test-file-id-003": {
            "name": "failing.xlsx",
            "failures": MAX_FILE_RETRIES - 1,
            "status": "retry",
        }
    }
    watcher._process_file(file_info, seen)

    jobs = job_store.list_jobs(owner_username="system_watcher", include_results=False)
    assert len(jobs) == 1
    assert "FINAL" in (jobs[0]["error"] or "")


def test_process_file_no_job_store_does_not_crash(mock_settings):
    """If no job_store is provided, processing should still work without SQLite."""
    from dms.metrics import MetricsCollector
    from dms.watcher import Watcher

    metrics = MagicMock(spec=MetricsCollector)
    metrics.get_pending_retry_count.return_value = 0

    w = Watcher(
        sharepoint_client=MagicMock(),
        pipeline_runner=MagicMock(),
        notification_service=MagicMock(),
        metrics=metrics,
        settings=mock_settings,
        job_store=None,  # No job store
    )
    w.pipeline_runner.run_pipeline.return_value = {
        "total_rows": 10,
        "duration_seconds": 1.0,
        "label_distribution": {},
    }
    w.sharepoint_client.download_file.return_value = None
    w.sharepoint_client.upload_output.return_value = None
    w.sharepoint_client.upload_checkpoint.return_value = None

    file_info = {"id": "id-001", "name": "test.xlsx", "lastModifiedDateTime": ""}
    seen = {}
    result = w._process_file(file_info, seen)
    assert result is True  # Should succeed even without job_store
