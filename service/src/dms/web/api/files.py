"""File management endpoints."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

import pandas as pd
from fastapi import APIRouter, HTTPException, UploadFile

from ...settings import SERVICE_DIR

logger = logging.getLogger("dms-web")

router = APIRouter(prefix="/api/files", tags=["files"])

WORK_DIR = SERVICE_DIR / "work"

# Mapping of logical folder names to physical directories (checked in order)
FOLDER_MAP: dict[str, list[Path]] = {
    "input": [WORK_DIR / "input", SERVICE_DIR / "Input"],
    "output": [WORK_DIR / "output", SERVICE_DIR / "Output"],
    "checkpoint": [WORK_DIR / "checkpoint", SERVICE_DIR / "Check_Point"],
    "keyword": [SERVICE_DIR / "Keyword"],
    "model": [SERVICE_DIR / "Model"],
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
    for folder_name, dirs in FOLDER_MAP.items():
        children: list[dict] = []
        for dir_path in dirs:
            if not dir_path.is_dir():
                continue
            dir_entry = {
                "path": str(dir_path),
                "files": [],
            }
            for item in sorted(dir_path.iterdir()):
                if item.is_file():
                    dir_entry["files"].append(_file_info(item))
            children.append(dir_entry)
        tree[folder_name] = children
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


# ---------- Path parameter routes LAST ----------


@router.get("/{folder}")
async def list_files(folder: str):
    """Liệt kê các file trong thư mục chỉ định."""
    dirs = FOLDER_MAP.get(folder.lower())
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
                files.append(info)

    return files


MAX_PREVIEW_BYTES = 512_000  # 500 KB cap for JSON / text files
MAX_TEXT_LINES = 200
EXCEL_EXTS = {".xlsx", ".xls"}
CSV_EXTS = {".csv"}
JSON_EXTS = {".json"}
TEXT_EXTS = {".txt", ".log", ".md", ".yaml", ".yml", ".cfg", ".ini", ".toml"}


def _safe_dataframe_records(df: "pd.DataFrame") -> list[dict]:
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
    """Đọc preview file — hỗ trợ Excel, CSV, JSON, text."""
    dirs = FOLDER_MAP.get(folder.lower())
    if dirs is None:
        raise HTTPException(status_code=400, detail=f"Thư mục không hợp lệ: {folder}")

    file_path: Path | None = None
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

