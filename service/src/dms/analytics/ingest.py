"""Persist managed input workbooks into the analytics projection."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from dms.classification_jobs import ClassificationJobStore

from .input_reader import read_feedback_workbook, sha256_file
from .repository import FeedbackAnalyticsRepository


@dataclass(frozen=True)
class ManagedWorkbookIngestResult:
    """Summary of one managed workbook ingest."""

    job_id: str
    source_file_key: str
    source_file_name: str
    persisted_rows: int


def ingest_managed_workbook(
    repository: FeedbackAnalyticsRepository,
    input_path: Path,
) -> ManagedWorkbookIngestResult:
    """Persist Excel metadata without running the AI classification pipeline."""
    digest = sha256_file(input_path)
    source_file_key = f"sha256:{digest}"
    job_id = f"analytics-ingest-{digest}"
    parsed = read_feedback_workbook(input_path)
    jobs = ClassificationJobStore(repository.db_path)

    existing_job = jobs.get_job(job_id, include_results=False)
    if existing_job is not None and existing_job["status"] == "completed":
        return ManagedWorkbookIngestResult(
            job_id=job_id,
            source_file_key=source_file_key,
            source_file_name=input_path.name,
            persisted_rows=len(parsed.records),
        )

    if existing_job is None:
        jobs.create_job(
            job_id=job_id,
            owner_username="system_analytics_ingest",
            owner_role="admin",
            filename=input_path.name,
            mode="analytics_ingest",
            input_path=input_path,
            output_path=input_path,
        )

    try:
        repository.persist_input_snapshot(
            job_id=job_id,
            source_file_key=source_file_key,
            source_file_name=input_path.name,
            records=parsed.records,
            deactivate_absent=False,
        )
        jobs.complete_job(
            job_id,
            total_rows=len(parsed.records),
            rows_done=len(parsed.records),
            output_path=input_path,
            duration_seconds=0.0,
        )
    except Exception as exc:
        jobs.fail_job(job_id, str(exc))
        raise

    return ManagedWorkbookIngestResult(
        job_id=job_id,
        source_file_key=source_file_key,
        source_file_name=input_path.name,
        persisted_rows=len(parsed.records),
    )
