"""FunctionRegistry — Pattern 1 SQL Template Execution (Khối [4] DATA).

Ánh xạ và thực thi 16 hàm SQL mẫu theo FUNCTION_REGISTRY_SPEC,
kết nối trực tiếp với FeedbackAnalyticsService và FeedbackAnalyticsRepository,
đồng thời tự động áp dụng Data Scoping Guard (ScopePolicy).
"""

from __future__ import annotations

import logging
from typing import Any

from ...analytics import AnalyticsFilter, FeedbackAnalyticsRepository, FeedbackAnalyticsService
from ..auth.scope_policy import ScopePolicy
from ..contract import FUNCTION_REGISTRY_SPEC, UserScope

logger = logging.getLogger("dms-chat-function-registry")


class FunctionRegistry:
    """Registry executing pre-built analytical query functions."""

    def __init__(self, repository: FeedbackAnalyticsRepository) -> None:
        self.repository = repository
        self.service = FeedbackAnalyticsService(repository)

    def execute(
        self, function_name: str, params: dict[str, Any], scope: UserScope
    ) -> list[dict[str, Any]]:
        """Thực thi một hàm SQL mẫu với phạm vi phân quyền của UserScope.

        Returns:
            list[dict[str, Any]] chứa kết quả truy vấn.
        """
        if function_name not in FUNCTION_REGISTRY_SPEC:
            raise ValueError(
                f"Hàm '{function_name}' không tồn tại trong Function Registry. "
                f"Các hàm hỗ trợ: {', '.join(FUNCTION_REGISTRY_SPEC)}"
            )

        # 1. Ép ràng buộc phân quyền đơn vị
        scoped_params = ScopePolicy.enforce_scope_on_params(params, scope)

        # 2. Xây dựng AnalyticsFilter
        analytics_filter = AnalyticsFilter(
            date_from=scoped_params.get("date_from"),
            date_to=scoped_params.get("date_to"),
            compare_from=scoped_params.get("compare_from"),
            compare_to=scoped_params.get("compare_to"),
            province=scoped_params.get("province"),
            district=scoped_params.get("district"),
            unit_name=scoped_params.get("unit_name"),
        )

        handler = getattr(self, f"_handle_{function_name}", None)
        if handler is None:
            raise NotImplementedError(f"Handler cho '{function_name}' chưa được triển khai.")

        return handler(analytics_filter, scoped_params, scope)

    # ═══════════════════════════════════════════════════════════════════
    # HANDLERS CHO 16 HÀM SQL MẪU
    # ═══════════════════════════════════════════════════════════════════

    def _handle_get_dashboard(
        self, flt: AnalyticsFilter, params: dict[str, Any], scope: UserScope
    ) -> list[dict[str, Any]]:
        res = self.service.dashboard(flt)
        # Nếu user bị giới hạn đơn vị, lọc panel units
        if not scope.is_admin and scope.unit_ids and "units" in res:
            res["units"]["items"] = [
                u for u in res["units"].get("items", []) if u["label"] in scope.unit_ids
            ]
        return [res]

    def _handle_get_overview(
        self, flt: AnalyticsFilter, params: dict[str, Any], scope: UserScope
    ) -> list[dict[str, Any]]:
        return [self.service.overview(flt)]

    def _handle_get_comparison(
        self, flt: AnalyticsFilter, params: dict[str, Any], scope: UserScope
    ) -> list[dict[str, Any]]:
        period = params.get("period", "month")
        return [self.service.comparison(flt, period=period)]

    def _handle_get_daily_trend(
        self, flt: AnalyticsFilter, params: dict[str, Any], scope: UserScope
    ) -> list[dict[str, Any]]:
        return [self.service.daily_trend(flt)]

    def _handle_get_issue_types(
        self, flt: AnalyticsFilter, params: dict[str, Any], scope: UserScope
    ) -> list[dict[str, Any]]:
        return [self.service.issue_types(flt)]

    def _handle_get_sources(
        self, flt: AnalyticsFilter, params: dict[str, Any], scope: UserScope
    ) -> list[dict[str, Any]]:
        return [self.service.sources(flt)]

    def _handle_get_units(
        self, flt: AnalyticsFilter, params: dict[str, Any], scope: UserScope
    ) -> list[dict[str, Any]]:
        res = self.service.units(flt)
        if not scope.is_admin and scope.unit_ids:
            res["items"] = [
                u for u in res.get("items", []) if u["label"] in scope.unit_ids
            ]
        return [res]

    def _handle_get_groups(
        self, flt: AnalyticsFilter, params: dict[str, Any], scope: UserScope
    ) -> list[dict[str, Any]]:
        return [self.service.groups(flt)]

    def _handle_get_products(
        self, flt: AnalyticsFilter, params: dict[str, Any], scope: UserScope
    ) -> list[dict[str, Any]]:
        return [self.service.products(flt)]

    def _handle_get_geography(
        self, flt: AnalyticsFilter, params: dict[str, Any], scope: UserScope
    ) -> list[dict[str, Any]]:
        return [self.service.geography(flt)]

    def _handle_get_status_backlog(
        self, flt: AnalyticsFilter, params: dict[str, Any], scope: UserScope
    ) -> list[dict[str, Any]]:
        return [self.service.status_backlog(flt)]

    def _handle_get_issues(
        self, flt: AnalyticsFilter, params: dict[str, Any], scope: UserScope
    ) -> list[dict[str, Any]]:
        page = int(params.get("page", 1))
        page_size = int(params.get("page_size", 50))
        effective_unit = flt.unit_name or params.get("unit_name")

        res = self.service.issues(
            flt,
            page=page,
            page_size=page_size,
            source=params.get("source"),
            unit_name=effective_unit,
            label=params.get("label"),
            product=params.get("product"),
            business_status=params.get("business_status"),
        )
        return [res]

    def _handle_get_priority_issues(
        self, flt: AnalyticsFilter, params: dict[str, Any], scope: UserScope
    ) -> list[dict[str, Any]]:
        limit = int(params.get("limit", 10))
        items = self.service.priority_issues(flt, limit=limit)
        # Lọc an toàn đơn vị
        if not scope.is_admin and scope.unit_ids:
            items = ScopePolicy.filter_rows_by_scope(items, scope)
        return items

    def _handle_get_duplicates(
        self, flt: AnalyticsFilter, params: dict[str, Any], scope: UserScope
    ) -> list[dict[str, Any]]:
        page = int(params.get("page", 1))
        page_size = int(params.get("page_size", 25))
        return [self.service.duplicate_details(flt, page=page, page_size=page_size)]

    def _handle_get_unit_issue_type_matrix(
        self, flt: AnalyticsFilter, params: dict[str, Any], scope: UserScope
    ) -> list[dict[str, Any]]:
        res = self.service.unit_issue_type_matrix(flt)
        if not scope.is_admin and scope.unit_ids:
            res["units"] = [u for u in res.get("units", []) if u in scope.unit_ids]
            res["rows"] = [
                r for r in res.get("rows", []) if r.get("unit") in scope.unit_ids
            ]
        return [res]

    def _handle_get_data_quality(
        self, flt: AnalyticsFilter, params: dict[str, Any], scope: UserScope
    ) -> list[dict[str, Any]]:
        return [self.service.data_quality(flt)]
