"""User được cấp nhiều đơn vị: mọi hàm SQL mẫu chỉ trả số liệu của các đơn vị đó."""

from __future__ import annotations

import json

import pytest
from analytics_support import seed_classified_records

from dms.analytics import AnalyticsFilter, FeedbackAnalyticsRepository
from dms.analytics.service import FeedbackAnalyticsService
from dms.chat.contract import FUNCTION_REGISTRY_SPEC, UserScope
from dms.chat.db.function_registry import FunctionRegistry

TV1, TV2, TV3 = "Truyền thống Vùng 1", "Truyền thống Vùng 2", "Truyền thống Vùng 3"
# Chỉ có ở phản hồi của Vùng 2: xuất hiện trong kết quả là lộ dữ liệu ngoài phạm vi.
TV2_PRODUCT = "Ổn áp riêng vùng hai"
TV2_CONTENT = "Ổn áp riêng vùng hai bị hỏng mạch"
PERIOD = {"date_from": "2026-08-01", "date_to": "2026-08-31"}
COMPARE = {"compare_from": "2026-07-01", "compare_to": "2026-07-31"}
MULTI = UserScope(username="nv", role="user", display_name="NV", unit_ids=[TV1, TV3])
ADMIN = UserScope(username="admin", role="admin", display_name="Admin", unit_ids=[])


@pytest.fixture
def repo(tmp_path) -> FeedbackAnalyticsRepository:
    repo = FeedbackAnalyticsRepository(tmp_path / "analytics.db")
    entries = [
        {"issue_code": "100001", "unit_name": TV1, "content": "Đèn chập chờn"},
        {"issue_code": "100002", "unit_name": TV3, "content": "Phích giữ nhiệt kém"},
        {"issue_code": "100003", "unit_name": TV2, "content": TV2_CONTENT},
        {"issue_code": "100004", "unit_name": TV2, "content": TV2_CONTENT},
    ]
    seed_classified_records(
        repo,
        db_path=repo.db_path,
        entries=[
            {
                **entry,
                "issue_date": date,
                "business_status": "Chờ xử lý",
                "product": TV2_PRODUCT if entry["unit_name"] == TV2 else "Bóng đèn LED",
                "labels": ["Báo lỗi"],
            }
            for entry in entries
            for date in ("2026-07-15", "2026-08-15")
        ],
    )
    return repo


@pytest.mark.parametrize("function_name", sorted(FUNCTION_REGISTRY_SPEC))
def test_multi_unit_user_never_sees_other_units(repo, function_name):
    params = {**PERIOD, **COMPARE} if function_name == "get_comparison" else dict(PERIOD)
    registry = FunctionRegistry(repo)

    scoped = json.dumps(registry.execute(function_name, params, MULTI), ensure_ascii=False)
    admin = json.dumps(registry.execute(function_name, params, ADMIN), ensure_ascii=False)

    assert TV2 not in scoped and TV2_PRODUCT not in scoped
    assert scoped != admin


def test_multi_unit_overview_counts_only_allowed_units(repo):
    [overview] = FunctionRegistry(repo).execute("get_overview", dict(PERIOD), MULTI)
    assert overview["total_issues"]["value"] == 2


def test_comparison_keeps_unit_scope_for_previous_period(repo):
    [result] = FunctionRegistry(repo).execute("get_comparison", {**PERIOD, **COMPARE}, MULTI)
    total = result["metrics"]["total_issues"]
    assert (total["current"], total["previous"]) == (2, 2)

    [overview] = FunctionRegistry(repo).execute("get_overview", {**PERIOD, **COMPARE}, MULTI)
    assert overview["total_issues"]["value"] == 2
    assert TV2 not in json.dumps(overview, ensure_ascii=False)


def test_empty_unit_names_matches_nothing(repo):
    overview = FeedbackAnalyticsService(repo).overview(AnalyticsFilter(unit_names=()))
    assert overview["total_issues"]["value"] == 0
