"""Polling watcher service."""

from __future__ import annotations

import json
import logging
import threading
import traceback
from pathlib import Path

from .classification_jobs import ClassificationJobStore
from .cleanup import RuntimeCleanup
from .config_assets import ConfigAssetSyncService
from .metrics import MetricsCollector
from .notification import NotificationService
from .pipeline.runner import PipelineRunner
from .settings import Settings
from .sharepoint import SharePointClient
from .time_utils import utc_now_iso
from .utils import atomic_write_json

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
        job_store: ClassificationJobStore | None = None,
    ) -> None:
        self.sharepoint_client = sharepoint_client
        self.pipeline_runner = pipeline_runner
        self.notification_service = notification_service
        self.metrics = metrics
        self.settings = settings
        self.runner_factory = runner_factory
        self.config_asset_sync = config_asset_sync
        self.job_store = job_store
        self.cleanup = RuntimeCleanup(settings)
        self._last_sync_health: dict = {}
        self._shutdown_event = threading.Event()

    def _load_seen(self) -> dict:
        if self.settings.seen_files_path.exists():
            try:
                return json.loads(self.settings.seen_files_path.read_text(encoding="utf-8"))
            except Exception as exc:
                logger.warning("Cannot read seen_files.json: %s", exc)
        return {}

    def _save_seen(self, seen: dict) -> None:
        atomic_write_json(self.settings.seen_files_path, seen)
        try:
            self.sharepoint_client.upload_checkpoint(self.settings.seen_files_path)
        except Exception as exc:
            logger.warning("Failed to upload seen_files.json to SharePoint Check_Point/: %s", exc)

    def _restore_state_from_sharepoint(self) -> None:
        try:
            seen_missing = True
            if self.settings.seen_files_path.exists():
                try:
                    seen_data = json.loads(
                        self.settings.seen_files_path.read_text(encoding="utf-8")
                    )
                    if seen_data and len(seen_data) > 0:
                        seen_missing = False
                except Exception:
                    pass

            metrics_missing = True
            if self.settings.metrics_path.exists():
                try:
                    metrics_data = json.loads(
                        self.settings.metrics_path.read_text(encoding="utf-8")
                    )
                    if metrics_data and metrics_data.get("files_processed", 0) > 0:
                        metrics_missing = False
                except Exception:
                    pass

            if not seen_missing and not metrics_missing:
                return

            logger.info(
                "Local state files missing or empty (seen_missing: %s, metrics_missing: %s). Attempting to restore from SharePoint Check_Point/...",
                seen_missing,
                metrics_missing,
            )
            ckpt_items = self.sharepoint_client.list_folder_items(
                self.settings.sp_checkpoint_folder
            )

            for item in ckpt_items:
                name = item.get("name")
                file_id = item.get("id")
                if not isinstance(file_id, str):
                    continue
                if name == "seen_files.json" and seen_missing:
                    logger.info("Restoring seen_files.json from SharePoint...")
                    self.settings.seen_files_path.parent.mkdir(parents=True, exist_ok=True)
                    self.sharepoint_client.download_file(file_id, self.settings.seen_files_path)
                    logger.info("Restoring seen_files.json complete")
                elif name == "metrics.json" and metrics_missing:
                    logger.info("Restoring metrics.json from SharePoint...")
                    self.settings.metrics_path.parent.mkdir(parents=True, exist_ok=True)
                    self.sharepoint_client.download_file(file_id, self.settings.metrics_path)
                    logger.info("Restoring metrics.json complete")
                    # Force metrics reload after download
                    self.metrics._load()
        except Exception as exc:
            logger.warning("Failed to restore state files from SharePoint Check_Point/: %s", exc)

    def _reconcile_state_with_sharepoint(self, seen: dict) -> None:
        try:
            logger.info("Starting self-healing state reconciliation with SharePoint...")
            input_files = self.sharepoint_client.list_files()
            output_files = self.sharepoint_client.list_folder_items(self.settings.sp_output_folder)

            output_stems = set()
            for out_f in output_files:
                name = out_f.get("name", "")
                if name.endswith(".xlsx"):
                    stem = Path(name).stem
                    if stem.endswith("_output"):
                        stem = stem[:-7]
                    output_stems.add(stem.lower())

            logger.info("Found %d completed output files in SharePoint Output/", len(output_stems))
            reconciled_count = 0

            for inp_f in input_files:
                file_id = inp_f.get("id")
                file_name = inp_f.get("name", "")
                if not file_name.endswith(".xlsx") or not file_id:
                    continue

                if file_id in seen:
                    continue

                inp_stem = Path(file_name).stem.lower()
                if inp_stem in output_stems:
                    logger.info(
                        "Self-healing: Matching output found for input %s. Registering as done.",
                        file_name,
                    )
                    seen[file_id] = {
                        "name": file_name,
                        "status": "done",
                        "processed_at": utc_now_iso(),
                        "lastModifiedDateTime": inp_f.get("lastModifiedDateTime", ""),
                        "total_rows": 0,
                        "duration_seconds": 0.0,
                        "label_distribution": {},
                    }
                    reconciled_count += 1

            if reconciled_count > 0:
                logger.info(
                    "Reconciliation complete. Marked %d missing files as done.", reconciled_count
                )
                self._save_seen(seen)
                # Force rebuild metrics after reconciliation
                self.metrics._load()
            else:
                logger.info("Reconciliation complete. No new files matched.")
        except Exception as exc:
            logger.warning("Failed to reconcile state with SharePoint: %s", exc)

    def _update_health(self, cycle: int = 0, queue_size: int = 0) -> None:
        payload = self.metrics.get_health_data(cycle=cycle, queue_size=queue_size)
        payload["model"] = self.settings.gemini_model
        payload["poll_interval"] = self.settings.poll_interval_seconds
        payload["pending_retry_files"] = self.metrics.get_pending_retry_count()
        if self._last_sync_health:
            payload["config_assets"] = self._last_sync_health
        atomic_write_json(self.settings.health_file, payload)

    def _sync_config_assets(self) -> None:
        if self.config_asset_sync is None:
            return
        try:
            result = self.config_asset_sync.sync()
        except Exception as exc:
            logger.warning("Config asset sync failed; keeping current snapshot: %s", exc)
            self._last_sync_health = {
                "status": "error",
                "checked_at": utc_now_iso(),
                "reload_required": False,
                "changed_assets": [],
                "downloaded_assets": [],
                "errors": [str(exc)],
            }
            return

        self._last_sync_health = result.as_health_dict()
        self._last_sync_health["status"] = "ok"
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
                raise RuntimeError(
                    "Config asset reload requested but no runner factory is configured"
                )
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
        import uuid

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

            # Preserve retry history before overwriting entry
            _old_entry = seen.get(file_id, {})
            was_previously_failed = _old_entry.get("status") in ("failed", "retry")

            seen[file_id] = {
                "name": file_name,
                "status": "done",
                "processed_at": utc_now_iso(),
                "lastModifiedDateTime": file_info.get("lastModifiedDateTime", ""),
                "total_rows": result.get("total_rows", 0),
                "duration_seconds": result.get("duration_seconds", 0),
                "label_distribution": result.get("label_distribution", {}),
                # Giữ lại số lần thất bại trước đó để track retry history
                "past_failures": _old_entry.get("failures", 0) or _old_entry.get("past_failures", 0),
                "failures": 0,
            }
            self._save_seen(seen)

            rows = result.get("total_rows", 0)
            duration = result.get("duration_seconds", 0)
            label_dist = result.get("label_distribution", {})
            self.metrics.record_success(
                file_name, rows, duration, label_dist, was_previously_failed=was_previously_failed
            )

            # --- Record event in SQLite (task 1.3 + 1.5) ---
            if self.job_store is not None:
                try:
                    watcher_job_id = str(uuid.uuid4())
                    self.job_store.create_job(
                        job_id=watcher_job_id,
                        owner_username="system_watcher",
                        owner_role="system",
                        filename=file_name,
                        mode="watcher",
                        input_path=local_input,
                        output_path=local_output,
                    )
                    self.job_store.complete_job(
                        watcher_job_id,
                        total_rows=rows,
                        rows_done=rows,
                        output_path=local_output,
                        duration_seconds=float(duration),
                    )
                    # Store label_distribution as a job result payload (task 1.5)
                    if label_dist:
                        self.job_store.append_results(
                            watcher_job_id,
                            [{"label_distribution": label_dist, "source": "watcher"}],
                        )
                except Exception as db_exc:
                    logger.warning("Failed to record watcher job in SQLite: %s", db_exc)

            try:
                self.sharepoint_client.upload_checkpoint(self.settings.metrics_path)
            except Exception as metrics_upload_exc:
                logger.warning(
                    "Failed to upload metrics.json to SharePoint Check_Point/: %s",
                    metrics_upload_exc,
                )
            if getattr(self.settings, "notify_on_success", True):
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

            entry = seen.get(file_id, {"name": file_name, "failures": 0})
            entry["failures"] = entry.get("failures", 0) + 1
            entry["last_error"] = error_msg
            entry["last_attempt"] = utc_now_iso()

            is_final = entry["failures"] >= MAX_FILE_RETRIES
            self.metrics.record_retry_failure(
                file_name, error_type, str(exc), is_final=is_final
            )

            # --- Record failure event in SQLite (task 1.4) ---
            if self.job_store is not None:
                try:
                    watcher_job_id = str(uuid.uuid4())
                    error_label = (
                        f"FINAL: {error_msg} (max {MAX_FILE_RETRIES} retries exceeded)"
                        if is_final
                        else error_msg
                    )
                    self.job_store.create_job(
                        job_id=watcher_job_id,
                        owner_username="system_watcher",
                        owner_role="system",
                        filename=file_name,
                        mode="watcher",
                        input_path=local_input,
                        output_path=local_output,
                    )
                    self.job_store.fail_job(watcher_job_id, error=error_label)
                except Exception as db_exc:
                    logger.warning("Failed to record watcher failure in SQLite: %s", db_exc)

            try:
                self.sharepoint_client.upload_checkpoint(self.settings.metrics_path)
            except Exception as upload_exc:
                logger.warning(
                    "Failed to upload metrics.json to SharePoint Check_Point/: %s",
                    upload_exc,
                )

            if is_final:
                entry["status"] = "failed"
                logger.error("Max retries reached for %s; marking as failed", file_name)
                if getattr(self.settings, "notify_on_error", True):
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


    def reload_settings(self) -> None:
        """Reload settings from disk and update dependent services in-place."""
        from .settings import get_settings

        if hasattr(get_settings, "cache_clear"):
            get_settings.cache_clear()
        try:
            new_settings = get_settings()

            # Save original configuration values to compare
            old_backend = self.settings.gemini_backend
            old_model = self.settings.gemini_model
            old_key = self.settings.gemini_api_key

            # 1. Update self.settings fields in-place
            for field in new_settings.__class__.model_fields:
                val = getattr(new_settings, field)
                setattr(self.settings, field, val)

            # 2. Re-initialize pipeline assets reload if Gemini configuration changed
            backend_changed = old_backend != self.settings.gemini_backend
            model_changed = old_model != self.settings.gemini_model
            key_changed = old_key != self.settings.gemini_api_key

            if backend_changed or model_changed or key_changed:
                logger.info(
                    "Watcher detected Gemini settings changed: backend=%s->%s, model=%s->%s",
                    old_backend,
                    self.settings.gemini_backend,
                    old_model,
                    self.settings.gemini_model,
                )

                # Clear cached lazy clients inside GeminiClient
                if hasattr(self.pipeline_runner, "gemini"):
                    self.pipeline_runner.gemini._vertex_client = None
                    self.pipeline_runner.gemini._apikey_model = None

                # Re-create RAGProductMatcher with new settings
                if hasattr(self.pipeline_runner, "rag"):
                    from .pipeline.rag_product import RAGProductMatcher

                    self.pipeline_runner.rag = RAGProductMatcher(
                        settings=self.settings, gemini=self.pipeline_runner.gemini
                    )

            # 3. Synchronize other modules that references settings
            self.cleanup.settings = self.settings
            logger.info("Watcher settings hot-reloaded successfully")

        except Exception as exc:
            logger.error("Failed to hot-reload settings in watcher: %s", exc)

    def poll_once(self, seen: dict) -> int:
        self.reload_settings()
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
            elif seen[file_id].get("status") == "failed":
                # Auto-reset if file was modified on SharePoint after it failed
                stored_modified = seen[file_id].get("lastModifiedDateTime", "")
                remote_modified = item.get("lastModifiedDateTime", "")
                if remote_modified > stored_modified:
                    logger.info(
                        "Auto-resetting failed file %s (modified on SharePoint: %s > %s)",
                        item.get("name", file_id),
                        remote_modified,
                        stored_modified,
                    )
                    seen[file_id]["status"] = "retry"
                    seen[file_id]["failures"] = 0
                    self._save_seen(seen)
                    new_files.append(item)

        if not new_files:
            logger.info("No new files to process")
            return 0

        logger.info("Found %d file(s) to process", len(new_files))
        processed = 0
        for item in new_files:
            if self._shutdown_event.is_set():
                logger.info("Shutdown requested; stopping mid-poll after current file")
                break
            if self._process_file(item, seen):
                processed += 1
        return processed

    def request_shutdown(self) -> None:
        """Request graceful shutdown. Safe to call from a signal handler."""
        logger.info("Shutdown requested — will stop after current operation")
        self._shutdown_event.set()

    def _migrate_seen_files_to_sqlite(self, seen: dict) -> None:
        """One-time migration of seen_files.json history into SQLite classification_jobs.

        Idempotent: skips if system_watcher records already exist.
        """
        import uuid

        if self.job_store is None:
            return
        if not seen:
            return

        # Idempotency check (task 2.2): skip if already migrated
        try:
            with self.job_store._lock, self.job_store._conn() as conn:
                count = conn.execute(
                    "SELECT COUNT(*) FROM classification_jobs WHERE owner_username = 'system_watcher'"
                ).fetchone()[0]
            if count > 0:
                logger.info(
                    "Migration skipped: %d system_watcher records already exist in SQLite", count
                )
                return
        except Exception as exc:
            logger.warning("Could not check migration status: %s", exc)
            return

        logger.info("Starting one-time migration of %d seen_files entries to SQLite...", len(seen))
        migrated = 0
        skipped = 0

        for _file_id, info in seen.items():
            status = info.get("status", "")
            file_name = info.get("name", "unknown")
            try:
                watcher_job_id = str(uuid.uuid4())
                # Determine timestamps
                last_modified = info.get("lastModifiedDateTime", "")
                processed_at = info.get("processed_at", "")
                last_attempt = info.get("last_attempt", "")

                # Use SharePoint modification date as completed_at for the daily chart
                if status == "done":
                    completed_at = last_modified or processed_at
                elif status in ("failed", "retry"):
                    completed_at = last_attempt or last_modified or processed_at
                else:
                    skipped += 1
                    continue

                if not completed_at:
                    skipped += 1
                    continue

                local_path = self.settings.work_dir / "input" / file_name
                self.job_store.create_job(
                    job_id=watcher_job_id,
                    owner_username="system_watcher",
                    owner_role="system",
                    filename=file_name,
                    mode="watcher",
                    input_path=local_path,
                    output_path=local_path,  # placeholder, no output for historical entries
                )

                if status == "done":
                    total_rows = int(info.get("total_rows", 0))
                    duration = float(info.get("duration_seconds", 0.0))
                    # Directly set completed_at to the historical date via SQL
                    with self.job_store._lock, self.job_store._conn() as conn:
                        conn.execute(
                            """UPDATE classification_jobs
                               SET status = 'completed', total_rows = ?, rows_done = ?, percent = 100,
                                   duration_seconds = ?, completed_at = ?, updated_at = ?
                               WHERE job_id = ?""",
                            (total_rows, total_rows, duration, completed_at, completed_at, watcher_job_id),
                        )
                        conn.commit()
                    # Migrate label distribution
                    label_dist = info.get("label_distribution", {})
                    if label_dist:
                        self.job_store.append_results(
                            watcher_job_id,
                            [{"label_distribution": label_dist, "source": "watcher_migration"}],
                        )
                else:  # failed / retry
                    error_msg = info.get("last_error", f"status={status} at migration")
                    with self.job_store._lock, self.job_store._conn() as conn:
                        conn.execute(
                            """UPDATE classification_jobs
                               SET status = 'error', error = ?, completed_at = ?, updated_at = ?
                               WHERE job_id = ?""",
                            (error_msg, completed_at, completed_at, watcher_job_id),
                        )
                        conn.commit()

                migrated += 1
            except Exception as exc:
                logger.warning("Failed to migrate entry %s (%s): %s", file_name, _file_id, exc)
                skipped += 1

        logger.info(
            "Migration complete: %d entries migrated, %d skipped", migrated, skipped
        )

    def _reconcile_sqlite_with_seen_files(self, seen: dict) -> None:
        """Fix SQLite records for files done in seen_files but only 'error' in SQLite.

        Edge case: file was retried successfully between old code (no SQLite) and new code.
        seen_files.json = done, SQLite = only error record → ghost red bar in chart.
        Fix: create a synthetic 'completed' record using processed_at from seen_files.json.
        """
        import uuid

        if self.job_store is None or not seen:
            return

        try:
            # Get all filenames that have only error records (no completed) in SQLite
            with self.job_store._lock, self.job_store._conn() as conn:
                error_only = conn.execute(
                    """
                    SELECT filename
                    FROM classification_jobs
                    WHERE owner_username = 'system_watcher'
                    GROUP BY filename
                    HAVING SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) = 0
                      AND  SUM(CASE WHEN status = 'error'     THEN 1 ELSE 0 END) > 0
                    """
                ).fetchall()

            if not error_only:
                return

            error_filenames = {row["filename"] for row in error_only}

            # Build name → seen_info map from seen_files
            name_to_info = {
                info.get("name", ""): (fid, info)
                for fid, info in seen.items()
            }

            reconciled = 0
            for filename in error_filenames:
                if filename not in name_to_info:
                    continue
                _fid, info = name_to_info[filename]
                if info.get("status") != "done":
                    continue  # still actually failed, leave it

                # File is done in seen_files but only error in SQLite → create completed record
                completed_at = info.get("processed_at") or info.get("lastModifiedDateTime") or ""
                total_rows = info.get("total_rows", 0)
                duration = info.get("duration_seconds", 0.0)
                synthetic_job_id = str(uuid.uuid4())

                try:
                    self.job_store.create_job(
                        job_id=synthetic_job_id,
                        owner_username="system_watcher",
                        owner_role="system",
                        filename=filename,
                        mode="watcher",
                        input_path="",
                        output_path="",
                    )
                    self.job_store.complete_job(
                        synthetic_job_id,
                        total_rows=total_rows,
                        rows_done=total_rows,
                        output_path="",
                        duration_seconds=duration,
                    )
                    # Override completed_at with the actual processed_at from seen_files
                    if completed_at:
                        with self.job_store._lock, self.job_store._conn() as conn:
                            conn.execute(
                                "UPDATE classification_jobs SET completed_at = ?, updated_at = ? "
                                "WHERE job_id = ?",
                                (completed_at, completed_at, synthetic_job_id),
                            )
                            conn.commit()
                    reconciled += 1
                    logger.info(
                        "Reconciled SQLite for '%s': added synthetic completed record (processed_at=%s)",
                        filename, completed_at[:10] if completed_at else "unknown",
                    )
                except Exception as exc:
                    logger.warning("Failed to reconcile '%s': %s", filename, exc)

            if reconciled:
                logger.info("SQLite reconciliation complete: %d file(s) fixed", reconciled)

        except Exception as exc:
            logger.warning("SQLite reconciliation error: %s", exc)

    def run_forever(self) -> None:
        logger.info("=" * 60)
        logger.info("DMS Feedback Classification Watcher starting...")
        logger.info("Poll interval: %d seconds", self.settings.poll_interval_seconds)
        logger.info("Work directory: %s", self.settings.work_dir)
        logger.info("=" * 60)

        self._restore_state_from_sharepoint()
        seen = self._load_seen()
        logger.info("Loaded %d previously seen files", len(seen))
        self._reconcile_state_with_sharepoint(seen)

        # One-time migration of seen_files.json history into SQLite (task 2.3)
        self._migrate_seen_files_to_sqlite(seen)

        # Reconcile SQLite vs seen_files: fix files that succeeded before SQLite recording
        # existed (e.g. retried between old code and new code → seen=done but SQLite=error only)
        self._reconcile_sqlite_with_seen_files(seen)

        # Always force metrics reconstruction after state restore + reconciliation.
        # This ensures MetricsCollector reflects the latest seen_files.json even when
        # metrics.json was initialised before seen_files was downloaded (Docker cold-start).
        if seen and self.metrics.files_processed == 0:
            logger.info("Forcing metrics reconstruction from %d seen entries", len(seen))
            self.metrics._load()
            self.metrics.flush()

        cycle = 0
        while not self._shutdown_event.is_set():
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
            try:
                self.sharepoint_client.upload_checkpoint(self.settings.metrics_path)
            except Exception as exc:
                logger.warning("Failed to upload metrics.json to SharePoint Check_Point/: %s", exc)
            self._update_health(cycle=cycle)
            self.cleanup.cleanup_housekeeping()

            if self._shutdown_event.is_set():
                break

            logger.info("Sleeping %d seconds...", self.settings.poll_interval_seconds)
            # Use Event.wait() instead of time.sleep() so SIGTERM wakes us immediately
            self._shutdown_event.wait(timeout=self.settings.poll_interval_seconds)

        logger.info("Watcher exited gracefully")

