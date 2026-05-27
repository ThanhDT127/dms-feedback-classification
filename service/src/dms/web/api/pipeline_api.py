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
