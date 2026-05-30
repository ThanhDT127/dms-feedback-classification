"""Health, metrics, and log endpoints."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Query

from ...settings import get_settings

logger = logging.getLogger("dms-web")

router = APIRouter(prefix="/api", tags=["metrics"])


def _work_dir() -> Path:
    return get_settings().work_dir


def _log_dir() -> Path:
    return get_settings().log_dir


# ---------- Health ----------


@router.get("/health")
async def get_health():
    """Trả về trạng thái hoạt động của dịch vụ."""
    health_path = _work_dir() / "health.json"
    logger.info("Health check: work_dir=%s, health_path=%s, exists=%s", _work_dir(), health_path, health_path.is_file())
    if health_path.is_file():
        try:
            return json.loads(health_path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Lỗi đọc health.json: %s", exc)

    # Generate basic health if file doesn't exist
    return {
        "status": "unknown",
        "last_poll": None,
        "uptime": "N/A",
        "current_cycle": 0,
        "files_in_queue": 0,
        "last_success": None,
        "last_error": None,
        "metrics_summary": {
            "processed_24h": 0,
            "failed_24h": 0,
            "success_rate": "N/A",
        },
        "web_api": True,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
    }


# ---------- Metrics ----------


@router.get("/metrics")
async def get_metrics():
    """Trả về số liệu thống kê vận hành."""
    metrics_path = _work_dir() / "metrics.json"
    seen_path = _work_dir() / "seen_files.json"

    data = {}
    if metrics_path.is_file():
        try:
            data = json.loads(metrics_path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Lỗi đọc metrics.json: %s", exc)

    # Ánh xạ key thống kê chuẩn sang format frontend tiêu thụ
    success_cnt = data.get("files_processed", 0)
    failed_cnt = data.get("files_failed", 0)
    data["total_files"] = success_cnt + failed_cnt
    data["success_files"] = success_cnt
    data["failed_files"] = failed_cnt
    data["avg_processing_time"] = data.get("avg_processing_seconds", 0.0)

    # Đọc seen_files.json để trả về danh sách recent_files động
    recent_files = []
    if seen_path.is_file():
        try:
            seen_data = json.loads(seen_path.read_text(encoding="utf-8"))
            for fid, info in seen_data.items():
                recent_files.append({
                    "id": fid,
                    "filename": info.get("name", "Unknown"),
                    "status": info.get("status", "done"),
                    "timestamp": info.get("processed_at") or info.get("last_attempt") or "",
                    "total_rows": info.get("total_rows", 0),
                    "duration_seconds": info.get("duration_seconds", 0.0),
                    "failures": info.get("failures", 0),
                    "last_error": info.get("last_error", ""),
                })
            # Sắp xếp giảm dần theo thời gian xử lý
            recent_files.sort(key=lambda x: x["timestamp"], reverse=True)
        except Exception as exc:
            logger.warning("Lỗi đọc seen_files.json trong metrics API: %s", exc)

    data["recent_files"] = recent_files
    return data


@router.get("/metrics/daily")
async def get_daily_metrics():
    """Trả về tổng hợp theo ngày từ daily-summary.jsonl."""
    summary_path = _log_dir() / "daily-summary.jsonl"
    if not summary_path.is_file():
        return []
    entries = []
    try:
        for line in summary_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    except Exception as exc:
        logger.warning("Lỗi đọc daily-summary.jsonl: %s", exc)
    return entries


# ---------- Logs ----------


def _find_latest_log() -> Path | None:
    """Find the most recent log file in the logs directory."""
    if not _log_dir().is_dir():
        return None
    log_files = sorted(_log_dir().glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    if log_files:
        return log_files[0]
    # Fallback: try .log files
    log_files = sorted(_log_dir().glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    return log_files[0] if log_files else None


def _parse_log_line(line: str) -> dict | None:
    """Parse a JSON log line or a plain-text log line."""
    line = line.strip()
    if not line:
        return None

    # Try JSON-lines format first
    try:
        data = json.loads(line)
        return {
            "timestamp": data.get("ts", ""),
            "level": data.get("level", "INFO"),
            "message": data.get("msg", line),
            "module": data.get("module", ""),
            "raw": data,
        }
    except json.JSONDecodeError:
        pass

    # Fallback: plain text format  "2024-01-01 12:00:00 [INFO] module: message"
    match = re.match(
        r"^(\d{4}-\d{2}-\d{2}[\sT]\d{2}:\d{2}:\d{2})\s*\[(\w+)\]\s*(\S+):\s*(.*)$",
        line,
    )
    if match:
        return {
            "timestamp": match.group(1),
            "level": match.group(2),
            "message": match.group(4),
            "module": match.group(3),
        }

    return {"timestamp": "", "level": "INFO", "message": line, "module": ""}


@router.get("/logs")
async def get_logs(
    level: str | None = Query(None, description="Lọc theo level: DEBUG, INFO, WARNING, ERROR"),
    limit: int = Query(200, ge=1, le=5000, description="Số dòng tối đa"),
):
    """Đọc log gần đây từ file log mới nhất."""
    log_file = _find_latest_log()
    if log_file is None:
        return []

    try:
        all_lines = log_file.read_text(encoding="utf-8").splitlines()
    except Exception as exc:
        logger.warning("Lỗi đọc file log %s: %s", log_file, exc)
        return []

    # Take the last N lines
    recent_lines = all_lines[-limit * 2 :] if len(all_lines) > limit * 2 else all_lines

    entries = []
    for line in recent_lines:
        parsed = _parse_log_line(line)
        if parsed is None:
            continue
        if level and parsed["level"].upper() != level.upper():
            continue
        entries.append(parsed)

    # Return the most recent entries, up to limit
    return entries[-limit:]
