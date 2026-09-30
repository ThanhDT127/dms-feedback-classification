from __future__ import annotations

import os
import time
from pathlib import Path

from dms.cleanup import RuntimeCleanup


def test_cleanup_success_artifacts_removes_temp_files(settings, tmp_path: Path):
    settings.enable_runtime_cleanup = True
    cleanup = RuntimeCleanup(settings)
    local_input = tmp_path / "input.xlsx"
    local_output = tmp_path / "output.xlsx"
    local_checkpoint = tmp_path / "checkpoint.json"
    for path in (local_input, local_output, local_checkpoint):
        path.write_text("x", encoding="utf-8")

    cleanup.cleanup_success_artifacts(
        local_input=local_input,
        local_output=local_output,
        local_checkpoint=local_checkpoint,
    )

    assert not local_input.exists()
    assert not local_output.exists()
    assert not local_checkpoint.exists()


def test_cleanup_housekeeping_preserves_active_and_state(settings, tmp_path: Path):
    settings.enable_runtime_cleanup = True
    settings.cleanup_output_ttl_days = 1
    settings.cleanup_log_ttl_days = 1
    settings.cleanup_staging_ttl_hours = 0
    settings.ensure_runtime_dirs()
    cleanup = RuntimeCleanup(settings)

    active_kw = settings.active_keyword_dir
    active_kw.mkdir(parents=True, exist_ok=True)
    (active_kw / "kw_map.json").write_text("{}", encoding="utf-8")
    settings.seen_files_path.write_text("{}", encoding="utf-8")
    old_stage = settings.config_assets_cache_dir / "cfgsync-old"
    old_stage.mkdir(parents=True, exist_ok=True)
    (old_stage / "tmp.txt").write_text("tmp", encoding="utf-8")
    old_output = settings.work_dir / "output" / "old.xlsx"
    old_log = settings.log_dir / "old.log"
    old_output.write_text("out", encoding="utf-8")
    old_log.write_text("log", encoding="utf-8")
    old_time = time.time() - (2 * 24 * 3600)
    for path in (old_output, old_log):
        path.touch()
        path.chmod(0o666)
        os.utime(path, (old_time, old_time))

    cleanup.cleanup_housekeeping()

    assert not old_stage.exists()
    assert not old_output.exists()
    assert not old_log.exists()
    assert active_kw.exists()
    assert settings.seen_files_path.exists()


def test_cleanup_housekeeping_trims_the_chat_usage_log(settings):
    """Dọn định kỳ xoá bản ghi usage chat quá hạn, giữ bản ghi còn trong hạn (b11 D8)."""
    import sqlite3
    from datetime import UTC, datetime, timedelta

    from dms.chat.db.migrations import apply_chat_migrations
    from dms.chat.db.usage_ledger import SqliteChatUsageLedger

    settings.enable_runtime_cleanup = True
    settings.chat_usage_retention_days = 90
    settings.ensure_runtime_dirs()

    db_path = settings.classification_jobs_db_path
    conn = sqlite3.connect(db_path)
    apply_chat_migrations(conn)
    ledger = SqliteChatUsageLedger(conn)
    now = datetime.now(UTC)
    for offset, request_id in ((timedelta(days=200), "cu"), (timedelta(days=1), "moi")):
        ledger.record(
            username="an",
            request_id=request_id,
            session_id=None,
            call_type="chat_plan",
            model="gemini-test",
            prompt_tokens=1,
            completion_tokens=1,
            total_tokens=2,
            cost_usd=0.0,
            success=True,
            at=now - offset,
        )
    conn.close()

    RuntimeCleanup(settings).cleanup_housekeeping()

    check = sqlite3.connect(db_path)
    rows = check.execute("SELECT request_id FROM chat_usage_log").fetchall()
    check.close()
    assert [row[0] for row in rows] == ["moi"]
