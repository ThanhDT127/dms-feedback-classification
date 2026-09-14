"""
Contract Tests — Kiểm tra Interface Contract giữa Dev A và Dev B.

Chạy bởi CẢ HAI dev, mỗi commit:
  pytest tests/chat/contract/test_contract.py -v

Đảm bảo:
  1. QueryPlan validate() chặn đúng input sai
  2. QueryPlan <-> dict roundtrip không mất dữ liệu
  3. QueryResult factory methods đúng format
  4. MockQueryExecutor trả đúng contract cho cả 4 Pattern
  5. Scope enforcement: user thường không thấy data đơn vị khác
  6. UserScope.from_user_dict() khớp format UserStore thật
"""

from __future__ import annotations

import json

import pytest

from dms.chat.contract import (
    FUNCTION_REGISTRY_SPEC,
    AnswerShape,
    ChatMessage,
    ChatSession,
    MessageRole,
    QueryPattern,
    QueryPlan,
    QueryResult,
    QueryResultMetadata,
    QueryStatus,
    UserScope,
)
from dms.chat.mock_executor import MockQueryExecutor


class TestQueryPlanValidation:
    """QueryPlan.validate() phải chặn đúng các input sai."""

    def test_valid_sql_template(self):
        plan = QueryPlan(
            pattern=QueryPattern.SQL_TEMPLATE,
            answer_shape=AnswerShape.NUMBER,
            original_query="Tổng quan tháng 8",
            function_name="get_overview",
            params={"date_from": "2026-08-01", "date_to": "2026-08-31"},
        )
        assert plan.validate() == []

    def test_missing_function_name(self):
        plan = QueryPlan(
            pattern=QueryPattern.SQL_TEMPLATE,
            answer_shape=AnswerShape.NUMBER,
            original_query="Tổng quan",
        )
        errors = plan.validate()
        assert any("function_name" in e for e in errors)

    def test_invalid_function_name(self):
        plan = QueryPlan(
            pattern=QueryPattern.SQL_TEMPLATE,
            answer_shape=AnswerShape.NUMBER,
            original_query="Tổng quan",
            function_name="get_nonexistent_thing",
        )
        errors = plan.validate()
        assert any("không tồn tại" in e for e in errors)

    def test_missing_sql_for_semantic_view(self):
        plan = QueryPlan(
            pattern=QueryPattern.SEMANTIC_VIEW,
            answer_shape=AnswerShape.TABLE,
            original_query="So sánh",
        )
        errors = plan.validate()
        assert any("sql" in e for e in errors)

    def test_missing_fts_query(self):
        plan = QueryPlan(
            pattern=QueryPattern.FTS5_SEARCH,
            answer_shape=AnswerShape.LIST,
            original_query="Tìm kiếm",
        )
        errors = plan.validate()
        assert any("fts_query" in e for e in errors)

    def test_missing_json_keys(self):
        plan = QueryPlan(
            pattern=QueryPattern.JSON_EXTRACT,
            answer_shape=AnswerShape.NARRATIVE,
            original_query="Chi tiết",
        )
        errors = plan.validate()
        assert any("json_keys" in e for e in errors)

    def test_invalid_confidence(self):
        plan = QueryPlan(
            pattern=QueryPattern.SQL_TEMPLATE,
            answer_shape=AnswerShape.NUMBER,
            original_query="Test",
            function_name="get_overview",
            confidence=1.5,
        )
        errors = plan.validate()
        assert any("confidence" in e for e in errors)

    def test_empty_query(self):
        plan = QueryPlan(
            pattern=QueryPattern.SQL_TEMPLATE,
            answer_shape=AnswerShape.NUMBER,
            original_query="   ",
            function_name="get_overview",
        )
        errors = plan.validate()
        assert any("original_query" in e for e in errors)


class TestQueryPlanRoundtrip:
    """QueryPlan.to_dict() <-> from_dict() không mất dữ liệu."""

    def test_sql_template_roundtrip(self):
        original = QueryPlan(
            pattern=QueryPattern.SQL_TEMPLATE,
            answer_shape=AnswerShape.TABLE,
            original_query="Top sản phẩm tháng 8",
            confidence=0.90,
            function_name="get_products",
            params={"date_from": "2026-08-01", "date_to": "2026-08-31"},
        )
        d = original.to_dict()
        restored = QueryPlan.from_dict(d)
        assert restored.pattern == original.pattern
        assert restored.function_name == original.function_name
        assert restored.params == original.params
        assert restored.confidence == original.confidence

    def test_fts5_roundtrip(self):
        original = QueryPlan(
            pattern=QueryPattern.FTS5_SEARCH,
            answer_shape=AnswerShape.LIST,
            original_query="Tìm cháy bóng",
            fts_query="cháy bóng đèn",
            fts_filters={"date_from": "2026-08-01"},
            fts_limit=15,
        )
        d = original.to_dict()
        # JSON serializable
        json_str = json.dumps(d, ensure_ascii=False)
        restored = QueryPlan.from_dict(json.loads(json_str))
        assert restored.fts_query == original.fts_query
        assert restored.fts_limit == original.fts_limit

    def test_json_extract_roundtrip(self):
        original = QueryPlan(
            pattern=QueryPattern.JSON_EXTRACT,
            answer_shape=AnswerShape.NARRATIVE,
            original_query="Chi tiết KH",
            json_keys=["Tên khách hàng", "Số điện thoại"],
            json_filters={"feedback_id": 10001},
        )
        d = original.to_dict()
        restored = QueryPlan.from_dict(d)
        assert restored.json_keys == original.json_keys


