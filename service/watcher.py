"""
Watcher service — main polling loop entry point.

Monitors SharePoint Input/ folder for new .xlsx files,
processes them through the classification pipeline,
and uploads results to Output/.
"""
import os
import sys
import json
import time
import traceback
from datetime import datetime
from pathlib import Path

# Ensure service root is in path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import (
    POLL_INTERVAL_SECONDS,
    SEEN_FILES_PATH,
    HEALTH_FILE,
    WORK_DIR,
    LOG_DIR,
    SP_OUTPUT_FOLDER,
    SP_CHECKPOINT_FOLDER,
    logger,
)
from sharepoint import list_input_files, download_file, upload_output, upload_checkpoint
from pipeline.runner import run_pipeline
from notification import notify_success, notify_error
from metrics import MetricsCollector

# ── Global metrics instance ──
metrics = MetricsCollector(WORK_DIR / "metrics.json")


# ── Seen files tracking ─────────────────────────────────────────────────────
def _load_seen() -> dict:
    """Load seen_files.json from disk."""
    if SEEN_FILES_PATH.exists():
        try:
            with open(SEEN_FILES_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning("Cannot read seen_files.json: %s", e)
    return {}


def _save_seen(seen: dict):
    """Persist seen_files.json to disk."""
    SEEN_FILES_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(SEEN_FILES_PATH, "w", encoding="utf-8") as f:
        json.dump(seen, f, ensure_ascii=False, indent=2)


def _update_health(cycle: int = 0, queue_size: int = 0):
    """Write enriched health data from metrics collector."""
    HEALTH_FILE.parent.mkdir(parents=True, exist_ok=True)
    health_data = metrics.get_health_data(cycle=cycle, queue_size=queue_size)
    with open(HEALTH_FILE, "w", encoding="utf-8") as f:
        json.dump(health_data, f, ensure_ascii=False, indent=2)


def _write_daily_summary(date_str: str):
    """
    Log daily summary and append to daily-summary.jsonl.

    Called when date changes between poll cycles.
    """
    summary = metrics.get_daily_summary()

    # Log to console/JSON log
    logger.info("═══ DAILY SUMMARY (%s) ═══", date_str)
    logger.info("  Files processed: %d | Failed: %d | Success rate: %s",
                summary["files_processed"], summary["files_failed"], summary["success_rate"])
    logger.info("  Total rows: %d | Avg time: %.1fs/file",
                summary["total_rows"], summary["avg_time_per_file"])
    logger.info("  Gemini calls: %d | Retries: %d | Polls: %d",
                summary["gemini_calls"], summary["gemini_retries"], summary["polls"])

    # Append to daily-summary.jsonl
    summary_path = LOG_DIR / "daily-summary.jsonl"
    try:
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        with open(summary_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(summary, ensure_ascii=False, default=str) + "\n")
    except Exception as e:
        logger.warning("Failed to write daily summary: %s", e)

MAX_FILE_RETRIES = 3


def _process_file(file_info: dict, seen: dict) -> bool:
    """
    Process a single file through the pipeline.

    Args:
        file_info: Dict with id, name from SharePoint.
        seen: The seen_files tracking dict (will be mutated).

    Returns:
        True if processing succeeded, False otherwise.
    """
    file_id = file_info["id"]
    file_name = file_info["name"]
    base_name = os.path.splitext(file_name)[0]

    # ── Prepare local paths ──
    local_input = str(WORK_DIR / "input" / file_name)
    local_output = str(WORK_DIR / "output" / f"{base_name}_output.xlsx")
    local_ckpt = str(WORK_DIR / "checkpoint" / f"{base_name}.json")

    os.makedirs(os.path.dirname(local_input), exist_ok=True)
    os.makedirs(os.path.dirname(local_output), exist_ok=True)
    os.makedirs(os.path.dirname(local_ckpt), exist_ok=True)

    try:
        # ── Download ──
        logger.info("📥 Downloading: %s", file_name)
        download_file(file_id, local_input)

        # ── Run pipeline ──
        logger.info("🔄 Processing: %s", file_name)
        result = run_pipeline(local_input, local_output, local_ckpt)

        # ── Upload results ──
        logger.info("📤 Uploading results for: %s", file_name)
        upload_output(local_output)
        upload_checkpoint(local_ckpt)

        # ── Mark as processed ──
        seen[file_id] = {
            "name": file_name,
            "status": "done",
            "processed_at": datetime.now().isoformat(),
            "total_rows": result.get("total_rows", 0),
            "duration_seconds": result.get("duration_seconds", 0),
        }
        _save_seen(seen)

        # ── Record metrics + notify ──
        rows = result.get("total_rows", 0)
        duration = result.get("duration_seconds", 0)
        metrics.record_success(file_name, rows, duration)
        notify_success(file_name, result)
        logger.info("✅ Completed: %s (%d rows in %.1fs)", file_name, rows, duration)
        return True

    except Exception as e:
        error_type = type(e).__name__
        error_msg = f"{error_type}: {e}"
        logger.error("❌ Failed processing %s: %s", file_name, error_msg)
        logger.debug(traceback.format_exc())
        metrics.record_failure(file_name, error_type, str(e))

        # Track failure count for retry logic
        entry = seen.get(file_id, {"name": file_name, "failures": 0})
        entry["failures"] = entry.get("failures", 0) + 1
        entry["last_error"] = error_msg
        entry["last_attempt"] = datetime.now().isoformat()

        if entry["failures"] >= MAX_FILE_RETRIES:
            entry["status"] = "failed"
            logger.error("🚫 Max retries reached for %s — marking as failed", file_name)
            notify_error(file_name, error_msg, retry_count=entry["failures"], max_retries=MAX_FILE_RETRIES)
        else:
            entry["status"] = "retry"
            logger.warning("🔁 Will retry %s (attempt %d/%d)",
                           file_name, entry["failures"], MAX_FILE_RETRIES)

        seen[file_id] = entry
        _save_seen(seen)
        return False


# ── Main polling loop ────────────────────────────────────────────────────────
def poll_once(seen: dict) -> int:
    """
    Run one polling cycle:
    1. List files in SharePoint Input/
    2. Find new files (not in seen or marked for retry)
    3. Process each new file sequentially

    Returns:
        Number of files processed in this cycle.
    """
    try:
        remote_files = list_input_files()
    except Exception as e:
        logger.error("Cannot list SharePoint files: %s", e)
        return 0

    # Find new or retry-eligible files
    new_files = []
    for f in remote_files:
        fid = f["id"]
        if fid not in seen:
            new_files.append(f)
        elif seen[fid].get("status") == "retry":
            new_files.append(f)

    if not new_files:
        logger.info("No new files to process")
        return 0

    logger.info("Found %d file(s) to process", len(new_files))
    processed = 0
    for f in new_files:
        success = _process_file(f, seen)
        if success:
            processed += 1

    return processed


def main():
    """Main entry point — runs the polling loop forever."""
    logger.info("=" * 60)
    logger.info("DMS Feedback Classification Watcher starting...")
    logger.info("Poll interval: %d seconds", POLL_INTERVAL_SECONDS)
    logger.info("Work directory: %s", WORK_DIR)
    logger.info("=" * 60)

    seen = _load_seen()
    logger.info("Loaded %d previously seen files", len(seen))

    cycle = 0
    while True:
        cycle += 1
        logger.info("─── Poll cycle %d ───", cycle)
        metrics.record_poll()

        # ── Daily summary check ──
        prev_date = metrics.check_date_change()
        if prev_date is not None:
            _write_daily_summary(prev_date)
            metrics.reset_daily()

        try:
            processed = poll_once(seen)
            if processed > 0:
                logger.info("Processed %d file(s) this cycle", processed)
        except Exception as e:
            logger.error("Unhandled error in poll cycle: %s", e)
            logger.debug(traceback.format_exc())

        # ── Flush metrics + health ──
        metrics.flush()
        _update_health(cycle=cycle)

        logger.info("Sleeping %d seconds...", POLL_INTERVAL_SECONDS)
        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
