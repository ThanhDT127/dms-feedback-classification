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