class TestQueryResult:
    """QueryResult factory methods và roundtrip."""

    def test_ok_result(self):
        result = QueryResult.ok(
            data=[{"product": "AT04", "count": 45}],
            total_rows=1, query_time_ms=23,
            scope_applied="admin", pattern_used="sql_template",
        )
        assert result.status == QueryStatus.OK
        assert result.data[0]["product"] == "AT04"
        assert result.metadata.cache_hit is False

    def test_error_result(self):
        result = QueryResult.error("SQL injection detected")
        assert result.status == QueryStatus.ERROR
        assert "injection" in result.error_message

    def test_no_data_result(self):
        result = QueryResult.no_data()
        assert result.status == QueryStatus.NO_DATA
        assert result.data == []

    def test_roundtrip(self):
        original = QueryResult.ok(
            data=[{"label": "Báo lỗi", "issue_count": 78}],
            total_rows=1, pattern_used="sql_template",
        )
        d = original.to_dict()
        restored = QueryResult.from_dict(d)
        assert restored.status == original.status
        assert restored.data == original.data

    def test_cache_key_deterministic(self):
        plan = QueryPlan(
            pattern=QueryPattern.SQL_TEMPLATE, answer_shape=AnswerShape.NUMBER,
            original_query="Test", function_name="get_overview",
        )
        scope = UserScope(username="u1", role="user", unit_ids=["CN Miền Nam"])
        result = QueryResult.ok(data=[])
        key1 = result.cache_key(plan, scope)
        key2 = result.cache_key(plan, scope)
        assert key1 == key2
        assert len(key1) == 16

    def test_cache_key_scope_sensitive(self):
        plan = QueryPlan(
            pattern=QueryPattern.SQL_TEMPLATE, answer_shape=AnswerShape.NUMBER,
            original_query="Test", function_name="get_overview",
        )
        scope_mn = UserScope(username="u1", role="user", unit_ids=["CN Miền Nam"])
        scope_mb = UserScope(username="u2", role="user", unit_ids=["CN Miền Bắc"])
        result = QueryResult.ok(data=[])
        assert result.cache_key(plan, scope_mn) != result.cache_key(plan, scope_mb)


class TestUserScope:
    """UserScope tương thích với UserStore thật."""

    def test_from_user_dict_admin(self):
        user_dict = {
            "username": "admin",
            "display_name": "Admin",
            "role": "admin",
            "is_active": True,
        }
        scope = UserScope.from_user_dict(user_dict)
        assert scope.is_admin is True
        assert scope.unit_ids == []
        assert "toàn quyền" in scope.scope_description

    def test_from_user_dict_user(self):
        user_dict = {
            "username": "user_mn",
            "display_name": "Nguyễn Văn A",
            "role": "user",
            "is_active": True,
            "unit_ids": ["CN Miền Nam"],
        }
        scope = UserScope.from_user_dict(user_dict)
        assert scope.is_admin is False
        assert scope.unit_ids == ["CN Miền Nam"]
        assert "CN Miền Nam" in scope.scope_description

    def test_from_user_dict_missing_unit_ids(self):
        """UserStore hiện tại chưa có unit_ids → default rỗng."""
        user_dict = {"username": "old_user", "role": "user"}
        scope = UserScope.from_user_dict(user_dict)
        assert scope.unit_ids == []


