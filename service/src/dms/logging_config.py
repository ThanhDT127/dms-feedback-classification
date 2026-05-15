"""Logging configuration helpers."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path


class JsonFormatter(logging.Formatter):
    """Format log records as JSON lines."""

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": datetime.fromtimestamp(record.created).isoformat(timespec="seconds"),
            "level": record.levelname,
            "module": record.name,
            "msg": record.getMessage(),
        }

        for key in (
            "file",
            "rows",
            "duration_s",
            "error",
            "error_type",
            "poll_cycle",
            "gemini_retries",
            "status",
        ):
            val = getattr(record, key, None)
            if val is not None:
                entry[key] = val

        if record.exc_info and record.exc_info[1]:
            entry["error_type"] = type(record.exc_info[1]).__name__
            entry["error"] = str(record.exc_info[1])
            entry["traceback"] = self.formatException(record.exc_info)

        return json.dumps(entry, ensure_ascii=False, default=str)


def setup_logging(
    log_dir: str | Path,
    log_file: str = "dms-service.jsonl",
    max_bytes: int = 10 * 1024 * 1024,
    backup_count: int = 7,
    console_level: int = logging.INFO,
    file_level: int = logging.DEBUG,
) -> None:
    """Configure console and JSON-line file logging."""
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / log_file

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.handlers.clear()

    console_handler = logging.StreamHandler()
    console_handler.setLevel(console_level)
    console_handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    )
    root.addHandler(console_handler)

    file_handler = RotatingFileHandler(
        str(log_path),
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    file_handler.setLevel(file_level)
    file_handler.setFormatter(JsonFormatter())
    root.addHandler(file_handler)

    for noisy in ("urllib3", "msal", "azure", "google", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    logging.getLogger("dms-watcher").info(
        "Logging initialized: console=%s, file=%s (rotation: %dMB x %d)",
        logging.getLevelName(console_level),
        log_path,
        max_bytes // (1024 * 1024),
        backup_count,
    )
