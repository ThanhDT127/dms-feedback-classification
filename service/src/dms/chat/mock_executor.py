"""
Mock Query Executor — Dev B test độc lập không cần DB thật.

Trả dữ liệu giả ĐÚNG CHUẨN FORMAT mà FeedbackAnalyticsService thật trả về.
Mỗi function mock được map 1:1 với method thật trong analytics/service.py.

Swap khi ghép Sprint:
    # Trước (Dev B test một mình):
    from dms.chat.mock_executor import MockQueryExecutor as QueryExecutor

    # Sau (ghép với Dev A):
    from dms.chat.db.query_executor import SecureQueryExecutor as QueryExecutor
"""

from __future__ import annotations

import time
from typing import Any

from .contract import (
    FUNCTION_REGISTRY_SPEC,
    QueryPattern,
    QueryPlan,
    QueryResult,
    QueryResultMetadata,
    QueryStatus,
    UserScope,
)

# ═══════════════════════════════════════════════════════════════════
# MOCK DATA — Dựa trên data thật và format thật của analytics/service.py
# ═══════════════════════════════════════════════════════════════════

def _mock_get_overview(params: dict, scope: UserScope) -> list[dict]:
    """Mock FeedbackAnalyticsService.overview() — analytics_api.py L100-105.

    Format thật: {total_issues: {value, denominator, available, ...}, processed_issues: {...}, ...}
    """
    return [{
        "total_issues": {
            "available": True, "value": 287, "denominator": 287,
            "excluded_missing_issue_code": 12,
        },
        "processed_issues": {
            "available": True, "value": 215, "denominator": 287,
            "excluded_missing_issue_code": 12,
        },
        "label_coverage": {
            "available": True, "value": 94.08, "denominator": 287,
            "numerator": 270, "excluded_missing_issue_code": 12,
        },
        "sentiment_coverage": {
            "available": True, "value": 89.20, "denominator": 287,
            "numerator": 256, "excluded_missing_issue_code": 12,
        },
        "product_coverage": {
            "available": True, "value": 82.58, "denominator": 287,
            "numerator": 237, "excluded_missing_issue_code": 12,
        },
        "multi_label_rate": {
            "available": True, "value": 15.33, "denominator": 287,
            "numerator": 44, "excluded_missing_issue_code": 12,
        },
        "duplicate_record_rate": {
            "available": True, "value": 8.70, "denominator": 299,
            "numerator": 26, "duplicate_rows": 26, "excluded_missing_issue_code": 12,
        },
        "duplicate_issue_rate": {
            "available": True, "value": 4.18, "denominator": 287,
            "numerator": 12, "duplicate_issue_codes": 12,
            "excluded_missing_issue_code": 12,
        },
        "model_accuracy": {
            "available": False, "value": None, "denominator": 0,
            "excluded_missing_issue_code": 12,
            "reason": "No human-verified ground-truth labels are stored.",
        },
    }]


def _mock_get_daily_trend(params: dict, scope: UserScope) -> list[dict]:
    """Mock FeedbackAnalyticsService.daily_trend() — format thật."""
    items = []
    for d in range(1, 32):
        items.append({
            "date": f"2026-08-{d:02d}",
            "issue_count": 8 + (d % 7) * 3,
            "sentiment_counts": {
                "Tích cực": 3 + d % 5,
                "Trung lập": 2 + d % 3,
                "Tiêu cực": 1 + d % 4,
                "Chưa gán": d % 2,
            },
            "sentiment_membership_count": 8 + (d % 7) * 3,
        })
    return [{
        "items": items,
        "total_issues": 287,
        "excluded_missing_date": 5,
        "count_semantics": "sentiment_memberships",
    }]


def _mock_get_sources(params: dict, scope: UserScope) -> list[dict]:
    """Mock FeedbackAnalyticsService.sources() — format thật."""
    return [{
        "items": [
            {"label": "DMS", "issue_count": 98, "percentage": 34.15},
            {"label": "Zalo", "issue_count": 72, "percentage": 25.09},
            {"label": "Hotline", "issue_count": 55, "percentage": 19.16},
            {"label": "Nhân viên KD", "issue_count": 42, "percentage": 14.63},
            {"label": "Facebook", "issue_count": 20, "percentage": 6.97},
        ],
        "membership_count": 287,
        "total_issues": 287,
        "excluded_missing_issue_code": 12,
    }]


