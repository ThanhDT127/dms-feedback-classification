"""Runtime artifact cleanup helpers."""

from __future__ import annotations

import logging
from datetime import timedelta
from pathlib import Path

from .settings import Settings
from .time_utils import utc_from_timestamp, utc_now

logger = logging.getLogger("dms-watcher")


class RuntimeCleanup:
    """Delete temporary runtime artifacts while preserving protected state."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def cleanup_success_artifacts(
        self,
        *,
        local_input: Path,
        local_output: Path,
        local_checkpoint: Path,
    ) -> None:
        if not self.settings.enable_runtime_cleanup:
            return
        for path in (local_input, local_output, local_checkpoint):
            self._delete_path(path, reason="post-success cleanup")

    def cleanup_housekeeping(self) -> None:
        if not self.settings.enable_runtime_cleanup:
            return
        self._cleanup_stale_sync_staging()
        self._cleanup_old_files(
            self.settings.work_dir / "output",
            ttl=timedelta(days=self.settings.cleanup_output_ttl_days),
        )
        self._cleanup_old_files(
            self.settings.log_dir,
            ttl=timedelta(days=self.settings.cleanup_log_ttl_days),
        )
        self._cleanup_chat_exports()
        self._cleanup_chat_usage_log()

    def _cleanup_chat_usage_log(self) -> None:
        """Xoá bản ghi usage chat cũ hơn ``CHAT_USAGE_RETENTION_DAYS`` (b11 D8)."""
        import sqlite3

        from .chat.db.usage_ledger import SqliteChatUsageLedger

        db_path = self.settings.classification_jobs_db_path
        if not db_path.exists():
            return
        cutoff = utc_now() - timedelta(days=self.settings.chat_usage_retention_days)
        try:
            with sqlite3.connect(str(db_path), timeout=10) as conn:
                has_table = conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='chat_usage_log'"
                ).fetchone()
                if not has_table:
                    return
                removed = SqliteChatUsageLedger(conn).cleanup(cutoff)
        except Exception as exc:
            logger.warning("Failed to clean chat usage log: %s", exc)
            return
        if removed:
            logger.info("Removed %s expired chat usage rows", removed)

    def _cleanup_chat_exports(self) -> None:
        """Xoá cặp .xlsx + .json của file xuất chat đã quá hạn (b10 D9)."""
        from .chat.ai.export.export_store import EXPORT_DIR_NAME, cleanup_chat_exports

        root = self.settings.work_dir / EXPORT_DIR_NAME
        if not root.exists():
            return
        try:
            removed = cleanup_chat_exports(root)
        except Exception as exc:
            logger.warning("Failed to clean chat exports: %s", exc)
            return
        if removed:
            logger.info("Removed %s expired chat export files", removed)

    def _cleanup_stale_sync_staging(self) -> None:
        cache_dir = self.settings.config_assets_cache_dir
        if not cache_dir.exists():
            return
        cutoff = utc_now() - timedelta(hours=self.settings.cleanup_staging_ttl_hours)
        for path in cache_dir.iterdir():
            if not path.is_dir():
                continue
            if path.name == "active" or not path.name.startswith("cfgsync-"):
                continue
            modified = utc_from_timestamp(path.stat().st_mtime)
            if modified >= cutoff:
                continue
            self._delete_path(path, reason="stale sync staging")

    def _cleanup_old_files(self, directory: Path, *, ttl: timedelta) -> None:
        if ttl.total_seconds() <= 0 or not directory.exists():
            return
        cutoff = utc_now() - ttl
        for path in directory.iterdir():
            if not path.is_file():
                continue
            modified = utc_from_timestamp(path.stat().st_mtime)
            if modified >= cutoff:
                continue
            self._delete_path(path, reason=f"retention>{ttl}")

    def _delete_path(self, path: Path, *, reason: str) -> None:
        try:
            if not path.exists():
                return
            if path.is_dir():
                for child in path.iterdir():
                    if child.is_dir():
                        self._delete_path(child, reason=reason)
                    else:
                        child.unlink(missing_ok=True)
                path.rmdir()
            else:
                path.unlink(missing_ok=True)
            logger.info("Removed %s (%s)", path, reason)
        except Exception as exc:
            logger.warning("Failed to remove %s (%s): %s", path, reason, exc)
