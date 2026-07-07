"""Classification endpoints: single-text and file-based classification."""

from __future__ import annotations

import logging
import uuid
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from ...classification_jobs import (
    JOB_STATUS_CANCELLED,
    JOB_STATUS_COMPLETED,
    JOB_STATUS_ERROR,
    JOB_STATUS_QUEUED,
    JOB_STATUS_RUNNING,
    TERMINAL_STATUSES,
)
from ...settings import SERVICE_DIR
from .. import deps
from ..deps import get_admin_user, get_current_user

logger = logging.getLogger("dms-web")

router = APIRouter(prefix="/api/classify", tags=["classify"])

WORK_DIR = SERVICE_DIR / "work"
CURRENT_USER_DEP = Depends(get_current_user)
ADMIN_USER_DEP = Depends(get_admin_user)


class TextClassifyRequest(BaseModel):
    """Request model for classifying a single text snippet."""

    text: str = Field(..., min_length=1, description="Text to classify")
    model: str | None = Field(None, description="Optional Gemini model")


@router.post("/text")
async def classify_text(body: TextClassifyRequest, user: dict = CURRENT_USER_DEP):
    """Classify one text snippet."""
    gemini = deps.get_gemini()
    if gemini is None:
        raise HTTPException(
            status_code=503,
            detail="GeminiClient is not configured. Check GEMINI_API_KEY or GCP credentials.",
        )

    rag = deps.get_rag()
    classifier = deps.get_issue_classifier()
    if classifier is None:
        raise HTTPException(status_code=503, detail="IssueClassifier is not ready.")

    try:
        product_result: dict = {}
        if rag is not None:
            try:
                rag_results = rag.retrieve_batch([body.text])
                if rag_results:
                    rag_results = rag.enrich_with_keyword_fallbacks(rag_results, [body.text])
                    product_result = rag_results[0]
            except Exception as exc:
                logger.warning("RAG matching failed: %s", exc)

        issue_result = classifier.classify_one(body.text)

        from ...pipeline.issue_classifier import MINOR_ORDER

        labels = {minor: minor in issue_result.get("final_minors", []) for minor in MINOR_ORDER}

        return {
            "text": body.text,
            "product": {
                "llm_extracted": product_result.get("LLM_Extracted", ""),
                "model": product_result.get("Model", ""),
                "dong_sp": product_result.get(
                    "DÃ²ng SP",
                    product_result.get("Dong SP", product_result.get("Dòng SP", "")),
                ),
                "san_pham": product_result.get(
                    "Sáº£n pháº©m",
                    product_result.get("San pham", product_result.get("Sản phẩm", "")),
                ),
                "score": product_result.get("Score", 0.0),
                "src": product_result.get("Src", "NONE"),
            },
            "labels": labels,
            "sentiment": issue_result.get("sentiment", ""),
            "brand": issue_result.get("brand", ""),
            "decision_log": issue_result.get("decision_log", []),
        }

    except Exception as exc:
        logger.error("Text classification failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Classification failed: {exc}") from exc


def _get_job_store_or_503():
    job_store = deps.get_classification_job_store()
    if job_store is None:
        raise HTTPException(status_code=503, detail="Classification job store is not ready.")
    return job_store


def _can_access_job(user: dict, job: dict) -> bool:
    return user.get("role") == "admin" or job.get("owner_username") == user.get("username")


def _get_authorized_job(job_id: str, user: dict, *, include_results: bool = True) -> dict:
    job_store = _get_job_store_or_503()
    job = job_store.get_job(job_id, include_results=include_results)
    if job is None or not _can_access_job(user, job):
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
    return job


