"""Unit tests for FunctionRegistry and ScopePolicy integration."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from dms.chat.contract import UserScope
from dms.chat.db.function_registry import FunctionRegistry


@pytest.fixture
def mock_repo():
    repo = MagicMock()
    repo.fetch_analytics_rows.return_value = [
        {
            "feedback_id": 1,
            "issue_code": "FB-001",
            "issue_date": "2026-08-15",
            "source": "Hotline",
            "unit_name": "CN Miền Nam",
            "business_status": "Đã xử lý",
            "product": "Đèn LED AT04",
            "sentiment": "Tiêu cực",
            "labels": [{"label": "Báo lỗi", "major_group": "Sản phẩm"}],
            "raw_data_json": "{}",
            "is_active": 1,
        },
        {
            "feedback_id": 2,
            "issue_code": "FB-002",
            "issue_date": "2026-08-16",
            "source": "Zalo",
            "unit_name": "CN Miền Bắc",
            "business_status": "Chờ xử lý",
            "product": "Ống nhựa PPR",
            "sentiment": "Tích cực",
            "labels": [{"label": "Báo CL tốt", "major_group": "Sản phẩm"}],
            "raw_data_json": "{}",
            "is_active": 1,
        },
    ]
    repo.fetch_issues_page.return_value = (
        [{"feedback_id": 1, "unit_name": "CN Miền Nam"}],
        1,
    )
    return repo


def test_registry_executes_overview_admin(mock_repo):
    registry = FunctionRegistry(mock_repo)
    admin_scope = UserScope(username="admin", role="admin")

    res = registry.execute(
        "get_overview",
        {"date_from": "2026-08-01", "date_to": "2026-08-31"},
        admin_scope,
    )
    assert len(res) == 1
    overview = res[0]
    assert "total_issues" in overview
    assert overview["total_issues"]["value"] == 2


def test_registry_executes_overview_scoped_user(mock_repo):
    registry = FunctionRegistry(mock_repo)
    user_scope = UserScope(username="user_mn", role="user", unit_ids=["CN Miền Nam"])

    res = registry.execute(
        "get_overview",
        {"date_from": "2026-08-01", "date_to": "2026-08-31"},
        user_scope,
    )
    assert len(res) == 1
    overview = res[0]
    assert overview["total_issues"]["value"] == 1


def test_registry_rejects_forbidden_unit(mock_repo):
    registry = FunctionRegistry(mock_repo)
    user_scope = UserScope(username="user_mn", role="user", unit_ids=["CN Miền Nam"])

    with pytest.raises(PermissionError) as exc:
        registry.execute(
            "get_overview",
            {"unit_name": "CN Miền Bắc"},
            user_scope,
        )
    assert "Bạn không có quyền xem dữ liệu của đơn vị 'CN Miền Bắc'" in str(exc.value)


def test_registry_unknown_function_raises(mock_repo):
    registry = FunctionRegistry(mock_repo)
    admin_scope = UserScope(username="admin", role="admin")

    with pytest.raises(ValueError) as exc:
        registry.execute("unknown_fn", {}, admin_scope)
    assert "không tồn tại" in str(exc.value)


def test_registry_units_filtered_for_user(mock_repo):
    registry = FunctionRegistry(mock_repo)
    user_scope = UserScope(username="user_mn", role="user", unit_ids=["CN Miền Nam"])

    res = registry.execute("get_units", {}, user_scope)
    assert len(res) == 1
    units_items = res[0]["items"]
    assert all(u["label"] == "CN Miền Nam" for u in units_items)


# ── get_priority_issues: kết quả là một dict {items, total}, đơn vị nằm ở "department" ──


def test_registry_priority_issues_admin_gets_wrapped_result(mock_repo):
    registry = FunctionRegistry(mock_repo)
    admin_scope = UserScope(username="admin", role="admin")

    res = registry.execute("get_priority_issues", {"limit": 5}, admin_scope)

    assert len(res) == 1
    body = res[0]
    assert {"items", "total"} <= set(body)
    assert {item["issue_code"] for item in body["items"]} == {"FB-001", "FB-002"}


def test_registry_priority_issues_filtered_for_user(mock_repo):
    """Trước đây hàm này ném AttributeError vì coi dict kết quả như list dòng."""
    registry = FunctionRegistry(mock_repo)
    user_scope = UserScope(username="user_mn", role="user", unit_ids=["CN Miền Nam"])

    res = registry.execute("get_priority_issues", {"limit": 5}, user_scope)

    assert len(res) == 1
    body = res[0]
    assert [item["issue_code"] for item in body["items"]] == ["FB-001"]
    assert all(item["department"] == "CN Miền Nam" for item in body["items"])
    assert body["total"] == 1


def test_registry_priority_issues_empty_for_user_without_units(mock_repo):
    registry = FunctionRegistry(mock_repo)
    scope = UserScope(username="user_x", role="user", unit_ids=["CN Miền Trung"])

    body = registry.execute("get_priority_issues", {}, scope)[0]

    assert body["items"] == [] and body["total"] == 0
