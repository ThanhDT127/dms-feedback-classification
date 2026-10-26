from __future__ import annotations

import json
from pathlib import Path

import pytest
from analytics_support import create_job, make_record, make_result

from dms.analytics.repository import FeedbackAnalyticsRepository
from dms.classification_jobs import ClassificationJobStore


def test_job_source_key_is_stable_and_rejects_ambiguous_snapshots(tmp_path: Path):
    db_path = tmp_path / "jobs.db"
    jobs = ClassificationJobStore(db_path)
    create_job(jobs, "retry-job")
    repo = FeedbackAnalyticsRepository(db_path)
    assert repo.get_job_source_file_key("retry-job") is None
    for key in ("sharepoint-original", "sha256:duplicate"):
        repo.persist_input_snapshot(
            job_id="retry-job",
            source_file_key=key,
            source_file_name="input.xlsx",
            records=[make_record(source_row_number=2)],
            deactivate_absent=False,
        )
        if key == "sharepoint-original":
            assert repo.get_job_source_file_key("retry-job") == key
    with pytest.raises(ValueError, match="Ambiguous input source keys"):
        repo.get_job_source_file_key("retry-job")


def test_persist_input_creates_current_row_and_immutable_version(tmp_path: Path):
    db_path = tmp_path / "classification_jobs.db"
    jobs = ClassificationJobStore(db_path)
    create_job(jobs, "job-1")
    repo = FeedbackAnalyticsRepository(db_path)

    repo.persist_input_snapshot(
        job_id="job-1",
        source_file_key="sha256:abc",
        source_file_name="a.xlsx",
        records=[make_record(source_row_number=2, issue_code=None, content="Đèn lỗi")],
        deactivate_absent=False,
    )

    current = repo.fetch_current_records()
    versions = repo.fetch_versions(job_id="job-1")

    assert current[0]["issue_code"] is None
    assert json.loads(current[0]["raw_data_json"])["Nội dung phản hồi"] == "Đèn lỗi"
    assert versions[0]["classification_state"] == "pending"


def test_fetch_versions_supports_optional_job_and_row_filters(tmp_path: Path):
    db_path = tmp_path / "classification_jobs.db"
    jobs = ClassificationJobStore(db_path)
    repo = FeedbackAnalyticsRepository(db_path)
    for job_id, row in (("job-1", 2), ("job-2", 3)):
        create_job(jobs, job_id)
        repo.persist_input_snapshot(
            job_id=job_id,
            source_file_key=f"sha256:{job_id}",
            source_file_name=f"{job_id}.xlsx",
            records=[make_record(source_row_number=row)],
            deactivate_absent=False,
        )

    assert [version["job_id"] for version in repo.fetch_versions()] == ["job-1", "job-2"]
    assert [version["job_id"] for version in repo.fetch_versions(row=3)] == ["job-2"]


def test_retry_preserves_completed_version_and_soft_deactivates_removed_watcher_rows(
    tmp_path: Path,
):
    db_path = tmp_path / "classification_jobs.db"
    jobs = ClassificationJobStore(db_path)
    create_job(jobs, "job-1")
    repo = FeedbackAnalyticsRepository(db_path)
    repo.persist_input_snapshot(
        job_id="job-1",
        source_file_key="sp-1",
        source_file_name="a.xlsx",
        records=[make_record(source_row_number=2), make_record(source_row_number=3)],
        deactivate_absent=True,
    )
    repo.apply_batch_results(
        job_id="job-1",
        results=[make_result(source_row_number=2, labels=["Báo lỗi"])],
        minor_to_major={"Báo lỗi": "Sản phẩm"},
    )

    repo.persist_input_snapshot(
        job_id="job-1",
        source_file_key="sp-1",
        source_file_name="a.xlsx",
        records=[make_record(source_row_number=2)],
        deactivate_absent=True,
    )

    assert repo.fetch_versions(job_id="job-1", row=2)[0]["classification_state"] == "completed"
    assert repo.fetch_current_records(row=3)[0]["is_active"] == 0


