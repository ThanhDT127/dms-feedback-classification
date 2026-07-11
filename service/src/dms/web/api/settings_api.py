"""Settings inspection and connection testing endpoints."""

from __future__ import annotations

import inspect
import logging
import time
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from ...prompt_renderer import DEFAULT_ISSUE_PROMPT_PATH, LEGACY_ISSUE_PROMPT_NAME
from ...settings import SERVICE_DIR, get_settings_provider, update_env_file
from .. import deps
from ..deps import get_admin_user

logger = logging.getLogger("dms-web")

router = APIRouter(prefix="/api/settings", tags=["settings"])


def _mask_secret(value: str, visible_chars: int = 4) -> str:
    """Mask a secret string completely with bullet points for security."""
    if not value:
        return ""
    return "••••••••••••"


# ---------- Settings ----------


@router.get("")
async def get_settings(admin: dict = Depends(get_admin_user)):
    """Trả về cấu hình hiện tại (ẩn bí mật)."""
    raw = deps.get_settings_partial()

    # Mask sensitive values regardless of source type
    secret_keys = {
        "azure_client_secret", "gemini_api_key", "jwt_secret_key", "default_admin_password",
        "AZURE_CLIENT_SECRET", "GEMINI_API_KEY", "JWT_SECRET_KEY", "DEFAULT_ADMIN_PASSWORD",
    }
    masked = {}
    for key, value in raw.items():
        if key in secret_keys and isinstance(value, str):
            masked[key] = _mask_secret(value)
        elif isinstance(value, Path):
            masked[key] = str(value)
        else:
            masked[key] = value

    return masked




# ---------- Prompt templates ----------


@router.get("/prompt")
async def get_issue_prompt(admin: dict = Depends(get_admin_user)):
    """Trả về template prompt của Issue Classifier."""
    try:
        settings = deps.get_settings()
        raw_template = ""
        prompt_override = None
        source_file_name = str(DEFAULT_ISSUE_PROMPT_PATH)
        if settings is not None:
            prompt_override = settings.keyword_dir / LEGACY_ISSUE_PROMPT_NAME
            if prompt_override.is_file():
                raw_template = prompt_override.read_text(encoding="utf-8")
                source_file_name = str(prompt_override)

        if not raw_template:
            raw_template = DEFAULT_ISSUE_PROMPT_PATH.read_text(encoding="utf-8")

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
async def get_rag_prompt(admin: dict = Depends(get_admin_user)):
    """Trả về template prompt RAG extraction."""
    try:
        from ...pipeline.rag_product import RAGProductMatcher

        source = inspect.getsource(RAGProductMatcher.llm_extract_batch)
        prompt_start = source.find("prompt = dedent(")
        prompt_end = source.find(").strip()", prompt_start)
        if prompt_start >= 0 and prompt_end >= 0:
            prompt_template = source[prompt_start : prompt_end + len(").strip()")]
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
async def get_available_models(admin: dict = Depends(get_admin_user)):
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
async def test_connection(admin: dict = Depends(get_admin_user)):
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
            "message": f"Kết nối thành công. Phản hồi: {response.text[:100]}",
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
    "gemini_model_pricing": "GEMINI_MODEL_PRICING",
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
    "upload_input_to_sharepoint": "UPLOAD_INPUT_TO_SHAREPOINT",
    # notify tab
    "email": "NOTIFICATION_RECIPIENTS",
    "notification_recipients": "NOTIFICATION_RECIPIENTS",
    "notification_recipients_raw": "NOTIFICATION_RECIPIENTS",
    "notify_on_success": "NOTIFY_ON_SUCCESS",
    "notify_on_error": "NOTIFY_ON_ERROR",
}


@router.put("")
async def update_settings(payload: dict, admin: dict = Depends(get_admin_user)):
    """Cập nhật cài đặt hệ thống vào file .env một cách an toàn."""
    env_file = SERVICE_DIR / ".env"

    # 1. Back up current .env content and os.environ values
    old_env_content = ""
    if env_file.exists():
        old_env_content = env_file.read_text(encoding="utf-8")

    import os

    old_os_env = {env_key: os.environ.get(env_key) for env_key in MAP.values()}

    try:
        # Prepare updates mapping
        updates = {}
        for key, value in payload.items():
            if key in MAP:
                env_key = MAP[key]

                # Check for masked secret values or empty string (to keep existing value)
                if isinstance(value, str) and (value.startswith("****") or "••" in value or value == "••••••••••••" or not value.strip()):
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

        # 2. Write updates to .env file and update current process's environment variables
        update_env_file(updates)
        for env_key, env_val in updates.items():
            os.environ[env_key] = env_val

        # 3. Validate by trying to load Settings
        deps.reset()
        settings = get_settings_provider().reload()

        # Test connection to Gemini if credentials exist
        has_creds = False
        if settings.gemini_backend == "apikey" and settings.gemini_api_key:
            has_creds = True
        elif settings.gemini_backend == "vertex" and settings.gcp_service_account_json:
            sa_path = Path(settings.gcp_service_account_json)
            if sa_path.is_file():
                has_creds = True

        if has_creds:
            try:
                from ...gemini_client import GeminiClient
                gemini = GeminiClient(settings)
                gemini.generate("Trả lời đúng 1 từ: xin chào", temperature=0.0)
            except Exception as exc:
                logger.error("Test connection failed during settings update: %s", exc)
                raise ValueError(
                    f"Xác thực kết nối Gemini thất bại. Khóa API hoặc tài khoản dịch vụ không hoạt động. Chi tiết lỗi: {exc}"
                ) from exc

        return {"success": True, "message": "Đã lưu cấu hình và xác thực kết nối Gemini thành công."}

    except Exception as exc:
        # 4. Rollback to original .env content and os.environ
        if old_env_content:
            env_file.write_text(old_env_content, encoding="utf-8")
        for env_key, old_val in old_os_env.items():
            if old_val is None:
                os.environ.pop(env_key, None)
            else:
                os.environ[env_key] = old_val
        deps.reset()

        logger.error("Lỗi lưu cấu hình: %s", exc)
        raise HTTPException(
            status_code=400,
            detail=f"Cấu hình không hợp lệ. Đã khôi phục cài đặt cũ. Chi tiết lỗi: {exc}",
        ) from exc


@router.put("/prompt")
async def save_custom_prompt(payload: dict, admin: dict = Depends(get_admin_user)):
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
        "{input_json}",
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
