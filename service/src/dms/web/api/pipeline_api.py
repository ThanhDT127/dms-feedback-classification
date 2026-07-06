"""Pipeline information endpoints: labels, keywords, brands, products."""

from __future__ import annotations

import json
import logging

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException

from .. import deps
from ..deps import get_admin_user, get_current_user

logger = logging.getLogger("dms-web")

router = APIRouter(prefix="/api/pipeline", tags=["pipeline"])


def _apply_label_payload(payload: dict) -> None:
    from ...pipeline.issue_classifier import (
        LABEL_DEFINITIONS,
        MINOR_ORDER,
        MINOR_TO_MAJOR,
    )

    LABEL_DEFINITIONS.clear()
    LABEL_DEFINITIONS.update(payload["label_definitions"])
    MINOR_ORDER.clear()
    MINOR_ORDER.extend(payload["minor_order"])
    MINOR_TO_MAJOR.clear()
    MINOR_TO_MAJOR.update(payload["minor_to_major"])


def _load_persisted_labels() -> None:
    settings = deps.get_settings()
    if settings is None or not settings.label_config_path.is_file():
        return
    try:
        data = json.loads(settings.label_config_path.read_text(encoding="utf-8"))
        if all(k in data for k in ("label_definitions", "minor_order", "minor_to_major")):
            _apply_label_payload(data)
    except Exception as exc:
        logger.warning("Cannot load persisted labels: %s", exc)


