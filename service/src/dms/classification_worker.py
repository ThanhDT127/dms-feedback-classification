"""Worker runtime for queued user-uploaded classification jobs."""

from __future__ import annotations

import logging
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .classification_jobs import JOB_STATUS_CANCELLED, ClassificationJobStore
from .exceptions import PipelineCancelled
from .settings import Settings

logger = logging.getLogger("dms-web")

RunnerFactory = Callable[[], Any]
SharePointFactory = Callable[[], Any]


def is_retryable_classification_error(exc: BaseException) -> bool:
    """Return whether a pipeline failure is likely transient enough to retry."""
    if isinstance(exc, PipelineCancelled):
        return False
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
    ) -> None:
        self.settings = settings
        self.job_store = job_store
        self.runner_factory = runner_factory
        self.sharepoint_factory = sharepoint_factory
        self.worker_id = f"classify-worker-{uuid.uuid4().hex[:8]}"
        self._stop_event = threading.Event()
        self._threads: list[threading.Thread] = []

    def start(self) -> None:
        if self._threads:
            return
        recovered = self.job_store.recover_stale_running_jobs(
            stale_after_seconds=self.settings.classification_stale_running_timeout_seconds,
            max_retries=self.settings.classification_retry_count,
        )
        if recovered:
            logger.info("Recovered %d stale running classification jobs", recovered)

        for idx in range(self.settings.classification_worker_concurrency):
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
        while not self._stop_event.is_set():
            job = self.job_store.claim_next_job(
                worker_id=self.worker_id,
                global_running_limit=self.settings.classification_worker_concurrency,
                per_user_running_limit=self.settings.classification_per_user_running_limit,
            )
            if job is None:
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
                self.job_store.mark_cancelled(job_id, "Job đã được yêu cầu hủy trước khi xử lý.")
                return

            if not input_path.is_file():
                self.job_store.fail_job(job_id, "Input file does not exist")
                return

            # Auto-upload input file to SharePoint if enabled
            if self.settings.upload_input_to_sharepoint:
                self._upload_input_to_sharepoint(job, input_path)

            if cancellation_check():
                self.job_store.mark_cancelled(
                    job_id, "Job đã được yêu cầu hủy trước khi chạy pipeline."
                )
                return

            runner = self.runner_factory()
            if runner is None:
                self.job_store.fail_job(job_id, "PipelineRunner is not ready. Check configuration.")
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
                    self.job_store.append_results(job_id, new_results)

            result = runner.run_pipeline(
                input_path=input_path,
                output_path=output_path,
                ckpt_path=ckpt_path,
                progress_callback=progress_callback,
                cancellation_check=cancellation_check,
            )

            if cancellation_check():
                self.job_store.mark_cancelled(job_id, "Job đã được yêu cầu hủy.")
                return

            sp_uploaded, sp_folder, sp_web_url = self._upload_output(job, output_path)
            latest = self.job_store.get_job(job_id, include_results=False)
            if latest and latest.get("status") == JOB_STATUS_CANCELLED:
                return
            if latest and latest.get("cancellation_requested"):
                self.job_store.mark_cancelled(job_id, "Job đã được yêu cầu hủy.")
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
        except PipelineCancelled:
            self.job_store.mark_cancelled(job_id, "Job đã được hủy ở ranh giới batch an toàn.")
        except Exception as exc:
            logger.error("Classification job %s failed: %s", job_id, exc, exc_info=True)
            if cancellation_check():
                self.job_store.mark_cancelled(job_id, "Job đã được hủy sau khi worker nhận lỗi.")
                return
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
            self.job_store.fail_job(job_id, str(exc))

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
            sp_client.upload_file(
                input_path,
                self.settings.sp_input_folder,
                remote_filename=remote_filename,
            )
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
    if settings is None or job_store is None:
        return None
    settings.ensure_runtime_dirs()
    return ClassificationWorkerManager(
        settings=settings,
        job_store=job_store,
        runner_factory=deps.get_pipeline_runner,
        sharepoint_factory=deps.get_sharepoint_client,
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
