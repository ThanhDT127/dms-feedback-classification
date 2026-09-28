"""Lớp phủ phía Dev B lên ``FUNCTION_REGISTRY_SPEC`` (design b02 D2).

``returns`` trong contract v1.0 lệch dữ liệu thật (review C03), nên catalog mô tả lại shape
theo ``analytics/service.py`` và tham số theo ``chat/db/function_registry.py`` (nhánh
``anthanh``, commit a523836). Đổi contract thì test bắt lệch trong
``tests/chat/test_function_catalog.py`` sẽ đỏ.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum

BASE_FILTER_PARAMS: frozenset[str] = frozenset(
    {"date_from", "date_to", "province", "district", "unit_name"}
)
COMPARE_PARAMS: frozenset[str] = frozenset({"compare_from", "compare_to"})

# Tham số FunctionRegistry thật sự đọc: mọi hàm dựng AnalyticsFilter từ 7 khoá lọc,
# cộng tham số riêng của từng handler.
_REGISTRY_EXTRAS: dict[str, frozenset[str]] = {
    "get_dashboard": frozenset(),
    "get_overview": frozenset(),
    "get_comparison": frozenset({"period"}),
    "get_daily_trend": frozenset(),
    "get_issue_types": frozenset(),
    "get_sources": frozenset(),
    "get_units": frozenset(),
    "get_groups": frozenset(),
    "get_products": frozenset(),
    "get_geography": frozenset(),
    "get_status_backlog": frozenset(),
    "get_issues": frozenset({"page", "page_size", "source", "label", "product", "business_status"}),
    "get_priority_issues": frozenset({"limit"}),
    "get_duplicates": frozenset({"page", "page_size"}),
    "get_unit_issue_type_matrix": frozenset(),
    "get_data_quality": frozenset(),
}
REGISTRY_READABLE_PARAMS: dict[str, frozenset[str]] = {
    name: BASE_FILTER_PARAMS | COMPARE_PARAMS | extras for name, extras in _REGISTRY_EXTRAS.items()
}

# Tên bộ lọc tiếng Việt cho thông báo người dùng (Plan Guard, b03).
PARAM_LABELS_VI: dict[str, str] = {
    "date_from": "từ ngày",
    "date_to": "đến ngày",
    "compare_from": "kỳ so sánh từ ngày",
    "compare_to": "kỳ so sánh đến ngày",
    "province": "tỉnh/thành",
    "district": "quận/huyện",
    "unit_name": "đơn vị",
    "source": "nguồn",
    "label": "nhãn",
    "product": "sản phẩm",
    "business_status": "trạng thái xử lý",
    "sentiment": "cảm xúc",
    "period": "kỳ so sánh",
    "page": "trang",
    "page_size": "số dòng mỗi trang",
    "limit": "số lượng",
}


# Nhãn tiếng Việt của từng KPI trong kết quả overview/comparison (design b05 D3).
KPI_LABELS_VI: dict[str, str] = {
    "total_issues": "Tổng số vấn đề",
    "processed_issues": "Đã xử lý",
    "label_coverage": "Tỉ lệ có nhãn",
    "sentiment_coverage": "Tỉ lệ có cảm xúc",
    "product_coverage": "Tỉ lệ có sản phẩm",
    "multi_label_rate": "Tỉ lệ nhiều nhãn",
    "duplicate_record_rate": "Tỉ lệ bản ghi trùng",
    "duplicate_issue_rate": "Tỉ lệ vấn đề trùng",
    "model_accuracy": "Độ chính xác mô hình",
    "backlog_rate": "Tỉ lệ tồn đọng",
    "processed_count": "Số đã xử lý",
    "backlog_count": "Số tồn đọng",
    "total_records": "Tổng bản ghi",
}

# KPI mang đơn vị phần trăm. ``get_comparison`` trộn số đếm và tỉ lệ trong cùng một cột nên
# không thể khai báo format cho cả cột; bảng tra danh sách này để quyết định theo từng dòng.
KPI_RATE_KEYS: frozenset[str] = frozenset(
    {
        "label_coverage",
        "sentiment_coverage",
        "product_coverage",
        "multi_label_rate",
        "duplicate_record_rate",
        "duplicate_issue_rate",
        "model_accuracy",
        "backlog_rate",
    }
)


@dataclass(frozen=True)
class BlockSpec:
    """Khai báo cách dựng ``data_block`` từ kết quả của một hàm (design b05 D3).

    Nhờ khai báo ở đây mà ``block_builders`` không phải rẽ nhánh theo tên hàm.
    """

    kind: str  # kpi | ranking | timeseries | quote | table
    title: str = ""  # tiêu đề hiển thị cho người dùng; không dùng summary_vi (dành cho prompt)
    chart_hint: str = "none"  # bar | line | donut | none
    # ── kind = kpi ──
    kpi_keys: tuple[str, ...] = ()
    # ── kind = ranking | quote | table ──
    items_path: str = "items"
    label_field: str = "label"
    value_field: str = "issue_count"
    percent_field: str = "percentage"
    total_path: str = "total_issues"
    # ── kind = timeseries ──
    date_field: str = "date"
    granularity: str = "day"
    aggregate_allowed: bool = True
    # ── kind = table ──
    columns: tuple[tuple[str, str, str], ...] = ()  # (key, header_vi, format)


class ResultKind(StrEnum):
    KPI = "kpi"
    DISTRIBUTION = "distribution"
    TIMESERIES = "timeseries"
    RECORDS = "records"
    MATRIX = "matrix"


@dataclass(frozen=True)
class FunctionInfo:
    name: str
    summary_vi: str
    supported_params: frozenset[str]
    returns_shape: str
    result_kind: ResultKind
    pattern: str = "sql_template"
    block_spec: BlockSpec | None = None
    # Thiếu tham số này thì hàm ném lỗi; Plan Guard hỏi lại thay vì để Executor sập.
    required_params: frozenset[str] = frozenset()


_DISTRIBUTION = (
    "{items[{label, issue_count, percentage}], total_issues, excluded_missing_issue_code}"
)

FUNCTION_CATALOG: dict[str, FunctionInfo] = {
    info.name: info
    for info in (
        FunctionInfo(
            "get_dashboard",
            "Toàn bộ dashboard gồm mọi panel",
            BASE_FILTER_PARAMS,
            "{overview, dailyTrend, issueTypes, geography, sources, units, groups, products, status}",
            ResultKind.KPI,
        ),
        FunctionInfo(
            "get_overview",
            "KPI tổng quan; có compare_from/compare_to thì kèm vế so sánh",
            BASE_FILTER_PARAMS | COMPARE_PARAMS,
            "{total_issues, processed_issues, label_coverage, sentiment_coverage, "
            "product_coverage, multi_label_rate, duplicate_record_rate, duplicate_issue_rate}; "
            "mỗi KPI có available, value, denominator",
            ResultKind.KPI,
        ),
        FunctionInfo(
            "get_comparison",
            "So sánh KPI với kỳ liền trước theo period = month | quarter | year",
            BASE_FILTER_PARAMS | {"period"},
            "{period, current_range, previous_range, metrics{<kpi>: {current, previous, change, "
            "change_percent, available}}}",
            ResultKind.KPI,
            required_params=frozenset({"date_from", "date_to"}),
        ),
        FunctionInfo(
            "get_daily_trend",
            "Số vấn đề theo từng ngày, kèm cảm xúc",
            BASE_FILTER_PARAMS,
            "{items[{date, issue_count, sentiment_counts}], total_issues}; đếm riêng từng ngày, "
            "không cộng dồn",
            ResultKind.TIMESERIES,
        ),
        FunctionInfo(
            "get_issue_types",
            "Phân bổ theo loại vấn đề",
            BASE_FILTER_PARAMS,
            _DISTRIBUTION,
            ResultKind.DISTRIBUTION,
        ),
        FunctionInfo(
            "get_sources",
            "Phân bổ theo nguồn phản hồi",
            BASE_FILTER_PARAMS,
            _DISTRIBUTION,
            ResultKind.DISTRIBUTION,
        ),
        FunctionInfo(
            "get_units",
            "Phân bổ, xếp hạng theo đơn vị",
            BASE_FILTER_PARAMS,
            _DISTRIBUTION,
            ResultKind.DISTRIBUTION,
        ),
        FunctionInfo(
            "get_groups",
            "Phân bổ theo nhóm nhãn chính",
            BASE_FILTER_PARAMS,
            "{items[{label, issue_count, percentage, sentiment_counts}], membership_count, "
            "total_issues}",
            ResultKind.DISTRIBUTION,
        ),
        FunctionInfo(
            "get_products",
            "Phân bổ, xếp hạng theo sản phẩm",
            BASE_FILTER_PARAMS,
            "{items[{label, issue_count, percentage, major_groups, quality_labels, labels}], "
            "membership_count, total_issues, excluded_missing_issue_code}",
            ResultKind.DISTRIBUTION,
        ),
        FunctionInfo(
            "get_geography",
            "Phân bổ theo tỉnh/thành và quận/huyện",
            BASE_FILTER_PARAMS,
            "{provinces[{label, issue_count, percentage}], districts[...], total_issues, "
            "top_province, top_district}",
            ResultKind.DISTRIBUTION,
        ),
        FunctionInfo(
            "get_status_backlog",
            "Trạng thái xử lý, tồn đọng, tuổi tồn",
            BASE_FILTER_PARAMS,
            "{statuses[{label, issue_count, percentage}], processed_count, backlog_count, "
            "backlog_rate, age_buckets[], total_issues}",
            ResultKind.KPI,
        ),
        FunctionInfo(
            "get_issues",
            "Danh sách phản hồi chi tiết, có phân trang",
            BASE_FILTER_PARAMS
            | {"source", "label", "product", "business_status", "page", "page_size"},
            "{items[{feedback_id, issue_code, issue_date, source, unit_name, business_status, "
            "content, product, sentiment, labels, source_file_name, source_row_number}], total, "
            "page, page_size, total_pages}",
            ResultKind.RECORDS,
        ),
        FunctionInfo(
            "get_priority_issues",
            "Vấn đề cần ưu tiên (tiêu cực, chưa xử lý, quá hạn)",
            BASE_FILTER_PARAMS | {"limit"},
            "{items[{issue_code, issue, department, product_group, sentiment, status, "
            "is_overdue, issue_date, summary}], total}",
            ResultKind.RECORDS,
        ),
        FunctionInfo(
            "get_duplicates",
            "Nhóm phản hồi trùng lặp, có phân trang",
            BASE_FILTER_PARAMS | {"page", "page_size"},
            "Các nhóm phản hồi trùng lặp kèm số bản ghi mỗi nhóm",
            ResultKind.RECORDS,
        ),
        FunctionInfo(
            "get_unit_issue_type_matrix",
            "Ma trận đơn vị × loại vấn đề",
            BASE_FILTER_PARAMS,
            "{units[], issue_types[], rows[{unit, total, counts{<loại>: số}}], column_totals, "
            "grand_total, top_unit, top_issue_type}",
            ResultKind.MATRIX,
        ),
        FunctionInfo(
            "get_data_quality",
            "Chất lượng dữ liệu: tỉ lệ thiếu trường",
            BASE_FILTER_PARAMS,
            "{total_records, fields{...}, invalid_issue_dates, missing_issue_dates}",
            ResultKind.KPI,
        ),
    )
}


def render_function_line(info: FunctionInfo) -> str:
    params = ", ".join(sorted(info.supported_params))
    return f"- {info.name}({params}): {info.summary_vi}. Trả về: {info.returns_shape}"


# ═══════════════════════════════════════════════════════════════════
# BLOCK SPEC — cách dựng data_block cho từng hàm (design b05 D3)
# ═══════════════════════════════════════════════════════════════════

_OVERVIEW_KPIS: tuple[str, ...] = (
    "total_issues",
    "processed_issues",
    "label_coverage",
    "sentiment_coverage",
    "product_coverage",
    "multi_label_rate",
    "duplicate_record_rate",
    "duplicate_issue_rate",
    "model_accuracy",
)

BLOCK_SPECS: dict[str, BlockSpec] = {
    "get_overview": BlockSpec(kind="kpi", title="Tổng quan", kpi_keys=_OVERVIEW_KPIS),
    "get_comparison": BlockSpec(
        kind="table",
        title="So sánh với kỳ trước",
        items_path="metrics",
        columns=(
            ("label", "Chỉ tiêu", "text"),
            ("current", "Kỳ này", "metric"),
            ("previous", "Kỳ trước", "metric"),
            ("change_percent", "Thay đổi", "pct"),
        ),
    ),
    "get_daily_trend": BlockSpec(
        kind="timeseries",
        title="Số vấn đề theo ngày",
        chart_hint="line",
        granularity="day",
        # daily_trend đếm distinct theo từng ngày nên tổng các điểm không có nghĩa.
        aggregate_allowed=False,
    ),
    "get_issue_types": BlockSpec(
        kind="ranking", title="Phân bổ theo loại vấn đề", chart_hint="donut"
    ),
    "get_sources": BlockSpec(kind="ranking", title="Phân bổ theo nguồn", chart_hint="donut"),
    "get_units": BlockSpec(kind="ranking", title="Xếp hạng đơn vị", chart_hint="bar"),
    "get_groups": BlockSpec(kind="ranking", title="Phân bổ theo nhóm nhãn", chart_hint="donut"),
    "get_products": BlockSpec(kind="ranking", title="Xếp hạng sản phẩm", chart_hint="bar"),
    "get_geography": BlockSpec(
        kind="ranking", title="Phân bổ theo tỉnh/thành", chart_hint="bar", items_path="provinces"
    ),
    "get_status_backlog": BlockSpec(
        kind="ranking", title="Tình trạng xử lý", chart_hint="donut", items_path="statuses"
    ),
    "get_issues": BlockSpec(kind="quote", title="Phản hồi tiêu biểu", total_path="total"),
    "get_priority_issues": BlockSpec(kind="quote", title="Vấn đề cần ưu tiên", total_path="total"),
    "get_unit_issue_type_matrix": BlockSpec(
        kind="table",
        title="Đơn vị × loại vấn đề",
        items_path="rows",
        columns=(("unit", "Đơn vị", "text"), ("total", "Tổng số vấn đề", "int")),
    ),
}

# Gắn block_spec vào catalog ở một chỗ, thay vì lặp lại ở 16 khai báo hàm.
FUNCTION_CATALOG = {
    name: replace(info, block_spec=BLOCK_SPECS.get(name)) for name, info in FUNCTION_CATALOG.items()
}
