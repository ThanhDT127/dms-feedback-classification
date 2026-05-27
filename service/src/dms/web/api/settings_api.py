"""Settings inspection and connection testing endpoints."""

from __future__ import annotations

import inspect
import logging
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException

from ...settings import SERVICE_DIR, Settings, update_env_file
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

        settings = deps.get_settings()
        raw_template = ""
        prompt_override = None
        if settings is not None:
            prompt_override = settings.keyword_dir / "system_prompt.txt"
            if prompt_override.is_file():
                raw_template = prompt_override.read_text(encoding="utf-8")

        source_file_name = "pipeline/issue_classifier.py"
        if not raw_template:
            # Read the source file directly — more reliable than inspect
            source_file = Path(inspect.getfile(IssueClassifier))
            source = source_file.read_text(encoding="utf-8")
            source_file_name = str(source_file.name)

            # Extract the prompt section between known markers
            start_marker = "Bạn là hệ thống phân loại phản hồi"
            end_marker = "ĐẦU RA BẮT BUỘC"
            start_idx = source.find(start_marker)
            end_idx = source.find(end_marker)

            if start_idx >= 0 and end_idx >= 0:
                raw_template = source[start_idx:end_idx + len(end_marker)]
            elif start_idx >= 0:
                raw_template = source[start_idx:start_idx + 5000]
            else:
                raw_template = "Không thể trích xuất prompt template từ source code."

        # Clean f-string artifacts for preview
        prompt_template = raw_template
        prompt_template = prompt_template.replace("{minor_order_json}", "[...20 nhãn...]")
        prompt_template = prompt_template.replace("{label_defs}", "[...định nghĩa nhãn...]")
        prompt_template = prompt_template.replace("{hints_json}", "[...keyword hints...]")
        prompt_template = prompt_template.replace("{brand_json}", "[...brand hints...]")
        prompt_template = prompt_template.replace("{input_json}", "[...input data...]")

        word_count = len(prompt_template.split())
        estimated_tokens = int(word_count * 1.3)

        return {
            "prompt_template": prompt_template,
            "raw_template": raw_template,
            "word_count": word_count,
            "estimated_tokens": estimated_tokens,
            "source_file": source_file_name,
            "is_custom": prompt_override is not None and prompt_override.is_file(),
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


# Mapping of incoming settings JSON keys to env variables
MAP = {
    # model tab
    "backend": "GEMINI_BACKEND",
    "gemini_backend": "GEMINI_BACKEND",
    "model": "GEMINI_MODEL",
    "gemini_model": "GEMINI_MODEL",
    "api_key": "GEMINI_API_KEY",
    "gemini_api_key": "GEMINI_API_KEY",
    "project_id": "GCP_PROJECT_ID",
    "gcp_project_id": "GCP_PROJECT_ID",
    "location": "GCP_LOCATION",
    "gcp_location": "GCP_LOCATION",
    # pipeline tab
    "llm_batch_size": "LLM_BATCH_SIZE",
    "checkpoint_every": "CKPT_EVERY",
    "poll_interval_seconds": "POLL_INTERVAL_SECONDS",
    "base_wait": "BASE_WAIT",
    "max_retry": "MAX_RETRY",
    "rate_limit_gap": "RATE_LIMIT_GAP",
    "rate_gap": "RATE_LIMIT_GAP",
    "bm25_min_score": "BM25_MIN_SCORE",
    "http_timeout": "HTTP_TIMEOUT_SECONDS",
    # sharepoint tab
    "site_url": "SHAREPOINT_SITE_URL",
    "client_id": "AZURE_CLIENT_ID",
    "client_secret": "AZURE_CLIENT_SECRET",
    "tenant_id": "AZURE_TENANT_ID",
    "drive_id": "SHAREPOINT_DRIVE_ID",
    "root_folder_id": "SHAREPOINT_ROOT_FOLDER_ID",
    # notify tab
    "email": "NOTIFICATION_RECIPIENTS",
    "notification_recipients": "NOTIFICATION_RECIPIENTS",
    "notification_recipients_raw": "NOTIFICATION_RECIPIENTS",
    "notify_on_success": "NOTIFY_ON_SUCCESS",
    "notify_on_error": "NOTIFY_ON_ERROR",
}


@router.put("")
async def update_settings(payload: dict):
    """Cập nhật cài đặt hệ thống vào file .env một cách an toàn."""
    env_file = SERVICE_DIR / ".env"
    
    # 1. Back up current .env content
    old_env_content = ""
    if env_file.exists():
        old_env_content = env_file.read_text(encoding="utf-8")
        
    try:
        # Prepare updates mapping
        updates = {}
        for key, value in payload.items():
            if key in MAP:
                env_key = MAP[key]
                
                # Check for masked secret values and skip them
                if isinstance(value, str) and (value.startswith("****") or "••" in value):
                    # Keep existing value if it was already set
                    continue
                
                # Normalize Gemini backend values
                if env_key == "GEMINI_BACKEND" and isinstance(value, str):
                    val_lower = value.lower().strip()
                    if val_lower == "vertex_ai":
                        value = "vertex"
                    elif val_lower == "api_key" or val_lower == "apikey":
                        value = "apikey"
                
                updates[env_key] = str(value)
                
        if not updates:
            return {"success": True, "message": "Không có thay đổi nào được áp dụng."}
            
        # 2. Write updates to .env file
        update_env_file(updates)
        
        # 3. Validate by trying to load Settings
        deps.reset()
        Settings()
        
        return {"success": True, "message": "Đã lưu cấu hình thành công."}
        
    except Exception as exc:
        # 4. Rollback to original .env content
        if old_env_content:
            env_file.write_text(old_env_content, encoding="utf-8")
        deps.reset()
        
        logger.error("Lỗi lưu cấu hình: %s", exc)
        raise HTTPException(
            status_code=400,
            detail=f"Cấu hình không hợp lệ. Đã khôi phục cài đặt cũ. Chi tiết lỗi: {exc}",
        )


@router.put("/prompt")
async def save_custom_prompt(payload: dict):
    """Lưu trữ system prompt tự chọn vào file system_prompt.txt."""
    settings = deps.get_settings()
    if settings is None:
        raise HTTPException(status_code=400, detail="Settings chưa được cấu hình")
        
    prompt_text = payload.get("prompt", "").strip()
    if not prompt_text:
        raise HTTPException(status_code=400, detail="Nội dung prompt không được để trống")
        
    # Validation: ensure all required placeholders are intact
    required_placeholders = [
        "{minor_order_json}",
        "{label_defs}",
        "{hints_json}",
        "{brand_json}",
        "{input_json}"
    ]
    missing = [ph for ph in required_placeholders if ph not in prompt_text]
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"Lỗi: Thiếu các placeholder bắt buộc để nội suy dữ liệu: {', '.join(missing)}. Vui lòng giữ lại các placeholder này trong prompt.",
        )
        
    try:
        prompt_override = settings.keyword_dir / "system_prompt.txt"
        prompt_override.parent.mkdir(parents=True, exist_ok=True)
        prompt_override.write_text(prompt_text, encoding="utf-8")
        
        # Reset cached pipeline classes so they reload the new prompt override
        deps.reset()
        return {"success": True, "message": "Đã lưu System Prompt tùy chỉnh thành công."}
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Không thể ghi file system_prompt.txt: {exc}",
        ) from exc
