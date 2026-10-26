"""File management endpoints."""

from __future__ import annotations

import io
import json
import logging
import uuid
from pathlib import Path

import pandas as pd
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from ...analytics import FeedbackAnalyticsRepository, ingest_managed_workbook
from ...exceptions import SharePointError
from ...settings import get_settings
from ...sharepoint_cache import get_sharepoint_list_cache
from ...time_utils import utc_from_timestamp
from ..deps import (
    get_admin_user,
    get_current_user,
    get_feedback_analytics_repository,
    get_sharepoint_client,
    get_sharepoint_sync_service,
    run_sync_in_threadpool,
)

logger = logging.getLogger("dms-web")

router = APIRouter(prefix="/api/files", tags=["files"])

WORK_DIR: Path | None = None
FOLDER_MAP: dict[str, list[Path]] = {}


def _work_dir() -> Path:
    if WORK_DIR is not None:
        return WORK_DIR
    return get_settings().work_dir


def _get_folder_map() -> dict[str, list[Path]]:
    """Return a mapping of logical folder names to physical directories dynamically based on settings."""
    if FOLDER_MAP:
        return FOLDER_MAP
    settings = get_settings()
    return {
        "input": [settings.work_dir / "input", settings.data_dir / "Input"],
        "output": [settings.work_dir / "output", settings.data_dir / "Output"],
        "checkpoint": [settings.work_dir / "checkpoint", settings.data_dir / "Check_Point"],
        "keyword": [settings.keyword_dir],
        "model": [settings.model_dir],
    }


def _staging_download_path(filename: str) -> Path:
    staging_dir = _work_dir() / "staging"
    staging_dir.mkdir(parents=True, exist_ok=True)
    safe_name = Path(filename).name
    if not safe_name:
        raise HTTPException(status_code=400, detail="TÃªn file khÃ´ng há»£p lá»‡")
    return staging_dir / f"{uuid.uuid4().hex}_{safe_name}"


def _file_info(path: Path) -> dict:
    """Build a file info dict from a path."""
    stat = path.stat()
    return {
        "name": path.name,
        "size": stat.st_size,
        "modified": utc_from_timestamp(stat.st_mtime).isoformat(timespec="seconds"),
        "extension": path.suffix.lstrip("."),
    }


MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50 MB


def _validate_safe_path(base_dir: Path, filename: str) -> Path:
    """Resolve candidate path and ensure it stays within base_dir.

    Raises HTTPException 400 if filename contains path traversal.
    Returns the safe resolved path.
    """
    # Strip directory components first (e.g. '../../evil.xlsx' → 'evil.xlsx')
    safe_name = Path(filename).name
    if not safe_name:
        raise HTTPException(status_code=400, detail="Tên file không hợp lệ")
    candidate = (base_dir / safe_name).resolve()
    try:
        candidate.relative_to(base_dir.resolve())
    except ValueError:
        raise HTTPException(status_code=400, detail="Tên file không hợp lệ")  # noqa: B904
    return candidate


# ---------- Fixed routes FIRST (before /{folder} path parameter) ----------


@router.get("/tree", name="file_tree")
async def get_folder_tree(user: dict = Depends(get_current_user)):
    """Trả về cấu trúc cây thư mục."""
    tree: dict = {}
    folder_map = _get_folder_map()
    for folder_name in folder_map.keys():
        files = await list_files(folder_name, user=user)
        tree[folder_name] = [
            {
                "path": files[0].get("source_dir", folder_name) if files else folder_name,
                "files": files,
            }
        ]
    return tree


@router.get("/seen", name="seen_files")
async def get_seen_files(user: dict = Depends(get_current_user)):
    """Trả về nội dung seen_files.json."""
    seen_path = _work_dir() / "seen_files.json"
    if not seen_path.is_file():
        return {}
    try:
        return json.loads(seen_path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Lỗi đọc seen_files.json: %s", exc)
        return {"error": str(exc)}


@router.post("/upload")
async def upload_file(
    file: UploadFile,
    admin: dict = Depends(get_admin_user),
    analytics_repository: FeedbackAnalyticsRepository | None = Depends(
        get_feedback_analytics_repository
    ),
):
    """Upload file .xlsx vào thư mục work/input/."""
    if not file.filename:
        raise HTTPException(status_code=400, detail="Thiếu tên file")

    if not file.filename.lower().endswith(".xlsx"):
        raise HTTPException(
            status_code=400,
            detail="Chỉ chấp nhận file .xlsx",
        )

    input_dir = _work_dir() / "input"
    input_dir.mkdir(parents=True, exist_ok=True)

    # Validate safe path (strip directory traversal)
    dest = _validate_safe_path(input_dir, file.filename)

    try:
        content = await file.read(MAX_UPLOAD_BYTES + 1)
        if len(content) > MAX_UPLOAD_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"File quá lớn. Giới hạn {MAX_UPLOAD_BYTES // (1024 * 1024)}MB.",
            )
        dest.write_bytes(content)

        # Validate Excel structure: must contain a column containing "nội dung" or "noi dung"
        try:
            df = pd.read_excel(dest, nrows=0)
            has_content_col = any(
                "nội dung" in str(col).lower() or "noi dung" in str(col).lower()
                for col in df.columns
            )
            if not has_content_col:
                if dest.is_file():
                    dest.unlink()
                raise HTTPException(
                    status_code=400,
                    detail="Cột dữ liệu không hợp lệ. File Excel tải lên bắt buộc phải chứa cột có tên 'Nội dung' hoặc 'noi dung' chứa thông tin phản hồi.",
                )
        except HTTPException:
            raise
        except Exception as exc:
            if dest.is_file():
                dest.unlink()
            raise HTTPException(
                status_code=400,
                detail=f"File Excel không hợp lệ hoặc bị lỗi định dạng: {exc}",
            ) from exc

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail="Lỗi lưu file. Vui lòng thử lại.",
        ) from exc

    ingested_rows = 0
    ingest_error = None
    already_classified = False
    try:
        if analytics_repository is None:
            raise RuntimeError("Kho dữ liệu analytics chưa sẵn sàng")
        ingest_result = await run_sync_in_threadpool(
            ingest_managed_workbook, analytics_repository, dest
        )
        ingested_rows = ingest_result.persisted_rows
        already_classified = getattr(ingest_result, "classified_rows", 0) > 0
    except Exception:
        ingest_error = "Không thể đưa file vào phân tích"
        logger.exception("Không thể đưa file %s vào analytics", dest.name)

    job_id = None
    classification_queued = False
    if not already_classified:
        try:
            from .. import deps as _deps

            job_store = _deps.get_classification_job_store()
            if (
                job_store is not None
                and Path(job_store.db_path).parent.resolve() == _work_dir().resolve()
            ):
                reclassify_res = await _reclassify_input_items(
                    [SharePointDeleteItem(name=dest.name)],
                    admin,
                )
                queued = reclassify_res.get("queued") or []
                if queued:
                    job_id = queued[0].get("job_id")
                    classification_queued = True
        except Exception as exc:
            logger.warning(
                "Không thể tự động đưa file %s vào hàng đợi phân loại: %s",
                dest.name,
                exc,
            )

    get_sharepoint_list_cache().invalidate("input")

    return {
        "filename": dest.name,
        "size": len(content),
        "message": f"Đã upload thành công: {dest.name}",
        "ingested_rows": ingested_rows,
        "ingest_error": ingest_error,
        "job_id": job_id,
        "classification_queued": classification_queued,
    }