def test_late_analytics_ingest_preserves_classification_completed_first(tmp_path: Path):
    db_path = tmp_path / "classification_jobs.db"
    jobs = ClassificationJobStore(db_path)
    repo = FeedbackAnalyticsRepository(db_path)
    jobs.create_job(
        job_id="analytics-ingest-late",
        owner_username="system_analytics_ingest",
        owner_role="admin",
        filename="a.xlsx",
        mode="analytics_ingest",
        input_path="a.xlsx",
        output_path="a.xlsx",
    )
    create_job(jobs, "classify-first")
    record = make_record(source_row_number=2, content="Nội dung gốc")
    repo.persist_input_snapshot(
        job_id="classify-first",
        source_file_key="sha256:a",
        source_file_name="a.xlsx",
        records=[record],
        deactivate_absent=False,
    )
    repo.apply_batch_results(
        job_id="classify-first",
        results=[make_result(source_row_number=2, labels=["Báo lỗi"], product="Sản phẩm A")],
        minor_to_major={"Báo lỗi": "Sản phẩm"},
    )

    repo.persist_input_snapshot(
        job_id="analytics-ingest-late",
        source_file_key="sha256:a",
        source_file_name="a.xlsx",
        records=[record],
        deactivate_absent=False,
    )

    current = repo.fetch_current_records(row=2)[0]
    assert current["last_job_id"] == "classify-first"
    assert current["classification_state"] == "completed"
    assert current["product"] == "Sản phẩm A"
    assert repo.fetch_current_labels(row=2) == ["Báo lỗi"]


def test_batch_replaces_current_labels_but_keeps_each_version_label_snapshot(tmp_path: Path):
    db_path = tmp_path / "classification_jobs.db"
    jobs = ClassificationJobStore(db_path)
    repo = FeedbackAnalyticsRepository(db_path)
    create_job(jobs, "job-1")
    repo.persist_input_snapshot(
        job_id="job-1",
        source_file_key="sha256:a",
        source_file_name="a.xlsx",
        records=[make_record(source_row_number=2)],
        deactivate_absent=False,
    )
    repo.apply_batch_results(
        job_id="job-1",
        results=[make_result(source_row_number=2, labels=["Báo lỗi", "Website"])],
        minor_to_major={"Báo lỗi": "Sản phẩm", "Website": "Website"},
    )
    create_job(jobs, "job-2")
    repo.persist_input_snapshot(
        job_id="job-2",
        source_file_key="sha256:a",
        source_file_name="a.xlsx",
        records=[make_record(source_row_number=2)],
        deactivate_absent=False,
    )
    repo.apply_batch_results(
        job_id="job-2",
        results=[make_result(source_row_number=2, labels=["Bảo hành"])],
        minor_to_major={"Bảo hành": "Dịch vụ"},
    )

    assert repo.fetch_current_labels(row=2) == ["Bảo hành"]
    assert (
        json.loads(repo.fetch_versions(job_id="job-1")[0]["labels_json"])[0]["label"] == "Báo lỗi"
    )


def test_duplicate_result_callback_preserves_completed_version_and_current_projection(
    tmp_path: Path,
):
    db_path = tmp_path / "classification_jobs.db"
    jobs = ClassificationJobStore(db_path)
    create_job(jobs, "job-1")
    repo = FeedbackAnalyticsRepository(db_path)
    repo.persist_input_snapshot(
        job_id="job-1",
        source_file_key="sha256:a",
        source_file_name="a.xlsx",
        records=[make_record()],
        deactivate_absent=False,
    )
    repo.apply_batch_results(
        job_id="job-1",
        results=[make_result(labels=["Báo lỗi"], product="Original")],
        minor_to_major={"Báo lỗi": "Sản phẩm"},
    )

    repo.apply_batch_results(
        job_id="job-1",
        results=[make_result(labels=["Website"], product="Replacement")],
        minor_to_major={"Website": "Website"},
    )

    version = repo.fetch_versions(job_id="job-1", row=2)[0]
    current = repo.fetch_current_records(row=2)[0]
    assert version["product"] == "Original"
    assert json.loads(version["labels_json"])[0]["label"] == "Báo lỗi"
    assert current["product"] == "Original"
    assert repo.fetch_current_labels(row=2) == ["Báo lỗi"]


