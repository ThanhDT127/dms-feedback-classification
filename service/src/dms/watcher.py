"""Polling watcher service."""

from __future__ import annotations

import json
import logging
import time
import traceback
from datetime import datetime
from pathlib import Path

from .cleanup import RuntimeCleanup
from .config_assets import ConfigAssetSyncService
from .metrics import MetricsCollector
from .notification import NotificationService
from .pipeline.runner import PipelineRunner
from .settings import Settings
from .sharepoint import SharePointClient

logger = logging.getLogger("dms-watcher")
MAX_FILE_RETRIES = 3


class Watcher:
    """Poll SharePoint for new files and process them sequentially."""

    def __init__(
        self,
        sharepoint_client: SharePointClient,
        pipeline_runner: PipelineRunner,
        notification_service: NotificationService,
        metrics: MetricsCollector,
        settings: Settings,
        runner_factory=None,
        config_asset_sync: ConfigAssetSyncService | None = None,
    ) -> None:
        self.sharepoint_client = sharepoint_client
        self.pipeline_runner = pipeline_runner
        self.notification_service = notification_service
        self.metrics = metrics
        self.settings = settings
        self.runner_factory = runner_factory
        self.config_asset_sync = config_asset_sync
        self.cleanup = RuntimeCleanup(settings)
        self._last_sync_health: dict = {}

    def _load_seen(self) -> dict:
        if self.settings.seen_files_path.exists():
            try:
                return json.loads(self.settings.seen_files_path.read_text(encoding="utf-8"))
            except Exception as exc:
                logger.warning("Cannot read seen_files.json: %s", exc)
        return {}

    def _save_seen(self, seen: dict) -> None:
        self.settings.seen_files_path.parent.mkdir(parents=True, exist_ok=True)
        self.settings.seen_files_path.write_text(
            json.dumps(seen, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _update_health(self, cycle: int = 0, queue_size: int = 0) -> None:
        self.settings.health_file.parent.mkdir(parents=True, exist_ok=True)
        payload = self.metrics.get_health_data(cycle=cycle, queue_size=queue_size)
        if self._last_sync_health:
            payload["config_assets"] = self._last_sync_health
        self.settings.health_file.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _sync_config_assets(self) -> None:
        if self.config_asset_sync is None:
            return
        try:
            result = self.config_asset_sync.sync()
        except Exception as exc:
            logger.warning("Config asset sync failed; keeping current snapshot: %s", exc)
            self._last_sync_health = {
                "checked_at": datetime.now().isoformat(timespec="seconds"),
                "reload_required": False,
                "changed_assets": [],
                "downloaded_assets": [],
                "errors": [str(exc)],
            }
            return

        self._last_sync_health = result.as_health_dict()
        if result.downloaded_assets:
            logger.info(
                "Config asset sync downloaded %d asset(s): %s",
                len(result.downloaded_assets),
                ", ".join(result.downloaded_assets),
            )
        if result.errors:
            for message in result.errors:
                logger.warning("%s", message)
        if result.reload_required:
            if self.runner_factory is None:
                raise RuntimeError("Config asset reload requested but no runner factory is configured")
            self.pipeline_runner = self.runner_factory()
            logger.info("Reloaded pipeline dependencies from refreshed config assets")

    def _write_daily_summary(self, date_str: str) -> None:
        summary = self.metrics.get_daily_summary()
        logger.info("=== DAILY SUMMARY (%s) ===", date_str)
        logger.info(
            "  Files processed: %d | Failed: %d | Success rate: %s",
            summary["files_processed"],
            summary["files_failed"],
            summary["success_rate"],
        )
        logger.info(
            "  Total rows: %d | Avg time: %.1fs/file",
            summary["total_rows"],
            summary["avg_time_per_file"],
        )
        logger.info(
            "  Gemini calls: %d | Retries: %d | Polls: %d",
            summary["gemini_calls"],
            summary["gemini_retries"],
            summary["polls"],
        )
        summary_path = self.settings.log_dir / "daily-summary.jsonl"
        try:
            summary_path.parent.mkdir(parents=True, exist_ok=True)
            with summary_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(summary, ensure_ascii=False, default=str) + "\n")
        except Exception as exc:
            logger.warning("Failed to write daily summary: %s", exc)

    def _process_file(self, file_info: dict, seen: dict) -> bool:
        file_id = file_info["id"]
        file_name = file_info["name"]
        base_name = Path(file_name).stem

        local_input = self.settings.work_dir / "input" / file_name
        local_output = self.settings.work_dir / "output" / f"{base_name}_output.xlsx"
        local_ckpt = self.settings.work_dir / "checkpoint" / f"{base_name}.json"

        try:
            logger.info("Downloading: %s", file_name)
            self.sharepoint_client.download_file(file_id, local_input)

            logger.info("Processing: %s", file_name)
            result = self.pipeline_runner.run_pipeline(local_input, local_output, local_ckpt)

            logger.info("Uploading results for: %s", file_name)
            self.sharepoint_client.upload_output(local_output)
            self.sharepoint_client.upload_checkpoint(local_ckpt)

            seen[file_id] = {
                "name": file_name,
                "status": "done",
                "processed_at": datetime.now().isoformat(),
                "total_rows": result.get("total_rows", 0),
                "duration_seconds": result.get("duration_seconds", 0),
            }
            self._save_seen(seen)

            rows = result.get("total_rows", 0)
            duration = result.get("duration_seconds", 0)
            self.metrics.record_success(file_name, rows, duration)
            self.notification_service.send_success(file_name, result)
            self.cleanup.cleanup_success_artifacts(
                local_input=local_input,
                local_output=local_output,
                local_checkpoint=local_ckpt,
            )
            logger.info("Completed: %s (%d rows in %.1fs)", file_name, rows, duration)
            return True
        except Exception as exc:
            error_type = type(exc).__name__
            error_msg = f"{error_type}: {exc}"
            logger.error("Failed processing %s: %s", file_name, error_msg)
            logger.debug(traceback.format_exc())
            self.metrics.record_failure(file_name, error_type, str(exc))

            entry = seen.get(file_id, {"name": file_name, "failures": 0})
            entry["failures"] = entry.get("failures", 0) + 1
            entry["last_error"] = error_msg
            entry["last_attempt"] = datetime.now().isoformat()

            if entry["failures"] >= MAX_FILE_RETRIES:
                entry["status"] = "failed"
                logger.error("Max retries reached for %s; marking as failed", file_name)
                self.notification_service.send_error(
                    file_name,
                    error_msg,
                    retry_count=entry["failures"],
                    max_retries=MAX_FILE_RETRIES,
                )
            else:
                entry["status"] = "retry"
                logger.warning(
                    "Will retry %s (attempt %d/%d)",
                    file_name,
                    entry["failures"],
                    MAX_FILE_RETRIES,
                )

            seen[file_id] = entry
            self._save_seen(seen)
            return False

    def poll_once(self, seen: dict) -> int:
        self._sync_config_assets()
        try:
            remote_files = self.sharepoint_client.list_files()
        except Exception as exc:
            logger.error("Cannot list SharePoint files: %s", exc)
            return 0

        new_files = []
        for item in remote_files:
            file_id = item["id"]
            if file_id not in seen:
                new_files.append(item)
            elif seen[file_id].get("status") == "retry":
                new_files.append(item)

        if not new_files:
            logger.info("No new files to process")
            return 0

        logger.info("Found %d file(s) to process", len(new_files))
        processed = 0
        for item in new_files:
            if self._process_file(item, seen):
                processed += 1
        return processed

    def run_forever(self) -> None:
        logger.info("=" * 60)
        logger.info("DMS Feedback Classification Watcher starting...")
        logger.info("Poll interval: %d seconds", self.settings.poll_interval_seconds)
        logger.info("Work directory: %s", self.settings.work_dir)
        logger.info("=" * 60)

        seen = self._load_seen()
        logger.info("Loaded %d previously seen files", len(seen))

        cycle = 0
        while True:
            cycle += 1
            logger.info("--- Poll cycle %d ---", cycle)
            self.metrics.record_poll()

            prev_date = self.metrics.check_date_change()
            if prev_date is not None:
                self._write_daily_summary(prev_date)
                self.metrics.reset_daily()

            try:
                processed = self.poll_once(seen)
                if processed > 0:
                    logger.info("Processed %d file(s) this cycle", processed)
            except Exception as exc:
                logger.error("Unhandled error in poll cycle: %s", exc)
                logger.debug(traceback.format_exc())

            self.metrics.flush()
            self._update_health(cycle=cycle)
            self.cleanup.cleanup_housekeeping()
            logger.info("Sleeping %d seconds...", self.settings.poll_interval_seconds)
            time.sleep(self.settings.poll_interval_seconds)
