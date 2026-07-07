from __future__ import annotations

from pathlib import Path
from threading import Thread

from dms.classification_jobs import (
    JOB_STATUS_CANCELLED,
    JOB_STATUS_COMPLETED,
    JOB_STATUS_ERROR,
    JOB_STATUS_QUEUED,
    JOB_STATUS_RUNNING,
    ClassificationJobStore,
)


def test_classification_job_store_persists_after_reload(tmp_path: Path):
    db_path = tmp_path / "work" / "classification_jobs.db"
    store = ClassificationJobStore(db_path)

    store.create_job(
        job_id="job-1",
        owner_username="alice",
        owner_role="user",
        filename="input.xlsx",
        mode="single",
        input_path=tmp_path / "input.xlsx",
        output_path=tmp_path / "output.xlsx",
    )
    store.mark_running("job-1")
    store.update_progress("job-1", done=20, total=100, step=3, step_status="running")
    store.append_results("job-1", [{"text": "row 1", "labels": ["Bao loi"]}])

    reloaded = ClassificationJobStore(db_path)
    job = reloaded.get_job("job-1")

    assert job is not None
    assert job["owner_username"] == "alice"
    assert job["status"] == "running"
    assert job["rows_done"] == 20
    assert job["total_rows"] == 100
    assert job["percent"] == 20
    assert job["step"] == 3
    assert job["step_status"] == "running"
    assert job["results"][0]["text"] == "row 1"


def test_classification_job_store_terminal_states(tmp_path: Path):
    store = ClassificationJobStore(tmp_path / "jobs.db")
    store.create_job(
        job_id="done",
        owner_username="alice",
        owner_role="user",
        filename="input.xlsx",
        mode="single",
        input_path="input.xlsx",
        output_path="output.xlsx",
    )
    store.complete_job(
        "done",
        total_rows=5,
        rows_done=5,
        output_path="output.xlsx",
        duration_seconds=1.5,
        sp_uploaded=True,
        sp_folder="Output",
        sp_web_url="https://example.test/output.xlsx",
    )
    done = store.get_job("done")
    assert done["status"] == JOB_STATUS_COMPLETED
    assert done["sp_uploaded"] is True
    assert done["sp_web_url"] == "https://example.test/output.xlsx"

    store.create_job(
        job_id="failed",
        owner_username="alice",
        owner_role="user",
        filename="input.xlsx",
        mode="single",
        input_path="input.xlsx",
        output_path="output.xlsx",
    )
    store.fail_job("failed", "boom")
    assert store.get_job("failed")["status"] == JOB_STATUS_ERROR
    assert store.get_job("failed")["error"] == "boom"

    store.create_job(
        job_id="cancelled",
        owner_username="alice",
        owner_role="user",
        filename="input.xlsx",
        mode="single",
        input_path="input.xlsx",
        output_path="output.xlsx",
    )
    store.cancel_job("cancelled")
    assert store.get_job("cancelled")["status"] == JOB_STATUS_CANCELLED


def test_classification_job_store_lists_by_owner(tmp_path: Path):
    store = ClassificationJobStore(tmp_path / "jobs.db")
    for job_id, owner in [("a", "alice"), ("b", "bob")]:
        store.create_job(
            job_id=job_id,
            owner_username=owner,
            owner_role="user",
            filename=f"{job_id}.xlsx",
            mode="single",
            input_path=f"{job_id}.xlsx",
            output_path=f"{job_id}_out.xlsx",
        )

    assert [job["job_id"] for job in store.list_jobs(owner_username="alice")] == ["a"]
    assert {job["job_id"] for job in store.list_jobs()} == {"a", "b"}


def test_classification_job_store_retry_and_queue_metrics(tmp_path: Path):
    store = ClassificationJobStore(tmp_path / "jobs.db")
    store.create_job(
        job_id="failed",
        owner_username="alice",
        owner_role="user",
        filename="feedback.xlsx",
        mode="single",
        input_path=tmp_path / "feedback.xlsx",
        output_path=tmp_path / "out.xlsx",
    )
    store.mark_running("failed")
    store.fail_job("failed", "boom")

    failed = store.get_job("failed", include_results=False)
    assert failed["status"] == JOB_STATUS_ERROR
    assert failed["terminal"] is True
    assert failed["can_retry"] is True
    assert failed["error_summary"] == "boom"

    retried = store.retry_job("failed")
    assert retried["status"] == JOB_STATUS_QUEUED
    assert retried["retry_count"] == 1
    assert retried["can_cancel"] is True
    assert retried["error"] is None

    metrics = store.queue_metrics()
    assert metrics["counts"]["queued"] == 1
    assert metrics["counts"]["retrying"] == 1
    assert metrics["counts"]["failed"] == 0


