from __future__ import annotations

import time
from pathlib import Path

from dms.classification_jobs import (
    JOB_STATUS_CANCELLED,
    JOB_STATUS_COMPLETED,
    JOB_STATUS_ERROR,
    JOB_STATUS_QUEUED,
    ClassificationJobStore,
)
from dms.classification_worker import ClassificationWorkerManager
from dms.exceptions import PipelineCancelled
from dms.settings import Settings


def _settings(tmp_path: Path, **overrides) -> Settings:
    values = {
        "azure_tenant_id": "tenant",
        "azure_client_id": "client",
        "azure_client_secret": "secret",
        "sharepoint_drive_id": "drive",
        "sharepoint_root_folder_id": "root",
        "gemini_backend": "vertex",
        "gcp_project_id": "project",
        "data_dir": tmp_path / "data",
        "work_dir": tmp_path / "work",
        "log_dir": tmp_path / "logs",
        "jwt_secret_key": "x" * 40,
        "classification_worker_concurrency": 1,
        "classification_per_user_running_limit": 1,
        "classification_per_user_queued_limit": 3,
        "classification_retry_count": 1,
        "classification_stale_running_timeout_seconds": 3600,
        "classification_worker_poll_interval_seconds": 0.01,
        "classification_worker_heartbeat_seconds": 0.01,
        "rate_gap_sec": 0,
    }
    values.update(overrides)
    return Settings(**values)


def _create_job(store: ClassificationJobStore, tmp_path: Path, job_id: str = "job") -> dict:
    input_path = tmp_path / f"{job_id}.xlsx"
    input_path.write_bytes(b"fake-xlsx")
    return store.create_job(
        job_id=job_id,
        owner_username="alice",
        owner_role="user",
        filename=f"{job_id}.xlsx",
        mode="single",
        input_path=input_path,
        output_path=tmp_path / f"{job_id}_out.xlsx",
    )


def _wait_for_status(store: ClassificationJobStore, job_id: str, status: str) -> dict:
    for _ in range(200):
        job = store.get_job(job_id, include_results=True)
        if job and job["status"] == status:
            return job
        time.sleep(0.01)
    raise AssertionError(f"job {job_id} did not reach {status}")


class SuccessfulRunner:
    def run_pipeline(
        self,
        input_path,
        output_path,
        ckpt_path,
        progress_callback=None,
        cancellation_check=None,
        job_id=None,
    ):
        if progress_callback:
            progress_callback(
                done=1, total=1, new_results=[{"text": "done"}], step=3, step_status="done"
            )
        Path(output_path).write_bytes(b"output")
        Path(ckpt_path).parent.mkdir(parents=True, exist_ok=True)
        Path(ckpt_path).write_text('{"last_index": 1}', encoding="utf-8")
        return {
            "total_rows": 1,
            "processed_rows": 1,
            "output_path": str(output_path),
            "duration_seconds": 0.1,
        }


class FlakyRunner:
    def __init__(self, failures: int) -> None:
        self.failures = failures
        self.calls = 0

    def run_pipeline(
        self,
        input_path,
        output_path,
        ckpt_path,
        progress_callback=None,
        cancellation_check=None,
        job_id=None,
    ):
        self.calls += 1
        Path(ckpt_path).parent.mkdir(parents=True, exist_ok=True)
        Path(ckpt_path).write_text('{"last_index": 1}', encoding="utf-8")
        if self.calls <= self.failures:
            raise RuntimeError("Gemini provider timeout")
        Path(output_path).write_bytes(b"output")
        return {
            "total_rows": 1,
            "processed_rows": 1,
            "output_path": str(output_path),
            "duration_seconds": 0.1,
        }


class CancellingRunner:
    def run_pipeline(
        self,
        input_path,
        output_path,
        ckpt_path,
        progress_callback=None,
        cancellation_check=None,
        job_id=None,
    ):
        if progress_callback:
            progress_callback(done=0, total=1, step=1, step_status="running")
        if cancellation_check and cancellation_check():
            raise PipelineCancelled("cancelled")
        time.sleep(0.1)
        if cancellation_check and cancellation_check():
            raise PipelineCancelled("cancelled")
        raise AssertionError("expected cancellation")


