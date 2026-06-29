"""File management endpoints."""

from __future__ import annotations

import io
import json
import logging
from datetime import datetime
from pathlib import Path

import pandas as pd
from fastapi import APIRouter, BackgroundTasks, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse

from ...settings import get_settings
from ..deps import get_sharepoint_client

logger = logging.getLogger("dms-web")

router = APIRouter(prefix="/api/files", tags=["files"])

WORK_DIR = get_settings().work_dir
FOLDER_MAP: dict[str, list[Path]] = {}


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


def _file_info(path: Path) -> dict:
    """Build a file info dict from a path."""
    stat = path.stat()
    return {
        "name": path.name,
        "size": stat.st_size,
        "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
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
async def get_folder_tree():
    """Trả về cấu trúc cây thư mục."""
    tree: dict = {}
    folder_map = _get_folder_map()
    for folder_name in folder_map.keys():
        files = await list_files(folder_name)
        tree[folder_name] = [
            {
                "path": files[0].get("source_dir", folder_name) if files else folder_name,
                "files": files,
            }
        ]
    return tree


@router.get("/seen", name="seen_files")
async def get_seen_files():
    """Trả về nội dung seen_files.json."""
    seen_path = WORK_DIR / "seen_files.json"
    if not seen_path.is_file():
        return {}
    try:
        return json.loads(seen_path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Lỗi đọc seen_files.json: %s", exc)
        return {"error": str(exc)}


@router.post("/upload")
async def upload_file(file: UploadFile):
    """Upload file .xlsx vào thư mục work/input/."""
    if not file.filename:
        raise HTTPException(status_code=400, detail="Thiếu tên file")

    if not file.filename.lower().endswith(".xlsx"):
        raise HTTPException(
            status_code=400,
            detail="Chỉ chấp nhận file .xlsx",
        )

    input_dir = WORK_DIR / "input"
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

    return {
        "filename": dest.name,
        "size": len(content),
        "message": f"Đã upload thành công: {dest.name}",
    }


@router.get("/template")
async def get_template():
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
async def sync_sharepoint():
    """Đồng bộ thủ công hai chiều: tải về Input mới và đẩy lên Output mới."""
    sp_client = get_sharepoint_client()
    settings = get_settings()
    if sp_client is None:
        raise HTTPException(
            status_code=503,
            detail="Không thể kết nối SharePoint (chưa cấu hình Azure credentials)",
        )

    downloads = 0
    uploads = 0

    # Đọc seen_files.json để đối chiếu tránh tải lại file đã xử lý xong
    seen_data = {}
    seen_path = settings.work_dir / "seen_files.json"
    if seen_path.is_file():
        try:
            seen_data = json.loads(seen_path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Lỗi đọc seen_files.json trong sync endpoint: %s", exc)

    # 1. Tải về file Input mới
    try:
        sp_input_items = sp_client.list_folder_items(settings.sp_input_folder)
        local_input_dir = settings.work_dir / "input"
        local_input_dir.mkdir(parents=True, exist_ok=True)

        for item in sp_input_items:
            if "folder" in item:
                continue
            name = item.get("name", "")
            if not name.lower().endswith(".xlsx"):
                continue

            # Kiểm tra xem file đã từng được xử lý chưa (dựa theo ID hoặc tên)
            item_id = item.get("id")
            if item_id and item_id in seen_data:
                status = seen_data[item_id].get("status")
                if status in ("done", "failed"):
                    continue

            # Fallback đối chiếu theo tên file
            already_processed = False
            for _fid, s_info in seen_data.items():
                if s_info.get("name") == name and s_info.get("status") in ("done", "failed"):
                    already_processed = True
                    break
            if already_processed:
                continue

            local_path = local_input_dir / name
            if not local_path.exists():
                logger.info("Manual Sync: Downloading new input file %s", name)
                sp_client.download_file(item["id"], local_path)
                downloads += 1
    except Exception as exc:
        logger.error("Lỗi đồng bộ Input từ SharePoint: %s", exc)
        raise HTTPException(
            status_code=502,
            detail=f"Lỗi tải file từ SharePoint: {exc}",
        ) from exc

    # 2. Đẩy lên file Output mới
    try:
        local_output_dir = settings.work_dir / "output"
        if local_output_dir.is_dir():
            sp_output_items = sp_client.list_folder_items(settings.sp_output_folder)
            sp_output_names = {item["name"] for item in sp_output_items if "folder" not in item}

            for path in local_output_dir.iterdir():
                if path.is_file() and path.suffix.lower() == ".xlsx":
                    if path.name not in sp_output_names:
                        logger.info("Manual Sync: Uploading completed output file %s", path.name)
                        sp_client.upload_file(path, settings.sp_output_folder)
                        uploads += 1
    except Exception as exc:
        logger.error("Lỗi đồng bộ Output lên SharePoint: %s", exc)
        raise HTTPException(
            status_code=502,
            detail=f"Lỗi đẩy file lên SharePoint: {exc}",
        ) from exc

    return {
        "success": True,
        "synced_downloaded": downloads,
        "synced_uploaded": uploads,
        "message": f"Đồng bộ SharePoint hoàn tất! Tải về {downloads} file đầu vào mới, tải lên {uploads} file kết quả mới.",
    }


# ---------- Path parameter routes LAST ----------


@router.get("/{folder}")
async def list_files(folder: str):
    """Liệt kê các file trong thư mục chỉ định (Duyệt SharePoint Cloud hoặc Local Fallback)."""
    folder_lower = folder.lower()

    # Quyết định xem có nên duyệt SharePoint không
    sp_folder_map = {
        "input": get_settings().sp_input_folder,
        "output": get_settings().sp_output_folder,
        "checkpoint": get_settings().sp_checkpoint_folder,
    }

    sp_folder = sp_folder_map.get(folder_lower)
    sp_client = get_sharepoint_client() if sp_folder else None

    # ─── Đọc seen_files.json để map trạng thái ───
    seen_data = {}
    seen_path = WORK_DIR / "seen_files.json"
    if seen_path.is_file():
        try:
            seen_data = json.loads(seen_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    # Nếu có kết nối SharePoint
    if sp_client is not None and sp_folder:
        try:
            items = sp_client.list_folder_items(sp_folder)
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
                if item_id and item_id in seen_data:
                    status = seen_data[item_id].get("status", "done")
                else:
                    # Fallback theo tên file nếu không lưu ID trong seen
                    for _fid, s_info in seen_data.items():
                        if s_info.get("name") == name:
                            status = s_info.get("status", "done")
                            break

                files.append(
                    {
                        "name": name,
                        "size": item.get("size", 0),
                        "modified": item.get("lastModifiedDateTime", "—"),
                        "extension": Path(name).suffix.lstrip("."),
                        "source_dir": "SharePoint",
                        "status": status,
                        "id": item_id,
                        "web_url": item.get("webUrl"),
                    }
                )
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

    files: list[dict] = []
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
                    status = "new"
                    for _fid, s_info in seen_data.items():
                        if s_info.get("name") == item.name:
                            status = s_info.get("status", "done")
                            break
                info["status"] = status
                info["web_url"] = None
                files.append(info)

    return files


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
async def preview_file(folder: str, filename: str, max_rows: int = 20):
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
            items = sp_client.list_folder_items(sp_folder)
            target_item = None
            for item in items:
                if item.get("name") == filename:
                    target_item = item
                    break

            if target_item:
                # Tải file về thư mục staging tạm thời
                staging_dir = WORK_DIR / "staging"
                staging_dir.mkdir(parents=True, exist_ok=True)

                # Sanitize filename to avoid path traversal in staging
                safe_name = Path(filename).name
                temp_path = staging_dir / safe_name

                sp_client.download_file(target_item["id"], temp_path)
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
                df = pd.read_excel(file_path, nrows=max_rows)
                return {
                    "type": "table",
                    "filename": filename,
                    "total_columns": len(df.columns),
                    "preview_rows": max_rows,
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
                df = pd.read_csv(file_path, nrows=max_rows, encoding="utf-8")
            except UnicodeDecodeError:
                df = pd.read_csv(file_path, nrows=max_rows, encoding="cp1252")
            except Exception as exc:
                raise HTTPException(
                    status_code=422,
                    detail=f"Không thể đọc file CSV: {exc}",
                ) from exc
            return {
                "type": "table",
                "filename": filename,
                "total_columns": len(df.columns),
                "preview_rows": max_rows,
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
async def download_file(folder: str, filename: str, background_tasks: BackgroundTasks):
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
                staging_dir = WORK_DIR / "staging"
                staging_dir.mkdir(parents=True, exist_ok=True)

                safe_name = Path(filename).name
                temp_path = staging_dir / safe_name

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
                raise HTTPException(status_code=400, detail="Tên file không hợp lệ")
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
        filename=file_path.name,
        media_type=media_type,
    )