def test_batch_rejects_result_without_matching_input_snapshot(tmp_path: Path):
    db_path = tmp_path / "classification_jobs.db"
    jobs = ClassificationJobStore(db_path)
    create_job(jobs, "job-1")
    repo = FeedbackAnalyticsRepository(db_path)
    repo.persist_input_snapshot(
        job_id="job-1",
        source_file_key="sha256:a",
        source_file_name="a.xlsx",
        records=[make_record(source_row_number=2)],
        deactivate_absent=False,
    )

    with pytest.raises(ValueError, match="No input snapshot"):
        repo.apply_batch_results(
            job_id="job-1",
            results=[make_result(source_row_number=999)],
            minor_to_major={},
        )

    assert repo.fetch_current_records(row=2)[0]["classification_state"] == "pending"
    assert repo.fetch_versions(job_id="job-1", row=2)[0]["classification_state"] == "pending"


def test_invalid_batch_rolls_back_valid_results_and_rejects_ambiguous_snapshots(tmp_path: Path):
    db_path = tmp_path / "classification_jobs.db"
    jobs = ClassificationJobStore(db_path)
    create_job(jobs, "job-1")
    repo = FeedbackAnalyticsRepository(db_path)
    repo.persist_input_snapshot(
        job_id="job-1",
        source_file_key="sha256:a",
        source_file_name="a.xlsx",
        records=[make_record(source_row_number=2), make_record(source_row_number=3)],
        deactivate_absent=False,
    )

    with pytest.raises(ValueError, match="No input snapshot"):
        repo.apply_batch_results(
            job_id="job-1",
            results=[make_result(source_row_number=2), make_result(source_row_number=999)],
            minor_to_major={},
        )

    assert repo.fetch_versions(job_id="job-1", row=2)[0]["classification_state"] == "pending"

    repo.persist_input_snapshot(
        job_id="job-1",
        source_file_key="sha256:b",
        source_file_name="b.xlsx",
        records=[make_record(source_row_number=2)],
        deactivate_absent=False,
    )
    with pytest.raises(ValueError, match="Ambiguous input snapshots"):
        repo.apply_batch_results(
            job_id="job-1",
            results=[make_result(source_row_number=2)],
            minor_to_major={},
        )


def test_stale_result_callback_does_not_overwrite_newer_current_record(tmp_path: Path):
    db_path = tmp_path / "classification_jobs.db"
    jobs = ClassificationJobStore(db_path)
    repo = FeedbackAnalyticsRepository(db_path)
    create_job(jobs, "job-1")
    create_job(jobs, "job-2")
    for job_id in ("job-1", "job-2"):
        repo.persist_input_snapshot(
            job_id=job_id,
            source_file_key="sha256:a",
            source_file_name="a.xlsx",
            records=[make_record()],
            deactivate_absent=False,
        )

    repo.apply_batch_results(
        job_id="job-1",
        results=[make_result(labels=["Báo lỗi"], product="Old")],
        minor_to_major={"Báo lỗi": "Sản phẩm"},
    )

    current = repo.fetch_current_records(row=2)[0]
    version = repo.fetch_versions(job_id="job-1", row=2)[0]
    assert current["product"] is None
    assert repo.fetch_current_labels(row=2) == []
    assert version["classification_state"] == "completed"
    assert version["product"] == "Old"


def test_mark_job_unfinished_failed_leaves_completed_rows_unchanged(tmp_path: Path):
    db_path = tmp_path / "classification_jobs.db"
    jobs = ClassificationJobStore(db_path)
    create_job(jobs, "job-1")
    repo = FeedbackAnalyticsRepository(db_path)
    repo.persist_input_snapshot(
        job_id="job-1",
        source_file_key="sha256:a",
        source_file_name="a.xlsx",
        records=[make_record(source_row_number=2), make_record(source_row_number=3)],
        deactivate_absent=False,
    )
    repo.apply_batch_results(
        job_id="job-1",
        results=[make_result(source_row_number=2, labels=["Website"])],
        minor_to_major={"Website": "Website"},
    )

    repo.mark_job_unfinished_failed("job-1")

    assert repo.fetch_versions(job_id="job-1", row=2)[0]["classification_state"] == "completed"
    assert repo.fetch_versions(job_id="job-1", row=3)[0]["classification_state"] == "failed"
    assert repo.fetch_current_records(row=2)[0]["classification_state"] == "completed"
    assert repo.fetch_current_records(row=3)[0]["classification_state"] == "failed"


