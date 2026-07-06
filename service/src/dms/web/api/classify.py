"""Classification endpoints: single-text and file-based classification."""

from __future__ import annotations

import logging
import threading
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from ...settings import SERVICE_DIR
from .. import deps
from ..deps import get_current_user

logger = logging.getLogger("dms-web")

router = APIRouter(prefix="/api/classify", tags=["classify"])

WORK_DIR = SERVICE_DIR / "work"


# ---------- Request / Response models ----------


class TextClassifyRequest(BaseModel):
    """Yêu cầu phân loại văn bản đơn lẻ."""

    text: str = Field(..., min_length=1, description="Văn bản cần phân loại")
    model: str | None = Field(None, description="Model Gemini tùy chọn")


# ---------- Single text classification ----------


@router.post("/text")
async def classify_text(body: TextClassifyRequest, user: dict = Depends(get_current_user)):
    """Phân loại một đoạn văn bản."""
    gemini = deps.get_gemini()
    if gemini is None:
        raise HTTPException(
            status_code=503,
            detail="GeminiClient chưa được cấu hình. Kiểm tra GEMINI_API_KEY hoặc GCP creds.",
        )

    rag = deps.get_rag()
    classifier = deps.get_issue_classifier()
    if classifier is None:
        raise HTTPException(
            status_code=503,
            detail="IssueClassifier chưa sẵn sàng.",
        )

    try:
        # RAG product matching
        product_result: dict = {}
        if rag is not None:
            try:
                rag_results = rag.retrieve_batch([body.text])
                if rag_results:
                    rag_results = rag.enrich_with_keyword_fallbacks(rag_results, [body.text])
                    product_result = rag_results[0]
            except Exception as exc:
                logger.warning("RAG matching thất bại: %s", exc)

        # Issue classification
        issue_result = classifier.classify_one(body.text)

        # Build response
        from ...pipeline.issue_classifier import MINOR_ORDER

        labels = {}
        for minor in MINOR_ORDER:
            labels[minor] = minor in issue_result.get("final_minors", [])

        return {
            "text": body.text,
            "product": {
                "llm_extracted": product_result.get("LLM_Extracted", ""),
                "model": product_result.get("Model", ""),
                "dong_sp": product_result.get("Dòng SP", ""),
                "san_pham": product_result.get("Sản phẩm", ""),
                "score": product_result.get("Score", 0.0),
                "src": product_result.get("Src", "NONE"),
            },
            "labels": labels,
            "sentiment": issue_result.get("sentiment", ""),
            "brand": issue_result.get("brand", ""),
            "decision_log": issue_result.get("decision_log", []),
        }

    except Exception as exc:
        logger.error("Lỗi phân loại văn bản: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Lỗi phân loại: {exc}",
        ) from exc


# ---------- File-based classification ----------


