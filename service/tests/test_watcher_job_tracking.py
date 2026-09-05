"""Integration tests for Watcher job and analytics persistence lifecycle."""

from __future__ import annotations

from unittest.mock import MagicMock

import pandas as pd
import pytest


@pytest.fixture
def mock_settings(tmp_path):
    """Minimal Settings mock for Watcher."""
    settings = MagicMock()
    settings.work_dir = tmp_path / "work"
    settings.seen_files_path = tmp_path / "work" / "seen_files.json"
    settings.metrics_path = tmp_path / "work" / "metrics.json"
    settings.log_dir = tmp_path / "logs"
    settings.poll_interval_seconds = 30
    settings.classification_jobs_db_path = tmp_path / "work" / "classification_jobs.db"
    settings.sp_checkpoint_folder = "Check_Point"
    settings.sp_output_folder = "Output"
    settings.notify_on_success = False
    settings.notify_on_error = False
    settings.enable_runtime_cleanup = False
    (tmp_path / "work" / "input").mkdir(parents=True, exist_ok=True)
    (tmp_path / "work" / "output").mkdir(parents=True, exist_ok=True)
    (tmp_path / "work" / "checkpoint").mkdir(parents=True, exist_ok=True)
    return settings


@pytest.fixture
def job_store(mock_settings):
    from dms.classification_jobs import ClassificationJobStore

    return ClassificationJobStore(mock_settings.classification_jobs_db_path)


@pytest.fixture
def analytics_repo(mock_settings):
    from dms.analytics import FeedbackAnalyticsRepository

    return FeedbackAnalyticsRepository(mock_settings.classification_jobs_db_path)


@pytest.fixture
def watcher(mock_settings, job_store, analytics_repo):
    from dms.metrics import MetricsCollector
    from dms.watcher import Watcher

    metrics = MagicMock(spec=MetricsCollector)
    metrics.get_pending_retry_count.return_value = 0

    return Watcher(
        sharepoint_client=MagicMock(),
        pipeline_runner=MagicMock(),
        notification_service=MagicMock(),
        metrics=metrics,
        settings=mock_settings,
        job_store=job_store,
        analytics_repository=analytics_repo,
    )


def _download_valid_workbook(_file_id, target) -> None:
    pd.DataFrame({"Nội dung phản hồi": ["Đèn lỗi"]}).to_excel(target, index=False)


def _batch_result() -> dict:
    return {
        "source_row_number": 2,
        "text": "Đèn lỗi",
        "product": "LED",
        "product_line": "Chiếu sáng",
        "model": "LED-1",
        "bm25_score": 8.5,
        "sentiment": "Tiêu cực",
        "labels": ["Báo lỗi"],
        "brand": "",
    }


def test_watcher_creates_running_job_and_persists_rows_before_pipeline(
    watcher, job_store, analytics_repo
):
    file_info = {
        "id": "test-file-id-001",
        "name": "feedback.xlsx",
        "lastModifiedDateTime": "2026-06-10T10:00:00Z",
    }
    watcher.sharepoint_client.download_file.side_effect = _download_valid_workbook

    def assert_persisted(*args, **kwargs):
        jobs = job_store.list_jobs(owner_username="system_watcher", include_results=False)
        assert len(jobs) == 1
        assert jobs[0]["status"] == "running"
        assert kwargs["job_id"] == jobs[0]["job_id"]
        assert analytics_repo.fetch_current_records()[0]["source_file_key"] == file_info["id"]
        kwargs["progress_callback"](
            done=1,
            total=1,
            new_results=[_batch_result()],
            step=3,
            step_status="done",
        )
        return {
            "total_rows": 1,
            "processed_rows": 1,
            "duration_seconds": 0.1,
            "label_distribution": {"Báo lỗi": 1},
        }

    watcher.pipeline_runner.run_pipeline.side_effect = assert_persisted

    assert watcher._process_file(file_info, {}) is True

    jobs = job_store.list_jobs(owner_username="system_watcher", include_results=False)
    assert len(jobs) == 1
    assert jobs[0]["status"] == "completed"
    assert jobs[0]["total_rows"] == 1
    results = job_store.list_results_after(jobs[0]["job_id"], after_id=0)
    assert results == [_batch_result() | {"_seq": results[0]["_seq"]}]
    assert analytics_repo.fetch_current_records()[0]["classification_state"] == "completed"
    assert analytics_repo.fetch_current_labels(row=2) == ["Báo lỗi"]


