"""Shared dependency container with thread-safe lazy initialization."""

from __future__ import annotations

import logging
import threading
from typing import Any

import jwt as pyjwt
from fastapi import Depends, Header, HTTPException

from ..jwt_utils import decode_token
from ..settings import SERVICE_DIR, Settings

logger = logging.getLogger("dms-web")

_lock = threading.RLock()
_cache: dict[str, Any] = {}


def _get_or_create(key: str, factory):
    """Thread-safe lazy singleton factory."""
    if key in _cache:
        return _cache[key]
    with _lock:
        if key in _cache:
            return _cache[key]
        obj = factory()
        _cache[key] = obj
        return obj


def reset() -> None:
    """Clear all cached singletons (useful for testing)."""
    with _lock:
        worker_manager = _cache.get("classification_worker_manager")
        if worker_manager is not None and hasattr(worker_manager, "stop"):
            try:
                worker_manager.stop()
            except Exception as exc:
                logger.warning("Could not stop cached classification worker manager: %s", exc)
        _cache.clear()


def get_settings() -> Settings | None:
    """Return Settings, or None if configuration is incomplete."""

    def _factory():
        try:
            return Settings()  # type: ignore[call-arg]
        except Exception as exc:
            logger.warning("Không thể tải cấu hình đầy đủ: %s", exc)
            return None

    return _get_or_create("settings", _factory)


def get_label_history_store():
    """Return a singleton LabelHistoryStore."""
    from ..label_history import LabelHistoryStore

    def _factory():
        settings = get_settings()
        if settings is None:
            return None
        return LabelHistoryStore(settings.label_history_db_path)

    return _get_or_create("label_history_store", _factory)


def get_classification_job_store():
    """Return a singleton ClassificationJobStore."""
    from ..classification_jobs import ClassificationJobStore

    def _factory():
        settings = get_settings()
        if settings is None:
            return None
        return ClassificationJobStore(settings.classification_jobs_db_path)

    return _get_or_create("classification_job_store", _factory)


def get_classification_worker_manager():
    """Return the in-process classification worker manager."""
    from ..classification_worker import build_default_worker_manager

    return _get_or_create("classification_worker_manager", build_default_worker_manager)


def get_user_store():
    """Return a singleton UserStore."""
    from ..user_store import UserStore

    def _factory():
        settings = get_settings()
        if settings is None:
            return None
        db_path = settings.data_dir / "users.json"
        return UserStore(db_path=db_path, default_admin_password=settings.default_admin_password)

    return _get_or_create("user_store", _factory)