def _run_classification_job(
    job_id: str,
    jobs: dict,
    input_path: Path,
    output_path: Path,
) -> None:
    """Background thread: classify all rows in the Excel file."""
    job = jobs[job_id]
    try:
        job["status"] = "running"
        job["started_at"] = datetime.now().isoformat(timespec="seconds")

        runner = deps.get_pipeline_runner()
        if runner is None:
            job["status"] = "error"
            job["error"] = "PipelineRunner chưa sẵn sàng. Kiểm tra cấu hình."
            return

        ckpt_path = WORK_DIR / "checkpoint" / f"{job_id}.json"

        def progress_callback(
            done: int | None = None,
            total: int | None = None,
            new_results: list[dict] | None = None,
            step: int | None = None,
            step_status: str | None = None,
        ):
            if step is not None:
                job["step"] = step
                job["step_status"] = step_status
            if done is not None:
                job["rows_done"] = done
                job["total_rows"] = total
                job["percent"] = int((done / total) * 100) if total > 0 else 0
            if new_results:
                if "results" not in job:
                    job["results"] = []
                job["results"].extend(new_results)

        result = runner.run_pipeline(
            input_path=input_path,
            output_path=output_path,
            ckpt_path=ckpt_path,
            progress_callback=progress_callback,
        )

        job["status"] = "completed"
        job["total_rows"] = result.get("total_rows", 0)
        job["rows_done"] = result.get("processed_rows", 0)
        job["percent"] = 100
        job["output_path"] = result.get("output_path", "")
        job["duration_seconds"] = result.get("duration_seconds", 0)
        job["completed_at"] = datetime.now().isoformat(timespec="seconds")

        # Upload output to SharePoint if configured
        try:
            settings = deps.get_settings()
            sp_client = deps.get_sharepoint_client()
            if sp_client and settings.sp_output_folder:
                orig_filename = job.get("filename", "")
                if orig_filename:
                    orig_stem = Path(orig_filename).stem
                    remote_filename = f"{orig_stem}_output.xlsx"
                else:
                    remote_filename = output_path.name
                logger.info(
                    "Uploading completed job %s output to SharePoint: %s as %s",
                    job_id,
                    output_path.name,
                    remote_filename,
                )
                res = sp_client.upload_file(
                    output_path, settings.sp_output_folder, remote_filename=remote_filename
                )
                job["sp_uploaded"] = True
                job["sp_folder"] = settings.sp_output_folder
                job["sp_web_url"] = res.get("webUrl")
        except Exception as exc:
            logger.warning("Không thể upload kết quả lên SharePoint cho job %s: %s", job_id, exc)

    except Exception as exc:
        logger.error("Job %s thất bại: %s", job_id, exc, exc_info=True)
        job["status"] = "error"
        job["error"] = str(exc)


@router.post("/file")
async def classify_file(
    request: Request,
    file: UploadFile,
    mode: str = Form("single"),
    user: dict = Depends(get_current_user),
):
    """Upload và phân loại file Excel trong background."""
    if not file.filename:
        raise HTTPException(status_code=400, detail="Thiếu tên file")

    if not file.filename.lower().endswith(".xlsx"):
        raise HTTPException(status_code=400, detail="Chỉ chấp nhận file .xlsx")

    job_id = str(uuid.uuid4())

    # Save uploaded file — use only basename to prevent path traversal
    safe_filename = Path(file.filename).name
    if not safe_filename:
        raise HTTPException(status_code=400, detail="Tên file không hợp lệ")

    input_dir = WORK_DIR / "input"
    input_dir.mkdir(parents=True, exist_ok=True)
    input_path = input_dir / f"{job_id}_{safe_filename}"

    MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50 MB
    try:
        content = await file.read(MAX_UPLOAD_BYTES + 1)
        if len(content) > MAX_UPLOAD_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"File quá lớn. Giới hạn {MAX_UPLOAD_BYTES // (1024 * 1024)}MB.",
            )
        input_path.write_bytes(content)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Lỗi lưu file. Vui lòng thử lại.") from exc

    # Prepare output path
    output_dir = WORK_DIR / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{job_id}_output_{file.filename}"

    # Register job
    jobs: dict = request.app.state.jobs
    jobs[job_id] = {
        "job_id": job_id,
        "status": "queued",
        "filename": file.filename,
        "mode": mode,
        "input_path": str(input_path),
        "output_path": str(output_path),
        "total_rows": 0,
        "rows_done": 0,
        "percent": 0,
        "results": [],
        "error": None,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "started_at": None,
        "completed_at": None,
        "sp_web_url": None,
    }

    # Start background thread
    thread = threading.Thread(
        target=_run_classification_job,
        args=(job_id, jobs, input_path, output_path),
        daemon=True,
        name=f"classify-{job_id[:8]}",
    )
    thread.start()

    return {
        "job_id": job_id,
        "status": "queued",
        "filename": file.filename,
        "message": f"Đã tạo job phân loại: {job_id}",
    }


# ---------- Job management ----------


@router.get("/jobs")
async def list_jobs(request: Request, user: dict = Depends(get_current_user)):
    """Liệt kê tất cả các job phân loại."""
    jobs: dict = request.app.state.jobs
    return list(jobs.values())


@router.get("/jobs/{job_id}")
async def get_job(request: Request, job_id: str, user: dict = Depends(get_current_user)):
    """Lấy trạng thái của một job."""
    jobs: dict = request.app.state.jobs
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Không tìm thấy job: {job_id}")
    return job