@router.get("/template")
async def get_template(user: dict = Depends(get_current_user)):
    """Tải file template Excel mẫu cho việc phân loại phản hồi."""
    try:
        df = pd.DataFrame(
            {
                "Nội dung": [
                    "Ví dụ: Ứng dụng chạy rất mượt nhưng đôi khi bị lag nhẹ khi tải dữ liệu lớn.",
                    "Ví dụ: Tôi không thể đăng nhập vào tài khoản của mình từ sáng nay.",
                ]
            }
        )
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine="openpyxl") as writer:
            df.to_excel(writer, index=False)
        output.seek(0)

        headers = {"Content-Disposition": 'attachment; filename="template_dms.xlsx"'}
        return StreamingResponse(
            output,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers=headers,
        )
    except Exception as exc:
        logger.error("Lỗi tạo file template: %s", exc)
        raise HTTPException(status_code=500, detail="Không thể tạo file template") from exc


@router.post("/sync")
@router.post("/sync-sharepoint")
async def sync_sharepoint(admin: dict = Depends(get_admin_user)):
    """Đồng bộ thủ công hai chiều: tải về Input & Output mới từ SharePoint, nạp vào Dashboard và đẩy lên Output mới."""
    sp_client = get_sharepoint_client()
    settings = get_settings()
    sync_service = get_sharepoint_sync_service()

    if sp_client is None or settings is None or sync_service is None:
        raise HTTPException(
            status_code=503,
            detail="Không thể kết nối SharePoint (chưa cấu hình Azure credentials hoặc database)",
        )

    # 1. Chạy tải và nạp toàn diện bằng SharePointSyncService
    try:
        stats = sync_service.sync_and_ingest()
    except Exception as exc:
        logger.error("Lỗi đồng bộ SharePoint: %s", exc)
        raise HTTPException(
            status_code=502,
            detail=f"Lỗi đồng bộ SharePoint: {exc}",
        ) from exc

    # 2. Đẩy lên file Output mới nếu có file local chưa có trên SharePoint
    uploads = 0
    try:
        local_output_dir = settings.work_dir / "output"
        if local_output_dir.is_dir():
            sp_output_items = sp_client.list_folder_items(settings.sp_output_folder)
            sp_output_names = {item["name"] for item in sp_output_items if "folder" not in item}

            for path in local_output_dir.iterdir():
                if path.is_file() and path.suffix.lower() == ".xlsx":
                    file_size = path.stat().st_size
                    if file_size < 1024:
                        continue
                    if path.name not in sp_output_names:
                        logger.info("Manual Sync: Uploading completed output file %s", path.name)
                        sp_client.upload_file(path, settings.sp_output_folder)
                        uploads += 1
    except Exception as exc:
        logger.warning("Lỗi đẩy Output mới lên SharePoint: %s", exc)

    get_sharepoint_list_cache().invalidate()

    return {
        "success": True,
        "synced_downloaded": stats.downloaded_inputs + stats.downloaded_outputs,
        "downloaded_inputs": stats.downloaded_inputs,
        "downloaded_outputs": stats.downloaded_outputs,
        "ingested_files": stats.ingested_files,
        "classified_files": stats.classified_files,
        "total_records": stats.total_records,
        "synced_uploaded": uploads,
        "message": (
            f"Đồng bộ SharePoint hoàn tất! Tải về {stats.downloaded_inputs} input mới, "
            f"{stats.downloaded_outputs} output mới. Đã nạp {stats.ingested_files} file "
            f"với {stats.total_records} bản ghi vào Dashboard."
        ),
    }


