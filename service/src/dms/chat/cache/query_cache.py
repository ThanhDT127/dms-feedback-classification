"""QueryCache — Thread-Safe Scope-Isolated Query Result Cache (Khối [6] CACHE)."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from typing import NamedTuple

from ..contract import QueryPlan, QueryResult, QueryResultMetadata, UserScope

logger = logging.getLogger("dms-chat-cache")


class _CacheEntry(NamedTuple):
    result: QueryResult
    expires_at: float


class QueryCache:
    """Thread-safe LRU/TTL cache for query execution results with revision support."""

    def __init__(
        self,
        default_ttl_seconds: int = 300,
        max_entries: int = 1000,
        revision_getter: Callable[[], int] | None = None,
    ) -> None:
        self.default_ttl_seconds = default_ttl_seconds
        self.max_entries = max_entries
        self.revision_getter = revision_getter
        self._lock = threading.RLock()
        self._cache: dict[str, _CacheEntry] = {}
        self._hits = 0
        self._misses = 0
        self._last_revision: int | None = None

    def get(
        self,
        plan: QueryPlan,
        scope: UserScope,
        *,
        revision: int | None = None,
    ) -> QueryResult | None:
        """Lấy kết quả từ cache nếu còn hạn (TTL) và revision không đổi."""
        dummy_result = QueryResult.ok(data=[])
        cache_key = dummy_result.cache_key(plan, scope)

        now = time.monotonic()
        with self._lock:
            curr_rev = revision
            if curr_rev is None and self.revision_getter is not None:
                try:
                    curr_rev = self.revision_getter()
                except Exception:
                    curr_rev = None

            if curr_rev is not None:
                if self._last_revision is not None and curr_rev != self._last_revision:
                    self._cache.clear()
                self._last_revision = curr_rev

            entry = self._cache.get(cache_key)
            if entry is None:
                self._misses += 1
                return None

            if now > entry.expires_at:
                del self._cache[cache_key]
                self._misses += 1
                return None

            self._hits += 1
            # Trả về bản sao kết quả và đánh dấu cache_hit = True
            cached = entry.result
            return QueryResult(
                status=cached.status,
                data=list(cached.data),
                metadata=QueryResultMetadata(
                    total_rows=cached.metadata.total_rows,
                    query_time_ms=0,
                    scope_applied=cached.metadata.scope_applied,
                    cache_hit=True,
                    pattern_used=cached.metadata.pattern_used,
                ),
                error_message=cached.error_message,
            )

    def set(
        self,
        plan: QueryPlan,
        scope: UserScope,
        result: QueryResult,
        ttl_seconds: int | None = None,
        *,
        revision: int | None = None,
    ) -> None:
        """Lưu kết quả vào cache."""
        dummy = QueryResult.ok(data=[])
        cache_key = dummy.cache_key(plan, scope)
        ttl = self.default_ttl_seconds if ttl_seconds is None else ttl_seconds
        expires_at = time.monotonic() + ttl

        with self._lock:
            curr_rev = revision
            if curr_rev is None and self.revision_getter is not None:
                try:
                    curr_rev = self.revision_getter()
                except Exception:
                    curr_rev = None
            if curr_rev is not None:
                if self._last_revision is not None and curr_rev != self._last_revision:
                    self._cache.clear()
                self._last_revision = curr_rev

            if len(self._cache) >= self.max_entries:
                # Xóa phần tử cũ nhất nếu đầy
                oldest_key = next(iter(self._cache))
                del self._cache[oldest_key]

            self._cache[cache_key] = _CacheEntry(result=result, expires_at=expires_at)

    def invalidate_if_revision_changed(self, current_revision: int) -> bool:
        """Xóa cache nếu revision SQLite đã thay đổi."""
        with self._lock:
            if self._last_revision is not None and current_revision != self._last_revision:
                self._cache.clear()
                self._last_revision = current_revision
                return True
            self._last_revision = current_revision
            return False

    def invalidate_all(self) -> None:
        """Xóa toàn bộ cache."""
        with self._lock:
            self._cache.clear()

    def stats(self) -> dict[str, int]:
        """Thống kê cache."""
        with self._lock:
            return {
                "hits": self._hits,
                "misses": self._misses,
                "size": len(self._cache),
            }
