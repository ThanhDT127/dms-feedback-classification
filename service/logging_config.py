"""
Logging configuration — Dual handler: Console (human) + File (JSON).

Usage:
    from logging_config import setup_logging
    setup_logging()  # Call once at startup

Produces:
    Console: "2026-05-15 08:30:00 [INFO] dms-watcher: Processing file.xlsx"
    File:    {"ts":"2026-05-15T08:30:00","level":"INFO","module":"watcher","msg":"Processing file.xlsx"}
"""
import json
import logging
import os
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path


class JsonFormatter(logging.Formatter):
    """
    JSON Lines formatter for structured log output.

    Each log line is a JSON object with fields:
        ts, level, module, msg, [extras: file, rows, duration_s, error, error_type]
    """

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": datetime.fromtimestamp(record.created).isoformat(timespec="seconds"),
            "level": record.levelname,
            "module": record.name,
            "msg": record.getMessage(),
        }

        # ── Structured extras (set via logger.info("...", extra={...})) ──
        for key in ("file", "rows", "duration_s", "error", "error_type",
                     "poll_cycle", "gemini_retries", "status"):
            val = getattr(record, key, None)
            if val is not None:
                entry[key] = val

        # ── Exception info ──
        if record.exc_info and record.exc_info[1]:
            entry["error_type"] = type(record.exc_info[1]).__name__
            entry["error"] = str(record.exc_info[1])
            entry["traceback"] = self.formatException(record.exc_info)

        return json.dumps(entry, ensure_ascii=False, default=str)


def setup_logging(
    log_dir: str | Path | None = None,
    log_file: str = "dms-service.jsonl",
    max_bytes: int = 10 * 1024 * 1024,   # 10 MB
    backup_count: int = 7,
    console_level: int = logging.INFO,
    file_level: int = logging.DEBUG,
) -> None:
    """
    Configure dual-output logging: Console (human-readable) + File (JSON Lines).

    Args:
        log_dir: Directory for log files. Defaults to SERVICE_DIR/../logs or WORK_DIR/../logs.
        log_file: Name of the JSON log file.
        max_bytes: Max size per log file before rotation.
        backup_count: Number of rotated backup files to keep.
        console_level: Minimum level for console output.
        file_level: Minimum level for file output.
    """
    # ── Determine log directory ──
    if log_dir is None:
        service_dir = Path(__file__).resolve().parent
        log_dir = Path(os.environ.get("LOG_DIR", str(service_dir / "logs")))
    else:
        log_dir = Path(log_dir)

    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / log_file

    # ── Root logger ──
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)  # Allow all levels; handlers filter

    # Remove any existing handlers (avoid duplicate on re-init)
    root.handlers.clear()

    # ── Console handler (human-readable) ──
    console_fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    console_handler = logging.StreamHandler()
    console_handler.setLevel(console_level)
    console_handler.setFormatter(logging.Formatter(console_fmt))
    root.addHandler(console_handler)

    # ── File handler (JSON Lines, rotating) ──
    file_handler = RotatingFileHandler(
        str(log_path),
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    file_handler.setLevel(file_level)
    file_handler.setFormatter(JsonFormatter())
    root.addHandler(file_handler)

    # ── Suppress noisy third-party loggers ──
    for noisy in ("urllib3", "msal", "azure", "google", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    logging.getLogger("dms-watcher").info(
        "Logging initialized: console=%s, file=%s (rotation: %dMB × %d)",
        logging.getLevelName(console_level),
        log_path,
        max_bytes // (1024 * 1024),
        backup_count,
    )