@router.post("/input/{filename}/ingest")
async def ingest_existing_input_file(
    filename: str,
    admin: dict = Depends(get_admin_user),
    analytics_repository: FeedbackAnalyticsRepository | None = Depends(
        get_feedback_analytics_repository
    ),
):
    """Đưa một file Excel local đã có trong thư mục Đầu vào vào analytics."""
    input_dir = _work_dir() / "input"
    input_path = _validate_safe_path(input_dir, filename)
    if input_path.suffix.lower() != ".xlsx":
        raise HTTPException(status_code=400, detail="Chỉ chấp nhận file .xlsx")
    if not input_path.is_file():
        raise HTTPException(status_code=404, detail=f"Không tìm thấy file: {filename}")
    if analytics_repository is None:
        raise HTTPException(status_code=503, detail="Kho dữ liệu analytics chưa sẵn sàng")

    try:
        result = ingest_managed_workbook(analytics_repository, input_path)
    except Exception as exc:
        logger.exception("Không thể đưa file %s vào analytics", input_path.name)
        raise HTTPException(
            status_code=422,
            detail="Không thể đưa file vào phân tích",
        ) from exc

    return {
        "filename": result.source_file_name,
        "ingested_rows": result.persisted_rows,
        "source_file_key": result.source_file_key,
        "message": f"Đã đưa {result.persisted_rows} dòng vào phân tích",
    }


class SharePointDeleteItem(BaseModel):
    name: str
    id: str | None = None


class ReclassifyInputRequest(BaseModel):
    filenames: list[str] = []
    items: list[SharePointDeleteItem] = []


