"""Fixtures for feedback analytics repository tests."""

from __future__ import annotations

from pathlib import Path

from dms.analytics import BatchClassificationResult, FeedbackInputRecord
from dms.analytics.input_reader import normalize_duplicate_content
from dms.analytics.repository import FeedbackAnalyticsRepository
from dms.classification_jobs import ClassificationJobStore


def make_record(**values: object) -> FeedbackInputRecord:
    content = str(values.get("content", "Đèn lỗi"))
    raw_data = {"Nội dung phản hồi": content}
    raw_data.update(dict(values.get("raw_data", {})))
    return FeedbackInputRecord(
        source_row_number=int(values.get("source_row_number", 2)),
        raw_data=raw_data,
        content=content,
        normalized_content=normalize_duplicate_content(content),
        issue_code=values.get("issue_code", "A"),
        issue_date=values.get("issue_date", "2026-08-01"),
        source=values.get("source", "CRM"),
        unit_name=values.get("unit_name", "North"),
        business_status=values.get("business_status"),
    )


def make_result(**values: object) -> BatchClassificationResult:
    return BatchClassificationResult(
        source_row_number=int(values.get("source_row_number", 2)),
        text="Đèn lỗi",
        product=values.get("product"),
        product_line=None,
        model=None,
        bm25_score=None,
        sentiment=values.get("sentiment"),
        labels=list(values.get("labels", [])),
        brand=None,
    )


def create_job(store: ClassificationJobStore, job_id: str) -> None:
    store.create_job(
        job_id=job_id,
        owner_username="tester",
        owner_role="user",
        filename=f"{job_id}.xlsx",
        mode="single",
        input_path="input.xlsx",
        output_path="output.xlsx",
    )


def seed_classified_records(
    repo: FeedbackAnalyticsRepository, *, db_path: Path, entries: list[dict[str, object]]
) -> None:
    jobs = ClassificationJobStore(db_path)
    groups = {
        "Báo lỗi": "Sản phẩm",
        "Báo CL tốt": "Sản phẩm",
        "Y/c cải tiến": "Sản phẩm",
        "Đề xuất SPM": "Sản phẩm",
        "Bảo hành": "Dịch vụ",
        "Website": "Website",
    }
    for index, entry in enumerate(entries):
        job_id = f"seed-{index}"
        create_job(jobs, job_id)
        record = make_record(
            source_row_number=2,
            **{
                key: value
                for key, value in entry.items()
                if key not in {"labels", "product", "sentiment"}
            },
        )
        repo.persist_input_snapshot(
            job_id=job_id,
            source_file_key=job_id,
            source_file_name=f"{job_id}.xlsx",
            records=[record],
            deactivate_absent=False,
        )
        repo.apply_batch_results(
            job_id=job_id,
            results=[
                make_result(
                    source_row_number=2,
                    labels=list(entry.get("labels", [])),
                    product=entry.get("product"),
                    sentiment=entry.get("sentiment"),
                )
            ],
            minor_to_major=groups,
        )
