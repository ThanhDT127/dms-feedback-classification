"""Health, metrics, and log endpoints."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from ...settings import get_settings
from ...time_utils import utc_now_iso
from ..deps import get_admin_user, get_current_user

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
        "timestamp": utc_now_iso(),
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

    # --- Watcher stats: file-level outcomes từ seen_files.json (Hướng A) ---
    # seen_files.json là source of truth về trạng thái cuối cùng của mỗi file.
    # File X thất bại 3 lần → retry thành công → status = "done" → tính là SUCCESS.
    # Không đếm theo SQLite attempts để tránh inflate failure rate.
    watcher_success = 0   # files với status "done"
    watcher_failed = 0    # files với status "failed" (stuck, chưa được retry thành công)
    watcher_retried = 0   # files từng thất bại nhưng cuối cùng thành công (failures > 0 AND done)
    label_distribution: dict = {}
    recent_files = []
    watcher_pending_retry = 0

    if seen_path.is_file():
        try:
            seen_data = json.loads(seen_path.read_text(encoding="utf-8"))
            for fid, info in seen_data.items():
                status = info.get("status", "done")
                failures = info.get("failures", 0)

                if status == "done":
                    watcher_success += 1
                    # past_failures: số lần thất bại trước khi thành công (preserved by Watcher)
                    if info.get("past_failures", 0) > 0:
                        watcher_retried += 1

                elif status == "failed":
                    watcher_failed += 1
                elif status == "retry":
                    watcher_pending_retry += 1

                recent_files.append(
                    {
                        "id": fid,
                        "filename": info.get("name", "Unknown"),
                        "status": status,
                        "timestamp": info.get("processed_at") or info.get("last_attempt") or "",
                        "total_rows": info.get("total_rows", 0),
                        "duration_seconds": info.get("duration_seconds", 0.0),
                        "failures": failures,
                        "last_error": info.get("last_error", ""),
                    }
                )
        except Exception as exc:
            logger.warning("Lỗi đọc seen_files.json trong metrics API: %s", exc)

    # Fallback về metrics.json nếu seen_files.json chưa có
    if watcher_success == 0 and watcher_failed == 0:
        watcher_success = data.get("files_processed", 0)
        watcher_failed = data.get("files_failed", 0)

    # --- Label distribution từ SQLite job results ---
    job_store = get_classification_job_store()
    web_stats: dict = {}
    if job_store is not None:
        try:
            web_stats = job_store.aggregate_stats()
            with job_store._lock, job_store._conn() as conn:
                result_rows = conn.execute(
                    "SELECT payload FROM classification_job_results"
                ).fetchall()
                for row in result_rows:
                    try:
                        payload = json.loads(row["payload"])
                        ld = payload.get("label_distribution", {})
                        for lbl, cnt in ld.items():
                            label_distribution[lbl] = label_distribution.get(lbl, 0) + int(cnt)
                    except Exception:
                        pass
        except Exception as exc:
            logger.warning("Lỗi query SQLite stats: %s", exc)

    if not label_distribution:
        label_distribution = data.get("label_distribution", {})


    # --- Web upload stats từ SQLite (deduplicated, không tính system_watcher) ---
    web_success = web_stats.get("completed_count", 0)
    web_failed = web_stats.get("failed_count", 0)
    # aggregate_stats() gồm ALL owners kể cả system_watcher — trừ ra để tránh double-count
    watcher_sqlite_success = 0
    watcher_sqlite_failed = 0
    if job_store is not None:
        try:
            with job_store._lock, job_store._conn() as conn:
                row = conn.execute(
                    "SELECT SUM(status='completed') AS s, SUM(status='error') AS f "
                    "FROM classification_jobs WHERE owner_username='system_watcher'"
                ).fetchone()
                watcher_sqlite_success = int(row[0] or 0)
                watcher_sqlite_failed = int(row[1] or 0)
        except Exception:
            pass
    web_only_success = max(0, web_success - watcher_sqlite_success)
    web_only_failed = max(0, web_failed - watcher_sqlite_failed)

    # --- Merge tổng ---
    success_cnt = watcher_success + web_only_success
    failed_cnt = watcher_failed + web_only_failed
    data["total_files"] = success_cnt + failed_cnt
    data["success_files"] = success_cnt
    data["failed_files"] = failed_cnt
    data["label_distribution"] = label_distribution
    data["watcher_retried_count"] = watcher_retried  # files từng thất bại nhưng cuối cùng thành công

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
    data["total_retries"] = data.get("total_retries", 0)
    data["pending_retry_files"] = watcher_pending_retry
    data["watcher_stats"] = {
        "success": watcher_success,
        "failed": watcher_failed,
        "pending_retry": watcher_pending_retry,
        "retried": watcher_retried,  # đã retry và thành công
    }
    data["web_upload_stats"] = {
        "success": web_only_success,
        "failed": web_only_failed,
    }
    return data


@router.get("/metrics/daily")
async def get_daily_metrics(user: dict = Depends(get_current_user)):
    """Trả về tổng hợp theo ngày cho biểu đồ frontend (Watcher + Web Upload).

    Đọc từ SQLite duy nhất — bao gồm cả Watcher (system_watcher) và Web Upload.
    """
    from ..deps import get_classification_job_store

    job_store = get_classification_job_store()
    if job_store is not None:
        try:
            return job_store.daily_stats()
        except Exception as exc:
            logger.warning("Lỗi query daily_stats từ SQLite: %s", exc)

    # Fallback: trả về empty nếu không có SQLite
    return {
        "dates": [],
        "counts": [],
        "success_counts": [],
        "failed_counts": [],
    }


class ResetFailedBody(BaseModel):
    """Request body for POST /metrics/reset-failed."""

    file_ids: list[str] | None = None


@router.post("/metrics/reset-failed")
async def reset_failed_files(
    body: ResetFailedBody | None = None,
    admin: dict = Depends(get_admin_user),
):
    """Reset failed files to retry status (admin only)."""
    seen_path = _work_dir() / "seen_files.json"
    metrics_path = _work_dir() / "metrics.json"

    if not seen_path.is_file():
        return {"reset_count": 0}

    try:
        seen_data = json.loads(seen_path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Cannot read seen_files.json for reset: %s", exc)
        return {"reset_count": 0, "error": str(exc)}

    file_ids = body.file_ids if body else None

    reset_count = 0
    for file_id, info in seen_data.items():
        if info.get("status") != "failed":
            continue
        if file_ids is not None and file_id not in file_ids:
            continue
        info["status"] = "retry"
        info["failures"] = 0
        reset_count += 1

    if reset_count > 0:
        from ...utils import atomic_write_json

        atomic_write_json(seen_path, seen_data)

        # Update metrics.json to decrease files_failed
        if metrics_path.is_file():
            try:
                metrics_data = json.loads(metrics_path.read_text(encoding="utf-8"))
                metrics_data["files_failed"] = max(
                    0, metrics_data.get("files_failed", 0) - reset_count
                )
                atomic_write_json(metrics_path, metrics_data)
            except Exception as exc:
                logger.warning("Failed to update metrics.json after reset: %s", exc)

    return {"reset_count": reset_count}


@router.get("/metrics/by-user")
async def get_metrics_by_user(admin: dict = Depends(get_admin_user)):
    """Return per-user classification statistics (admin only)."""
    from ..deps import get_classification_job_store

    job_store = get_classification_job_store()
    if job_store is None:
        return {"users": []}

    try:
        user_stats = job_store.aggregate_stats_by_user()
    except Exception as exc:
        logger.warning("Error querying user stats: %s", exc)
        return {"users": []}

    return {"users": user_stats}


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
