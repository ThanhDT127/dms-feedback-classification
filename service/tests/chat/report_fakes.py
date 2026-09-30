"""Fake dùng chung cho test báo cáo (b10): executor trả dữ liệu theo từng hàm."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from dms.chat.contract import QueryPlan, QueryResult, UserScope

TV1 = "Truyền thống Vùng 1"
TV2 = "Truyền thống Vùng 2"


def overview_row(total: int = 1230, previous: int = 1000, change: float = 23.0) -> dict:
    def metric(value, available=True, denominator=None):
        return {"available": available, "value": value, "denominator": denominator}

    row = {
        "total_issues": {
            **metric(total),
            "comparison": {
                "available": True,
                "value": previous,
                "change_percent": change,
                "direction": "up" if change > 0 else "down",
            },
        },
        "processed_issues": metric(900, denominator=total),
        "label_coverage": metric(88.5),
    }
    return row


PRODUCTS_ROW = {
    "items": [
        {"label": "Đèn LED Bulb", "issue_count": 420, "percentage": 34.1},
        {"label": "Đèn LED Tube", "issue_count": 210, "percentage": 17.0},
    ],
    "total_issues": 1230,
}
GEOGRAPHY_ROW = {
    "provinces": [
        {"label": "Hà Nội", "issue_count": 300, "percentage": 24.3},
        {"label": "Hồ Chí Minh", "issue_count": 280, "percentage": 22.7},
    ],
    "districts": [],
    "total_issues": 1230,
}
UNITS_ROW = {
    "items": [
        {"label": TV1, "issue_count": 700, "percentage": 56.9},
        {"label": TV2, "issue_count": 530, "percentage": 43.1},
    ],
    "total_issues": 1230,
}
TREND_ROW = {
    "items": [
        {"date": "2026-09-07", "issue_count": 150},
        {"date": "2026-09-08", "issue_count": 210},
        {"date": "2026-09-09", "issue_count": 180},
    ],
    "total_issues": 540,
}
PRIORITY_ROW = {
    "items": [
        {
            "issue_code": "NT-0106",
            "summary": "Đèn cháy sau 2 tuần",
            "department": TV1,
            "issue_date": "2026-09-09",
            "sentiment": "Tiêu cực",
            "status": "Chờ xử lý",
        }
    ],
    "total": 1,
}
ISSUE_TYPES_ROW = {
    "items": [{"label": "Chất lượng", "issue_count": 500, "percentage": 40.7}],
    "total_issues": 1230,
}
BACKLOG_ROW = {
    "statuses": [
        {"label": "Đã xử lý", "issue_count": 900, "percentage": 73.2},
        {"label": "Chờ xử lý", "issue_count": 330, "percentage": 26.8},
    ],
    "processed_count": 900,
    "backlog_count": 330,
    "backlog_rate": 26.8,
    "total_issues": 1230,
}
ISSUES_ROW = {
    "items": [
        {
            "feedback_id": 1,
            "issue_code": "NT-0106",
            "content": "Đèn cháy sau 2 tuần",
            "unit_name": TV1,
            "issue_date": "2026-09-09",
            "product": "Đèn LED Bulb",
        }
    ],
    "total": 1,
    "page": 1,
    "page_size": 50,
    "total_pages": 1,
}

DEFAULT_ROWS: dict[str, dict] = {
    "get_overview": overview_row(),
    "get_products": PRODUCTS_ROW,
    "get_geography": GEOGRAPHY_ROW,
    "get_units": UNITS_ROW,
    "get_daily_trend": TREND_ROW,
    "get_priority_issues": PRIORITY_ROW,
    "get_issue_types": ISSUE_TYPES_ROW,
    "get_status_backlog": BACKLOG_ROW,
    "get_issues": ISSUES_ROW,
}


class ReportFakeExecutor:
    """Trả dữ liệu mẫu theo tên hàm; ``failures``/``no_data`` để dựng phần lỗi, phần rỗng."""

    def __init__(
        self,
        rows: Mapping[str, dict] | None = None,
        *,
        failures: Sequence[str] = (),
        no_data: Sequence[str] = (),
        delays: Mapping[str, float] | None = None,
    ) -> None:
        self.rows = dict(rows or DEFAULT_ROWS)
        self.failures = set(failures)
        self.no_data = set(no_data)
        self.delays = dict(delays or {})
        self.calls: list[tuple[QueryPlan, UserScope]] = []

    def execute(self, plan: QueryPlan, scope: UserScope) -> QueryResult:
        import time

        self.calls.append((plan, scope))
        name = str(plan.function_name or "")
        delay = self.delays.get(name)
        if delay:
            time.sleep(delay)
        if name in self.failures:
            return QueryResult.error("Lỗi thực thi giả lập")
        if name in self.no_data or name not in self.rows:
            return QueryResult.no_data()
        return QueryResult.ok([dict(self.rows[name])], total_rows=1)
