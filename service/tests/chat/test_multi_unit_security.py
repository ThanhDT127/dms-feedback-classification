from __future__ import annotations

import sqlite3
from typing import Any

import pytest

from dms.analytics.models import AnalyticsFilter
from dms.analytics.service import FeedbackAnalyticsService
from dms.analytics.sql_queries import issues_where, register_issue_functions
from dms.chat.auth.scope_policy import ScopePolicy
from dms.chat.contract import UserScope
from dms.chat.db.function_registry import FunctionRegistry


class DummyRepo:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    def fetch_analytics_rows(self) -> list[dict[str, Any]]:
        return self.rows


def test_analytics_filter_unit_names_rows_filtering():
    rows = [
        {"feedback_id": 1, "is_active": 1, "unit_name": "Vùng 1", "issue_code": "C1", "labels": []},
        {"feedback_id": 2, "is_active": 1, "unit_name": "Vùng 2", "issue_code": "C2", "labels": []},
        {"feedback_id": 3, "is_active": 1, "unit_name": "Vùng 3", "issue_code": "C3", "labels": []},
    ]
    service = FeedbackAnalyticsService(DummyRepo(rows))  # type: ignore[arg-type]

    # Filter by 2 units
    flt = AnalyticsFilter(unit_names=("Vùng 1", "Vùng 2"))
    filtered = service._rows(flt)
    assert len(filtered) == 2
    assert {r["unit_name"] for r in filtered} == {"Vùng 1", "Vùng 2"}

    # Filter by 1 unit
    flt_single = AnalyticsFilter(unit_name="Vùng 1")
    assert len(service._rows(flt_single)) == 1

    # Empty unit_names filter matches all active
    flt_all = AnalyticsFilter()
    assert len(service._rows(flt_all)) == 3


def test_issues_where_with_unit_names():
    conn = sqlite3.connect(":memory:")
    register_issue_functions(conn)

    flt = AnalyticsFilter(unit_names=("Vùng 1", "Vùng 2"))
    where, params = issues_where(
        flt, source=None, unit_name=None, label=None, product=None, business_status=None
    )
    assert "analytics_fold(r.unit_name) IN (?, ?)" in where
    assert params == ["vùng 1", "vùng 2"]


def test_function_registry_enforces_multiple_units():
    rows = [
        {"feedback_id": 1, "is_active": 1, "unit_name": "Vùng 1", "issue_code": "C1", "labels": []},
        {"feedback_id": 2, "is_active": 1, "unit_name": "Vùng 2", "issue_code": "C2", "labels": []},
        {"feedback_id": 3, "is_active": 1, "unit_name": "Vùng 3", "issue_code": "C3", "labels": []},
    ]
    repo = DummyRepo(rows)
    reg = FunctionRegistry(repo)  # type: ignore[arg-type]

    # Multi-unit scope restricts to assigned units
    scope = UserScope(
        username="operator",
        role="user",
        display_name="Operator",
        unit_ids=["Vùng 1", "Vùng 2"],
    )
    res = reg.execute("get_overview", {}, scope)
    assert res[0]["total_issues"]["value"] == 2

    # Single-unit scope
    scope_single = UserScope(
        username="op1",
        role="user",
        display_name="Op1",
        unit_ids=["Vùng 1"],
    )
    res_single = reg.execute("get_overview", {}, scope_single)
    assert res_single[0]["total_issues"]["value"] == 1


def test_user_narrowing_to_subset_preserves_unit_names():
    scope = UserScope(
        username="user3",
        role="user",
        unit_ids=["Vùng 1", "Vùng 2", "Vùng 3"],
    )
    params = {"unit_names": ["Vùng 1", "Vùng 2"]}
    scoped = ScopePolicy.enforce_scope_on_params(params, scope)

    assert scoped["unit_names"] == ["Vùng 1", "Vùng 2"]
    assert scoped["_allowed_unit_ids"] == ["Vùng 1", "Vùng 2"]
    assert "unit_name" not in scoped


def test_user_narrowing_to_unauthorized_unit_raises():
    scope = UserScope(
        username="user3",
        role="user",
        unit_ids=["Vùng 1", "Vùng 2"],
    )
    params = {"unit_names": ["Vùng 1", "Vùng 4"]}
    with pytest.raises(PermissionError) as exc:
        ScopePolicy.enforce_scope_on_params(params, scope)
    assert "Vùng 4" in str(exc.value)


def test_function_registry_with_requested_subset():
    rows = [
        {"feedback_id": 1, "is_active": 1, "unit_name": "Vùng 1", "issue_code": "C1", "labels": []},
        {"feedback_id": 2, "is_active": 1, "unit_name": "Vùng 2", "issue_code": "C2", "labels": []},
        {"feedback_id": 3, "is_active": 1, "unit_name": "Vùng 3", "issue_code": "C3", "labels": []},
    ]
    repo = DummyRepo(rows)
    reg = FunctionRegistry(repo)  # type: ignore[arg-type]

    # User assigned 3 units queries specifically for 2 units
    scope = UserScope(
        username="operator",
        role="user",
        display_name="Operator",
        unit_ids=["Vùng 1", "Vùng 2", "Vùng 3"],
    )
    res = reg.execute("get_overview", {"unit_names": ["Vùng 1", "Vùng 2"]}, scope)
    assert res[0]["total_issues"]["value"] == 2

