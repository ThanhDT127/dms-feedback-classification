"""Health, metrics, and log endpoints."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, Query

from ...settings import get_settings
from ..deps import get_current_user, get_admin_user

logger = logging.getLogger("dms-web")

router = APIRouter(prefix="/api", tags=["metrics"])


def _work_dir() -> Path:
    return get_settings().work_dir


def _log_dir() -> Path:
    return get_settings().log_dir


# ---------- Health ----------


@router.get("/health")
async def get_health(user: dict = Depends(get_current_user)):
    """Trả về trạng thái hoạt động của dịch vụ."""
    health_path = _work_dir() / "health.json"
    logger.info(
        "Health check: work_dir=%s, health_path=%s, exists=%s",
        _work_dir(),
        health_path,
        health_path.is_file(),
    )
    if health_path.is_file():
        try:
            data = json.loads(health_path.read_text(encoding="utf-8"))
            if "model" not in data:
                data["model"] = get_settings().gemini_model
            return data
        except Exception as exc:
            logger.warning("Lỗi đọc health.json: %s", exc)

    # Generate basic health if file doesn't exist
    return {
        "status": "unknown",
        "last_poll": None,
        "uptime": "N/A",
        "current_cycle": 0,
        "poll_interval": get_settings().poll_interval_seconds,
        "files_in_queue": 0,
        "last_success": None,
        "last_error": None,
        "metrics_summary": {
            "processed_24h": 0,
            "failed_24h": 0,
            "success_rate": "N/A",
        },
        "model": get_settings().gemini_model,
        "web_api": True,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
    }


# ---------- Metrics ----------


@router.get("/metrics")
async def get_metrics(user: dict = Depends(get_current_user)):
    """Trả về số liệu thống kê vận hành (Watcher + Web Upload)."""
    from ..deps import get_classification_job_store

    metrics_path = _work_dir() / "metrics.json"
    seen_path = _work_dir() / "seen_files.json"

    data = {}
    if metrics_path.is_file():
        try:
            data = json.loads(metrics_path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Lỗi đọc metrics.json: %s", exc)

    # --- Watcher counts (from JSON) ---
    watcher_success = data.get("files_processed", 0)
    watcher_failed = data.get("files_failed", 0)

    # --- Web Upload counts (from SQLite) ---
    web_stats: dict = {}
    job_store = get_classification_job_store()
    if job_store is not None:
        try:
            web_stats = job_store.aggregate_stats()
        except Exception as exc:
            logger.warning("Lỗi query aggregate_stats: %s", exc)

    web_success = web_stats.get("completed_count", 0)
    web_failed = web_stats.get("failed_count", 0)

    # --- Merge counts ---
    success_cnt = watcher_success + web_success
    failed_cnt = watcher_failed + web_failed
    data["total_files"] = success_cnt + failed_cnt
    data["success_files"] = success_cnt
    data["failed_files"] = failed_cnt

    # Đọc seen_files.json để trả về danh sách recent_files động
    recent_files = []
    if seen_path.is_file():
        try:
            seen_data = json.loads(seen_path.read_text(encoding="utf-8"))
            for fid, info in seen_data.items():
                recent_files.append(
                    {
                        "id": fid,
                        "filename": info.get("name", "Unknown"),
                        "status": info.get("status", "done"),
                        "timestamp": info.get("processed_at") or info.get("last_attempt") or "",
                        "total_rows": info.get("total_rows", 0),
                        "duration_seconds": info.get("duration_seconds", 0.0),
                        "failures": info.get("failures", 0),
                        "last_error": info.get("last_error", ""),
                    }
                )
        except Exception as exc:
            logger.warning("Lỗi đọc seen_files.json trong metrics API: %s", exc)

    # --- Merge recent_files from Web Upload ---
    for wf in web_stats.get("recent_files", []):
        recent_files.append(
            {
                "id": f"web_{hash(wf.get('filename', '') + wf.get('timestamp', ''))}",
                "filename": wf.get("filename", "Unknown"),
                "status": wf.get("status", "done"),
                "timestamp": wf.get("timestamp", ""),
                "total_rows": wf.get("total_rows", 0),
                "duration_seconds": wf.get("duration_seconds", 0.0),
                "failures": 0,
                "last_error": "",
                "source": "web",
            }
        )
    # Sắp xếp giảm dần theo thời gian xử lý
    recent_files.sort(key=lambda x: x["timestamp"], reverse=True)

    # Tính toán trung bình thời gian thực tế chỉ cho các file có ghi nhận thời gian chạy > 0
    durations = [f["duration_seconds"] for f in recent_files if f["duration_seconds"] > 0]
    if durations:
        data["avg_processing_time"] = round(sum(durations) / len(durations), 1)
    else:
        data["avg_processing_time"] = data.get("avg_processing_seconds", 0.0)

    data["recent_files"] = recent_files
    return data


@router.get("/metrics/daily")
async def get_daily_metrics(user: dict = Depends(get_current_user)):
    """Trả về tổng hợp theo ngày cho biểu đồ frontend (Watcher + Web Upload)."""
    from ..deps import get_classification_job_store

    daily_counts: dict[str, int] = {}

    seen_path = _work_dir() / "seen_files.json"
    if seen_path.is_file():
        try:
            seen_data = json.loads(seen_path.read_text(encoding="utf-8"))
            for _fid, info in seen_data.items():
                if info.get("status") == "done":
                    # Lấy ngày sửa đổi cuối cùng trên SharePoint hoặc ngày xử lý
                    date_src = info.get("lastModifiedDateTime") or info.get("processed_at") or ""
                    if date_src:
                        if "T" in date_src:
                            date_str = date_src.split("T")[0]
                        elif " " in date_src:
                            date_str = date_src.split(" ")[0]
                        else:
                            date_str = date_src

                        if re.match(r"^\d{4}-\d{2}-\d{2}$", date_str):
                            daily_counts[date_str] = daily_counts.get(date_str, 0) + 1
        except Exception as exc:
            logger.warning("Lỗi đọc seen_files.json trong daily metrics: %s", exc)

    # --- Merge Web Upload daily counts from SQLite ---
    job_store = get_classification_job_store()
    if job_store is not None:
        try:
            web_stats = job_store.aggregate_stats()
            for day, cnt in web_stats.get("daily_counts", {}).items():
                daily_counts[day] = daily_counts.get(day, 0) + cnt
        except Exception as exc:
            logger.warning("Lỗi query aggregate_stats trong daily metrics: %s", exc)

    # Sắp xếp danh sách ngày và tạo mảng trả về cho biểu đồ
    sorted_dates = sorted(daily_counts.keys())
    counts = [daily_counts[d] for d in sorted_dates]

    return {"dates": sorted_dates, "counts": counts}


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
    user: dict = Depends(get_current_user),
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


# ---------- Gemini Usage Analytics ----------


@router.get("/metrics/usage")
async def get_usage_metrics(
    period: str = Query("week", description="Period: day, week, month, custom"),
    from_date: str | None = Query(None, alias="from", description="Start date YYYY-MM-DD"),
    to_date: str | None = Query(None, alias="to", description="End date YYYY-MM-DD"),
    admin: dict = Depends(get_admin_user),
):
    """Return aggregated Gemini API token usage statistics."""
    from .. import deps as _deps

    usage_tracker = _deps.get_usage_tracker()
    if usage_tracker is None:
        return {
            "today_tokens": 0, "today_cost": 0, "today_requests": 0,
            "total_requests": 0, "total_tokens": 0,
            "total_input_tokens": 0, "total_output_tokens": 0,
            "total_cost": 0, "daily": [], "cost_by_type": {}, "top_jobs": [],
        }

    result = usage_tracker.query_usage(period=period, from_date=from_date, to_date=to_date)
    summary = result.get("summary", {})
    daily = result.get("daily", [])
    by_type = result.get("by_type", {})

    # Today stats (always query day period for today cards)
    today = usage_tracker.query_usage(period="day")
    today_sum = today.get("summary", {})

    # Top jobs
    top_jobs_raw = usage_tracker.get_top_jobs(limit=10, from_date=from_date, to_date=to_date)
    # Enrich with filename from classification_jobs table
    job_store = _deps.get_classification_job_store()
    top_jobs = []
    for tj in top_jobs_raw:
        filename = tj.get("job_id", "")
        date_str = ""
        if job_store and tj.get("job_id"):
            try:
                job = job_store.get_job(tj["job_id"])
                if job:
                    filename = job.get("filename", tj["job_id"])
                    date_str = (job.get("created_at") or "")[:10]
            except Exception:
                pass
        top_jobs.append({
            "filename": filename,
            "total_tokens": tj.get("total_tokens", 0),
            "cost": tj.get("cost_usd", 0),
            "date": date_str,
        })

    # Flatten cost_by_type for doughnut chart
    cost_by_type = {k: v.get("cost_usd", 0) for k, v in by_type.items()}

    # Flatten daily for charts
    daily_flat = [
        {
            "date": d["date"],
            "input_tokens": d.get("prompt_tokens", 0),
            "output_tokens": d.get("completion_tokens", 0),
            "cost": d.get("cost_usd", 0),
        }
        for d in daily
    ]

    return {
        "today_tokens": today_sum.get("total_tokens", 0),
        "today_cost": today_sum.get("total_cost_usd", 0),
        "today_requests": today_sum.get("total_calls", 0),
        "total_requests": summary.get("total_calls", 0),
        "total_tokens": summary.get("total_tokens", 0),
        "total_input_tokens": summary.get("total_prompt_tokens", 0),
        "total_output_tokens": summary.get("total_completion_tokens", 0),
        "total_cost": summary.get("total_cost_usd", 0),
        "daily": daily_flat,
        "cost_by_type": cost_by_type,
        "top_jobs": top_jobs,
    }


@router.get("/metrics/usage/config")
async def get_usage_config(admin: dict = Depends(get_admin_user)):
    """Return the current Gemini model pricing configuration."""
    settings = get_settings()
    try:
        pricing = json.loads(settings.gemini_model_pricing)
    except Exception:
        pricing = {}
    return {
        "current_model": settings.gemini_model,
        "pricing": pricing,
    }
