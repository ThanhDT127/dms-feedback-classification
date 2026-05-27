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
    dest = input_dir / file.filename

    try:
        content = await file.read()
        dest.write_bytes(content)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Lỗi lưu file: {exc}",
        ) from exc

    return {
        "filename": file.filename,
        "size": len(content),
        "path": str(dest),
        "message": f"Đã upload thành công: {file.filename}",
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


@router.get("/{folder}/{filename}/preview")
async def preview_file(folder: str, filename: str, max_rows: int = 20):
    """Đọc preview 20 dòng đầu tiên của file Excel."""
    dirs = FOLDER_MAP.get(folder.lower())
    if dirs is None:
        raise HTTPException(status_code=400, detail=f"Thư mục không hợp lệ: {folder}")

    file_path: Path | None = None
    for dir_path in dirs:
        candidate = dir_path / filename
        if candidate.is_file():
            file_path = candidate
            break

    if file_path is None:
        raise HTTPException(status_code=404, detail=f"Không tìm thấy file: {filename}")

    try:
        df = pd.read_excel(file_path, nrows=max_rows)
        # Replace NaN with None for clean JSON serialization
        df = df.where(df.notna(), None)
        return {
            "filename": filename,
            "total_columns": len(df.columns),
            "preview_rows": max_rows,
            "columns": list(df.columns),
            "data": df.to_dict(orient="records"),
        }
    except Exception as exc:
        raise HTTPException(
            status_code=422,
            detail=f"Không thể đọc file Excel: {exc}",
        ) from exc
