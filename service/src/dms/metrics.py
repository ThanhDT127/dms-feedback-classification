"""Operational metrics collection."""

from __future__ import annotations

import json
import logging
import threading
from collections import defaultdict
from pathlib import Path

from .time_utils import parse_utc_datetime, utc_now, utc_now_iso

logger = logging.getLogger("dms-watcher")


class MetricsCollector:
    """Collect and persist operational metrics for the watcher."""

    def __init__(self, metrics_path: Path | str) -> None:
        self._path = Path(metrics_path)
        self._start_time = utc_now()
        self._lock = threading.RLock()

        self.total_polls = 0
        self.files_processed = 0
        self.files_failed = 0
        self.files_skipped = 0
        self.total_retries = 0
        self.total_rows = 0
        self.total_processing_seconds = 0.0
        self.gemini_calls = 0
        self.gemini_retries = 0
        self.errors_by_type: dict[str, int] = defaultdict(int)
        self.label_distribution: dict[str, int] = defaultdict(int)

        self.daily_polls = 0
        self.daily_processed = 0
        self.daily_failed = 0
        self.daily_retries = 0
        self.daily_rows = 0
        self.daily_processing_seconds = 0.0
        self.daily_gemini_calls = 0
        self.daily_gemini_retries = 0
        self.daily_prompt_tokens = 0
        self.daily_completion_tokens = 0
        self.daily_cost_usd = 0.0
        self._current_date = utc_now().date().isoformat()

        # Token usage totals
        self.total_prompt_tokens = 0
        self.total_completion_tokens = 0
        self.total_cost_usd = 0.0

        self.last_success: dict | None = None
        self.last_error: dict | None = None
        self._consecutive_failures = 0
        self._load()
        self.flush()

    def _load(self) -> None:
        with self._lock:
            if not self._path.exists():
                self._reconstruct_from_seen()
                return
            try:
                data = json.loads(self._path.read_text(encoding="utf-8"))
            except Exception as exc:
                logger.warning("Could not load metrics: %s", exc)
                self._reconstruct_from_seen()
                return

            self.total_polls = data.get("total_polls", 0)
            self.files_processed = data.get("files_processed", 0)
            self.files_failed = data.get("files_failed", 0)
            self.files_skipped = data.get("files_skipped", 0)
            self.total_retries = data.get("total_retries", 0)
            self.total_rows = data.get("total_rows_processed", 0)
            self.total_processing_seconds = data.get("total_processing_seconds", 0.0)
            self.gemini_calls = data.get("gemini_calls", 0)
            self.gemini_retries = data.get("gemini_retries", 0)
            self.errors_by_type = defaultdict(int, data.get("errors_by_type", {}))
            self.last_success = data.get("last_success")
            self.last_error = data.get("last_error")
            self.label_distribution = defaultdict(int, data.get("label_distribution", {}))
            self.total_prompt_tokens = data.get("total_prompt_tokens", 0)
            self.total_completion_tokens = data.get("total_completion_tokens", 0)
            self.total_cost_usd = data.get("total_cost_usd", 0.0)

            self._reconstruct_from_seen()

            if not self.label_distribution and self.files_processed > 0:
                self._scan_existing_outputs()

    def _reconstruct_from_seen(self) -> None:
        with self._lock:
            seen_path = self._path.parent / "seen_files.json"
            if not seen_path.exists():
                return
            try:
                seen_data = json.loads(seen_path.read_text(encoding="utf-8"))
            except Exception as exc:
                logger.warning("Could not load seen_files.json for metrics reconstruction: %s", exc)
                return

        reconstructed_processed = 0
        reconstructed_failed = 0
        reconstructed_rows = 0
        reconstructed_seconds = 0.0
        reconstructed_labels: dict[str, int] = defaultdict(int)

        last_done_entry = None
        last_failed_entry = None

        for _file_id, entry in seen_data.items():
            status = entry.get("status")
            if status == "done":
                reconstructed_processed += 1
                reconstructed_rows += entry.get("total_rows", 0)
                reconstructed_seconds += entry.get("duration_seconds", 0.0)
                lbl_dist = entry.get("label_distribution", {})
                for lbl, cnt in lbl_dist.items():
                    reconstructed_labels[lbl] += cnt

                processed_at = entry.get("processed_at", "")
                if not last_done_entry or processed_at > last_done_entry.get("processed_at", ""):
                    last_done_entry = entry
            elif status == "failed":
                reconstructed_failed += 1
                last_attempt = entry.get("last_attempt", "")
                if not last_failed_entry or last_attempt > last_failed_entry.get(
                    "last_attempt", ""
                ):
                    last_failed_entry = entry

        # Reconstruct if seen_files has more progress, or more processed rows, or more distinct labels
        if (
            reconstructed_processed > self.files_processed
            or reconstructed_failed > self.files_failed
            or reconstructed_rows > self.total_rows
            or len(reconstructed_labels) > len(self.label_distribution)
        ):
            logger.info(
                "Reconstructing metrics from seen_files.json: processed %d -> %d, failed %d -> %d, rows %d -> %d, labels %d -> %d",
                self.files_processed,
                reconstructed_processed,
                self.files_failed,
                reconstructed_failed,
                self.total_rows,
                reconstructed_rows,
                len(self.label_distribution),
                len(reconstructed_labels),
            )
            self.files_processed = max(self.files_processed, reconstructed_processed)
            self.files_failed = max(self.files_failed, reconstructed_failed)
            self.total_rows = max(self.total_rows, reconstructed_rows)
            self.total_processing_seconds = max(
                self.total_processing_seconds, reconstructed_seconds
            )

            if not self.last_success and last_done_entry:
                self.last_success = {
                    "file": last_done_entry.get("name", "Unknown"),
                    "at": self._metric_timestamp(
                        last_done_entry.get("processed_at") or utc_now_iso()
                    ),
                    "rows": last_done_entry.get("total_rows", 0),
                    "duration": round(last_done_entry.get("duration_seconds", 0.0), 1),
                }
            if not self.last_error and last_failed_entry:
                self.last_error = {
                    "file": last_failed_entry.get("name", "Unknown"),
                    "at": self._metric_timestamp(
                        last_failed_entry.get("last_attempt") or utc_now_iso()
                    ),
                    "error_type": "WatcherError",
                    "error": last_failed_entry.get("last_error", "Unknown error"),
                }
            self.flush()

        # Always overwrite label_distribution from seen_files when data exists.
        # This runs independently of the main reconstruct condition to handle
        # edge cases where label content differs but count is the same.
        if len(reconstructed_labels) > 0:
            self.label_distribution = defaultdict(int, reconstructed_labels)
            self.flush()

    def record_success(
        self,
        file_name: str,
        rows: int,
        duration: float,
        label_dist: dict[str, int] | None = None,
        *,
        was_previously_failed: bool = False,
    ) -> None:
        with self._lock:
            self.files_processed += 1
            self.total_rows += rows
            self.total_processing_seconds += duration
            self._consecutive_failures = 0

            if was_previously_failed:
                self.files_failed = max(0, self.files_failed - 1)

            self.daily_processed += 1
            self.daily_rows += rows
            self.daily_processing_seconds += duration

            if label_dist:
                for lbl, cnt in label_dist.items():
                    self.label_distribution[lbl] += cnt

            self.last_success = {
                "file": file_name,
                "at": utc_now_iso(),
                "rows": rows,
                "duration": round(duration, 1),
            }
            self.flush()

    def record_retry_failure(
        self, file_name: str, error_type: str, error_msg: str, *, is_final: bool = False
    ) -> None:
        with self._lock:
            self.total_retries += 1
            self.daily_retries += 1
            self.errors_by_type[error_type] += 1
            self._consecutive_failures += 1

            if is_final:
                self.files_failed += 1
                self.daily_failed += 1

            self.last_error = {
                "file": file_name,
                "at": utc_now_iso(),
                "error_type": error_type,
                "error": error_msg[:300],
            }
            self.flush()

    def record_poll(self) -> None:
        with self._lock:
            self.total_polls += 1
            self.daily_polls += 1

    def record_gemini_call(
        self,
        retries: int = 0,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        cost_usd: float = 0.0,
    ) -> None:
        with self._lock:
            self.gemini_calls += 1
            self.gemini_retries += retries
            self.daily_gemini_calls += 1
            self.daily_gemini_retries += retries
            self.total_prompt_tokens += prompt_tokens
            self.total_completion_tokens += completion_tokens
            self.total_cost_usd += cost_usd
            self.daily_prompt_tokens += prompt_tokens
            self.daily_completion_tokens += completion_tokens
            self.daily_cost_usd += cost_usd

    @property
    def uptime_seconds(self) -> float:
        return (utc_now() - self._start_time).total_seconds()

    @property
    def avg_processing_seconds(self) -> float:
        if self.files_processed == 0:
            return 0.0
        return round(self.total_processing_seconds / self.files_processed, 1)

    @property
    def avg_rows_per_file(self) -> float:
        if self.files_processed == 0:
            return 0.0
        return round(self.total_rows / self.files_processed, 1)

    @property
    def success_rate_pct(self) -> float:
        total = self.files_processed + self.files_failed
        if total == 0:
            return 100.0
        return round(self.files_processed / total * 100, 1)

    @property
    def health_status(self) -> str:
        if self._consecutive_failures >= 3:
            return "degraded"
        return "ok"

    def flush(self) -> None:
        with self._lock:
            data = {
                "start_time": self._start_time.isoformat(timespec="seconds"),
                "uptime_seconds": round(self.uptime_seconds),
                "total_polls": self.total_polls,
                "files_processed": self.files_processed,
                "files_failed": self.files_failed,
                "files_skipped": self.files_skipped,
                "total_rows_processed": self.total_rows,
                "total_processing_seconds": round(self.total_processing_seconds, 1),
                "avg_processing_seconds": self.avg_processing_seconds,
                "avg_rows_per_file": self.avg_rows_per_file,
                "success_rate_pct": self.success_rate_pct,
                "last_success": self.last_success,
                "last_error": self.last_error,
                "errors_by_type": dict(self.errors_by_type),
                "label_distribution": dict(self.label_distribution),
                "gemini_calls": self.gemini_calls,
                "gemini_retries": self.gemini_retries,
                "total_retries": self.total_retries,
                "total_prompt_tokens": self.total_prompt_tokens,
                "total_completion_tokens": self.total_completion_tokens,
                "total_cost_usd": round(self.total_cost_usd, 6),
            }
            try:
                from .utils import atomic_write_json

                atomic_write_json(self._path, data)
            except Exception as exc:
                logger.warning("Failed to write metrics: %s", exc)

    def get_health_data(self, cycle: int = 0, queue_size: int = 0) -> dict:
        uptime = self.uptime_seconds
        hours, rem = divmod(int(uptime), 3600)
        days, hours = divmod(hours, 24)
        mins = rem // 60
        uptime_str = f"{days}d {hours}h {mins}m" if days else f"{hours}h {mins}m"

        return {
            "status": self.health_status,
            "last_poll": utc_now_iso(),
            "uptime": uptime_str,
            "current_cycle": cycle,
            "files_in_queue": queue_size,
            "last_success": (
                f"{self.last_success['file']} ({self.last_success['rows']} rows, "
                f"{self.last_success['duration']}s) @ {self._display_time(self.last_success['at'])}"
                if self.last_success
                else None
            ),
            "last_error": (
                f"{self.last_error['error_type']}: {self.last_error['error'][:100]} "
                f"@ {self._display_time(self.last_error['at'])}"
                if self.last_error
                else None
            ),
            "metrics_summary": {
                "processed_24h": self.daily_processed,
                "failed_24h": self.daily_failed,
                "success_rate": f"{self.success_rate_pct}%",
            },
        }

    def get_daily_summary(self) -> dict:
        return {
            "date": self._current_date,
            "files_processed": self.daily_processed,
            "files_failed": self.daily_failed,
            "retries": self.daily_retries,
            "total_rows": self.daily_rows,
            "total_processing_seconds": round(self.daily_processing_seconds, 1),
            "avg_time_per_file": (
                round(self.daily_processing_seconds / self.daily_processed, 1)
                if self.daily_processed > 0
                else 0.0
            ),
            "gemini_calls": self.daily_gemini_calls,
            "gemini_retries": self.daily_gemini_retries,
            "prompt_tokens": self.daily_prompt_tokens,
            "completion_tokens": self.daily_completion_tokens,
            "cost_usd": round(self.daily_cost_usd, 6),
            "polls": self.daily_polls,
            "success_rate": f"{self.success_rate_pct}%",
        }

    def check_date_change(self) -> str | None:
        today = utc_now().date().isoformat()
        if today != self._current_date:
            prev = self._current_date
            self._current_date = today
            return prev
        return None

    def reset_daily(self) -> None:
        with self._lock:
            self.daily_polls = 0
            self.daily_processed = 0
            self.daily_failed = 0
            self.daily_retries = 0
            self.daily_rows = 0
            self.daily_processing_seconds = 0.0
            self.daily_gemini_calls = 0
            self.daily_gemini_retries = 0
            self.daily_prompt_tokens = 0
            self.daily_completion_tokens = 0
            self.daily_cost_usd = 0.0

    @staticmethod
    def _metric_timestamp(value: str) -> str:
        try:
            return parse_utc_datetime(value).isoformat(timespec="seconds")
        except Exception:
            return utc_now_iso()

    @staticmethod
    def _display_time(value: str) -> str:
        try:
            return parse_utc_datetime(value).time().isoformat(timespec="seconds")
        except Exception:
            return value[11:19] if len(value) >= 19 else value

    def get_pending_retry_count(self) -> int:
        """Count files with status 'retry' from seen_files.json."""
        seen_path = self._path.parent / "seen_files.json"
        if not seen_path.exists():
            return 0
        try:
            seen_data = json.loads(seen_path.read_text(encoding="utf-8"))
            return sum(1 for entry in seen_data.values() if entry.get("status") == "retry")
        except Exception:
            return 0

    def _scan_existing_outputs(self) -> None:
        try:
            import pandas as pd

            from .pipeline.issue_classifier import MINOR_ORDER

            output_dir = self._path.parent / "output"
            if not output_dir.is_dir():
                return

            logger.info("Starting one-time migration to build label_distribution from work/output")
            for path in output_dir.glob("*.xlsx"):
                try:
                    df = pd.read_excel(path, header=1)
                    for col in MINOR_ORDER:
                        if col in df.columns:
                            col_series = df[col].dropna()
                            count = sum(1 for val in col_series if str(val).strip() != "")
                            self.label_distribution[col] += count
                except Exception as exc:
                    logger.warning("One-time scan failed for %s: %s", path, exc)

            logger.info(
                "One-time scan populated label distribution: %s", dict(self.label_distribution)
            )
        except Exception as exc:
            logger.warning("Failed during one-time scan of output files: %s", exc)