def test_rollback_cancelled_job_deactivates_same_file_records_and_preserves_other_files(
    tmp_path: Path,
):
    from dms.analytics import AnalyticsFilter, FeedbackAnalyticsService

    db_path = tmp_path / "classification_jobs.db"
    jobs = ClassificationJobStore(db_path)
    repo = FeedbackAnalyticsRepository(db_path)

    # 1. Valid completed dataset (e.g. 260401 - TTPA T1-T3.2026.xlsx)
    jobs.create_job(
        job_id="job-valid",
        owner_username="admin",
        owner_role="admin",
        filename="260401 - TTPA T1-T3.2026.xlsx",
        mode="single",
        input_path="valid.xlsx",
        output_path="valid_out.xlsx",
    )
    repo.persist_input_snapshot(
        job_id="job-valid",
        source_file_key="sha256:valid",
        source_file_name="260401 - TTPA T1-T3.2026.xlsx",
        records=[
            make_record(source_row_number=2, issue_code="V-1"),
            make_record(source_row_number=3, issue_code="V-2"),
        ],
        deactivate_absent=False,
    )
    jobs.complete_job(
        "job-valid",
        total_rows=2,
        rows_done=2,
        output_path=tmp_path / "valid_out.xlsx",
        duration_seconds=1.0,
    )

    # 2. Admin uploads TTPA 2024.xlsx (source_file_key = sha256:ttpa2024) and cancels it
    jobs.create_job(
        job_id="job-admin-cancel",
        owner_username="admin",
        owner_role="admin",
        filename="TTPA 2024.xlsx",
        mode="single",
        input_path="ttpa2024.xlsx",
        output_path="ttpa2024_out.xlsx",
    )
    repo.persist_input_snapshot(
        job_id="job-admin-cancel",
        source_file_key="sha256:ttpa2024",
        source_file_name="TTPA 2024.xlsx",
        records=[
            make_record(source_row_number=2, issue_code="A-1"),
            make_record(source_row_number=3, issue_code="A-2"),
            make_record(source_row_number=4, issue_code="A-3"),
        ],
        deactivate_absent=False,
    )
    jobs.mark_cancelled("job-admin-cancel")

    # 3. Watcher also picked up TTPA 2024.xlsx (source_file_key = sp-id-ttpa2024) and admin cancels it
    jobs.create_job(
        job_id="job-watcher-cancel",
        owner_username="system_watcher",
        owner_role="system",
        filename="TTPA 2024.xlsx",
        mode="watcher",
        input_path="ttpa2024.xlsx",
        output_path="ttpa2024_out.xlsx",
    )
    repo.persist_input_snapshot(
        job_id="job-watcher-cancel",
        source_file_key="sp-id-ttpa2024",
        source_file_name="TTPA 2024.xlsx",
        records=[
            make_record(source_row_number=2, issue_code="W-1"),
            make_record(source_row_number=3, issue_code="W-2"),
            make_record(source_row_number=4, issue_code="W-3"),
        ],
        deactivate_absent=True,
    )
    jobs.mark_cancelled("job-watcher-cancel")

    service = FeedbackAnalyticsService(repo)
    assert service.overview(AnalyticsFilter())["total_issues"]["value"] == 8

    repo.rollback_cancelled_job("job-watcher-cancel")
    assert service.overview(AnalyticsFilter())["total_issues"]["value"] == 2


def test_purge_file_records_removes_records_by_input_or_output_filename(tmp_path: Path):
    from dms.analytics import AnalyticsFilter, FeedbackAnalyticsService

    db_path = tmp_path / "classification_jobs.db"
    jobs = ClassificationJobStore(db_path)
    repo = FeedbackAnalyticsRepository(db_path)

    jobs.create_job(
        job_id="job-keep",
        owner_username="admin",
        owner_role="admin",
        filename="keep.xlsx",
        mode="single",
        input_path="keep.xlsx",
        output_path="keep_out.xlsx",
    )
    repo.persist_input_snapshot(
        job_id="job-keep",
        source_file_key="sha256:keep",
        source_file_name="keep.xlsx",
        records=[make_record(source_row_number=2, issue_code="K-1")],
        deactivate_absent=False,
    )
    jobs.create_job(
        job_id="job-check",
        owner_username="admin",
        owner_role="admin",
        filename="check.xlsx",
        mode="single",
        input_path="check.xlsx",
        output_path="check_out.xlsx",
    )
    repo.persist_input_snapshot(
        job_id="job-check",
        source_file_key="sha256:check",
        source_file_name="check.xlsx",
        records=[
            make_record(source_row_number=2, issue_code="C-1"),
            make_record(source_row_number=3, issue_code="C-2"),
        ],
        deactivate_absent=False,
    )
    repo.apply_batch_results(
        job_id="job-check",
        results=[make_result(source_row_number=2, labels=["Báo lỗi"])],
        minor_to_major={"Báo lỗi": "Sản phẩm"},
    )

    service = FeedbackAnalyticsService(repo)
    assert service.overview(AnalyticsFilter())["total_issues"]["value"] == 3

    purged = repo.purge_file_records(["check_output.xlsx"])
    assert purged == 2
    assert service.overview(AnalyticsFilter())["total_issues"]["value"] == 1
    assert repo.fetch_current_labels(row=2) == []


