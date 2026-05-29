"""Shared dependency container with thread-safe lazy initialization."""

from __future__ import annotations

import logging
import threading
from typing import Any

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


def get_pipeline_runner():
    """Return a PipelineRunner, or None if dependencies are unavailable."""

    def _factory():
        settings = get_settings()
        gemini = get_gemini()
        rag = get_rag()
        metrics = get_metrics()
        if any(dep is None for dep in (settings, gemini, rag, metrics)):
            return None
        try:
            from ..pipeline.runner import PipelineRunner

            return PipelineRunner(
                gemini=gemini,
                rag=rag,
                metrics=metrics,
                settings=settings,
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