def test_watcher_failure_marks_pending_versions_and_same_job_failed(
    watcher, job_store, analytics_repo
):
    file_info = {
        "id": "test-file-id-002",
        "name": "bad_file.xlsx",
        "lastModifiedDateTime": "2026-06-11T10:00:00Z",
    }
    watcher.sharepoint_client.download_file.side_effect = _download_valid_workbook
    watcher.pipeline_runner.run_pipeline.side_effect = RuntimeError("provider unavailable")

    assert watcher._process_file(file_info, {}) is False

    jobs = job_store.list_jobs(owner_username="system_watcher", include_results=False)
    assert len(jobs) == 1
    assert jobs[0]["status"] == "error"
    assert "provider unavailable" in (jobs[0]["error"] or "")
    assert analytics_repo.fetch_current_records()[0]["classification_state"] == "failed"
    assert (
        analytics_repo.fetch_versions(job_id=jobs[0]["job_id"])[0]["classification_state"]
        == "failed"
    )


def test_watcher_retry_soft_deactivates_removed_rows_for_same_sharepoint_file(
    watcher, job_store, analytics_repo
):
    file_info = {
        "id": "test-file-id-retry",
        "name": "changed.xlsx",
        "lastModifiedDateTime": "2026-06-12T10:00:00Z",
    }
    input_path = watcher.settings.work_dir / "input" / file_info["name"]
    downloads = [
        ["row one", "row two"],
        ["row one updated"],
    ]

    def download_version(_file_id, target) -> None:
        pd.DataFrame({"Nội dung phản hồi": downloads.pop(0)}).to_excel(target, index=False)

    def complete_pipeline(*args, **kwargs):
        parsed_rows = pd.read_excel(input_path).shape[0]
        return {
            "total_rows": parsed_rows,
            "processed_rows": parsed_rows,
            "duration_seconds": 0.1,
            "label_distribution": {},
        }

    watcher.sharepoint_client.download_file.side_effect = download_version
    watcher.pipeline_runner.run_pipeline.side_effect = complete_pipeline

    assert watcher._process_file(file_info, {}) is True
    file_info["lastModifiedDateTime"] = "2026-06-12T11:00:00Z"
    assert watcher._process_file(file_info, {}) is True

    jobs = job_store.list_jobs(owner_username="system_watcher", include_results=False)
    assert len(jobs) == 2
    records = analytics_repo.fetch_current_records()
    assert len(records) == 2
    assert records[0]["content"] == "row one updated"
    assert records[0]["is_active"] == 1
    assert records[1]["is_active"] == 0


def test_watcher_download_failure_records_one_error_job(watcher, job_store):
    file_info = {
        "id": "test-file-id-003",
        "name": "download_error.xlsx",
        "lastModifiedDateTime": "2026-06-12T10:00:00Z",
    }
    watcher.sharepoint_client.download_file.side_effect = RuntimeError("Network error")

    assert watcher._process_file(file_info, {}) is False

    jobs = job_store.list_jobs(owner_username="system_watcher", include_results=False)
    assert len(jobs) == 1
    assert jobs[0]["status"] == "error"
    assert "Network error" in (jobs[0]["error"] or "")


def test_watcher_final_failure_marks_same_job_final(watcher, job_store):
    from dms.watcher import MAX_FILE_RETRIES

    file_info = {
        "id": "test-file-id-004",
        "name": "failing.xlsx",
        "lastModifiedDateTime": "2026-06-12T10:00:00Z",
    }
    watcher.sharepoint_client.download_file.side_effect = RuntimeError("Persistent error")
    seen = {
        file_info["id"]: {
            "name": file_info["name"],
            "failures": MAX_FILE_RETRIES - 1,
            "status": "retry",
        }
    }

    watcher._process_file(file_info, seen)

    jobs = job_store.list_jobs(owner_username="system_watcher", include_results=False)
    assert len(jobs) == 1
    assert "FINAL" in (jobs[0]["error"] or "")


def test_process_file_without_job_store_or_analytics_does_not_crash(mock_settings):
    from dms.metrics import MetricsCollector
    from dms.watcher import Watcher

    metrics = MagicMock(spec=MetricsCollector)
    metrics.get_pending_retry_count.return_value = 0
    watcher = Watcher(
        sharepoint_client=MagicMock(),
        pipeline_runner=MagicMock(),
        notification_service=MagicMock(),
        metrics=metrics,
        settings=mock_settings,
    )
    watcher.pipeline_runner.run_pipeline.return_value = {
        "total_rows": 10,
        "processed_rows": 10,
        "duration_seconds": 1.0,
        "label_distribution": {},
    }

    file_info = {"id": "id-001", "name": "test.xlsx", "lastModifiedDateTime": ""}
    assert watcher._process_file(file_info, {}) is True