def _manager(settings: Settings, store: ClassificationJobStore, runner):
    return ClassificationWorkerManager(
        settings=settings,
        job_store=store,
        runner_factory=lambda: runner,
        sharepoint_factory=lambda: None,
    )


def test_worker_processes_upload_queue_to_completion(tmp_path: Path):
    settings = _settings(tmp_path)
    store = ClassificationJobStore(tmp_path / "jobs.db")
    _create_job(store, tmp_path, "complete")
    manager = _manager(settings, store, SuccessfulRunner())

    manager.start()
    try:
        job = _wait_for_status(store, "complete", JOB_STATUS_COMPLETED)
    finally:
        manager.stop()

    assert job["rows_done"] == 1
    assert job["total_rows"] == 1
    assert job["results"][0]["text"] == "done"


def test_worker_retries_recoverable_failure_and_preserves_checkpoint(tmp_path: Path):
    settings = _settings(tmp_path, classification_retry_count=2)
    store = ClassificationJobStore(tmp_path / "jobs.db")
    _create_job(store, tmp_path, "retry")
    runner = FlakyRunner(failures=1)
    manager = _manager(settings, store, runner)

    manager.start()
    try:
        job = _wait_for_status(store, "retry", JOB_STATUS_COMPLETED)
    finally:
        manager.stop()

    assert runner.calls == 2
    assert job["retry_count"] == 1
    assert (settings.work_dir / "checkpoint" / "retry.json").is_file()


def test_worker_start_recovers_stale_running_job(tmp_path: Path):
    settings = _settings(
        tmp_path,
        classification_retry_count=1,
        classification_stale_running_timeout_seconds=1,
    )
    store = ClassificationJobStore(tmp_path / "jobs.db")
    _create_job(store, tmp_path, "stale")
    store.mark_running("stale")
    with store._conn() as conn:
        conn.execute(
            "UPDATE classification_jobs SET updated_at = ?, heartbeat_at = ? WHERE job_id = ?",
            ("2000-01-01T00:00:00+00:00", "2000-01-01T00:00:00+00:00", "stale"),
        )
        conn.commit()
    manager = _manager(settings, store, SuccessfulRunner())

    manager.start()
    try:
        job = _wait_for_status(store, "stale", JOB_STATUS_COMPLETED)
    finally:
        manager.stop()

    assert job["retry_count"] == 1
    assert job["rows_done"] == 1


def test_worker_marks_error_after_retry_exhaustion(tmp_path: Path):
    settings = _settings(tmp_path, classification_retry_count=1)
    store = ClassificationJobStore(tmp_path / "jobs.db")
    _create_job(store, tmp_path, "exhausted")
    manager = _manager(settings, store, FlakyRunner(failures=3))

    manager.start()
    try:
        job = _wait_for_status(store, "exhausted", JOB_STATUS_ERROR)
    finally:
        manager.stop()

    assert job["retry_count"] == 1
    assert "Gemini provider timeout" in job["error"]


def test_worker_handles_queued_and_running_cancellation(tmp_path: Path):
    settings = _settings(tmp_path)
    store = ClassificationJobStore(tmp_path / "jobs.db")
    _create_job(store, tmp_path, "queued")
    assert store.cancel_job("queued")["status"] == JOB_STATUS_CANCELLED

    _create_job(store, tmp_path, "running")
    runner = CancellingRunner()
    manager = _manager(settings, store, runner)

    manager.start()
    try:
        for _ in range(200):
            job = store.get_job("running", include_results=False)
            if job["status"] != JOB_STATUS_QUEUED:
                break
            time.sleep(0.01)
        store.cancel_job("running")
        cancelled = _wait_for_status(store, "running", JOB_STATUS_CANCELLED)
    finally:
        manager.stop()

    assert cancelled["terminal"] is True
