"""Settings inspection and connection testing endpoints."""

from __future__ import annotations

import inspect
import logging
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException

from ...settings import SERVICE_DIR
from .. import deps

logger = logging.getLogger("dms-web")

router = APIRouter(prefix="/api/settings", tags=["settings"])


def _mask_secret(value: str, visible_chars: int = 4) -> str:
    """Mask a secret string, showing only the last N characters."""
    if not value:
        return ""
    if len(value) <= visible_chars:
        return "****"
    return "*" * (len(value) - visible_chars) + value[-visible_chars:]


# ---------- Settings ----------


@router.get("")
async def get_settings():
    """Trả về cấu hình hiện tại (ẩn bí mật)."""
    raw = deps.get_settings_partial()

    # Mask sensitive values regardless of source type
    secret_keys = {"azure_client_secret", "gemini_api_key", "AZURE_CLIENT_SECRET", "GEMINI_API_KEY"}
    masked = {}
    for key, value in raw.items():
        if key in secret_keys and isinstance(value, str):
            masked[key] = _mask_secret(value)
        elif isinstance(value, Path):
            masked[key] = str(value)
        else:
            masked[key] = value

    # Add computed paths if full settings are available
    settings = deps.get_settings()
    if settings is not None:
        masked["_computed"] = {
            "service_dir": str(SERVICE_DIR),
            "keyword_dir": str(settings.keyword_dir),
            "model_dir": str(settings.model_dir),
            "work_dir": str(settings.work_dir),
            "log_dir": str(settings.log_dir),
            "kw_map_path": str(settings.kw_map_path),
            "df_products_path": str(settings.df_products_path),
            "seen_files_path": str(settings.seen_files_path),
        }

    return masked


# ---------- Prompt templates ----------


@router.get("/prompt")
async def get_issue_prompt():
    """Trả về template prompt của Issue Classifier."""
    try:
        from ...pipeline.issue_classifier import IssueClassifier

        # Read the source file directly — more reliable than inspect
        source_file = Path(inspect.getfile(IssueClassifier))
        source = source_file.read_text(encoding="utf-8")

        # Extract the prompt section between known markers
        start_marker = "Bạn là hệ thống phân loại phản hồi"
        end_marker = "ĐẦU RA BẮT BUỘC"
        start_idx = source.find(start_marker)
        end_idx = source.find(end_marker)

        if start_idx >= 0 and end_idx >= 0:
            prompt_template = source[start_idx:end_idx + len(end_marker)]
            # Clean f-string artifacts
            prompt_template = prompt_template.replace("{minor_order_json}", "[...20 nhãn...]")
            prompt_template = prompt_template.replace("{label_defs}", "[...định nghĩa nhãn...]")
            prompt_template = prompt_template.replace("{hints_json}", "[...keyword hints...]")
            prompt_template = prompt_template.replace("{brand_json}", "[...brand hints...]")
            prompt_template = prompt_template.replace("{input_json}", "[...input data...]")
        elif start_idx >= 0:
            # Fallback: get from start marker to next 5000 chars
            prompt_template = source[start_idx:start_idx + 5000]
        else:
            prompt_template = "Không thể trích xuất prompt template từ source code."

        word_count = len(prompt_template.split())
        # Rough token estimate: ~1.3 tokens per Vietnamese word
        estimated_tokens = int(word_count * 1.3)

        return {
            "prompt_template": prompt_template,
            "word_count": word_count,
            "estimated_tokens": estimated_tokens,
            "source_file": str(source_file.name),
        }
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Lỗi đọc prompt template: {exc}",
        ) from exc


@router.get("/prompt/rag")
async def get_rag_prompt():
    """Trả về template prompt RAG extraction."""
    try:
        from ...pipeline.rag_product import RAGProductMatcher

        source = inspect.getsource(RAGProductMatcher.llm_extract_batch)
        prompt_start = source.find('prompt = dedent(')
        prompt_end = source.find(').strip()', prompt_start)
        if prompt_start >= 0 and prompt_end >= 0:
            prompt_template = source[prompt_start:prompt_end + len(').strip()')]
        else:
            prompt_template = "Không thể trích xuất prompt template từ source code."

        word_count = len(prompt_template.split())
        estimated_tokens = int(word_count * 1.3)

        return {
            "prompt_template": prompt_template,
            "word_count": word_count,
            "estimated_tokens": estimated_tokens,
            "source_file": "pipeline/rag_product.py",
        }
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Lỗi đọc RAG prompt template: {exc}",
        ) from exc


# ---------- Available models ----------


@router.get("/models")
async def get_available_models():
    """Trả về danh sách các model Gemini khả dụng."""
    return [
        {
            "id": "gemini-2.5-flash-lite",
            "name": "Gemini 2.5 Flash Lite",
            "description": "Nhanh, tiết kiệm chi phí",
        },
        {
            "id": "gemini-2.5-flash",
            "name": "Gemini 2.5 Flash",
            "description": "Cân bằng tốc độ và chất lượng",
        },
        {
            "id": "gemini-2.5-pro",
            "name": "Gemini 2.5 Pro",
            "description": "Chính xác cao nhất",
        },
        {
            "id": "gemini-2.0-flash",
            "name": "Gemini 2.0 Flash",
            "description": "Phiên bản cũ",
        },
    ]


# ---------- Test connection ----------


@router.post("/test-connection")
async def test_connection():
    """Kiểm tra kết nối tới Gemini API."""
    gemini = deps.get_gemini()
    if gemini is None:
        return {
            "success": False,
            "message": "GeminiClient chưa được cấu hình. Kiểm tra cài đặt API key hoặc Vertex AI.",
            "response_time_ms": 0,
        }

    try:
        t0 = time.time()
        response = gemini.generate("Trả lời đúng 1 từ: xin chào", temperature=0.0)
        elapsed_ms = round((time.time() - t0) * 1000)

        return {
            "success": True,
            "message": f"Kết nối thành công. Phản hồi: {response[:100]}",
            "response_time_ms": elapsed_ms,
        }
    except Exception as exc:
        elapsed_ms = round((time.time() - t0) * 1000)
        return {
            "success": False,
            "message": f"Kết nối thất bại: {exc}",
            "response_time_ms": elapsed_ms,
        }
