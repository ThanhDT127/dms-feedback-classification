"""Worker runtime for queued user-uploaded classification jobs."""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .analytics import (
    BatchClassificationResult,
    FeedbackAnalyticsRepository,
    read_feedback_workbook,
    sha256_file,
)
from .classification_jobs import JOB_STATUS_CANCELLED, ClassificationJobStore
from .exceptions import GatewayError, PipelineCancelled
from .pipeline.issue_classifier import get_label_config_snapshot
from .settings import Settings

logger = logging.getLogger("dms-web")

RunnerFactory = Callable[[], Any]
SharePointFactory = Callable[[], Any]
NotificationFactory = Callable[[], Any]


def is_retryable_classification_error(exc: BaseException) -> bool:
    """Return whether a pipeline failure is likely transient enough to retry."""
    if isinstance(exc, PipelineCancelled):
        return False
    if isinstance(exc, GatewayError):
        return exc.retryable
    if isinstance(exc, (FileNotFoundError, ValueError)):
        return False

    message = str(exc).lower()
    non_retryable_markers = (
        "cannot find text column",
        "missing input",
        "not a zip file",
        "invalid file",
        "unsupported file",
        "file is not a zip file",
    )
    return not any(marker in message for marker in non_retryable_markers)


class ClassificationWorkerManager:
    """Claim and execute persisted classification jobs in background worker loops."""

    def __init__(
        self,
        *,
        settings: Settings,
        job_store: ClassificationJobStore,
        runner_factory: RunnerFactory,
        sharepoint_factory: SharePointFactory,
        analytics_repository: FeedbackAnalyticsRepository | None = None,
        notification_factory: NotificationFactory | None = None,
    ) -> None:
        self.settings = settings
        self.job_store = job_store
        self.runner_factory = runner_factory
        self.sharepoint_factory = sharepoint_factory
        self.analytics_repository = analytics_repository
        self.notification_factory = notification_factory
        self.worker_id = f"classify-worker-{uuid.uuid4().hex[:8]}"
        self._stop_event = threading.Event()
        self._threads: list[threading.Thread] = []

    def start(self) -> None:
        self._threads = [thread for thread in self._threads if thread.is_alive()]
        if len(self._threads) >= self.settings.classification_worker_concurrency:
            return
        recovered = self.job_store.recover_stale_running_jobs(
            stale_after_seconds=self.settings.classification_stale_running_timeout_seconds,
            max_retries=self.settings.classification_retry_count,
        )
        if recovered:
            logger.info("Recovered %d stale running classification jobs", recovered)

        for idx in range(len(self._threads), self.settings.classification_worker_concurrency):
            thread = threading.Thread(
                target=self._run_loop,
                name=f"{self.worker_id}-{idx + 1}",
                daemon=True,
            )
            thread.start()
            self._threads.append(thread)
        logger.info("Started %d classification worker loops", len(self._threads))

    def stop(self, timeout: float = 5.0) -> None:
        self._stop_event.set()
        deadline = time.monotonic() + timeout
        for thread in self._threads:
            remaining = max(0.0, deadline - time.monotonic())
            thread.join(timeout=remaining)
        self._threads.clear()

    def _run_loop(self) -> None:
        last_sweep = time.monotonic()
        while not self._stop_event.is_set():
            try:
                job = self.job_store.claim_next_job(
                    worker_id=self.worker_id,
                    global_running_limit=self.settings.classification_worker_concurrency,
                    per_user_running_limit=self.settings.classification_per_user_running_limit,
                )
            except sqlite3.OperationalError as exc:
                if getattr(exc, "sqlite_errorcode", 0) & 0xFF not in (
                    sqlite3.SQLITE_BUSY,
                    sqlite3.SQLITE_LOCKED,
                ):
                    raise
                logger.warning(
                    "Classification queue is busy; retrying after poll interval: %s", exc
                )
                self._stop_event.wait(self.settings.classification_worker_poll_interval_seconds)
                continue
            if job is None:
                if time.monotonic() - last_sweep >= 60.0:
                    last_sweep = time.monotonic()
                    try:
                        recovered = self.job_store.recover_stale_running_jobs(
                            stale_after_seconds=self.settings.classification_stale_running_timeout_seconds,
                            max_retries=self.settings.classification_retry_count,
                        )
                        if recovered:
                            logger.info("Recovered %d stale running classification jobs", recovered)
                    except Exception as exc:  # janitor khong duoc giet worker loop
                        logger.warning("Stale job sweep skipped: %s", exc)
                self._stop_event.wait(self.settings.classification_worker_poll_interval_seconds)
                continue
            self._process_job(job)

    def _process_job(self, job: dict) -> None:
        job_id = job["job_id"]
        input_path = Path(job["input_path"])
        output_path = Path(job["output_path"])
        ckpt_path = self.settings.work_dir / "checkpoint" / f"{job_id}.json"
        last_heartbeat = 0.0

        def heartbeat_if_due(force: bool = False) -> None:
            nonlocal last_heartbeat
            now = time.monotonic()
            if (
                force
                or now - last_heartbeat >= self.settings.classification_worker_heartbeat_seconds
            ):
                self.job_store.heartbeat(job_id)
                last_heartbeat = now

        def cancellation_check() -> bool:
            heartbeat_if_due()
            return self._stop_event.is_set() or self.job_store.is_cancellation_requested(job_id)

        try:
            if cancellation_check():
                if self.analytics_repository is not None:
                    self.analytics_repository.rollback_cancelled_job(job_id)
                self.job_store.mark_cancelled(job_id, "Job đã được yêu cầu hủy trước khi xử lý.")
                self._sync_seen_files_status(job, status="cancelled")
                return

            if not input_path.is_file():
                self.job_store.fail_job(job_id, "Input file does not exist")
                self._sync_seen_files_status(
                    job, status="failed", error="Input file does not exist"
                )
                self._notify("error", job, error="Input file does not exist")
                return

            # Auto-upload input file to SharePoint if enabled (skip for reclassify to avoid SP timestamp bump)
            if self.settings.upload_input_to_sharepoint and job.get("mode") != "reclassify":
                self._upload_input_to_sharepoint(job, input_path)

            if cancellation_check():
                if self.analytics_repository is not None:
                    self.analytics_repository.rollback_cancelled_job(job_id)
                self.job_store.mark_cancelled(
                    job_id, "Job đã được yêu cầu hủy trước khi chạy pipeline."
                )
                self._sync_seen_files_status(job, status="cancelled")
                return

            if self.analytics_repository is not None:
                parsed_input = read_feedback_workbook(input_path)
                source_file_key = self.analytics_repository.get_job_source_file_key(job_id)
                self.analytics_repository.persist_input_snapshot(
                    job_id=job_id,
                    source_file_key=source_file_key or f"sha256:{sha256_file(input_path)}",
                    source_file_name=job["filename"],
                    records=parsed_input.records,
                    deactivate_absent=False,
                )

            runner = self.runner_factory()
            if runner is None:
                if self.analytics_repository is not None:
                    self.analytics_repository.mark_job_unfinished_failed(job_id)
                self.job_store.fail_job(job_id, "PipelineRunner is not ready. Check configuration.")
                self._sync_seen_files_status(
                    job,
                    status="failed",
                    error="PipelineRunner is not ready. Check configuration.",
                )
                return

            heartbeat_if_due(force=True)

            def progress_callback(
                done: int | None = None,
                total: int | None = None,
                new_results: list[dict] | None = None,
                step: int | None = None,
                step_status: str | None = None,
            ) -> None:
                heartbeat_if_due(force=True)
                if cancellation_check():
                    return
                self.job_store.update_progress(
                    job_id,
                    done=done,
                    total=total,
                    step=step,
                    step_status=step_status,
                )
                if new_results and not cancellation_check():
                    if self.analytics_repository is not None:
                        batch_results = [
                            BatchClassificationResult(
                                source_row_number=int(item["source_row_number"]),
                                text=str(item.get("text", "")),
                                product=item.get("product") or None,
                                product_line=item.get("product_line") or None,
                                model=item.get("model") or None,
                                bm25_score=item.get("bm25_score"),
                                sentiment=item.get("sentiment") or None,
                                labels=list(item.get("labels", [])),
                                brand=item.get("brand") or None,
                            )
                            for item in new_results
                        ]
                        self.analytics_repository.apply_batch_results(
                            job_id=job_id,
                            results=batch_results,
                            minor_to_major=get_label_config_snapshot()["minor_to_major"],
                        )
                    self.job_store.append_results(job_id, new_results)

            runner_settings = getattr(runner, "settings", self.settings)
            gateway_kwargs = (
                {"actor": job.get("owner_username")}
                if runner_settings.gemini_backend == "gateway"
                else {}
            )
            result = runner.run_pipeline(
                input_path=input_path,
                output_path=output_path,
                ckpt_path=ckpt_path,
                progress_callback=progress_callback,
                cancellation_check=cancellation_check,
                job_id=job_id,
                **gateway_kwargs,
            )

            if cancellation_check():
                if self.analytics_repository is not None:
                    self.analytics_repository.rollback_cancelled_job(job_id)
                self.job_store.mark_cancelled(job_id, "Job đã được yêu cầu hủy.")
                self._sync_seen_files_status(job, status="cancelled")
                return

            sp_uploaded, sp_folder, sp_web_url = self._upload_output(job, output_path)
            latest = self.job_store.get_job(job_id, include_results=False)
            if latest and latest.get("status") == JOB_STATUS_CANCELLED:
                if self.analytics_repository is not None:
                    self.analytics_repository.rollback_cancelled_job(job_id)
                self._sync_seen_files_status(job, status="cancelled")
                return
            if latest and latest.get("cancellation_requested"):
                if self.analytics_repository is not None:
                    self.analytics_repository.rollback_cancelled_job(job_id)
                self.job_store.mark_cancelled(job_id, "Job đã được yêu cầu hủy.")
                self._sync_seen_files_status(job, status="cancelled")
                return

            self.job_store.complete_job(
                job_id,
                total_rows=result.get("total_rows", 0),
                rows_done=result.get("processed_rows", 0),
                output_path=result.get("output_path", output_path),
                duration_seconds=result.get("duration_seconds", 0),
                sp_uploaded=sp_uploaded,
                sp_folder=sp_folder,
                sp_web_url=sp_web_url,
            )
            self._sync_seen_files_status(
                job,
                status="done",
                total_rows=int(result.get("total_rows", 0) or 0),
                duration_seconds=float(result.get("duration_seconds", 0) or 0.0),
            )
            self._notify(
                "success",
                job,
                result={
                    "total_rows": int(result.get("total_rows", 0) or 0),
                    "duration_seconds": float(result.get("duration_seconds", 0) or 0.0),
                },
            )
        except PipelineCancelled:
            if self.analytics_repository is not None:
                self.analytics_repository.rollback_cancelled_job(job_id)
            self.job_store.mark_cancelled(job_id, "Job đã được hủy ở ranh giới batch an toàn.")
            self._sync_seen_files_status(job, status="cancelled")
        except Exception as exc:
            logger.error("Classification job %s failed: %s", job_id, exc, exc_info=True)
            if cancellation_check():
                if self.analytics_repository is not None:
                    self.analytics_repository.rollback_cancelled_job(job_id)
                self.job_store.mark_cancelled(job_id, "Job đã được hủy sau khi worker nhận lỗi.")
                self._sync_seen_files_status(job, status="cancelled")
                return
            if self.analytics_repository is not None:
                self.analytics_repository.mark_job_unfinished_failed(job_id)
            if is_retryable_classification_error(exc):
                retried = self.job_store.maybe_retry_after_failure(
                    job_id,
                    error=str(exc),
                    max_retries=self.settings.classification_retry_count,
                )
                if retried and retried.get("status") != "error":
                    logger.info(
                        "Re-queued classification job %s for retry %s/%s",
                        job_id,
                        retried.get("retry_count"),
                        self.settings.classification_retry_count,
                    )
                    return
                self._sync_seen_files_status(job, status="failed", error=str(exc))
                self._notify(
                    "error", job, error=str(exc), retry_count=int(job.get("retry_count") or 0)
                )
                return
            self.job_store.fail_job(job_id, str(exc))
            self._sync_seen_files_status(job, status="failed", error=str(exc))
            self._notify(
                "error", job, error=str(exc), retry_count=int(job.get("retry_count") or 0)
            )

    def _notify(
        self,
        kind: str,
        job: dict,
        *,
        result: dict | None = None,
        error: str = "",
        retry_count: int = 0,
    ) -> None:
        """Gui mail/Teams cho job cua web (watcher.py chi bao cho luong SharePoint)."""
        if self.notification_factory is None:
            return
        from .notification import refresh_notification_settings_from_env

        refresh_notification_settings_from_env(self.settings)
        if not getattr(self.settings, f"notify_on_{kind}", True):
            return
        file_name = job.get("filename") or Path(job.get("input_path", "")).name
        try:
            service = self.notification_factory()
            if service is None:
                return
            if kind == "success":
                service.send_success(file_name, result or {})
            else:
                service.send_error(
                    file_name,
                    error,
                    retry_count=retry_count,
                    max_retries=self.settings.classification_retry_count,
                )
        except Exception as exc:  # thong bao khong duoc lam chet job
            logger.warning("Notification (%s) failed for %s: %s", kind, file_name, exc)

    def _sync_seen_files_status(
        self,
        job: dict,
        *,
        status: str,
        total_rows: int = 0,
        duration_seconds: float = 0.0,
        error: str = "",
    ) -> None:
        """Keep seen_files.json in sync when a job completes or terminally fails."""
        import json

        from .sharepoint_cache import get_sharepoint_list_cache
        from .time_utils import utc_now_iso
        from .utils import atomic_write_json

        filename = job.get("filename", "")
        if not filename:
            return
        seen_path = self.settings.work_dir / "seen_files.json"
        seen_data: dict[str, Any] = {}
        if seen_path.is_file():
            try:
                loaded = json.loads(seen_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    seen_data = loaded
            except Exception as exc:
                logger.warning("Could not read seen_files.json to sync status: %s", exc)
                return
        elif job.get("mode") != "reclassify":
            return

        matched_keys = [
            fid
            for fid, info in seen_data.items()
            if isinstance(info, dict) and info.get("name") == filename
        ]
        if not matched_keys and (job.get("mode") == "reclassify" or status == "cancelled"):
            fallback_id = f"local:{filename}"
            seen_data[fallback_id] = {"name": filename}
            matched_keys = [fallback_id]

        if not matched_keys:
            return

        now_iso = utc_now_iso()
        for fid in matched_keys:
            info = seen_data[fid]
            prev_failures = int(info.get("failures", 0) or 0)
            info["status"] = status
            if status == "done":
                if prev_failures > 0:
                    info["past_failures"] = max(
                        int(info.get("past_failures", 0) or 0), prev_failures
                    )
                info["failures"] = 0
                info["processed_at"] = now_iso
                if total_rows:
                    info["total_rows"] = total_rows
                if duration_seconds:
                    info["duration_seconds"] = round(duration_seconds, 2)
                info.pop("last_error", None)
            elif status == "cancelled":
                info["cancelled_at"] = now_iso
                info.pop("last_error", None)
            elif status == "failed":
                info["failures"] = max(1, prev_failures + 1)
                info["last_attempt"] = now_iso
                if error:
                    info["last_error"] = str(error)[:300]

        try:
            atomic_write_json(seen_path, seen_data)
            get_sharepoint_list_cache().invalidate()
        except Exception as exc:
            logger.warning("Could not write seen_files.json after job sync: %s", exc)

    def _upload_output(self, job: dict, output_path: Path) -> tuple[bool, str | None, str | None]:
        try:
            sp_client = self.sharepoint_factory()
            if not sp_client or not self.settings.sp_output_folder:
                return False, None, None
            orig_filename = job.get("filename", "")
            remote_filename = (
                f"{Path(orig_filename).stem}_output.xlsx" if orig_filename else output_path.name
            )
            result = sp_client.upload_file(
                output_path,
                self.settings.sp_output_folder,
                remote_filename=remote_filename,
            )
            return True, self.settings.sp_output_folder, result.get("webUrl")
        except Exception as exc:
            logger.warning("Could not upload job %s output to SharePoint: %s", job["job_id"], exc)
            return False, None, None

    def _upload_input_to_sharepoint(self, job: dict, input_path: Path) -> None:
        """Upload the classification input file to the SharePoint Input folder."""
        try:
            sp_client = self.sharepoint_factory()
            if not sp_client or not self.settings.sp_input_folder:
                return
            # Use the original filename (without UUID prefix) for the remote file
            remote_filename = job.get("filename", "") or input_path.name
            uploaded = sp_client.upload_file(
                input_path,
                self.settings.sp_input_folder,
                remote_filename=remote_filename,
            )
            if isinstance(uploaded, dict) and uploaded.get("id"):
                try:
                    import json

                    from .time_utils import utc_now_iso
                    from .utils import atomic_write_json

                    seen_path = self.settings.work_dir / "seen_files.json"
                    seen_data: dict[str, Any] = {}
                    if seen_path.is_file():
                        loaded = json.loads(seen_path.read_text(encoding="utf-8"))
                        if isinstance(loaded, dict):
                            seen_data = loaded
                    seen_data[str(uploaded["id"])] = {
                        "name": remote_filename,
                        "status": "processing",
                        "failures": 0,
                        "last_attempt": utc_now_iso(),
                        "lastModifiedDateTime": uploaded.get("lastModifiedDateTime", ""),
                    }
                    atomic_write_json(seen_path, seen_data)
                except Exception as seen_exc:
                    logger.debug("Could not record uploaded SharePoint input in seen_files.json: %s", seen_exc)
            logger.info(
                "Uploaded input file for job %s to SharePoint/%s/%s",
                job["job_id"],
                self.settings.sp_input_folder,
                remote_filename,
            )
        except Exception as exc:
            logger.warning(
                "Could not upload job %s input to SharePoint: %s",
                job["job_id"],
                exc,
            )


def build_default_worker_manager() -> ClassificationWorkerManager | None:
    """Build a worker manager from the web dependency container."""
    from .web import deps

    settings = deps.get_settings()
    job_store = deps.get_classification_job_store()
    analytics_repository = deps.get_feedback_analytics_repository()
    if settings is None or job_store is None or analytics_repository is None:
        return None
    settings.ensure_runtime_dirs()
    return ClassificationWorkerManager(
        settings=settings,
        job_store=job_store,
        runner_factory=deps.get_pipeline_runner,
        sharepoint_factory=deps.get_sharepoint_client,
        analytics_repository=analytics_repository,
        notification_factory=deps.get_notification_service,
    )


def main() -> None:
    """Run the classification worker as a standalone process."""
    manager = build_default_worker_manager()
    if manager is None:
        raise SystemExit("Classification worker could not start because settings are incomplete.")
    manager.start()
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        manager.stop()


if __name__ == "__main__":
    main()
