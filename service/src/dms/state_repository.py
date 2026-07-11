"""Repository helpers for runtime state files."""

from __future__ import annotations

import json
import shutil
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .utils import atomic_write_json


class JsonStateRepository:
    """Thread-safe JSON state repository with atomic write semantics."""

    def __init__(
        self,
        path: Path,
        *,
        default_factory: Callable[[], dict] | None = None,
        validator: Callable[[dict], None] | None = None,
    ) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._default_factory = default_factory or dict
        self._validator = validator
        if not self.path.exists():
            self.write(self._default_factory())

    def read(self) -> dict[str, Any]:
        with self._lock:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self._validate(data)
            return data

    def write(self, data: dict[str, Any]) -> None:
        with self._lock:
            self._validate(data)
            atomic_write_json(self.path, data)

    def update(self, updater: Callable[[dict[str, Any]], dict[str, Any] | None]) -> dict[str, Any]:
        with self._lock:
            data = self.read()
            updated = updater(data)
            if updated is not None:
                data = updated
            self.write(data)
            return data

    def backup(self, backup_dir: Path | None = None) -> Path | None:
        with self._lock:
            if not self.path.exists():
                return None
            target_dir = backup_dir or self.path.parent / "backups"
            target_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
            backup_path = target_dir / f"{self.path.name}.{stamp}.bak"
            shutil.copy2(self.path, backup_path)
            return backup_path

    def _validate(self, data: dict[str, Any]) -> None:
        if not isinstance(data, dict):
            raise ValueError("State payload must be a JSON object")
        if self._validator is not None:
            self._validator(data)