def test_startup_reconciliation_cleans_cancelled_jobs_and_deleted_local_inputs(tmp_path: Path):
    import sqlite3

    from dms.analytics import AnalyticsFilter, FeedbackAnalyticsService

    work_dir = tmp_path / "work"
    input_dir = work_dir / "input"
    input_dir.mkdir(parents=True, exist_ok=True)
    db_path = work_dir / "classification_jobs.db"

    keep_file = input_dir / "keep.xlsx"
    keep_file.write_bytes(b"keep")
    deleted_file = input_dir / "check.xlsx"
    deleted_file.write_bytes(b"check")

    jobs = ClassificationJobStore(db_path)
    repo = FeedbackAnalyticsRepository(db_path)

    # 1. Active valid job with existing input file
    jobs.create_job(
        job_id="job-keep",
        owner_username="admin",
        owner_role="admin",
        filename="keep.xlsx",
        mode="single",
        input_path=keep_file,
        output_path=work_dir / "output" / "keep_out.xlsx",
    )
    jobs.complete_job(
        "job-keep",
        total_rows=1,
        rows_done=1,
        output_path=work_dir / "output" / "keep_out.xlsx",
        duration_seconds=0.1,
    )
    repo.persist_input_snapshot(
        job_id="job-keep",
        source_file_key="sha256:keep",
        source_file_name="keep.xlsx",
        records=[make_record(source_row_number=2)],
        deactivate_absent=False,
    )

    # 2. Completed job whose local input file was deleted from disk before cleanup fix
    jobs.create_job(
        job_id="job-deleted",
        owner_username="admin",
        owner_role="admin",
        filename="check.xlsx",
        mode="single",
        input_path=deleted_file,
        output_path=work_dir / "output" / "check_out.xlsx",
    )
    jobs.complete_job(
        "job-deleted",
        total_rows=2,
        rows_done=2,
        output_path=work_dir / "output" / "check_out.xlsx",
        duration_seconds=0.1,
    )
    repo.persist_input_snapshot(
        job_id="job-deleted",
        source_file_key="sha256:check",
        source_file_name="check.xlsx",
        records=[make_record(source_row_number=2), make_record(source_row_number=3)],
        deactivate_absent=False,
    )
    deleted_file.unlink()

    # 3. Cancelled job whose records were left active before rollback fix
    jobs.create_job(
        job_id="job-cancelled",
        owner_username="system_watcher",
        owner_role="system",
        filename="TTPA 2024.xlsx",
        mode="watcher",
        input_path=input_dir / "TTPA 2024.xlsx",
        output_path=work_dir / "output" / "TTPA 2024_output.xlsx",
    )
    jobs.mark_running("job-cancelled")
    repo.persist_input_snapshot(
        job_id="job-cancelled",
        source_file_key="sp-ttpa-2024",
        source_file_name="TTPA 2024.xlsx",
        records=[make_record(source_row_number=2), make_record(source_row_number=3)],
        deactivate_absent=True,
    )
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE classification_jobs SET cancellation_requested = 1 WHERE job_id = 'job-cancelled'"
        )
        conn.commit()

    # Re-instantiate repository to trigger _init_db() -> _reconcile_startup_state()
    reconciled_repo = FeedbackAnalyticsRepository(db_path)
    service = FeedbackAnalyticsService(reconciled_repo)
    assert service.overview(AnalyticsFilter())["total_issues"]["value"] == 1
