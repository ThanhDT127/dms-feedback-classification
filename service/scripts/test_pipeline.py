"""Run one watcher poll cycle against the refactored package."""

from __future__ import annotations

import argparse
import logging
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, "src")

from dms.auth import AuthProvider
from dms.gemini_client import GeminiClient
from dms.http_client import create_session
from dms.logging_config import setup_logging
from dms.metrics import MetricsCollector
from dms.notification import NotificationService
from dms.pipeline.rag_product import RAGProductMatcher
from dms.pipeline.runner import PipelineRunner
from dms.settings import get_settings
from dms.sharepoint import SharePointClient
from dms.watcher import Watcher


def main() -> None:
    parser = argparse.ArgumentParser(description="Test pipeline with 1 poll cycle")
    parser.add_argument(
        "--force-file",
        metavar="NAME",
        help="Remove this file from seen_files.json so it can be processed again",
    )
    args = parser.parse_args()

    settings = get_settings()
    settings.ensure_runtime_dirs()
    setup_logging(settings.log_dir)

    session = create_session(default_timeout=settings.http_timeout_seconds)
    auth = AuthProvider(settings)
    gemini = GeminiClient(settings)
    sharepoint = SharePointClient(auth=auth, settings=settings, session=session)
    notifications = NotificationService(auth=auth, settings=settings, session=session)
    metrics = MetricsCollector(settings.metrics_path)
    rag = RAGProductMatcher(settings=settings, gemini=gemini)
    runner = PipelineRunner(gemini=gemini, rag=rag, metrics=metrics, settings=settings)
    watcher = Watcher(
        sharepoint_client=sharepoint,
        pipeline_runner=runner,
        notification_service=notifications,
        metrics=metrics,
        settings=settings,
    )
    logger = logging.getLogger("dms-watcher")

    logger.info("=" * 60)
    logger.info("PIPELINE TEST - single poll cycle")
    logger.info("=" * 60)

    seen = watcher._load_seen()
    logger.info("Loaded %d seen files", len(seen))

    if args.force_file:
        removed = [k for k, v in seen.items() if v.get("name") == args.force_file]
        if removed:
            for key in removed:
                del seen[key]
            watcher._save_seen(seen)
            logger.info("Removed '%s' from seen -> will reprocess", args.force_file)
        else:
            logger.warning("File '%s' not found in seen_files.json", args.force_file)

    processed = watcher.poll_once(seen)
    logger.info("=" * 60)
    if processed > 0:
        logger.info("Processed %d file(s)", processed)
        print(f"\n>>> PIPELINE TEST PASSED! ({processed} file processed) <<<")
    else:
        logger.info("No files processed (all already done, or no new files)")
        print("\n>>> No files processed - check seen_files.json or SharePoint Input/ <<<")


if __name__ == "__main__":
    main()
