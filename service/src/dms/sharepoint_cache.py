"""In-memory thread-safe TTL cache for SharePoint file listings and metadata."""

from __future__ import annotations

import threading
import time
from typing import Any


class SharePointListCache:
    """Thread-safe TTL cache for SharePoint folder listings."""

    def __init__(self, default_ttl: float = 45.0) -> None:
        self.default_ttl = default_ttl
        self._lock = threading.Lock()
        self._cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}

    def get(self, folder: str) -> list[dict[str, Any]] | None:
        """Get cached folder items if not expired."""
        key = folder.lower()
        with self._lock:
            if key not in self._cache:
                return None
            expires_at, items = self._cache[key]
            if time.time() > expires_at:
                del self._cache[key]
                return None
            # Return shallow copy of list to prevent external mutation
            return list(items)

    def set(self, folder: str, items: list[dict[str, Any]], ttl: float | None = None) -> None:
        """Cache folder items with TTL in seconds."""
        key = folder.lower()
        duration = ttl if ttl is not None else self.default_ttl
        with self._lock:
            self._cache[key] = (time.time() + duration, list(items))

    def invalidate(self, folder: str | None = None) -> None:
        """Invalidate cached entries for a folder or all folders."""
        with self._lock:
            if folder is None:
                self._cache.clear()
            else:
                self._cache.pop(folder.lower(), None)


# Global singleton cache instance for the web process
_sharepoint_list_cache = SharePointListCache(default_ttl=45.0)


def get_sharepoint_list_cache() -> SharePointListCache:
    """Return singleton SharePointListCache instance."""
    return _sharepoint_list_cache