def _save_persisted_labels(payload: dict) -> None:
    settings = deps.get_settings()
    if settings is None:
        return
    settings.label_config_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = settings.label_config_path.with_suffix(settings.label_config_path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(settings.label_config_path)


# ---------- Labels ----------


@router.get("/labels")
async def get_labels(user: dict = Depends(get_current_user)):
    """Trả về MINOR_ORDER, MINOR_TO_MAJOR và LABEL_DEFINITIONS."""
    _load_persisted_labels()
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


@router.put("/labels")
async def update_labels(payload: dict, admin: dict = Depends(get_admin_user)):
    """Update labels and record history."""
    from ...pipeline.issue_classifier import (
        LABEL_DEFINITIONS,
        MINOR_ORDER,
        MINOR_TO_MAJOR,
    )

    # Validate
    new_defs = payload.get("label_definitions")
    new_order = payload.get("minor_order")
    new_mapping = payload.get("minor_to_major")
    if new_defs is None or new_order is None or new_mapping is None:
        raise HTTPException(status_code=400, detail="Missing required fields: label_definitions, minor_order, minor_to_major")

    # Record diff
    old_labels = {
        "label_definitions": dict(LABEL_DEFINITIONS),
        "minor_order": list(MINOR_ORDER),
        "minor_to_major": dict(MINOR_TO_MAJOR),
    }
    new_labels = {
        "label_definitions": new_defs,
        "minor_order": new_order,
        "minor_to_major": new_mapping,
    }

    store = deps.get_label_history_store()
    changes = 0
    if store:
        changes = store.record_diff(
            old_labels,
            new_labels,
            user=admin.get("username") or admin.get("display_name") or "Admin",
        )
    _save_persisted_labels(new_labels)
    _apply_label_payload(new_labels)

    return {"success": True, "changes_recorded": changes}


@router.get("/labels/history")
async def get_label_history(
    limit: int = 20,
    offset: int = 0,
    date_from: str | None = None,
    date_to: str | None = None,
    admin: dict = Depends(get_admin_user),
):
    """Get label change history with pagination."""
    store = deps.get_label_history_store()
    if not store:
        return {"items": [], "total": 0, "has_more": False}
    return store.get_history(limit=limit, offset=offset, date_from=date_from, date_to=date_to)


# ---------- Keywords ----------


@router.get("/keywords/raw")
async def get_raw_keywords(admin: dict = Depends(get_admin_user)):
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


@router.get("/keywords/search")
async def search_keywords(q: str = "", admin: dict = Depends(get_admin_user)):
    """Search keywords across all groups with case-insensitive substring matching."""
    if not q or len(q.strip()) < 1:
        return {"results": []}

    settings = deps.get_settings()
    if settings is None:
        return {"results": []}

    kw_map_path = settings.kw_map_path
    if not kw_map_path.is_file():
        raise HTTPException(status_code=404, detail="kw_map.json not found")

    try:
        kw_map = json.loads(kw_map_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    query = q.strip().lower()
    exact = []
    partial = []

    for group, keywords in kw_map.items():
        if group == "manual_brand_alias":
            continue
        if not isinstance(keywords, list):
            continue
        for kw in keywords:
            kw_str = str(kw)
            kw_lower = kw_str.lower()
            if kw_lower == query:
                exact.append({"keyword": kw_str, "group": group})
            elif query in kw_lower:
                partial.append({"keyword": kw_str, "group": group})

    results = (exact + partial)[:20]
    return {"results": results}


@router.get("/keywords")
async def get_keywords(admin: dict = Depends(get_admin_user)):
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
async def get_brands(admin: dict = Depends(get_admin_user)):
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
        hints = brand_hints(kw_map)
        return list(hints.keys())
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Lỗi đọc kw_map.json: {exc}",
        ) from exc


# ---------- Products ----------


@router.get("/products")
async def get_products(admin: dict = Depends(get_admin_user)):
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
async def save_keywords(data: dict, admin: dict = Depends(get_admin_user)):
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
async def list_products(admin: dict = Depends(get_admin_user)):
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
                    "products": df.to_dict(orient="records"),
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
async def save_products(payload: dict, admin: dict = Depends(get_admin_user)):
    """Lưu danh sách sản phẩm mới cho một sheet cụ thể vào file Excel Phân Chia Nhóm Sản Phẩm V2.xlsx, bảo toàn các sheet khác."""
    settings = deps.get_settings()
    if settings is None:
        raise HTTPException(status_code=400, detail="Settings chưa được cấu hình")

    sheet_name = payload.get("sheet_name")
    products = payload.get("products")

    if not sheet_name or products is None:
        raise HTTPException(
            status_code=400,
            detail="Thiếu thông tin sheet_name hoặc danh sách sản phẩm trong payload.",
        )

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
        with pd.ExcelWriter(products_path, engine="openpyxl") as writer:
            for name, df_sheet in sheets_data.items():
                df_sheet.to_excel(writer, sheet_name=name, index=False)

        # Reset dependencies so cached RAG index is re-initialized
        deps.reset()
        return {
            "success": True,
            "message": f"Đã lưu danh mục sản phẩm của sheet '{sheet_name}' thành công.",
        }
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


# ---------- Sync to SharePoint ----------


def _update_config_asset_state(asset_key: str, sp_response: dict) -> None:
    """Update config_assets_state.json so ConfigAssetSyncService won't re-download.

    Writes the same version fields that ``ConfigAssetSyncService._item_version()``
    produces, ensuring ``_is_changed()`` returns *False* for this asset on the
    next automatic sync cycle.
    """
    settings = deps.get_settings()
    if settings is None:
        return

    state_path = settings.config_assets_state_path
    state: dict = {"assets": {}, "last_success_at": None}
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Cannot read config asset state for update: %s", exc)

    version = {
        "item_id": str(sp_response.get("id", "")),
        "e_tag": str(sp_response.get("eTag", "")),
        "last_modified": str(sp_response.get("lastModifiedDateTime", "")),
        "size": str(sp_response.get("size", "")),
    }
    state.setdefault("assets", {})[asset_key] = version

    try:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        logger.warning("Cannot write config asset state after sync: %s", exc)


@router.post("/sync-keywords-to-sp")
async def sync_keywords_to_sharepoint(admin: dict = Depends(get_admin_user)):
    """Upload kw_map.json từ local lên thư mục Keyword/ trên SharePoint."""
    settings = deps.get_settings()
    if settings is None:
        raise HTTPException(status_code=400, detail="Settings chưa được cấu hình")

    sp_client = deps.get_sharepoint_client()
    if sp_client is None:
        raise HTTPException(
            status_code=503,
            detail="Không thể kết nối SharePoint (thiếu cấu hình Azure credentials)",
        )

    kw_map_path = settings.kw_map_path
    if not kw_map_path.is_file():
        raise HTTPException(
            status_code=404,
            detail=f"Không tìm thấy kw_map.json local để upload tại {kw_map_path}",
        )

    try:
        result = sp_client.upload_file(kw_map_path, settings.sp_keyword_folder)
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Upload kw_map.json lên SharePoint thất bại: {exc}",
        ) from exc

    _update_config_asset_state("keyword/kw_map.json", result)
    return {
        "success": True,
        "message": f"Đã upload kw_map.json lên SharePoint/{settings.sp_keyword_folder}/",
        "sharepoint_item_id": result.get("id"),
    }


@router.post("/sync-products-to-sp")
async def sync_products_to_sharepoint(admin: dict = Depends(get_admin_user)):
    """Upload file Excel sản phẩm từ local lên thư mục Keyword/ trên SharePoint."""
    settings = deps.get_settings()
    if settings is None:
        raise HTTPException(status_code=400, detail="Settings chưa được cấu hình")

    sp_client = deps.get_sharepoint_client()
    if sp_client is None:
        raise HTTPException(
            status_code=503,
            detail="Không thể kết nối SharePoint (thiếu cấu hình Azure credentials)",
        )

    products_path = settings.df_products_path
    if not products_path.is_file():
        raise HTTPException(
            status_code=404,
            detail=f"Không tìm thấy file sản phẩm Excel local để upload tại {products_path}",
        )

    try:
        result = sp_client.upload_file(products_path, settings.sp_keyword_folder)
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Upload file sản phẩm lên SharePoint thất bại: {exc}",
        ) from exc

    _update_config_asset_state(f"keyword/{products_path.name}", result)
    return {
        "success": True,
        "message": f"Đã upload {products_path.name} lên SharePoint/{settings.sp_keyword_folder}/",
        "sharepoint_item_id": result.get("id"),
    }
