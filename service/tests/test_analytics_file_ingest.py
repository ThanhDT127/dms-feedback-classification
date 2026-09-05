from __future__ import annotations

from pathlib import Path

import pandas as pd

from dms.analytics import (
    AnalyticsFilter,
    BatchClassificationResult,
    FeedbackAnalyticsRepository,
    FeedbackAnalyticsService,
    ingest_managed_workbook,
    read_feedback_workbook,
)
from dms.classification_jobs import ClassificationJobStore


def _write_workbook(path: Path, *, issue_code: str, content: str) -> None:
    pd.DataFrame(
        {
            "Tên đơn vị": ["Truyền thống Vùng 1"],
            "Mã vấn đề": [issue_code],
            "Ngày": ["31/08/2026"],
            "Tỉnh/TP": ["Thành phố Hà Nội"],
            "Quận/huyện": ["Ba Đình"],
            "Loại vấn đề": ["Yêu cầu khách hàng"],
            "Trạng thái": ["Chờ xử lý"],
            "Nội dung vấn đề": [content],
        }
    ).to_excel(path, index=False)


def test_ingest_managed_workbook_persists_excel_metadata_for_analytics(tmp_path: Path):
    db_path = tmp_path / "classification_jobs.db"
    workbook = tmp_path / "feedback.xlsx"
    _write_workbook(workbook, issue_code="A-001", content="Khách cần thêm sản phẩm")
    repository = FeedbackAnalyticsRepository(db_path)

    result = ingest_managed_workbook(repository, workbook)

    overview = FeedbackAnalyticsService(repository).overview(AnalyticsFilter())
    current = repository.fetch_current_records()
    ingest_job = ClassificationJobStore(db_path).get_job(result.job_id)
    assert result.persisted_rows == 1
    assert result.source_file_name == "feedback.xlsx"
    assert overview["total_issues"]["value"] == 1
    assert current[0]["issue_code"] == "A-001"
    assert current[0]["unit_name"] == "Truyền thống Vùng 1"
    assert current[0]["content"] == "Khách cần thêm sản phẩm"
    assert current[0]["classification_state"] == "pending"
    assert ingest_job is not None
    assert ingest_job["mode"] == "analytics_ingest"
    assert ingest_job["status"] == "completed"


def test_ingest_same_workbook_is_idempotent(tmp_path: Path):
    repository = FeedbackAnalyticsRepository(tmp_path / "classification_jobs.db")
    workbook = tmp_path / "feedback.xlsx"
    _write_workbook(workbook, issue_code="A-001", content="Nội dung không đổi")

    first = ingest_managed_workbook(repository, workbook)
    second = ingest_managed_workbook(repository, workbook)

    assert first.job_id == second.job_id
    assert len(repository.fetch_current_records()) == 1
    assert len(repository.fetch_versions()) == 1


def test_ingest_overwritten_filename_keeps_old_and_new_file_contents(tmp_path: Path):
    repository = FeedbackAnalyticsRepository(tmp_path / "classification_jobs.db")
    workbook = tmp_path / "feedback.xlsx"
    _write_workbook(workbook, issue_code="OLD-001", content="Nội dung file cũ")
    old_result = ingest_managed_workbook(repository, workbook)

    _write_workbook(workbook, issue_code="NEW-001", content="Nội dung file mới")
    new_result = ingest_managed_workbook(repository, workbook)

    current = repository.fetch_current_records()
    versions = repository.fetch_versions()
    assert old_result.source_file_key != new_result.source_file_key
    assert {row["content"] for row in current} == {"Nội dung file cũ", "Nội dung file mới"}
    assert {row["issue_code"] for row in current} == {"OLD-001", "NEW-001"}
    assert {row["content"] for row in versions} == {"Nội dung file cũ", "Nội dung file mới"}
    assert (
        FeedbackAnalyticsService(repository).overview(AnalyticsFilter())["total_issues"]["value"]
        == 2
    )


def test_classify_after_ingest_enriches_same_record_without_duplication(tmp_path: Path):
    db_path = tmp_path / "classification_jobs.db"
    repository = FeedbackAnalyticsRepository(db_path)
    workbook = tmp_path / "feedback.xlsx"
    _write_workbook(workbook, issue_code="A-001", content="Nội dung gốc cần giữ")
    ingested = ingest_managed_workbook(repository, workbook)
    jobs = ClassificationJobStore(db_path)
    jobs.create_job(
        job_id="classify-1",
        owner_username="tester",
        owner_role="admin",
        filename=workbook.name,
        mode="single",
        input_path=workbook,
        output_path=tmp_path / "output.xlsx",
    )
    parsed = read_feedback_workbook(workbook)

    repository.persist_input_snapshot(
        job_id="classify-1",
        source_file_key=ingested.source_file_key,
        source_file_name=workbook.name,
        records=parsed.records,
        deactivate_absent=False,
    )
    repository.apply_batch_results(
        job_id="classify-1",
        results=[
            BatchClassificationResult(
                source_row_number=2,
                text="Nội dung gốc cần giữ",
                product="Sản phẩm A",
                product_line="Dòng A",
                model="Model A",
                bm25_score=0.92,
                sentiment="Tiêu cực",
                labels=["Báo lỗi"],
                brand="Thương hiệu A",
            )
        ],
        minor_to_major={"Báo lỗi": "Sản phẩm"},
    )

    current = repository.fetch_current_records()
    assert len(current) == 1
    assert current[0]["issue_code"] == "A-001"
    assert current[0]["content"] == "Nội dung gốc cần giữ"
    assert current[0]["unit_name"] == "Truyền thống Vùng 1"
    assert current[0]["product"] == "Sản phẩm A"
    assert current[0]["classification_state"] == "completed"
    assert repository.fetch_current_labels(row=2) == ["Báo lỗi"]

    ingest_managed_workbook(repository, workbook)

    current_after_reingest = repository.fetch_current_records()
    assert len(current_after_reingest) == 1
    assert current_after_reingest[0]["classification_state"] == "completed"
    assert current_after_reingest[0]["product"] == "Sản phẩm A"
    assert repository.fetch_current_labels(row=2) == ["Báo lỗi"]
