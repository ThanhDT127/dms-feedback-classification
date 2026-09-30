"""Taxonomy intent của chatbot — nguồn chuẩn (spec ``chat-intent-taxonomy``, design b02 D1).

Planner (prompt), Plan Guard (b03) và bộ đánh giá đều đọc bảng này, nên ánh xạ intent → hàm
chỉ khai báo ở một nơi.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum


class Intent(StrEnum):
    OVERVIEW = "OVERVIEW"
    TREND = "TREND"
    COMPARISON = "COMPARISON"
    DRILL_PRODUCT = "DRILL_PRODUCT"
    DRILL_UNIT = "DRILL_UNIT"
    DRILL_GEOGRAPHY = "DRILL_GEOGRAPHY"
    DRILL_ISSUE = "DRILL_ISSUE"
    LOOKUP_FEEDBACK = "LOOKUP_FEEDBACK"
    LOOKUP_FILE = "LOOKUP_FILE"
    LOOKUP_SIMILAR = "LOOKUP_SIMILAR"
    REPORT_DAILY = "REPORT_DAILY"
    REPORT_WEEKLY = "REPORT_WEEKLY"
    REPORT_CUSTOM = "REPORT_CUSTOM"
    REPORT_EXPORT = "REPORT_EXPORT"
    HELP = "HELP"


class PlannerState(StrEnum):
    """Trạng thái hệ thống do Planner quyết định. UNAUTHORIZED_SCOPE chỉ do Plan Guard quyết định."""

    CLARIFY = "CLARIFY"
    OUT_OF_DOMAIN = "OUT_OF_DOMAIN"


MILESTONES: tuple[str, ...] = ("M1", "M2", "M3", "M4", "M5")

# Bước lấy mẫu minh hoạ cho câu trả lời tường thuật được phép với mọi intent có plan.
SAMPLE_FUNCTION = "get_issues"


def milestone_index(milestone: str) -> int:
    return MILESTONES.index(milestone.strip().upper())


@dataclass(frozen=True)
class IntentSpec:
    intent: Intent
    group: str
    description_vi: str
    disambiguation_vi: str
    examples: tuple[str, ...]
    allowed_functions: Mapping[str, str] = field(default_factory=dict)  # hàm → mốc bật
    needs_plan: bool = True
    fts_enabled_at: str | None = None  # mốc cho phép bước fts5_search (b08)
    semantic_enabled_at: str | None = None  # mốc cho phép bước semantic_view (b09)
    # Mốc bật chế độ báo cáo: bước do template Python dựng, Planner không trả bước (b10 D1).
    report_enabled_at: str | None = None


# Hàm của template báo cáo (b10 D2); dùng chung cho cả kiểm tham số của Plan Guard.
_DAILY_REPORT_FUNCTIONS: dict[str, str] = {
    "get_overview": "M5",
    "get_priority_issues": "M5",
    "get_issue_types": "M5",
    "get_status_backlog": "M5",
}
_PERIOD_REPORT_FUNCTIONS: dict[str, str] = {
    "get_overview": "M5",
    "get_daily_trend": "M5",
    "get_products": "M5",
    "get_geography": "M5",
    "get_units": "M5",
    "get_priority_issues": "M5",
}
_EXPORT_FUNCTIONS: dict[str, str] = {
    **_DAILY_REPORT_FUNCTIONS,
    **_PERIOD_REPORT_FUNCTIONS,
    "get_issues": "M5",
}

INTENT_SPECS: dict[Intent, IntentSpec] = {
    spec.intent: spec
    for spec in (
        IntentSpec(
            Intent.OVERVIEW,
            "Tổng quan",
            "Số liệu chung (tổng số vấn đề, tỉ lệ đã xử lý, KPI) của một kỳ hoặc đơn vị.",
            "Không có từ 'báo cáo'; hỏi KPI chung, không phân theo chiều nào.",
            ("Quý 2 có tổng cộng bao nhiêu vấn đề?",),
            {"get_overview": "M1"},
            semantic_enabled_at="M4",
        ),
        IntentSpec(
            Intent.TREND,
            "Tổng quan",
            "Diễn biến theo thời gian (theo ngày), tăng hay giảm dần.",
            "Có ý 'xu hướng', 'theo từng ngày', 'diễn biến'; 'báo cáo tuần' là REPORT_WEEKLY.",
            ("Phản hồi biến động theo ngày thế nào?",),
            {"get_daily_trend": "M1"},
            semantic_enabled_at="M4",
        ),
        IntentSpec(
            Intent.COMPARISON,
            "Tổng quan",
            "So sánh hai kỳ, hoặc hai đơn vị/đối tượng với nhau.",
            "Có 'so sánh', 'so với', 'A vs B', 'cùng kỳ'.",
            ("Quý này so với quý trước ra sao?",),
            {"get_overview": "M1", "get_comparison": "M1"},
            semantic_enabled_at="M4",
        ),
        IntentSpec(
            Intent.DRILL_PRODUCT,
            "Drill-down",
            "Phân bổ hoặc xếp hạng theo sản phẩm.",
            "Chiều chính là sản phẩm/mặt hàng.",
            ("Mặt hàng nào bị phản hồi nhiều?",),
            {"get_products": "M1"},
            semantic_enabled_at="M4",
        ),
        IntentSpec(
            Intent.DRILL_UNIT,
            "Drill-down",
            "Phân bổ, xếp hạng theo đơn vị/chi nhánh; ma trận đơn vị × loại vấn đề.",
            "Chiều chính là đơn vị/chi nhánh/vùng.",
            ("Chi nhánh nào đứng đầu về phản hồi?",),
            {"get_units": "M1", "get_unit_issue_type_matrix": "M1"},
            semantic_enabled_at="M4",
        ),
        IntentSpec(
            Intent.DRILL_GEOGRAPHY,
            "Drill-down",
            "Phân bổ theo tỉnh/thành, quận/huyện.",
            "Chiều chính là địa lý (tỉnh, thành, khu vực, quận, huyện).",
            ("Địa bàn nào có nhiều phản hồi?",),
            {"get_geography": "M1"},
            semantic_enabled_at="M4",
        ),
        IntentSpec(
            Intent.DRILL_ISSUE,
            "Drill-down",
            "Đếm/phân bổ theo loại vấn đề, nhóm nhãn, trạng thái xử lý, tồn đọng, vấn đề ưu tiên.",
            "Hỏi 'bao nhiêu', 'loại nào nhiều' theo vấn đề; muốn đọc nội dung phản hồi là "
            "LOOKUP_FEEDBACK; hỏi nghĩa của nhãn là HELP.",
            ("Nhóm vấn đề nào chiếm nhiều nhất?",),
            {
                "get_issue_types": "M1",
                "get_groups": "M1",
                "get_status_backlog": "M1",
                "get_priority_issues": "M1",
            },
            semantic_enabled_at="M4",
        ),
        IntentSpec(
            Intent.LOOKUP_FEEDBACK,
            "Tra cứu",
            "Xem, liệt kê nội dung các phản hồi cụ thể theo bộ lọc.",
            "Có 'xem', 'liệt kê', 'cho tôi các phản hồi'; tìm phản hồi giống một phản hồi khác là "
            "LOOKUP_SIMILAR.",
            ("Cho xem phản hồi của khách về bóng đèn.",),
            {"get_issues": "M1"},
            fts_enabled_at="M3",
        ),
        IntentSpec(
            Intent.LOOKUP_FILE,
            "Tra cứu",
            "Hỏi phản hồi nằm ở file nguồn/dòng nào, hoặc các phản hồi trong một file.",
            "Có 'file', 'dòng', 'tệp nguồn'.",
            ("Phản hồi đó lấy từ tệp nào?",),
            fts_enabled_at="M3",
        ),
        IntentSpec(
            Intent.LOOKUP_SIMILAR,
            "Tra cứu",
            "Tìm phản hồi giống hoặc tương tự một phản hồi/mã cho trước.",
            "Có 'giống', 'tương tự', 'na ná' kèm một phản hồi tham chiếu.",
            ("Có phản hồi nào na ná phản hồi kia?",),
            fts_enabled_at="M3",
        ),
        IntentSpec(
            Intent.REPORT_DAILY,
            "Báo cáo",
            "Báo cáo theo một ngày.",
            "Có từ 'báo cáo' kèm một ngày (hôm nay, hôm qua, ngày cụ thể); không có 'báo cáo' "
            "là OVERVIEW.",
            ("Báo cáo ngày cho tôi.",),
            _DAILY_REPORT_FUNCTIONS,
            report_enabled_at="M5",
        ),
        IntentSpec(
            Intent.REPORT_WEEKLY,
            "Báo cáo",
            "Báo cáo theo tuần.",
            "Có 'báo cáo tuần'; chỉ hỏi diễn biến theo ngày là TREND.",
            ("Báo cáo hằng tuần.",),
            _PERIOD_REPORT_FUNCTIONS,
            report_enabled_at="M5",
        ),
        IntentSpec(
            Intent.REPORT_CUSTOM,
            "Báo cáo",
            "Báo cáo cho khoảng thời gian tuỳ chọn (tháng, quý, từ ngày đến ngày).",
            "Có 'báo cáo' kèm tháng, quý hoặc khoảng ngày.",
            ("Báo cáo cho giai đoạn đầu năm.",),
            _PERIOD_REPORT_FUNCTIONS,
            report_enabled_at="M5",
        ),
        IntentSpec(
            Intent.REPORT_EXPORT,
            "Báo cáo",
            "Xuất hoặc tải file (Excel) báo cáo hay kết quả.",
            "Có 'xuất', 'tải', 'export', 'file Excel' thì là REPORT_EXPORT dù có từ 'báo cáo'.",
            ("Xuất file cho tôi.",),
            _EXPORT_FUNCTIONS,
            report_enabled_at="M5",
        ),
        IntentSpec(
            Intent.HELP,
            "Hệ thống",
            "Cách dùng trợ lý, hoặc định nghĩa một nhãn phân loại.",
            "Hỏi 'nghĩa là gì', 'dùng thế nào', 'hỏi được gì'.",
            ("Trợ lý hỗ trợ những gì?",),
            needs_plan=False,
        ),
    )
}


def spec_for(intent: Intent) -> IntentSpec:
    return INTENT_SPECS[intent]


def supported_functions(intent: Intent, milestone: str) -> tuple[str, ...]:
    rank = milestone_index(milestone)
    return tuple(
        name
        for name, enabled_at in INTENT_SPECS[intent].allowed_functions.items()
        if milestone_index(enabled_at) <= rank
    )


FTS_PATTERN = "fts5_search"


def supports_fts(intent: Intent, milestone: str, patterns: Iterable[str] | None = None) -> bool:
    """Intent được dùng bước ``fts5_search`` ở mốc này (và pattern đã bật nếu truyền vào)."""
    enabled_at = INTENT_SPECS[intent].fts_enabled_at
    if enabled_at is None or milestone_index(enabled_at) > milestone_index(milestone):
        return False
    return patterns is None or FTS_PATTERN in set(patterns)


SEMANTIC_PATTERN = "semantic_view"


def supports_semantic(
    intent: Intent, milestone: str, patterns: Iterable[str] | None = None
) -> bool:
    """Intent được dùng bước ``semantic_view`` (Pattern 2) ở mốc này và pattern đã bật."""
    enabled_at = INTENT_SPECS[intent].semantic_enabled_at
    if enabled_at is None or milestone_index(enabled_at) > milestone_index(milestone):
        return False
    return patterns is None or SEMANTIC_PATTERN in set(patterns)


REPORT_INTENTS: frozenset[Intent] = frozenset(
    {Intent.REPORT_DAILY, Intent.REPORT_WEEKLY, Intent.REPORT_CUSTOM, Intent.REPORT_EXPORT}
)


def supports_report(intent: Intent, milestone: str) -> bool:
    """Intent báo cáo đã bật ở mốc này; bước do template dựng nên Planner không trả bước."""
    enabled_at = INTENT_SPECS[intent].report_enabled_at
    return enabled_at is not None and milestone_index(enabled_at) <= milestone_index(milestone)


def is_supported(intent: Intent, milestone: str, patterns: Iterable[str] | None = None) -> bool:
    spec = INTENT_SPECS[intent]
    if not spec.needs_plan or supported_functions(intent, milestone):
        return True
    return patterns is not None and supports_fts(intent, milestone, patterns)


def functions_for_milestone(milestone: str) -> tuple[str, ...]:
    names: dict[str, None] = {}
    for intent in Intent:
        for name in supported_functions(intent, milestone):
            names.setdefault(name, None)
    return tuple(names)
