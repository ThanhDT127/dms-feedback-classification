from __future__ import annotations

import json
from pathlib import Path

import pytest
from analytics_support import create_job, make_record, make_result

from dms.analytics.repository import FeedbackAnalyticsRepository
from dms.classification_jobs import ClassificationJobStore


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