def _mock_get_units(params: dict, scope: UserScope) -> list[dict]:
    """Mock FeedbackAnalyticsService.units() — format thật, filtered by scope."""
    all_units = [
        {"label": "CN Miền Nam", "issue_count": 95, "percentage": 33.10},
        {"label": "CN Miền Bắc", "issue_count": 82, "percentage": 28.57},
        {"label": "CN Hà Nội", "issue_count": 65, "percentage": 22.65},
        {"label": "CN Đà Nẵng", "issue_count": 45, "percentage": 15.68},
    ]
    if scope.is_admin:
        items = all_units
    else:
        items = [u for u in all_units if u["label"] in scope.unit_ids]
    total = sum(u["issue_count"] for u in items)
    return [{
        "items": items,
        "membership_count": total,
        "total_issues": total,
        "excluded_missing_issue_code": 3,
    }]


def _mock_get_issue_types(params: dict, scope: UserScope) -> list[dict]:
    """Mock FeedbackAnalyticsService.issue_types() — format thật."""
    return [{
        "items": [
            {"label": "Báo lỗi", "issue_count": 78, "percentage": 27.18},
            {"label": "Báo CL tốt", "issue_count": 52, "percentage": 18.12},
            {"label": "Y/c cải tiến", "issue_count": 35, "percentage": 12.20},
            {"label": "Hàng hoá", "issue_count": 28, "percentage": 9.76},
            {"label": "Bảo hành", "issue_count": 25, "percentage": 8.71},
            {"label": "Đề xuất SPM", "issue_count": 22, "percentage": 7.67},
            {"label": "HTPP", "issue_count": 18, "percentage": 6.27},
            {"label": "Hãng", "issue_count": 15, "percentage": 5.23},
            {"label": "Khác", "issue_count": 14, "percentage": 4.88},
        ],
        "total_issues": 287,
        "excluded_missing_issue_code": 12,
    }]


def _mock_get_geography(params: dict, scope: UserScope) -> list[dict]:
    """Mock FeedbackAnalyticsService.geography() — format thật."""
    return [{
        "provinces": [
            {"label": "TP Hồ Chí Minh", "issue_count": 68, "percentage": 23.69},
            {"label": "Hà Nội", "issue_count": 55, "percentage": 19.16},
            {"label": "Đà Nẵng", "issue_count": 38, "percentage": 13.24},
            {"label": "Hải Phòng", "issue_count": 25, "percentage": 8.71},
            {"label": "Cần Thơ", "issue_count": 22, "percentage": 7.67},
        ],
        "districts": [
            {"label": "Quận 1", "issue_count": 15, "percentage": 5.23},
            {"label": "Quận Bình Thạnh", "issue_count": 12, "percentage": 4.18},
        ],
        "total_issues": 287,
        "missing_province_count": 35,
        "missing_district_count": 120,
        "top_province": {"label": "TP Hồ Chí Minh", "issue_count": 68, "percentage": 23.69},
        "top_district": {"label": "Quận 1", "issue_count": 15, "percentage": 5.23},
    }]


def _mock_get_products(params: dict, scope: UserScope) -> list[dict]:
    """Mock FeedbackAnalyticsService.products() — format thật."""
    return [{
        "items": [
            {"label": "Đèn LED AT04.S 9W", "issue_count": 45, "percentage": 15.68},
            {"label": "Búp trụ RLT02", "issue_count": 28, "percentage": 9.76},
            {"label": "Đèn panel RDP01", "issue_count": 22, "percentage": 7.67},
            {"label": "Đèn năng lượng NL-01", "issue_count": 18, "percentage": 6.27},
            {"label": "Ống nhựa PPR D20", "issue_count": 15, "percentage": 5.23},
        ],
        "membership_count": 237,
        "total_issues": 287,
        "excluded_missing_issue_code": 12,
    }]


def _mock_get_groups(params: dict, scope: UserScope) -> list[dict]:
    """Mock FeedbackAnalyticsService.groups() — format thật."""
    return [{
        "items": [
            {"label": "Sản phẩm", "issue_count": 187, "percentage": 65.16},
            {"label": "Dịch vụ", "issue_count": 43, "percentage": 14.98},
            {"label": "Yêu cầu công cụ BH", "issue_count": 22, "percentage": 7.67},
            {"label": "Giá, cơ chế RD", "issue_count": 18, "percentage": 6.27},
            {"label": "Đối thủ cạnh tranh", "issue_count": 15, "percentage": 5.23},
        ],
        "membership_count": 287,
        "total_issues": 287,
        "excluded_missing_issue_code": 12,
    }]


def _mock_get_status_backlog(params: dict, scope: UserScope) -> list[dict]:
    """Mock FeedbackAnalyticsService.status_backlog() — format thật."""
    return [{
        "statuses": [
            {"label": "Đã xử lý", "issue_count": 215, "percentage": 74.91},
            {"label": "Chờ xử lý", "issue_count": 48, "percentage": 16.72},
            {"label": "Đang xử lý", "issue_count": 24, "percentage": 8.36},
        ],
        "processed_count": 215,
        "backlog_count": 72,
        "backlog_rate": 25.09,
        "age_as_of": "2026-08-31",
        "age_buckets": [
            {"label": "0–2 ngày", "issue_count": 18},
            {"label": "3–7 ngày", "issue_count": 22},
            {"label": "8–30 ngày", "issue_count": 25},
            {"label": "31+ ngày", "issue_count": 5},
            {"label": "Thiếu ngày", "issue_count": 2},
        ],
        "total_issues": 287,
    }]


