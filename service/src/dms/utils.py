"""Shared utility functions for the DMS service."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def atomic_write_text(path: Path, content: str, encoding: str = "utf-8") -> None:
    """Write *content* to *path* atomically using a temp file + os.replace().

    If the process is killed between writing and replacing, the original file
    at *path* remains intact (or absent if it never existed).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, prefix=".tmp_")
    try:
        with os.fdopen(fd, "w", encoding=encoding) as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except Exception:
        # Clean up the temp file on any error
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def atomic_write_json(path: Path, data: Any, indent: int = 2) -> None:
    """Serialize *data* as JSON and write to *path* atomically.

    Guarantees that *path* is never in a partially-written state.
    """
    content = json.dumps(data, ensure_ascii=False, indent=indent, default=str)
    atomic_write_text(path, content)
