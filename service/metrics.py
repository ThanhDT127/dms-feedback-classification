"""
Metrics collector — In-memory counters with periodic flush to JSON.

Usage:
    from metrics import metrics
    metrics.record_success("file.xlsx", rows=207, duration=99.2)
    metrics.record_failure("bad.xlsx", "ValueError", "No header row")
    metrics.record_poll()
    metrics.record_gemini_call(retries=1)
    metrics.flush()  # writes metrics.json

Designed for single-threaded watcher service. No concurrency locking needed.
"""
import json
import logging
from datetime import datetime
from pathlib import Path
from collections import defaultdict

logger = logging.getLogger("dms-watcher")


class MetricsCollector:
    """
    Collects operational metrics: counters, timers, last events.

    Metrics are stored in memory and periodically flushed to a JSON file.
    Supports both cumulative (total) and daily (resettable) counters.
    """

    def __init__(self, metrics_path: Path | str = "metrics.json"):
        self._path = Path(metrics_path)
        self._start_time = datetime.now()

        # ── Cumulative counters ──
        self.total_polls: int = 0
        self.files_processed: int = 0
        self.files_failed: int = 0
        self.files_skipped: int = 0
        self.total_rows: int = 0
        self.total_processing_seconds: float = 0.0
        self.gemini_calls: int = 0
        self.gemini_retries: int = 0
        self.errors_by_type: dict[str, int] = defaultdict(int)

        # ── Daily counters (reset at midnight) ──
        self.daily_polls: int = 0
        self.daily_processed: int = 0
        self.daily_failed: int = 0
        self.daily_rows: int = 0
        self.daily_processing_seconds: float = 0.0
        self.daily_gemini_calls: int = 0
        self.daily_gemini_retries: int = 0
        self._current_date: str = datetime.now().strftime("%Y-%m-%d")

        # ── Last events ──
        self.last_success: dict | None = None
        self.last_error: dict | None = None

        # ── Consecutive failures tracker (for health status) ──
        self._consecutive_failures: int = 0

        # Try loading existing metrics from file
        self._load()

    def _load(self) -> None:
        """Load previous metrics from file if exists (survive restart)."""
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            self.total_polls = data.get("total_polls", 0)
            self.files_processed = data.get("files_processed", 0)
            self.files_failed = data.get("files_failed", 0)
            self.files_skipped = data.get("files_skipped", 0)
            self.total_rows = data.get("total_rows_processed", 0)
            self.total_processing_seconds = data.get("total_processing_seconds", 0.0)
            self.gemini_calls = data.get("gemini_calls", 0)
            self.gemini_retries = data.get("gemini_retries", 0)
            self.errors_by_type = defaultdict(int, data.get("errors_by_type", {}))
            self.last_success = data.get("last_success")
            self.last_error = data.get("last_error")
            logger.info("Metrics restored from %s (processed=%d, failed=%d)",
                        self._path, self.files_processed, self.files_failed)
        except Exception as e:
            logger.warning("Could not load metrics: %s", e)

    # ── Recording methods ──

    def record_success(self, file_name: str, rows: int, duration: float) -> None:
        """Record a successfully processed file."""
        self.files_processed += 1
        self.total_rows += rows
        self.total_processing_seconds += duration
        self._consecutive_failures = 0

        # Daily
        self.daily_processed += 1
        self.daily_rows += rows
        self.daily_processing_seconds += duration

        self.last_success = {
            "file": file_name,
            "at": datetime.now().isoformat(timespec="seconds"),
            "rows": rows,
            "duration": round(duration, 1),
        }

        logger.debug("Metrics: record_success file=%s rows=%d duration=%.1f",
                      file_name, rows, duration)

    def record_failure(self, file_name: str, error_type: str, error_msg: str) -> None:
        """Record a failed file processing attempt."""
        self.files_failed += 1
        self.errors_by_type[error_type] += 1
        self._consecutive_failures += 1

        # Daily
        self.daily_failed += 1

        self.last_error = {
            "file": file_name,
            "at": datetime.now().isoformat(timespec="seconds"),
            "error_type": error_type,
            "error": error_msg[:300],
        }

        logger.debug("Metrics: record_failure file=%s error_type=%s",
                      file_name, error_type)

    def record_poll(self) -> None:
        """Record a polling cycle."""
        self.total_polls += 1
        self.daily_polls += 1

    def record_gemini_call(self, retries: int = 0) -> None:
        """Record a Gemini API call (with optional retry count)."""
        self.gemini_calls += 1
        self.gemini_retries += retries

        # Daily
        self.daily_gemini_calls += 1
        self.daily_gemini_retries += retries

    # ── Derived metrics ──

    @property
    def uptime_seconds(self) -> float:
        return (datetime.now() - self._start_time).total_seconds()

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
        """Determine health: ok / degraded / error."""
        if self._consecutive_failures >= 3:
            return "degraded"
        return "ok"

    # ── Flush to file ──

    def flush(self) -> None:
        """Compute derived metrics and write metrics.json."""
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
            "gemini_calls": self.gemini_calls,
            "gemini_retries": self.gemini_retries,
        }
        try:
            self._path.write_text(
                json.dumps(data, indent=2, ensure_ascii=False, default=str),
                encoding="utf-8",
            )
        except Exception as e:
            logger.warning("Failed to write metrics: %s", e)

    # ── Health data ──

    def get_health_data(self, cycle: int = 0, queue_size: int = 0) -> dict:
        """
        Return health data dict for health.json.

        Args:
            cycle: Current poll cycle number.
            queue_size: Number of files currently in queue.
        """
        uptime = self.uptime_seconds
        hours, rem = divmod(int(uptime), 3600)
        days, hours = divmod(hours, 24)
        mins = rem // 60
        uptime_str = f"{days}d {hours}h {mins}m" if days else f"{hours}h {mins}m"

        return {
            "status": self.health_status,
            "last_poll": datetime.now().isoformat(timespec="seconds"),
            "uptime": uptime_str,
            "current_cycle": cycle,
            "files_in_queue": queue_size,
            "last_success": (
                f"{self.last_success['file']} ({self.last_success['rows']} rows, "
                f"{self.last_success['duration']}s) @ {self.last_success['at'][-8:]}"
                if self.last_success else None
            ),
            "last_error": (
                f"{self.last_error['error_type']}: {self.last_error['error'][:100]} "
                f"@ {self.last_error['at'][-8:]}"
                if self.last_error else None
            ),
            "metrics_summary": {
                "processed_24h": self.daily_processed,
                "failed_24h": self.daily_failed,
                "success_rate": f"{self.success_rate_pct}%",
            },
        }

    # ── Daily summary ──

    def get_daily_summary(self) -> dict:
        """Return daily summary dict for logging and daily-summary.jsonl."""
        return {
            "date": self._current_date,
            "files_processed": self.daily_processed,
            "files_failed": self.daily_failed,
            "total_rows": self.daily_rows,
            "total_processing_seconds": round(self.daily_processing_seconds, 1),
            "avg_time_per_file": (
                round(self.daily_processing_seconds / self.daily_processed, 1)
                if self.daily_processed > 0 else 0.0
            ),
            "gemini_calls": self.daily_gemini_calls,
            "gemini_retries": self.daily_gemini_retries,
            "polls": self.daily_polls,
            "success_rate": f"{self.success_rate_pct}%",
        }

    def check_date_change(self) -> str | None:
        """
        Check if the date has changed since last check.

        Returns the previous date string if changed (trigger daily summary),
        or None if still the same day.
        """
        today = datetime.now().strftime("%Y-%m-%d")
        if today != self._current_date:
            prev = self._current_date
            self._current_date = today
            return prev
        return None

    def reset_daily(self) -> None:
        """Reset daily counters (called after daily summary)."""
        self.daily_polls = 0
        self.daily_processed = 0
        self.daily_failed = 0
        self.daily_rows = 0
        self.daily_processing_seconds = 0.0
        self.daily_gemini_calls = 0
        self.daily_gemini_retries = 0
