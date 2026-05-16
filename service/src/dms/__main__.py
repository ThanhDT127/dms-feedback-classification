"""Composition root for the DMS service."""

from __future__ import annotations

import logging

from .auth import AuthProvider
from .exceptions import ConfigurationError
from .gemini_client import GeminiClient
from .http_client import create_session
from .logging_config import setup_logging
from .metrics import MetricsCollector
from .notification import NotificationService
from .pipeline.baseline_classifier import BaselineIssueClassifier
from .pipeline.rag_product import RAGProductMatcher
from .pipeline.runner import PipelineRunner
from .settings import get_settings
from .sharepoint import SharePointClient
from .watcher import Watcher


def main() -> None:
    """Bootstrap and start the watcher."""
    try:
        settings = get_settings()
    except ConfigurationError as exc:
        raise SystemExit(str(exc)) from exc

    settings.ensure_runtime_dirs()
    setup_logging(settings.log_dir)
    logger = logging.getLogger("dms-watcher")

    session = create_session(default_timeout=settings.http_timeout_seconds)
    auth = AuthProvider(settings)
    gemini = GeminiClient(settings)
    sharepoint = SharePointClient(auth=auth, settings=settings, session=session)
    notifications = NotificationService(auth=auth, settings=settings, session=session)
    metrics = MetricsCollector(settings.metrics_path)
    baseline = BaselineIssueClassifier(settings=settings)
    rag = RAGProductMatcher(settings=settings, gemini=gemini)
    runner = PipelineRunner(
        gemini=gemini,
        rag=rag,
        metrics=metrics,
        settings=settings,
        baseline_classifier=baseline,
    )
    watcher = Watcher(
        sharepoint_client=sharepoint,
        pipeline_runner=runner,
        notification_service=notifications,
        metrics=metrics,
        settings=settings,
    )

    logger.info("Baseline model ready from %s", settings.model_dir)
    logger.info("Composition root ready")
    watcher.run_forever()


if __name__ == "__main__":
    main()