def test_classification_job_store_atomic_claim_prevents_duplicates(tmp_path: Path):
    store = ClassificationJobStore(tmp_path / "jobs.db")
    store.create_job(
        job_id="queued",
        owner_username="alice",
        owner_role="user",
        filename="feedback.xlsx",
        mode="single",
        input_path=tmp_path / "feedback.xlsx",
        output_path=tmp_path / "out.xlsx",
    )

    claimed: list[str | None] = []

    def claim(worker_id: str) -> None:
        job = store.claim_next_job(
            worker_id=worker_id,
            global_running_limit=4,
            per_user_running_limit=4,
        )
        claimed.append(job["job_id"] if job else None)

    threads = [Thread(target=claim, args=(f"w-{idx}",)) for idx in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert claimed.count("queued") == 1
    assert claimed.count(None) == 1
    assert store.get_job("queued", include_results=False)["status"] == JOB_STATUS_RUNNING


def test_classification_job_store_claim_respects_capacity_and_user_limit(tmp_path: Path):
    store = ClassificationJobStore(tmp_path / "jobs.db")
    for job_id, owner in [("a1", "alice"), ("a2", "alice"), ("b1", "bob")]:
        store.create_job(
            job_id=job_id,
            owner_username=owner,
            owner_role="user",
            filename=f"{job_id}.xlsx",
            mode="single",
            input_path=tmp_path / f"{job_id}.xlsx",
            output_path=tmp_path / f"{job_id}_out.xlsx",
        )

    first = store.claim_next_job(
        worker_id="worker",
        global_running_limit=2,
        per_user_running_limit=1,
    )
    second = store.claim_next_job(
        worker_id="worker",
        global_running_limit=2,
        per_user_running_limit=1,
    )
    third = store.claim_next_job(
        worker_id="worker",
        global_running_limit=2,
        per_user_running_limit=1,
    )

    assert first["job_id"] == "a1"
    assert second["job_id"] == "b1"
    assert third is None
    assert store.get_job("a2", include_results=False)["status"] == JOB_STATUS_QUEUED


def test_classification_job_store_cancellation_and_stale_recovery(tmp_path: Path):
    store = ClassificationJobStore(tmp_path / "jobs.db")
    store.create_job(
        job_id="queued",
        owner_username="alice",
        owner_role="user",
        filename="queued.xlsx",
        mode="single",
        input_path=tmp_path / "queued.xlsx",
        output_path=tmp_path / "queued_out.xlsx",
    )
    assert store.cancel_job("queued")["status"] == JOB_STATUS_CANCELLED

    store.create_job(
        job_id="running",
        owner_username="alice",
        owner_role="user",
        filename="running.xlsx",
        mode="single",
        input_path=tmp_path / "running.xlsx",
        output_path=tmp_path / "running_out.xlsx",
    )
    store.mark_running("running")
    cancelled = store.cancel_job("running")
    assert cancelled["status"] == JOB_STATUS_RUNNING
    assert cancelled["cancellation_requested"] is True
    assert store.is_cancellation_requested("running") is True
    assert store.mark_cancelled("running")["status"] == JOB_STATUS_CANCELLED

    store.create_job(
        job_id="stale",
        owner_username="bob",
        owner_role="user",
        filename="stale.xlsx",
        mode="single",
        input_path=tmp_path / "stale.xlsx",
        output_path=tmp_path / "stale_out.xlsx",
    )
    store.mark_running("stale")
    assert store.recover_stale_running_jobs(stale_after_seconds=0, max_retries=1) == 1
    recovered = store.get_job("stale", include_results=False)
    assert recovered["status"] == JOB_STATUS_QUEUED
    assert recovered["retry_count"] == 1