async def get_current_user(authorization: str = Header(None)) -> dict:
    """FastAPI dependency: extract and validate JWT from Authorization header."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid authorization header")

    token = authorization[7:].strip()  # Strip 'Bearer '
    if not token:
        raise HTTPException(status_code=401, detail="Missing or invalid authorization header")
    settings = get_settings()
    if settings is None:
        raise HTTPException(status_code=500, detail="Server configuration error")

    try:
        payload = decode_token(token, settings.jwt_secret_key, expected_type="access")
    except pyjwt.ExpiredSignatureError as exc:
        raise HTTPException(status_code=401, detail="Token has expired") from exc
    except (pyjwt.InvalidTokenError, ValueError) as exc:
        raise HTTPException(status_code=401, detail="Invalid token") from exc

    username = payload.get("sub")
    if not username:
        raise HTTPException(status_code=401, detail="Invalid token payload")

    user_store = get_user_store()
    if user_store is None:
        raise HTTPException(status_code=500, detail="User store not available")

    user = user_store.get_user(username)
    if user is None:
        raise HTTPException(status_code=401, detail="User not found")
    if user.get("is_active", True) is False:
        raise HTTPException(status_code=403, detail="User is inactive")

    return user


CURRENT_USER_DEP = Depends(get_current_user)


async def get_admin_user(user: dict = CURRENT_USER_DEP):
    """FastAPI dependency: require admin role."""
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


async def get_optional_user(authorization: str = Header(None)):
    """FastAPI dependency: return user or None."""
    if not authorization or not authorization.startswith("Bearer "):
        return None
    try:
        return await get_current_user(authorization)
    except HTTPException:
        return None


def get_settings_partial() -> dict:
    """Return a best-effort settings dict even if validation fails.

    Reads the .env file directly and returns raw key-value pairs so the
    UI can still display *something* when full Settings validation fails
    (e.g. missing Azure creds).
    """
    settings = get_settings()
    if settings is not None:
        return settings.model_dump()

    # Fallback: read .env manually
    env_file = SERVICE_DIR / ".env"
    data: dict[str, str] = {}
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                k, _, v = line.partition("=")
                data[k.strip()] = v.strip().strip('"').strip("'")
    return data


def get_gemini():
    """Return a GeminiClient, or None if Gemini is not configured."""

    def _factory():
        settings = get_settings()
        if settings is None:
            return None
        try:
            from ..gemini_client import GeminiClient

            return GeminiClient(settings)
        except Exception as exc:
            logger.warning("Không thể khởi tạo GeminiClient: %s", exc)
            return None

    return _get_or_create("gemini", _factory)


def get_rag():
    """Return a RAGProductMatcher, or None if resources are unavailable."""

    def _factory():
        settings = get_settings()
        gemini = get_gemini()
        if settings is None or gemini is None:
            return None
        try:
            from ..pipeline.rag_product import RAGProductMatcher

            return RAGProductMatcher(settings=settings, gemini=gemini)
        except Exception as exc:
            logger.warning("Không thể khởi tạo RAGProductMatcher: %s", exc)
            return None

    return _get_or_create("rag", _factory)


def get_issue_classifier():
    """Return an IssueClassifier, or None if Gemini is not available."""

    def _factory():
        settings = get_settings()
        gemini = get_gemini()
        if settings is None or gemini is None:
            return None
        try:
            from ..pipeline.issue_classifier import IssueClassifier

            return IssueClassifier(gemini=gemini, settings=settings)
        except Exception as exc:
            logger.warning("Không thể khởi tạo IssueClassifier: %s", exc)
            return None

    return _get_or_create("issue_classifier", _factory)


def get_metrics():
    """Return a MetricsCollector, or None if settings are unavailable."""

    def _factory():
        settings = get_settings()
        if settings is None:
            return None
        try:
            from ..metrics import MetricsCollector

            return MetricsCollector(settings.metrics_path)
        except Exception as exc:
            logger.warning("Không thể khởi tạo MetricsCollector: %s", exc)
            return None

    return _get_or_create("metrics", _factory)


def get_usage_tracker():
    """Return a UsageTracker, or None if settings are unavailable."""

    def _factory():
        settings = get_settings()
        if settings is None:
            return None
        try:
            from ..usage_tracker import UsageTracker

            db_path = settings.work_dir / "classification_jobs.db"
            return UsageTracker(db_path)
        except Exception as exc:
            logger.warning("Không thể khởi tạo UsageTracker: %s", exc)
            return None

    return _get_or_create("usage_tracker", _factory)


def get_pipeline_runner():
    """Return a PipelineRunner, or None if dependencies are unavailable."""

    def _factory():
        settings = get_settings()
        gemini = get_gemini()
        rag = get_rag()
        metrics = get_metrics()
        usage_tracker = get_usage_tracker()
        if any(dep is None for dep in (settings, gemini, rag, metrics)):
            return None
        try:
            from ..pipeline.runner import PipelineRunner

            return PipelineRunner(
                gemini=gemini,
                rag=rag,
                metrics=metrics,
                settings=settings,
                usage_tracker=usage_tracker,
            )
        except Exception as exc:
            logger.warning("Không thể khởi tạo PipelineRunner: %s", exc)
            return None

    return _get_or_create("pipeline_runner", _factory)


def get_sharepoint_client():
    """Return a SharePointClient, or None if credentials are incomplete."""

    def _factory():
        settings = get_settings()
        if settings is None:
            return None
        try:
            from ..auth import AuthProvider
            from ..http_client import create_session
            from ..sharepoint import SharePointClient

            session = create_session(default_timeout=settings.http_timeout_seconds)
            auth = AuthProvider(settings)
            return SharePointClient(auth=auth, settings=settings, session=session)
        except Exception as exc:
            logger.warning("Không thể khởi tạo SharePointClient: %s", exc)
            return None

    return _get_or_create("sharepoint_client", _factory)
