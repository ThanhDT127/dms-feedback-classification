"""Pipeline information endpoints: labels, keywords, brands, products."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pandas as pd
from fastapi import APIRouter, HTTPException

from .. import deps

logger = logging.getLogger("dms-web")

router = APIRouter(prefix="/api/pipeline", tags=["pipeline"])


# ---------- Labels ----------


@router.get("/labels")
async def get_labels():
    """Trả về MINOR_ORDER, MINOR_TO_MAJOR và LABEL_DEFINITIONS."""
    from ...pipeline.issue_classifier import (
        LABEL_DEFINITIONS,
        MINOR_ORDER,
        MINOR_TO_MAJOR,
    )

    return {
        "minor_order": MINOR_ORDER,
        "minor_to_major": MINOR_TO_MAJOR,
        "label_definitions": LABEL_DEFINITIONS,
    }


# ---------- Keywords ----------


@router.get("/keywords/raw")
async def get_raw_keywords():
    """Trả về toàn bộ nội dung gốc của kw_map.json."""
    settings = deps.get_settings()
    if settings is None:
        return {"error": "Settings chưa được cấu hình"}

    kw_map_path = settings.kw_map_path
    if not kw_map_path.is_file():
        raise HTTPException(status_code=404, detail=f"Không tìm thấy kw_map.json tại {kw_map_path}")

    try:
        return json.loads(kw_map_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Lỗi đọc kw_map.json: {exc}",
        ) from exc


@router.get("/keywords")
async def get_keywords():
    """Trả về keyword_hints từ kw_map.json."""
    from ...pipeline.issue_classifier import keyword_hints

    settings = deps.get_settings()
    if settings is None:
        return {"error": "Settings chưa được cấu hình"}

    kw_map_path = settings.kw_map_path
    if not kw_map_path.is_file():
        return {"error": f"Không tìm thấy kw_map.json tại {kw_map_path}"}

    try:
        kw_map = json.loads(kw_map_path.read_text(encoding="utf-8"))
        return keyword_hints(kw_map)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Lỗi đọc kw_map.json: {exc}",
        ) from exc


# ---------- Brands ----------


@router.get("/brands")
async def get_brands():
    """Trả về brand_hints từ kw_map.json."""
    from ...pipeline.issue_classifier import brand_hints

    settings = deps.get_settings()
    if settings is None:
        return {"error": "Settings chưa được cấu hình"}

    kw_map_path = settings.kw_map_path
    if not kw_map_path.is_file():
        return {"error": f"Không tìm thấy kw_map.json tại {kw_map_path}"}

    try:
        kw_map = json.loads(kw_map_path.read_text(encoding="utf-8"))
        return brand_hints(kw_map)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Lỗi đọc kw_map.json: {exc}",
        ) from exc


# ---------- Products ----------


@router.get("/products")
async def get_products():
    """Trả về tóm tắt danh mục sản phẩm."""
    settings = deps.get_settings()
    if settings is None:
        return {"error": "Settings chưa được cấu hình"}

    products_path = settings.df_products_path
    if not products_path.is_file():
        return {"error": f"Không tìm thấy file sản phẩm tại {products_path}"}

    try:
        df = pd.read_excel(products_path)

        categories: list[str] = []
        product_lines: list[str] = []
        sample_models: list[str] = []

        if "Sản phẩm" in df.columns:
            categories = sorted(df["Sản phẩm"].dropna().unique().tolist())
        if "Dòng SP" in df.columns:
            product_lines = sorted(df["Dòng SP"].dropna().unique().tolist())
        if "Model" in df.columns:
            sample_models = df["Model"].dropna().head(20).tolist()

        return {
            "total_products": len(df),
            "categories": categories,
            "product_lines": product_lines,
            "sample_models": sample_models,
            "file_path": str(products_path),
        }
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Lỗi đọc danh mục sản phẩm: {exc}",
        ) from exc


@router.put("/keywords")
async def save_keywords(data: dict):
    """Lưu danh sách keyword gợi ý vào file kw_map.json."""
    settings = deps.get_settings()
    if settings is None:
        raise HTTPException(status_code=400, detail="Settings chưa được cấu hình")
        
    kw_map_path = settings.kw_map_path
    try:
        # If the directory doesn't exist, create it
        kw_map_path.parent.mkdir(parents=True, exist_ok=True)
        # Write to file with indent=2, ensure_ascii=False
        kw_map_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        
        # Reset dependencies so that loaded keyword_hints are re-initialized
        deps.reset()
        return {"success": True, "message": "Đã lưu từ khóa gợi ý thành công."}
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Lỗi khi lưu kw_map.json: {exc}",
        ) from exc


@router.get("/products/list")
async def list_products():
    """Trả về toàn bộ danh sách sản phẩm theo từng sheet trong file Excel."""
    settings = deps.get_settings()
    if settings is None:
        raise HTTPException(status_code=400, detail="Settings chưa được cấu hình")
        
    products_path = settings.df_products_path
    if not products_path.is_file():
        raise HTTPException(status_code=404, detail="Không tìm thấy file sản phẩm Excel.")
        
    try:
        sheets_data = {}
        sheet_names = []
        with pd.ExcelFile(products_path) as xl:
            sheet_names = list(xl.sheet_names)
            for sheet_name in sheet_names:
                df = pd.read_excel(xl, sheet_name)
                df = df.fillna("")
                sheets_data[sheet_name] = {
                    "columns": list(df.columns),
                    "products": df.to_dict(orient="records")
                }
        return {
            "sheets": sheets_data,
            "sheet_names": sheet_names,
            "file_path": str(products_path),
        }
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Lỗi đọc danh mục sản phẩm Excel: {exc}",
        ) from exc


@router.put("/products")
async def save_products(payload: dict):
    """Lưu danh sách sản phẩm mới cho một sheet cụ thể vào file Excel Phân Chia Nhóm Sản Phẩm V2.xlsx, bảo toàn các sheet khác."""
    settings = deps.get_settings()
    if settings is None:
        raise HTTPException(status_code=400, detail="Settings chưa được cấu hình")
        
    sheet_name = payload.get("sheet_name")
    products = payload.get("products")
    
    if not sheet_name or products is None:
        raise HTTPException(status_code=400, detail="Thiếu thông tin sheet_name hoặc danh sách sản phẩm trong payload.")
        
    products_path = settings.df_products_path
    try:
        # Create parent directories if they don't exist
        products_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Load all existing sheets first to preserve them
        sheets_data = {}
        if products_path.is_file():
            with pd.ExcelFile(products_path) as xl:
                for name in xl.sheet_names:
                    sheets_data[name] = pd.read_excel(xl, name)
                    
        # Update the target sheet
        sheets_data[sheet_name] = pd.DataFrame(products)
        
        # Save all sheets back to excel using openpyxl engine
        with pd.ExcelWriter(products_path, engine='openpyxl') as writer:
            for name, df_sheet in sheets_data.items():
                df_sheet.to_excel(writer, sheet_name=name, index=False)
                
        # Reset dependencies so cached RAG index is re-initialized
        deps.reset()
        return {"success": True, "message": f"Đã lưu danh mục sản phẩm của sheet '{sheet_name}' thành công."}
    except PermissionError:
        raise HTTPException(
            status_code=400,
            detail="Không thể lưu. File Excel đang mở hoặc bị khóa bởi một tiến trình khác (vui lòng đóng file Excel trên máy chủ và thử lại).",
        )
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Lỗi khi lưu file sản phẩm Excel: {exc}",
        ) from exc
