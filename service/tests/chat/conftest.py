"""
Shared fixtures cho test module chat — Dev A + Dev B đều dùng.

Fixtures:
  - admin_scope / user_scope: UserScope giả cho admin và user thường
  - sample_plans: Dict[str, QueryPlan] cho 4 pattern
  - mock_executor: MockQueryExecutor instance
"""

from __future__ import annotations

import pytest

from dms.chat.contract import (
    AnswerShape,
    QueryPattern,
    QueryPlan,
    UserScope,
)
from dms.chat.mock_executor import MockQueryExecutor

# ── User Scopes ──


@pytest.fixture
def admin_scope() -> UserScope:
    """Admin user — thấy toàn bộ đơn vị."""
    return UserScope(
        username="admin",
        role="admin",
        display_name="Admin",
        unit_ids=[],
    )


@pytest.fixture
def user_scope_mn() -> UserScope:
    """User thuộc CN Miền Nam — chỉ thấy data Miền Nam."""
    return UserScope(
        username="user_mn",
        role="user",
        display_name="Nguyễn Văn A",
        unit_ids=["CN Miền Nam"],
    )


@pytest.fixture
def user_scope_mb() -> UserScope:
    """User thuộc CN Miền Bắc — chỉ thấy data Miền Bắc."""
    return UserScope(
        username="user_mb",
        role="user",
        display_name="Trần Thị B",
        unit_ids=["CN Miền Bắc"],
    )


# ── Sample QueryPlans — 1 cho mỗi Pattern ──


@pytest.fixture
def plan_overview() -> QueryPlan:
    """Pattern 1: Tổng quan phản hồi tháng 8."""
    return QueryPlan(
        pattern=QueryPattern.SQL_TEMPLATE,
        answer_shape=AnswerShape.NUMBER,
        original_query="Tổng quan phản hồi tháng 8 năm 2026",
        confidence=0.92,
        function_name="get_overview",
        params={"date_from": "2026-08-01", "date_to": "2026-08-31"},
    )


@pytest.fixture
def plan_daily_trend() -> QueryPlan:
    """Pattern 1: Xu hướng hàng ngày."""
    return QueryPlan(
        pattern=QueryPattern.SQL_TEMPLATE,
        answer_shape=AnswerShape.TABLE,
        original_query="Xu hướng phản hồi từ ngày 1 đến 31 tháng 8",
        confidence=0.88,
        function_name="get_daily_trend",
        params={"date_from": "2026-08-01", "date_to": "2026-08-31"},
    )


@pytest.fixture
def plan_products() -> QueryPlan:
    """Pattern 1: Top sản phẩm bị phản hồi."""
    return QueryPlan(
        pattern=QueryPattern.SQL_TEMPLATE,
        answer_shape=AnswerShape.TABLE,
        original_query="Top sản phẩm bị phản hồi nhiều nhất tháng 8",
        confidence=0.90,
        function_name="get_products",
        params={"date_from": "2026-08-01", "date_to": "2026-08-31"},
    )


@pytest.fixture
def plan_semantic_view() -> QueryPlan:
    """Pattern 2: SQL trên semantic view."""
    return QueryPlan(
        pattern=QueryPattern.SEMANTIC_VIEW,
        answer_shape=AnswerShape.TABLE,
        original_query="So sánh số phản hồi tiêu cực theo sản phẩm tháng 8",
        confidence=0.80,
        sql="SELECT product, COUNT(*) as count FROM v_issues_current WHERE sentiment = 'Tiêu cực' GROUP BY product ORDER BY count DESC LIMIT 10",
    )


@pytest.fixture
def plan_fts5_search() -> QueryPlan:
    """Pattern 3: Tìm kiếm toàn văn."""
    return QueryPlan(
        pattern=QueryPattern.FTS5_SEARCH,
        answer_shape=AnswerShape.LIST,
        original_query="Tìm phản hồi về bóng đèn LED bị cháy",
        confidence=0.85,
        fts_query="bóng đèn LED cháy",
        fts_filters={"date_from": "2026-08-01"},
        fts_limit=10,
    )


@pytest.fixture
def plan_json_extract() -> QueryPlan:
    """Pattern 4: Trích dữ liệu raw JSON."""
    return QueryPlan(
        pattern=QueryPattern.JSON_EXTRACT,
        answer_shape=AnswerShape.NARRATIVE,
        original_query="Chi tiết khách hàng phản hồi FB-2026-10001",
        confidence=0.88,
        json_keys=["Tên khách hàng", "Số điện thoại", "Tỉnh/TP", "Nội dung phản hồi"],
        json_filters={"issue_code": "FB-2026-10001"},
    )


# ── Mock Executor ──


@pytest.fixture
def mock_executor() -> MockQueryExecutor:
    """MockQueryExecutor — Dev B dùng test mà không cần DB thật."""
    return MockQueryExecutor()