def _mock_get_issues(params: dict, scope: UserScope) -> list[dict]:
    """Mock FeedbackAnalyticsService.issues() — format thật (paginated records)."""
    page = params.get("page", 1)
    page_size = params.get("page_size", 10)
    records = [
        {
            "feedback_id": 10001 + i,
            "issue_code": f"FB-2026-{10001 + i}",
            "content": content,
            "issue_date": f"2026-08-{15 + i % 15:02d}",
            "source": src,
            "unit_name": unit,
            "business_status": status,
            "product": prod,
            "product_line": pline,
            "sentiment": sent,
            "labels": labels,
            "raw_data_json": "{}",
        }
        for i, (content, src, unit, status, prod, pline, sent, labels) in enumerate([
            ("Bóng đèn LED AT04 cháy sau 2 tháng sử dụng", "Hotline", "CN Miền Bắc", "Đã xử lý", "Đèn LED AT04.S 9W", "Đèn LED", "Tiêu cực", [{"label": "Báo lỗi", "major_group": "Sản phẩm"}]),
            ("Đèn panel RDP01 chất lượng rất tốt, sáng đều", "Zalo", "CN Miền Nam", "Đã xử lý", "Đèn panel RDP01", "Đèn Panel", "Tích cực", [{"label": "Báo CL tốt", "major_group": "Sản phẩm"}]),
            ("Ống nhựa PPR bị rò rỉ tại mối nối", "Nhân viên KD", "CN Đà Nẵng", "Chờ xử lý", "Ống nhựa PPR D20", "Ống nhựa", "Tiêu cực", [{"label": "Báo lỗi", "major_group": "Sản phẩm"}]),
            ("Đèn năng lượng mặt trời NL-01 pin yếu sau 6 tháng", "DMS", "CN Miền Nam", "Đang xử lý", "Đèn năng lượng NL-01", "Đèn năng lượng mặt trời", "Tiêu cực", [{"label": "Báo lỗi", "major_group": "Sản phẩm"}]),
            ("Búp trụ RLT02 màu ánh sáng không đồng nhất", "Hotline", "CN Hà Nội", "Chờ xử lý", "Búp trụ RLT02", "Đèn LED", "Tiêu cực", [{"label": "Y/c cải tiến", "major_group": "Sản phẩm"}]),
            ("Hỏi chiết khấu cho đại lý cấp 2 Đà Nẵng", "Zalo", "CN Đà Nẵng", "Đã xử lý", None, None, "Trung lập", [{"label": "Khác", "major_group": "Yêu cầu công cụ BH"}]),
            ("Đèn LED AT04 dòng mới cải thiện nhiều", "Facebook", "CN Miền Nam", "Đã xử lý", "Đèn LED AT04.S 9W", "Đèn LED", "Tích cực", [{"label": "Báo CL tốt", "major_group": "Sản phẩm"}]),
            ("Yêu cầu bảo hành đèn panel lắp trần thạch cao", "DMS", "CN Miền Bắc", "Đang xử lý", "Đèn panel RDP01", "Đèn Panel", "Trung lập", [{"label": "Bảo hành", "major_group": "Dịch vụ"}]),
        ])
    ]
    # Apply scope filter
    if not scope.is_admin and scope.unit_ids:
        records = [r for r in records if r["unit_name"] in scope.unit_ids]
    start = (page - 1) * page_size
    page_items = records[start:start + page_size]
    return [{
        "items": page_items,
        "total": len(records),
        "page": page,
        "page_size": page_size,
        "total_pages": max(1, (len(records) + page_size - 1) // page_size),
    }]


def _mock_fts5_search(fts_query: str, fts_filters: dict, fts_limit: int, scope: UserScope) -> list[dict]:
    """Mock FTS5 search — trả records giống get_issues nhưng filtered by text match."""
    all_records = _mock_get_issues({}, scope)[0]["items"]
    q = fts_query.lower()
    matched = [r for r in all_records if q in r["content"].lower()]
    if not matched:
        matched = all_records[:2]  # Fallback: trả vài record gần đúng
    return matched[:fts_limit]


def _mock_json_extract(json_keys: list[str], json_filters: dict, scope: UserScope) -> list[dict]:
    """Mock json_extract — trả raw_data fields từ 1 record."""
    # Giả lập dữ liệu raw_data_json đã parse
    raw = {
        "Mã vấn đề": "FB-2026-10001",
        "Ngày ghi nhận": "2026-08-15",
        "Nguồn": "Hotline",
        "Tên đơn vị": "CN Miền Bắc",
        "Trạng thái": "Đã xử lý",
        "Tỉnh/TP": "Hà Nội",
        "Quận/huyện": "Hoàng Mai",
        "Tên khách hàng": "Nguyễn Văn A",
        "Số điện thoại": "0912345678",
        "Nội dung phản hồi": "Bóng đèn LED AT04 cháy sau 2 tháng sử dụng",
        "Loại vấn đề": "Chất lượng sản phẩm",
        "Hình thức xử lý": "Đổi mới sản phẩm",
    }
    if json_keys:
        return [{k: raw.get(k) for k in json_keys}]
    return [raw]


# ═══════════════════════════════════════════════════════════════════
# FUNCTION MAP
# ═══════════════════════════════════════════════════════════════════

_FUNCTION_MAP: dict[str, Any] = {
    "get_dashboard": lambda p, s: _mock_get_overview(p, s),   # Simplified: return overview
    "get_overview": _mock_get_overview,
    "get_comparison": lambda p, s: [{"period": p.get("period", "month"), "metrics": {}, "note": "mock"}],
    "get_daily_trend": _mock_get_daily_trend,
    "get_issue_types": _mock_get_issue_types,
    "get_sources": _mock_get_sources,
    "get_units": _mock_get_units,
    "get_groups": _mock_get_groups,
    "get_products": _mock_get_products,
    "get_geography": _mock_get_geography,
    "get_status_backlog": _mock_get_status_backlog,
    "get_issues": _mock_get_issues,
    "get_priority_issues": lambda p, s: _mock_get_issues({"page_size": p.get("limit", 10)}, s),
    "get_duplicates": lambda p, s: [{"groups": [], "total": 0, "note": "mock"}],
    "get_unit_issue_type_matrix": lambda p, s: [{"matrix": [], "note": "mock"}],
    "get_data_quality": lambda p, s: [{"quality_score": 87.5, "note": "mock"}],
}


# ═══════════════════════════════════════════════════════════════════
# MOCK EXECUTOR
# ═══════════════════════════════════════════════════════════════════


class MockQueryExecutor:
    """Drop-in mock cho SecureQueryExecutor (Dev A).

    CÙNG SIGNATURE: execute(plan: QueryPlan, scope: UserScope) -> QueryResult
    Trả dữ liệu giả đúng format thật của FeedbackAnalyticsService.

    Dùng trong Sprint 1-2 cho Dev B test toàn bộ pipeline AI mà không cần DB.
    """

    def execute(self, plan: QueryPlan, scope: UserScope) -> QueryResult:
        """Execute QueryPlan trên mock data.

        Signature GIỐNG HỆT SecureQueryExecutor.execute().
        """
        start = time.monotonic()

        # Validate plan
        errors = plan.validate()
        if errors:
            return QueryResult.error(f"Invalid plan: {'; '.join(errors)}")

        try:
            data = self._dispatch(plan, scope)
        except Exception as exc:
            return QueryResult.error(f"Lỗi mock: {exc}")

        elapsed_ms = int((time.monotonic() - start) * 1000)

        return QueryResult(
            status=QueryStatus.OK if data else QueryStatus.NO_DATA,
            data=data,
            metadata=QueryResultMetadata(
                total_rows=len(data),
                query_time_ms=elapsed_ms,
                scope_applied=f"unit_ids={scope.unit_ids}" if not scope.is_admin else "admin",
                cache_hit=False,
                pattern_used=plan.pattern.value,
            ),
            error_message="Không tìm thấy dữ liệu phù hợp." if not data else None,
        )

    def _dispatch(self, plan: QueryPlan, scope: UserScope) -> list[dict[str, Any]]:
        """Route tới mock handler đúng pattern."""

        if plan.pattern == QueryPattern.SQL_TEMPLATE:
            fn = _FUNCTION_MAP.get(plan.function_name or "")
            if fn is None:
                raise ValueError(
                    f"Hàm '{plan.function_name}' không tồn tại. "
                    f"Có: {', '.join(FUNCTION_REGISTRY_SPEC)}"
                )
            return fn(plan.params, scope)

        if plan.pattern == QueryPattern.SEMANTIC_VIEW:
            # Mock: trả aggregation data giống analytics
            return _mock_get_products(plan.params, scope)

        if plan.pattern == QueryPattern.FTS5_SEARCH:
            return _mock_fts5_search(
                plan.fts_query or "", plan.fts_filters, plan.fts_limit, scope
            )

        if plan.pattern == QueryPattern.JSON_EXTRACT:
            return _mock_json_extract(plan.json_keys, plan.json_filters, scope)

        raise ValueError(f"Pattern không hợp lệ: {plan.pattern}")