async def _reclassify_input_items(
    requested: list[SharePointDeleteItem],
    admin: dict,
) -> dict:
    """Queue classification jobs for one or more existing Input files (local or SharePoint)."""
    import shutil

    from ...time_utils import utc_now_iso
    from ...utils import atomic_write_json
    from .. import deps as _deps

    if not requested:
        raise HTTPException(status_code=400, detail="Danh sách file cần phân loại lại đang trống")

    job_store = _deps.get_classification_job_store()
    if job_store is None:
        raise HTTPException(status_code=503, detail="Classification job store chưa sẵn sàng")

    input_dir = _work_dir() / "input"
    output_dir = _work_dir() / "output"
    input_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    seen_path = _work_dir() / "seen_files.json"
    seen_data: dict = {}
    if seen_path.is_file():
        try:
            loaded = json.loads(seen_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                seen_data = loaded
        except Exception as exc:
            logger.warning("Không thể đọc seen_files.json khi reclassify: %s", exc)

    # Deduplicate requested items by safe filename
    deduped: list[SharePointDeleteItem] = []
    seen_req_names: set[str] = set()
    failed: list[dict] = []

    for item in requested:
        raw_name = (item.name or "").strip()
        if not raw_name:
            continue
        safe_name = Path(raw_name).name
        if not safe_name or safe_name in seen_req_names:
            continue
        seen_req_names.add(safe_name)
        if not safe_name.lower().endswith(".xlsx"):
            failed.append({"name": safe_name, "reason": "Chỉ hỗ trợ phân loại file .xlsx"})
            continue
        deduped.append(SharePointDeleteItem(name=safe_name, id=item.id))

    if not deduped and failed:
        raise HTTPException(status_code=400, detail=failed[0]["reason"])

    # Check currently active (queued/running) jobs to avoid duplicate queueing for same file
    active_by_filename: dict[str, str] = {}
    try:
        for active_job in job_store.list_jobs(include_all=True, limit=200):
            if active_job.get("status") in ("queued", "running") and active_job.get("filename"):
                active_by_filename[active_job["filename"]] = active_job["job_id"]
    except Exception:
        pass

    sp_folder_items_map: dict[str, str] | None = None
    queued_jobs: list[dict] = []
    reset_failed_metric_count = 0

    for item in deduped:
        safe_name = item.name
        if safe_name in active_by_filename:
            queued_jobs.append(
                {
                    "job_id": active_by_filename[safe_name],
                    "filename": safe_name,
                    "status": "already_active",
                }
            )
            continue

        # 1. Locate file locally across input folders
        local_source: Path | None = None
        for candidate_dir in [input_dir, *_get_folder_map().get("input", [])]:
            try:
                cand = _validate_safe_path(candidate_dir, safe_name)
                if cand.is_file():
                    local_source = cand
                    break
            except HTTPException:
                continue

        canonical_input_path = _validate_safe_path(input_dir, safe_name)
        if local_source is not None and local_source.resolve() != canonical_input_path.resolve():
            try:
                shutil.copy2(local_source, canonical_input_path)
                local_source = canonical_input_path
            except Exception as exc:
                logger.warning("Không thể copy file input về work/input: %s", exc)

        # 2. If not found locally, try downloading from SharePoint Input folder
        if local_source is None or not local_source.is_file():
            sp_client = get_sharepoint_client()
            sp_input_folder = get_settings().sp_input_folder
            sp_item_id = item.id
            if not sp_item_id:
                for fid, s_info in seen_data.items():
                    if (
                        isinstance(s_info, dict)
                        and s_info.get("name") == safe_name
                        and not str(fid).startswith("local:")
                    ):
                        sp_item_id = str(fid)
                        break
            if not sp_item_id and sp_client is not None and sp_input_folder:
                if sp_folder_items_map is None:
                    sp_folder_items_map = {}
                    try:
                        sp_items = await run_sync_in_threadpool(
                            sp_client.list_folder_items, sp_input_folder
                        )
                        for sp_it in sp_items:
                            if "folder" not in sp_it and sp_it.get("name") and sp_it.get("id"):
                                sp_folder_items_map[sp_it["name"]] = sp_it["id"]
                    except Exception as exc:
                        logger.warning("Không thể liệt kê SharePoint Input khi reclassify: %s", exc)
                sp_item_id = sp_folder_items_map.get(safe_name)

            if sp_client is not None and sp_item_id:
                try:
                    await run_sync_in_threadpool(
                        sp_client.download_file, sp_item_id, canonical_input_path
                    )
                    if canonical_input_path.is_file():
                        local_source = canonical_input_path
                except Exception as exc:
                    logger.warning(
                        "Không thể tải file %s (id=%s) từ SharePoint để phân loại lại: %s",
                        safe_name,
                        sp_item_id,
                        exc,
                    )

        if local_source is None or not local_source.is_file():
            failed.append(
                {
                    "name": safe_name,
                    "reason": "Không tìm thấy file trong bộ nhớ local hoặc SharePoint",
                }
            )
            continue

        # 3. Create classification job
        job_id = str(uuid.uuid4())
        output_path = output_dir / f"{job_id}_{Path(safe_name).stem}_output.xlsx"
        job_store.create_job(
            job_id=job_id,
            owner_username=admin.get("username", "admin"),
            owner_role=admin.get("role", "admin"),
            filename=safe_name,
            mode="reclassify",
            input_path=local_source,
            output_path=output_path,
        )

        # 4. Update seen_files.json status -> "processing"
        matched_fids = [
            fid
            for fid, s_info in seen_data.items()
            if isinstance(s_info, dict) and s_info.get("name") == safe_name
        ]
        if item.id and item.id in seen_data and item.id not in matched_fids:
            matched_fids.append(item.id)
        if not matched_fids:
            target_fid = item.id or f"local:{safe_name}"
            seen_data[target_fid] = {"name": safe_name}
            matched_fids = [target_fid]

        now_iso = utc_now_iso()
        for fid in matched_fids:
            info = seen_data[fid]
            if info.get("status") == "failed":
                reset_failed_metric_count += 1
                prev_fail = int(info.get("failures", 0) or 0)
                if prev_fail > 0:
                    info["past_failures"] = max(int(info.get("past_failures", 0) or 0), prev_fail)
            info["name"] = safe_name
            info["status"] = "processing"
            info["failures"] = 0
            info["last_attempt"] = now_iso

        queued_jobs.append(
            {
                "job_id": job_id,
                "filename": safe_name,
                "status": "queued",
            }
        )

    if queued_jobs:
        try:
            atomic_write_json(seen_path, seen_data)
        except Exception as exc:
            logger.warning("Không thể lưu seen_files.json khi reclassify: %s", exc)

        if reset_failed_metric_count > 0:
            metrics_path = _work_dir() / "metrics.json"
            if metrics_path.is_file():
                try:
                    metrics_data = json.loads(metrics_path.read_text(encoding="utf-8"))
                    metrics_data["files_failed"] = max(
                        0, int(metrics_data.get("files_failed", 0) or 0) - reset_failed_metric_count
                    )
                    atomic_write_json(metrics_path, metrics_data)
                except Exception as exc:
                    logger.warning("Không thể cập nhật metrics.json khi reclassify: %s", exc)

        get_sharepoint_list_cache().invalidate("input")

        try:
            worker = _deps.get_classification_worker_manager()
            if worker is not None:
                worker.start()
        except Exception as exc:
            logger.warning("Không thể khởi động worker khi reclassify: %s", exc)

    if not queued_jobs and failed:
        raise HTTPException(
            status_code=404,
            detail=failed[0].get("reason", "Không thể đưa file vào hàng đợi phân loại"),
        )

    return {
        "queued": queued_jobs,
        "failed": failed,
        "queued_count": len(queued_jobs),
        "failed_count": len(failed),
        "message": f"Đã đưa {len(queued_jobs)} file vào hàng đợi phân loại lại.",
    }


@router.post("/input/reclassify")
async def reclassify_input_files_bulk(
    payload: ReclassifyInputRequest,
    admin: dict = Depends(get_admin_user),
):
    """Đưa một hoặc nhiều file trong thư mục Input vào hàng đợi phân loại lại (admin only)."""
    requested: list[SharePointDeleteItem] = list(payload.items)
    requested.extend(SharePointDeleteItem(name=name) for name in payload.filenames)
    return await _reclassify_input_items(requested, admin)


@router.post("/input/{filename}/reclassify")
async def reclassify_single_input_file(
    filename: str,
    admin: dict = Depends(get_admin_user),
):
    """Đưa một file trong thư mục Input vào hàng đợi phân loại lại (admin only)."""
    return await _reclassify_input_items([SharePointDeleteItem(name=filename)], admin)


# ---------- Path parameter routes LAST ----------


@router.get("/{folder}")
async def list_files(
    folder: str,
    refresh: bool = False,
    user: dict = Depends(get_current_user),
):
    """Liệt kê các file trong thư mục chỉ định (Duyệt SharePoint Cloud hoặc Local Fallback)."""
    folder_lower = folder.lower()
    cache = get_sharepoint_list_cache()

    if not refresh:
        cached = cache.get(folder_lower)
        if cached is not None:
            return cached

    # Quyết định xem có nên duyệt SharePoint không
    sp_folder_map = {
        "input": get_settings().sp_input_folder,
        "output": get_settings().sp_output_folder,
        "checkpoint": get_settings().sp_checkpoint_folder,
        "keyword": get_settings().sp_keyword_folder,
        "model": get_settings().sp_model_folder,
    }

    sp_folder = sp_folder_map.get(folder_lower)
    sp_client = get_sharepoint_client() if sp_folder else None

    # ─── Đọc seen_files.json để map trạng thái ───
    seen_data = {}
    seen_path = _work_dir() / "seen_files.json"
    if seen_path.is_file():
        try:
            seen_data = json.loads(seen_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    seen_name_map = {
        s_info.get("name"): s_info.get("status", "done")
        for _fid, s_info in seen_data.items()
        if isinstance(s_info, dict) and s_info.get("name")
    }

    active_job_files: set[str] = set()
    if folder_lower == "input":
        try:
            from .. import deps as _deps

            job_store = _deps.get_classification_job_store()
            if job_store is not None:
                for j in job_store.list_jobs(include_all=True, limit=100):
                    j_name = j.get("filename")
                    j_status = j.get("status")
                    if j_name and j_status in ("queued", "running"):
                        active_job_files.add(j_name)
        except Exception:
            pass

    # Nếu có kết nối SharePoint
    if sp_client is not None and sp_folder:
        try:
            items = await run_sync_in_threadpool(sp_client.list_folder_items, sp_folder)
            files = []
            for item in items:
                name = item.get("name", "")
                # Bỏ qua nếu là thư mục
                if "folder" in item:
                    continue

                # Trạng thái mặc định là "new" (hoặc None cho output/checkpoint)
                status = "new" if folder_lower == "input" else None
                item_id = item.get("id")

                # Đối chiếu chéo từ seen_files.json theo ID hoặc Tên file
                if folder_lower == "input" and name in active_job_files:
                    status = "processing"
                elif item_id and item_id in seen_data:
                    status = seen_data[item_id].get("status", "done")
                elif name in seen_name_map:
                    status = seen_name_map[name]

                files.append(
                    {
                        "name": name,
                        "size": item.get("size", 0),
                        "modified": item.get("lastModifiedDateTime", "—"),
                        "extension": Path(name).suffix.lstrip("."),
                        "source_dir": "SharePoint",
                        "source": "sharepoint",
                        "status": status,
                        "id": item_id,
                        "web_url": item.get("webUrl"),
                    }
                )
            if folder_lower == "input":
                try:
                    analytics_repo = get_feedback_analytics_repository()
                    if analytics_repo is not None:
                        await run_sync_in_threadpool(
                            analytics_repo.reconcile_remote_input_files,
                            [f["name"] for f in files if f.get("name")],
                            seen_filenames=list(seen_name_map.keys()),
                        )
                except Exception as reconcile_exc:
                    logger.debug("Could not reconcile remote input files in list_files: %s", reconcile_exc)
            cache.set(folder_lower, files)
            return files
        except Exception as exc:
            logger.warning(
                "Không thể duyệt SharePoint cho thư mục %s, chuyển sang fallback local: %s",
                folder,
                exc,
            )

    # ─── Fallback Local (Keyword, Model hoặc khi SharePoint lỗi) ───
    dirs = _get_folder_map().get(folder_lower)
    if dirs is None:
        raise HTTPException(status_code=400, detail=f"Thư mục không hợp lệ: {folder}")

    files = []
    seen_names: set[str] = set()
    for dir_path in dirs:
        if not dir_path.is_dir():
            continue
        for item in sorted(dir_path.iterdir()):
            if item.is_file() and item.name not in seen_names:
                seen_names.add(item.name)
                info = _file_info(item)
                info["source_dir"] = str(dir_path)

                # Gán trạng thái cho file local
                status = None
                if folder_lower == "input":
                    if item.name in active_job_files:
                        status = "processing"
                    else:
                        status = seen_name_map.get(item.name, "new")
                info["status"] = status
                info["source"] = "local_cache"
                info["id"] = None
                info["web_url"] = None
                files.append(info)

    cache.set(folder_lower, files)
    return files


class BulkDeleteRequest(BaseModel):
    folder: str
    filenames: list[str]


class SharePointDeleteRequest(BaseModel):
    folder: str
    items: list[SharePointDeleteItem] = []
    filenames: list[str] = []


def _get_sharepoint_folder_map() -> dict[str, str]:
    settings = get_settings()
    return {
        "input": settings.sp_input_folder,
        "output": settings.sp_output_folder,
        "checkpoint": settings.sp_checkpoint_folder,
        "keyword": settings.sp_keyword_folder,
        "model": settings.sp_model_folder,
    }


@router.get("/{folder}/{filename}/metadata")
async def get_file_metadata(
    folder: str,
    filename: str,
    user: dict = Depends(get_current_user),
):
    """Trả về metadata chi tiết của file Excel hoặc các định dạng khác (row_count, columns, source)."""
    folder_lower = folder.lower()
    dirs = _get_folder_map().get(folder_lower)
    if dirs is None:
        raise HTTPException(status_code=400, detail=f"Thư mục không hợp lệ: {folder}")

    file_path = None
    source = "local"

    # Resolve local path first
    for dir_path in dirs:
        try:
            candidate = _validate_safe_path(dir_path, filename)
            if candidate.is_file():
                file_path = candidate
                # If path resides in SharePoint cache dir
                if "config_assets" in str(dir_path) or "sharepoint" in str(dir_path).lower():
                    source = "sharepoint"
                break
        except HTTPException:
            continue

    if file_path is None:
        raise HTTPException(status_code=404, detail=f"Không tìm thấy file: {filename}")

    # Check if SharePoint file based on seen_files or webUrl
    seen_path = _work_dir() / "seen_files.json"
    if seen_path.is_file():
        try:
            seen_data = json.loads(seen_path.read_text(encoding="utf-8"))
            for _fid, s_info in seen_data.items():
                if s_info.get("name") == filename:
                    if s_info.get("web_url") or s_info.get("id"):
                        source = "sharepoint"
                    break
        except Exception:
            pass

    ext = file_path.suffix.lower()
    row_count = None
    columns = []

    if ext in {".xlsx", ".xls"}:
        try:
            # Fast columns read
            df_cols = pd.read_excel(file_path, nrows=0)
            columns = list(df_cols.columns)

            # Fast row count using openpyxl for .xlsx
            if ext == ".xlsx":
                import openpyxl

                wb = openpyxl.load_workbook(file_path, read_only=True)
                sheet = wb.active
                row_count = max(0, sheet.max_row - 1)
                wb.close()
            else:
                df = pd.read_excel(file_path)
                row_count = len(df)
        except Exception as exc:
            logger.warning("Không thể đọc metadata của file Excel %s: %s", filename, exc)
            # fallback
            row_count = None
            columns = []

    return {"row_count": row_count, "columns": columns, "source": source}


def _cleanup_deleted_files_state(
    deleted_filenames: list[str],
    analytics_repository: FeedbackAnalyticsRepository | None,
) -> int:
    """Purge analytics records, cancel active jobs, and clean seen_files.json for deleted files."""
    if not deleted_filenames:
        return 0
    from ...analytics.repository import _expand_candidate_filenames
    from ...utils import atomic_write_json

    candidates = _expand_candidate_filenames(deleted_filenames)
    purged_rows = 0
    if analytics_repository is not None:
        try:
            purged_rows = analytics_repository.purge_file_records(candidates)
        except Exception as exc:
            logger.warning("Could not purge analytics records for deleted files %s: %s", candidates, exc)

    try:
        from .. import deps as _deps

        job_store = _deps.get_classification_job_store()
        if job_store is not None:
            for job in job_store.list_jobs(include_all=True, limit=200):
                if job.get("filename") in candidates and job.get("status") in ("queued", "running"):
                    job_store.cancel_job(str(job["job_id"]))
    except Exception as exc:
        logger.debug("Could not cancel active jobs for deleted files: %s", exc)

    seen_path = _work_dir() / "seen_files.json"
    if seen_path.is_file():
        try:
            seen_data = json.loads(seen_path.read_text(encoding="utf-8"))
            if isinstance(seen_data, dict):
                to_remove = [
                    fid
                    for fid, info in seen_data.items()
                    if (isinstance(info, dict) and info.get("name") in candidates)
                    or str(fid) in {f"local:{c}" for c in candidates}
                ]
                if to_remove:
                    for fid in to_remove:
                        seen_data.pop(fid, None)
                    atomic_write_json(seen_path, seen_data)
        except Exception as exc:
            logger.debug("Could not clean seen_files.json after file delete: %s", exc)

    return purged_rows


@router.post("/bulk-delete")
async def bulk_delete_files(
    payload: BulkDeleteRequest,
    admin: dict = Depends(get_admin_user),
    analytics_repository: FeedbackAnalyticsRepository | None = Depends(
        get_feedback_analytics_repository
    ),
):
    """Xóa hàng loạt file được chọn trong thư mục chỉ định (admin only)."""
    folder = payload.folder.lower()
    filenames = payload.filenames
    dirs = _get_folder_map().get(folder)
    if dirs is None:
        raise HTTPException(status_code=400, detail=f"Thư mục không hợp lệ: {folder}")
    if not filenames:
        raise HTTPException(status_code=400, detail="Danh sách file xóa trống")

    deleted = []
    failed = []

    for name in filenames:
        file_deleted = False
        safe_base = Path(name).name
        for dir_path in dirs:
            try:
                candidate = _validate_safe_path(dir_path, name)
                if candidate.is_file():
                    candidate.unlink()
                    file_deleted = True
                if folder in ("input", "output") and dir_path.is_dir() and safe_base:
                    for item in list(dir_path.iterdir()):
                        if item.is_file() and (
                            item.name.endswith(f"_{safe_base}")
                            or item.name.endswith(f"_output_{safe_base}")
                        ):
                            item.unlink(missing_ok=True)
                            file_deleted = True
                if file_deleted:
                    deleted.append(name)
                    break
            except Exception as exc:
                failed.append({"name": name, "reason": str(exc)})
                file_deleted = True
                break
        if not file_deleted:
            failed.append({"name": name, "reason": "File không tồn tại"})

    purged_rows = 0
    if deleted and folder in ("input", "output"):
        purged_rows = await run_sync_in_threadpool(
            _cleanup_deleted_files_state, deleted, analytics_repository
        )

    success_count = len(deleted)
    total_count = len(filenames)
    get_sharepoint_list_cache().invalidate(folder)
    return {
        "deleted": deleted,
        "failed": failed,
        "purged_analytics_rows": purged_rows,
        "delete_scope": "local_cache",
        "message": (
            f"Đã xóa local/cache {success_count}/{total_count} file. "
            "SharePoint không bị xóa; dùng Xóa trên SharePoint nếu cần xóa file Cloud."
        ),
    }


@router.post("/sharepoint-delete")
async def delete_sharepoint_files(
    payload: SharePointDeleteRequest,
    admin: dict = Depends(get_admin_user),
    analytics_repository: FeedbackAnalyticsRepository | None = Depends(
        get_feedback_analytics_repository
    ),
):
    """Delete selected files from SharePoint explicitly. Local/cache delete is separate."""
    folder = payload.folder.lower()
    sp_folder = _get_sharepoint_folder_map().get(folder)
    if not sp_folder:
        raise HTTPException(
            status_code=400, detail=f"Thư mục không hỗ trợ SharePoint delete: {folder}"
        )

    requested: list[SharePointDeleteItem] = list(payload.items)
    requested.extend(SharePointDeleteItem(name=name) for name in payload.filenames)
    if not requested:
        raise HTTPException(status_code=400, detail="Danh sách file xóa trống")

    sp_client = get_sharepoint_client()
    if sp_client is None:
        raise HTTPException(
            status_code=503, detail="SharePoint chưa được cấu hình hoặc không kết nối được"
        )

    ids_by_name: dict[str, str] = {}
    missing_id = [item.name for item in requested if not item.id]
    if missing_id:
        try:
            for item in sp_client.list_folder_items(sp_folder):
                if "file" in item and item.get("name"):
                    ids_by_name[item["name"]] = item.get("id", "")
        except Exception as exc:
            raise HTTPException(
                status_code=502, detail=f"Không thể liệt kê SharePoint: {exc}"
            ) from exc

    remote_deleted: list[str] = []
    failed: list[dict] = []

    for item in requested:
        item_id = item.id or ids_by_name.get(item.name)
        if not item_id:
            failed.append({"name": item.name, "reason": "Không tìm thấy SharePoint item id"})
            continue
        try:
            sp_client.delete_item(item_id)
            remote_deleted.append(item.name)
        except SharePointError as exc:
            failed.append({"name": item.name, "reason": str(exc)})
        except Exception as exc:
            failed.append({"name": item.name, "reason": f"Lỗi không xác định: {exc}"})

    purged_rows = 0
    if remote_deleted and folder in ("input", "output"):
        purged_rows = await run_sync_in_threadpool(
            _cleanup_deleted_files_state, remote_deleted, analytics_repository
        )

    get_sharepoint_list_cache().invalidate(folder)
    return {
        "delete_scope": "sharepoint",
        "remote_deleted": remote_deleted,
        "failed": failed,
        "purged_analytics_rows": purged_rows,
        "message": f"Đã xóa SharePoint {len(remote_deleted)}/{len(requested)} file.",
    }


MAX_PREVIEW_BYTES = 512_000  # 500 KB cap for JSON / text files
MAX_TEXT_LINES = 200
EXCEL_EXTS = {".xlsx", ".xls"}
CSV_EXTS = {".csv"}
JSON_EXTS = {".json"}
TEXT_EXTS = {".txt", ".log", ".md", ".yaml", ".yml", ".cfg", ".ini", ".toml"}


def _safe_dataframe_records(df: pd.DataFrame) -> list[dict]:
    """Convert a DataFrame to JSON-safe list of dicts.

    Replaces NaN/Inf/-Inf with empty strings so ``json.dumps`` never crashes
    with ``ValueError: Out of range float values are not JSON compliant``.
    """
    import numpy as np

    df = df.fillna("")
    # Replace Inf / -Inf with string representation
    df = df.replace([np.inf, -np.inf], "Inf")
    return df.to_dict(orient="records")


@router.get("/{folder}/{filename}/preview")
async def preview_file(
    folder: str, filename: str, max_rows: int | None = None, user: dict = Depends(get_current_user)
):
    """Đọc preview file — hỗ trợ Excel, CSV, JSON, text (SharePoint Cloud hoặc Local Fallback)."""
    folder_lower = folder.lower()

    # ─── Lấy file trên SharePoint nếu được hỗ trợ ───
    sp_folder_map = {
        "input": get_settings().sp_input_folder,
        "output": get_settings().sp_output_folder,
        "checkpoint": get_settings().sp_checkpoint_folder,
    }

    sp_folder = sp_folder_map.get(folder_lower)
    sp_client = get_sharepoint_client() if sp_folder else None

    file_path: Path | None = None
    temp_downloaded_path: Path | None = None

    if sp_client is not None and sp_folder:
        try:
            items = await run_sync_in_threadpool(sp_client.list_folder_items, sp_folder)
            target_item = None
            for item in items:
                if item.get("name") == filename:
                    target_item = item
                    break

            if target_item:
                # Tải file về thư mục staging tạm thời qua threadpool
                temp_path = _staging_download_path(filename)
                await run_sync_in_threadpool(sp_client.download_file, target_item["id"], temp_path)
                file_path = temp_path
                temp_downloaded_path = temp_path
        except Exception as exc:
            logger.warning("Không thể tải preview từ SharePoint cho %s: %s", filename, exc)

    # ─── Fallback Local (Keyword, Model hoặc khi SharePoint lỗi) ───
    if file_path is None:
        dirs = _get_folder_map().get(folder_lower)
        if dirs is None:
            raise HTTPException(status_code=400, detail=f"Thư mục không hợp lệ: {folder}")

        for dir_path in dirs:
            # Validate path traversal before looking up file
            safe_name = Path(filename).name
            if not safe_name:
                raise HTTPException(status_code=400, detail="Tên file không hợp lệ")
            candidate = (dir_path / safe_name).resolve()
            try:
                candidate.relative_to(dir_path.resolve())
            except ValueError:
                raise HTTPException(status_code=400, detail="Tên file không hợp lệ")  # noqa: B904
            if candidate.is_file():
                file_path = candidate
                break

    if file_path is None:
        raise HTTPException(status_code=404, detail=f"Không tìm thấy file: {filename}")

    try:
        ext = file_path.suffix.lower()

        # ── Excel ──
        if ext in EXCEL_EXTS:
            try:
                limit_rows = max_rows or 200
                df = await run_sync_in_threadpool(pd.read_excel, file_path, nrows=limit_rows)
                return {
                    "type": "table",
                    "filename": filename,
                    "total_columns": len(df.columns),
                    "total_rows": len(df),
                    "columns": list(df.columns),
                    "data": _safe_dataframe_records(df),
                }
            except Exception as exc:
                raise HTTPException(
                    status_code=422,
                    detail=f"Không thể đọc file Excel: {exc}",
                ) from exc

        # ── CSV ──
        if ext in CSV_EXTS:
            try:
                df = (
                    pd.read_csv(file_path, nrows=max_rows, encoding="utf-8")
                    if max_rows
                    else pd.read_csv(file_path, encoding="utf-8")
                )
            except UnicodeDecodeError:
                df = (
                    pd.read_csv(file_path, nrows=max_rows, encoding="cp1252")
                    if max_rows
                    else pd.read_csv(file_path, encoding="cp1252")
                )
            except Exception as exc:
                raise HTTPException(
                    status_code=422,
                    detail=f"Không thể đọc file CSV: {exc}",
                ) from exc
            return {
                "type": "table",
                "filename": filename,
                "total_columns": len(df.columns),
                "total_rows": len(df),
                "columns": list(df.columns),
                "data": _safe_dataframe_records(df),
            }

        # ── JSON ──
        if ext in JSON_EXTS:
            try:
                raw = file_path.read_bytes()
                truncated = len(raw) > MAX_PREVIEW_BYTES
                text = raw[:MAX_PREVIEW_BYTES].decode("utf-8", errors="replace")
                content = json.loads(text) if not truncated else text
                return {
                    "type": "json",
                    "filename": filename,
                    "content": content,
                    "truncated": truncated,
                    "size": len(raw),
                }
            except json.JSONDecodeError as exc:
                return {
                    "type": "json",
                    "filename": filename,
                    "content": raw[:MAX_PREVIEW_BYTES].decode("utf-8", errors="replace"),
                    "truncated": False,
                    "parse_error": str(exc),
                    "size": len(raw),
                }
            except Exception as exc:
                raise HTTPException(
                    status_code=422,
                    detail=f"Không thể đọc file JSON: {exc}",
                ) from exc

        # ── Text-based ──
        if ext in TEXT_EXTS:
            try:
                raw = file_path.read_bytes()
                truncated = len(raw) > MAX_PREVIEW_BYTES
                text = raw[:MAX_PREVIEW_BYTES].decode("utf-8", errors="replace")
                lines = text.splitlines()
                if len(lines) > MAX_TEXT_LINES:
                    lines = lines[:MAX_TEXT_LINES]
                    truncated = True
                return {
                    "type": "text",
                    "filename": filename,
                    "content": "\n".join(lines),
                    "truncated": truncated,
                    "total_lines": len(text.splitlines()),
                    "size": len(raw),
                }
            except Exception as exc:
                raise HTTPException(
                    status_code=422,
                    detail=f"Không thể đọc file text: {exc}",
                ) from exc

        # ── Unsupported ──
        return {
            "type": "unsupported",
            "filename": filename,
            "extension": ext,
            "message": f"Không hỗ trợ xem trước file {ext}",
        }
    finally:
        # Xóa file tạm sau khi đã đọc xong
        if temp_downloaded_path and temp_downloaded_path.is_file():
            try:
                temp_downloaded_path.unlink()
            except Exception:
                pass


def cleanup_file(path: Path) -> None:
    """Xóa file tạm trong thư mục staging sau khi tải về thành công."""
    try:
        if path.is_file():
            path.unlink()
            logger.info("Đã xóa file staging tạm thời: %s", path)
    except Exception as exc:
        logger.warning("Không thể xóa file staging tạm thời %s: %s", path, exc)


@router.get("/{folder}/{filename}/download")
async def download_file(
    folder: str,
    filename: str,
    background_tasks: BackgroundTasks,
    user: dict = Depends(get_current_user),
):
    """Tải file — hỗ trợ Excel, CSV, JSON, text (SharePoint Cloud hoặc Local Fallback)."""
    folder_lower = folder.lower()

    # Quyết định xem có nên tải từ SharePoint không
    sp_folder_map = {
        "input": get_settings().sp_input_folder,
        "output": get_settings().sp_output_folder,
        "checkpoint": get_settings().sp_checkpoint_folder,
    }

    sp_folder = sp_folder_map.get(folder_lower)
    sp_client = get_sharepoint_client() if sp_folder else None

    file_path: Path | None = None
    temp_downloaded_path: Path | None = None

    if sp_client is not None and sp_folder:
        try:
            items = sp_client.list_folder_items(sp_folder)
            target_item = None
            for item in items:
                if item.get("name") == filename:
                    target_item = item
                    break

            if target_item:
                temp_path = _staging_download_path(filename)
                sp_client.download_file(target_item["id"], temp_path)
                file_path = temp_path
                temp_downloaded_path = temp_path
        except Exception as exc:
            logger.warning("Không thể tải file từ SharePoint cho %s: %s", filename, exc)

    # Fallback Local
    if file_path is None:
        dirs = _get_folder_map().get(folder_lower)
        if dirs is None:
            raise HTTPException(status_code=400, detail=f"Thư mục không hợp lệ: {folder}")

        for dir_path in dirs:
            safe_name = Path(filename).name
            if not safe_name:
                raise HTTPException(status_code=400, detail="Tên file không hợp lệ")
            candidate = (dir_path / safe_name).resolve()
            try:
                candidate.relative_to(dir_path.resolve())
            except ValueError:
                raise HTTPException(status_code=400, detail="Tên file không hợp lệ") from None
            if candidate.is_file():
                file_path = candidate
                break

    if file_path is None or not file_path.is_file():
        raise HTTPException(status_code=404, detail=f"Không tìm thấy file: {filename}")

    if temp_downloaded_path:
        background_tasks.add_task(cleanup_file, temp_downloaded_path)

    # Determine media type based on extension
    ext = file_path.suffix.lower()
    media_type = "application/octet-stream"
    if ext in EXCEL_EXTS:
        media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    elif ext in CSV_EXTS:
        media_type = "text/csv"
    elif ext in JSON_EXTS:
        media_type = "application/json"
    elif ext in TEXT_EXTS:
        media_type = "text/plain"

    return FileResponse(
        path=file_path,
        filename=Path(filename).name,
        media_type=media_type,
    )