def _enforce_user_classification_limits(job_store, user: dict) -> None:
    settings = deps.get_settings()
    if settings is None:
        raise HTTPException(status_code=503, detail="Server configuration error")

    username = user.get("username", "")
    queued = job_store.count_user_jobs(username, {JOB_STATUS_QUEUED})
    if queued >= settings.classification_per_user_queued_limit:
        logger.warning(
            "Classification upload rejected username=%s limit_type=queued limit=%s current=%s",
            username,
            settings.classification_per_user_queued_limit,
            queued,
        )
        raise HTTPException(
            status_code=429,
            detail=(
                "Bạn đã đạt giới hạn job đang chờ phân loại "
                f"({queued}/{settings.classification_per_user_queued_limit}). "
                "Vui lòng chờ job hiện tại chạy xong hoặc hủy bớt job trong hàng đợi."
            ),
        )

    running = job_store.count_user_jobs(username, {JOB_STATUS_RUNNING})
    if running >= settings.classification_per_user_running_limit:
        logger.warning(
            "Classification upload rejected username=%s limit_type=running limit=%s current=%s",
            username,
            settings.classification_per_user_running_limit,
            running,
        )
        raise HTTPException(
            status_code=429,
            detail=(
                "Bạn đã đạt giới hạn job phân loại đang chạy "
                f"({running}/{settings.classification_per_user_running_limit}). "
                "Vui lòng chờ job đang chạy hoàn tất trước khi gửi thêm file."
            ),
        )


def _ensure_worker_started() -> None:
    try:
        worker_manager = deps.get_classification_worker_manager()
        if worker_manager is not None:
            worker_manager.start()
    except Exception as exc:
        logger.warning("Classification worker could not be started on demand: %s", exc)


@router.post("/file")
async def classify_file(
    request: Request,
    file: UploadFile,
    mode: str = Form("single"),
    user: dict = CURRENT_USER_DEP,
):
    """Upload and classify an Excel file in the background."""
    if not file.filename:
        raise HTTPException(status_code=400, detail="Missing filename")

    if not file.filename.lower().endswith(".xlsx"):
        raise HTTPException(status_code=400, detail="Only .xlsx files are accepted")

    safe_filename = Path(file.filename).name
    if not safe_filename:
        raise HTTPException(status_code=400, detail="Invalid filename")

    job_store = _get_job_store_or_503()
    _enforce_user_classification_limits(job_store, user)

    job_id = str(uuid.uuid4())

    input_dir = WORK_DIR / "input"
    input_dir.mkdir(parents=True, exist_ok=True)
    input_path = input_dir / f"{job_id}_{safe_filename}"

    max_upload_bytes = 50 * 1024 * 1024
    try:
        content = await file.read(max_upload_bytes + 1)
        if len(content) > max_upload_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"File is too large. Limit {max_upload_bytes // (1024 * 1024)}MB.",
            )
        input_path.write_bytes(content)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Could not save file. Please try again.") from exc

    output_dir = WORK_DIR / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{job_id}_output_{safe_filename}"

    job_store.create_job(
        job_id=job_id,
        owner_username=user.get("username", ""),
        owner_role=user.get("role", "user"),
        filename=file.filename,
        mode=mode,
        input_path=input_path,
        output_path=output_path,
    )
    _ensure_worker_started()

    return {
        "job_id": job_id,
        "status": "queued",
        "filename": file.filename,
        "message": f"Đã đưa job phân loại vào hàng đợi: {job_id}",
    }


@router.get("/jobs")
async def list_jobs(request: Request, user: dict = CURRENT_USER_DEP):
    """List classification jobs visible to the authenticated user."""
    job_store = _get_job_store_or_503()
    if user.get("role") == "admin":
        return job_store.list_jobs()
    return job_store.list_jobs(owner_username=user.get("username", ""))


@router.get("/jobs/metrics")
async def get_job_metrics(request: Request, admin: dict = ADMIN_USER_DEP):
    """Return classification queue health metrics for administrators."""
    job_store = _get_job_store_or_503()
    return job_store.queue_metrics()


@router.get("/jobs/{job_id}")
async def get_job(request: Request, job_id: str, user: dict = CURRENT_USER_DEP):
    """Get one classification job status."""
    return _get_authorized_job(job_id, user)


@router.delete("/jobs/{job_id}")
async def cancel_job(request: Request, job_id: str, user: dict = CURRENT_USER_DEP):
    """Mark a classification job as cancelled."""
    job = _get_authorized_job(job_id, user, include_results=False)
    if job["status"] in TERMINAL_STATUSES:
        return {"message": f"Job already ended with status: {job['status']}"}

    job_store = _get_job_store_or_503()
    cancelled = job_store.cancel_job(job_id)
    if cancelled and cancelled.get("status") == JOB_STATUS_RUNNING:
        return {
            "message": f"Đã ghi nhận yêu cầu hủy job đang chạy: {job_id}",
            "status": cancelled["status"],
            "cancellation_requested": cancelled.get("cancellation_requested", False),
        }
    return {"message": f"Đã hủy job: {job_id}", "status": cancelled.get("status") if cancelled else None}


