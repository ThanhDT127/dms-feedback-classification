"""Unit tests for QueryCache (thread-safe, TTL, scope-isolated cache)."""

from __future__ import annotations

import time

from dms.chat.cache.query_cache import QueryCache
from dms.chat.contract import (
    AnswerShape,
    QueryPattern,
    QueryPlan,
    QueryResult,
    UserScope,
)


def _sample_plan(query: str = "Test") -> QueryPlan:
    return QueryPlan(
        pattern=QueryPattern.SQL_TEMPLATE,
        answer_shape=AnswerShape.NUMBER,
        original_query=query,
        function_name="get_overview",
        params={"date_from": "2026-08-01"},
    )


def test_cache_miss_then_hit():
    cache = QueryCache(default_ttl_seconds=60)
    plan = _sample_plan()
    scope = UserScope(username="admin", role="admin")

    # Ban đầu miss
    assert cache.get(plan, scope) is None

    # Set cache
    result = QueryResult.ok(data=[{"total": 100}])
    cache.set(plan, scope, result)

    # Lấy lại -> Hit
    cached = cache.get(plan, scope)
    assert cached is not None
    assert cached.metadata.cache_hit is True
    assert cached.data == [{"total": 100}]

    stats = cache.stats()
    assert stats["hits"] == 1
    assert stats["misses"] == 1
    assert stats["size"] == 1


def test_cache_scope_isolation():
    """Hai user khác đơn vị cùng câu hỏi KHÔNG ĐƯỢC dùng chung cache."""
    cache = QueryCache(default_ttl_seconds=60)
    plan = _sample_plan("Tổng quan")

    scope_mn = UserScope(username="u1", role="user", unit_ids=["CN Miền Nam"])
    scope_mb = UserScope(username="u2", role="user", unit_ids=["CN Miền Bắc"])

    result_mn = QueryResult.ok(data=[{"total": 50, "unit": "CN Miền Nam"}])
    cache.set(plan, scope_mn, result_mn)

    # User Miền Bắc hỏi cùng câu -> Phải Miss, không được lấy data Miền Nam!
    cached_mb = cache.get(plan, scope_mb)
    assert cached_mb is None

    # User Miền Nam hỏi lại -> Hit
    cached_mn = cache.get(plan, scope_mn)
    assert cached_mn is not None
    assert cached_mn.data[0]["unit"] == "CN Miền Nam"


def test_cache_ttl_expiry():
    # Cache có TTL cực ngắn (0.05 giây)
    cache = QueryCache(default_ttl_seconds=1)
    plan = _sample_plan()
    scope = UserScope(username="admin", role="admin")

    result = QueryResult.ok(data=[{"total": 100}])
    cache.set(plan, scope, result, ttl_seconds=0.01)

    time.sleep(0.02)
    # Đã hết hạn -> Miss
    assert cache.get(plan, scope) is None


def test_cache_invalidate_all():
    cache = QueryCache()
    plan = _sample_plan()
    scope = UserScope(username="admin", role="admin")

    cache.set(plan, scope, QueryResult.ok(data=[]))
    assert cache.stats()["size"] == 1

    cache.invalidate_all()
    assert cache.stats()["size"] == 0
    assert cache.get(plan, scope) is None
