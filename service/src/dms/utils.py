"""Shared utility functions for the DMS service."""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

logger = logging.getLogger("dms-utils")


def atomic_write_text(path: Path, content: str, encoding: str = "utf-8") -> None:
    """Write *content* to *path* atomically using a temp file + os.replace().

    If the process is killed between writing and replacing, the original file
    at *path* remains intact (or absent if it never existed).

    If os.replace fails due to Docker single-file volume mount constraints
    ([Errno 16] Device or resource busy), it falls back to direct in-place write.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, prefix=".tmp_")
    try:
        with os.fdopen(fd, "w", encoding=encoding) as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        try:
            os.replace(tmp_path, path)
        except OSError as exc:
            # Handle Docker single-file volume mount issue: [Errno 16] Device or resource busy
            # and invalid cross-device link: [Errno 18] Invalid cross-device link
            if exc.errno in (16, 18):
                logger.warning(
                    "Atomic replacement failed for %s (%s). Falling back to direct in-place write.",
                    path,
                    exc,
                )
                path.write_text(content, encoding=encoding)
            else:
                raise
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
    except Exception:
        # Clean up the temp file on any other error
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
