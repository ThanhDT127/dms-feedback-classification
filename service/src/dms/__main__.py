"""Composition root for the DMS service."""

from __future__ import annotations

import logging
import signal

from .auth import AuthProvider
from .config_assets import ConfigAssetSyncService
from .exceptions import ConfigurationError
from .gemini_client import GeminiClient
from .http_client import create_session
from .logging_config import setup_logging
from .metrics import MetricsCollector
from .notification import NotificationService
from .pipeline.rag_product import RAGProductMatcher
from .pipeline.runner import PipelineRunner
from .settings import Settings, get_settings
from .sharepoint import SharePointClient
from .watcher import Watcher


def _validate_asset_snapshot(
    settings: Settings,
    gemini: GeminiClient,
    keyword_dir,
    model_dir,
) -> None:
    snapshot_settings = settings.model_copy(
        update={
            "keyword_dir_override": keyword_dir,
            "model_dir_override": model_dir,
        }
    )
    # We no longer validate baseline classifier as it is deprecated
    RAGProductMatcher(settings=snapshot_settings, gemini=gemini)


def _build_runtime_settings(settings: Settings, config_asset_sync: ConfigAssetSyncService) -> Settings:
    return config_asset_sync.get_runtime_settings()


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
    config_asset_sync = ConfigAssetSyncService(
        settings=settings,
        sharepoint_client=sharepoint,
        snapshot_validator=lambda keyword_dir, model_dir: _validate_asset_snapshot(
            settings=settings,
            gemini=gemini,
            keyword_dir=keyword_dir,
            model_dir=model_dir,
        ),
    )
    if settings.enable_sharepoint_config_sync:
        config_asset_sync.sync()

    def build_runner() -> PipelineRunner:
        runtime_settings = _build_runtime_settings(settings, config_asset_sync)
        rag = RAGProductMatcher(settings=runtime_settings, gemini=gemini)
        return PipelineRunner(
            gemini=gemini,
            rag=rag,
            metrics=metrics,
            settings=runtime_settings,
        )

    runner = build_runner()
    watcher = Watcher(
        sharepoint_client=sharepoint,
        pipeline_runner=runner,
        notification_service=notifications,
        metrics=metrics,
        settings=settings,
        runner_factory=build_runner,
        config_asset_sync=config_asset_sync,
    )

    runtime_settings = _build_runtime_settings(settings, config_asset_sync)
    logger.info("Baseline model ready from %s", runtime_settings.model_dir)
    logger.info("Composition root ready")

    # Register graceful shutdown handlers (SIGTERM from docker stop, SIGINT from Ctrl+C)
    def _handle_shutdown(signum, frame):  # noqa: ANN001
        logger.info("Received signal %d — requesting graceful shutdown", signum)
        watcher.request_shutdown()

    signal.signal(signal.SIGTERM, _handle_shutdown)
    signal.signal(signal.SIGINT, _handle_shutdown)

    watcher.run_forever()


if __name__ == "__main__":
    main()