class TestMockExecutorContract:
    """MockQueryExecutor trả về đúng contract cho cả 4 pattern."""

    @pytest.fixture
    def executor(self):
        return MockQueryExecutor()

    @pytest.fixture
    def admin(self):
        return UserScope(username="admin", role="admin")

    @pytest.fixture
    def user_mn(self):
        return UserScope(username="user_mn", role="user", unit_ids=["CN Miền Nam"])

    # ── Pattern 1: SQL Template ──

    def test_get_overview(self, executor, admin):
        plan = QueryPlan(
            pattern=QueryPattern.SQL_TEMPLATE, answer_shape=AnswerShape.NUMBER,
            original_query="Tổng quan tháng 8", function_name="get_overview",
            params={"date_from": "2026-08-01", "date_to": "2026-08-31"},
        )
        result = executor.execute(plan, admin)
        assert result.status == QueryStatus.OK
        assert result.metadata.pattern_used == "sql_template"
        assert len(result.data) == 1
        overview = result.data[0]
        # Kiểm tra format thật từ FeedbackAnalyticsService.overview()
        assert "total_issues" in overview
        assert "value" in overview["total_issues"]
        assert "processed_issues" in overview
        assert "sentiment_coverage" in overview

    def test_get_daily_trend(self, executor, admin):
        plan = QueryPlan(
            pattern=QueryPattern.SQL_TEMPLATE, answer_shape=AnswerShape.TABLE,
            original_query="Xu hướng tháng 8", function_name="get_daily_trend",
            params={"date_from": "2026-08-01", "date_to": "2026-08-31"},
        )
        result = executor.execute(plan, admin)
        assert result.status == QueryStatus.OK
        trend = result.data[0]
        assert "items" in trend
        assert len(trend["items"]) == 31
        first_day = trend["items"][0]
        assert "date" in first_day
        assert "issue_count" in first_day
        assert "sentiment_counts" in first_day
        assert "Tích cực" in first_day["sentiment_counts"]

    def test_invalid_function_rejected(self, executor, admin):
        plan = QueryPlan(
            pattern=QueryPattern.SQL_TEMPLATE, answer_shape=AnswerShape.NUMBER,
            original_query="Test", function_name="get_nonexistent",
        )
        result = executor.execute(plan, admin)
        assert result.status == QueryStatus.ERROR

    # ── Pattern 3: FTS5 Search ──

    def test_fts5_search(self, executor, admin):
        plan = QueryPlan(
            pattern=QueryPattern.FTS5_SEARCH, answer_shape=AnswerShape.LIST,
            original_query="Tìm cháy bóng", fts_query="cháy",
            fts_limit=5,
        )
        result = executor.execute(plan, admin)
        assert result.status == QueryStatus.OK
        assert len(result.data) <= 5

    # ── Pattern 4: JSON Extract ──

    def test_json_extract(self, executor, admin):
        plan = QueryPlan(
            pattern=QueryPattern.JSON_EXTRACT, answer_shape=AnswerShape.NARRATIVE,
            original_query="Chi tiết KH", json_keys=["Tên khách hàng", "Số điện thoại"],
            json_filters={"issue_code": "FB-2026-10001"},
        )
        result = executor.execute(plan, admin)
        assert result.status == QueryStatus.OK
        assert "Tên khách hàng" in result.data[0]
        assert "Số điện thoại" in result.data[0]

    # ── Scope Enforcement ──

    def test_units_scope_filtered(self, executor, user_mn):
        """User CN Miền Nam chỉ thấy data Miền Nam."""
        plan = QueryPlan(
            pattern=QueryPattern.SQL_TEMPLATE, answer_shape=AnswerShape.TABLE,
            original_query="Đơn vị", function_name="get_units",
        )
        result = executor.execute(plan, user_mn)
        assert result.status == QueryStatus.OK
        units = result.data[0]["items"]
        for unit in units:
            assert unit["label"] in user_mn.unit_ids

    def test_issues_scope_filtered(self, executor, user_mn):
        """User CN Miền Nam chỉ thấy issues Miền Nam."""
        plan = QueryPlan(
            pattern=QueryPattern.SQL_TEMPLATE, answer_shape=AnswerShape.TABLE,
            original_query="Danh sách phản hồi", function_name="get_issues",
        )
        result = executor.execute(plan, user_mn)
        assert result.status == QueryStatus.OK
        issues = result.data[0]["items"]
        for issue in issues:
            assert issue["unit_name"] in user_mn.unit_ids

    # ── Validation ──

    def test_missing_required_field_rejected(self, executor, admin):
        """Plan thiếu field bắt buộc bị reject."""
        plan = QueryPlan(
            pattern=QueryPattern.SQL_TEMPLATE, answer_shape=AnswerShape.NUMBER,
            original_query="Test",
            # Missing function_name
        )
        result = executor.execute(plan, admin)
        assert result.status == QueryStatus.ERROR
        assert "function_name" in result.error_message


class TestFunctionRegistrySpec:
    """FUNCTION_REGISTRY_SPEC phải khớp với analytics_api.py endpoints."""

    def test_all_functions_have_required_fields(self):
        for name, spec in FUNCTION_REGISTRY_SPEC.items():
            assert "description" in spec, f"{name} thiếu description"
            assert "params" in spec, f"{name} thiếu params"
            assert "returns" in spec, f"{name} thiếu returns"
            assert "maps_to" in spec, f"{name} thiếu maps_to"
            assert "api_endpoint" in spec, f"{name} thiếu api_endpoint"

    def test_all_functions_in_mock(self):
        from dms.chat.mock_executor import _FUNCTION_MAP
        for name in FUNCTION_REGISTRY_SPEC:
            assert name in _FUNCTION_MAP, f"Function '{name}' missing from MockQueryExecutor"


class TestChatModels:
    """ChatSession và ChatMessage basic tests."""

    def test_chat_message_creation(self):
        msg = ChatMessage(role=MessageRole.USER, content="Tổng quan tháng 8?")
        assert msg.role == MessageRole.USER
        assert msg.created_at  # Auto-populated

    def test_chat_session_creation(self):
        session = ChatSession(session_id="sess-001", user_id="admin")
        assert session.session_id == "sess-001"
        assert session.created_at  # Auto-populated