@router.delete("/jobs/{job_id}")
async def cancel_job(request: Request, job_id: str, user: dict = Depends(get_current_user)):
    """Hủy một job (đánh dấu cancelled, thread sẽ dừng tự nhiên)."""
    jobs: dict = request.app.state.jobs
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Không tìm thấy job: {job_id}")

    if job["status"] in ("completed", "error", "cancelled"):
        return {"message": f"Job đã kết thúc với trạng thái: {job['status']}"}

    job["status"] = "cancelled"
    return {"message": f"Đã hủy job: {job_id}"}


@router.get("/jobs/{job_id}/download")
async def download_job_result(request: Request, job_id: str, user: dict = Depends(get_current_user)):
    """Tải file Excel kết quả phân loại."""
    jobs: dict = request.app.state.jobs
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Không tìm thấy job: {job_id}")

    if job["status"] != "completed":
        raise HTTPException(
            status_code=400,
            detail=f"Job chưa hoàn thành. Trạng thái hiện tại: {job['status']}",
        )

    output_path = Path(job["output_path"])
    if not output_path.is_file():
        raise HTTPException(status_code=404, detail="File kết quả không tồn tại trên hệ thống")

    from urllib.parse import quote

    orig_filename = job.get("filename", "")
    if orig_filename:
        orig_stem = Path(orig_filename).stem
        remote_filename = f"{orig_stem}_output.xlsx"
    else:
        remote_filename = output_path.name

    headers = {
        "Content-Disposition": f"attachment; filename=\"{remote_filename}\"; filename*=utf-8''{quote(remote_filename)}'"
    }

    return FileResponse(
        path=output_path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers=headers,
    )


@router.post("/jobs/{job_id}/sharepoint")
async def upload_job_to_sharepoint(request: Request, job_id: str, user: dict = Depends(get_current_user)):
    """Upload cả file input và output của job lên SharePoint."""
    jobs: dict = request.app.state.jobs
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Không tìm thấy job: {job_id}")

    if job["status"] != "completed":
        raise HTTPException(
            status_code=400,
            detail=f"Job chưa hoàn thành. Trạng thái hiện tại: {job['status']}",
        )

    settings = deps.get_settings()
    sp_client = deps.get_sharepoint_client()
    if not settings or not sp_client:
        raise HTTPException(
            status_code=503,
            detail="SharePoint client chưa được cấu hình.",
        )

    input_path = Path(job["input_path"])
    output_path = Path(job["output_path"])

    if not input_path.is_file():
        raise HTTPException(status_code=404, detail="File đầu vào không tồn tại trên hệ thống")
    if not output_path.is_file():
        raise HTTPException(status_code=404, detail="File kết quả không tồn tại trên hệ thống")

    try:
        orig_filename = job.get("filename", "")
        if orig_filename:
            orig_stem = Path(orig_filename).stem
            remote_input_name = orig_filename
            remote_output_name = f"{orig_stem}_output.xlsx"
        else:
            remote_input_name = input_path.name
            remote_output_name = output_path.name

        # Upload input file to Input folder
        logger.info(
            "Manually uploading job %s input to SharePoint: %s as %s",
            job_id,
            input_path.name,
            remote_input_name,
        )
        sp_client.upload_file(
            input_path, settings.sp_input_folder, remote_filename=remote_input_name
        )

        # Upload output file to Output folder
        logger.info(
            "Manually uploading job %s output to SharePoint: %s as %s",
            job_id,
            output_path.name,
            remote_output_name,
        )
        res_out = sp_client.upload_file(
            output_path, settings.sp_output_folder, remote_filename=remote_output_name
        )

        job["sp_uploaded"] = True
        job["sp_folder"] = settings.sp_output_folder
        job["sp_web_url"] = res_out.get("webUrl")

        return {
            "message": "Đã tải thành công file input và output lên SharePoint",
            "sp_web_url": res_out.get("webUrl"),
        }
    except Exception as exc:
        logger.error("Lỗi upload SharePoint cho job %s: %s", job_id, exc, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Không thể upload lên SharePoint: {exc}",
        ) from exc