@router.post("/jobs/{job_id}/retry")
async def retry_job(request: Request, job_id: str, admin: dict = ADMIN_USER_DEP):
    """Retry a failed or cancelled classification job."""
    job_store = _get_job_store_or_503()
    job = job_store.get_job(job_id, include_results=False)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
    if job["status"] not in {JOB_STATUS_ERROR, JOB_STATUS_CANCELLED}:
        raise HTTPException(
            status_code=400,
            detail=f"Job cannot be retried from status: {job['status']}",
        )

    input_path = Path(job["input_path"])
    if not input_path.is_file():
        raise HTTPException(status_code=404, detail="Input file does not exist")

    retried = job_store.retry_job(job_id)
    _ensure_worker_started()
    return {
        "message": f"Đã đưa job phân loại vào hàng đợi retry: {job_id}",
        "job": retried,
    }


@router.get("/jobs/{job_id}/download")
async def download_job_result(request: Request, job_id: str, user: dict = CURRENT_USER_DEP):
    """Download the completed classification workbook."""
    job = _get_authorized_job(job_id, user, include_results=False)

    if job["status"] != JOB_STATUS_COMPLETED:
        raise HTTPException(
            status_code=400,
            detail=f"Job is not completed. Current status: {job['status']}",
        )

    output_path = Path(job["output_path"])
    if not output_path.is_file():
        raise HTTPException(status_code=404, detail="Result file does not exist")

    orig_filename = job.get("filename", "")
    remote_filename = f"{Path(orig_filename).stem}_output.xlsx" if orig_filename else output_path.name

    headers = {
        "Content-Disposition": f"attachment; filename=\"{remote_filename}\"; filename*=utf-8''{quote(remote_filename)}"
    }

    return FileResponse(
        path=output_path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers=headers,
    )


@router.post("/jobs/{job_id}/sharepoint")
async def upload_job_to_sharepoint(request: Request, job_id: str, user: dict = CURRENT_USER_DEP):
    """Upload a completed job input and output workbook to SharePoint."""
    job = _get_authorized_job(job_id, user, include_results=False)

    if job["status"] != JOB_STATUS_COMPLETED:
        raise HTTPException(
            status_code=400,
            detail=f"Job is not completed. Current status: {job['status']}",
        )

    settings = deps.get_settings()
    sp_client = deps.get_sharepoint_client()
    if not settings or not sp_client:
        raise HTTPException(status_code=503, detail="SharePoint client is not configured.")

    input_path = Path(job["input_path"])
    output_path = Path(job["output_path"])

    if not input_path.is_file():
        raise HTTPException(status_code=404, detail="Input file does not exist")
    if not output_path.is_file():
        raise HTTPException(status_code=404, detail="Result file does not exist")

    try:
        orig_filename = job.get("filename", "")
        if orig_filename:
            orig_stem = Path(orig_filename).stem
            remote_input_name = orig_filename
            remote_output_name = f"{orig_stem}_output.xlsx"
        else:
            remote_input_name = input_path.name
            remote_output_name = output_path.name

        logger.info(
            "Manually uploading job %s input to SharePoint: %s as %s",
            job_id,
            input_path.name,
            remote_input_name,
        )
        sp_client.upload_file(input_path, settings.sp_input_folder, remote_filename=remote_input_name)

        logger.info(
            "Manually uploading job %s output to SharePoint: %s as %s",
            job_id,
            output_path.name,
            remote_output_name,
        )
        res_out = sp_client.upload_file(
            output_path,
            settings.sp_output_folder,
            remote_filename=remote_output_name,
        )

        job_store = _get_job_store_or_503()
        job_store.update_sharepoint(
            job_id,
            sp_uploaded=True,
            sp_folder=settings.sp_output_folder,
            sp_web_url=res_out.get("webUrl"),
        )

        return {
            "message": "Uploaded input and output files to SharePoint",
            "sp_web_url": res_out.get("webUrl"),
        }
    except Exception as exc:
        logger.error("SharePoint upload failed for job %s: %s", job_id, exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Could not upload to SharePoint: {exc}") from exc
