"""Template báo cáo (spec ``chat-report-composition``, design b10 D1, D2).

Báo cáo cùng loại luôn có cùng cấu trúc: các bước lấy từ bảng dưới đây chứ không từ Planner.
Module này thuần Python — không gọi LLM, không chạm executor.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from ...contract import AnswerShape, QueryPattern, QueryPlan
from ..date_resolver import last_full_week, previous_period, yesterday
from ..intents import Intent
from ..refusal_templates import render
from ..types import DateRange, Reason
from ..vi_format import format_date, format_date_range

# Khoảng dài hơn ngần này thì bỏ phần xu hướng (trend theo tuần/tháng chờ WP2 của Dev A).
TREND_MAX_DAYS = 92
# Khoảng ngắn hơn ngần này thì phần xu hướng không có gì để nói.
TREND_MIN_DAYS = 3

PRIORITY_TOP_N = 5
RANKING_TOP_N = 10

SECTION_OVERVIEW = "overview"
SECTION_TREND = "trend"
SECTION_PRODUCTS = "products"
SECTION_GEOGRAPHY = "geography"
SECTION_UNITS = "units"
SECTION_PRIORITY = "priority"
SECTION_ISSUE_TYPES = "issue_types"
SECTION_BACKLOG = "backlog"
SECTION_HIGHLIGHTS = "highlights"
SECTION_EXPORT = "export"

SECTION_TITLES_VI: dict[str, str] = {
    SECTION_OVERVIEW: "Tổng quan",
    SECTION_TREND: "Diễn biến theo ngày",
    SECTION_PRODUCTS: "Sản phẩm bị phản hồi nhiều",
    SECTION_GEOGRAPHY: "Phân bổ theo địa bàn",
    SECTION_UNITS: "Phân bổ theo đơn vị",
    SECTION_PRIORITY: "Vấn đề cần ưu tiên",
    SECTION_ISSUE_TYPES: "Loại vấn đề",
    SECTION_BACKLOG: "Tình trạng tồn đọng",
    SECTION_HIGHLIGHTS: "Điểm chính",
    SECTION_EXPORT: "File xuất",
}

# Gợi ý chiều dữ liệu của Normalizer/Planner → phần của template.
DIMENSION_TO_SECTION: dict[str, str] = {
    "product": SECTION_PRODUCTS,
    "products": SECTION_PRODUCTS,
    "unit": SECTION_UNITS,
    "unit_name": SECTION_UNITS,
    "units": SECTION_UNITS,
    "province": SECTION_GEOGRAPHY,
    "district": SECTION_GEOGRAPHY,
    "geography": SECTION_GEOGRAPHY,
    "label": SECTION_ISSUE_TYPES,
    "issue_types": SECTION_ISSUE_TYPES,
    "status": SECTION_BACKLOG,
    "backlog": SECTION_BACKLOG,
    "trend": SECTION_TREND,
    "priority": SECTION_PRIORITY,
    "overview": SECTION_OVERVIEW,
}


@dataclass(frozen=True)
class ReportRange:
    """Khoảng báo cáo và kỳ so sánh liền trước."""

    current: DateRange
    compare: DateRange | None = None

    @property
    def days(self) -> int:
        return (self.current.date_to - self.current.date_from).days + 1

    @property
    def date_from(self) -> date:
        return self.current.date_from

    @property
    def date_to(self) -> date:
        return self.current.date_to

    def params(self) -> dict[str, str]:
        return {
            "date_from": self.current.date_from.isoformat(),
            "date_to": self.current.date_to.isoformat(),
        }

    def compare_params(self) -> dict[str, str]:
        if self.compare is None:
            return {}
        return {
            "compare_from": self.compare.date_from.isoformat(),
            "compare_to": self.compare.date_to.isoformat(),
        }

    def label(self) -> str:
        return format_date_range(self.current.date_from, self.current.date_to)


@dataclass(frozen=True)
class ReportContext:
    """Thông tin để quyết định phần nào được hiện."""

    scope_units: tuple[str, ...] = ()
    is_admin: bool = False
    requested_sections: tuple[str, ...] = ()
    report_range: ReportRange | None = None

    @property
    def unit_count(self) -> int:
        return len(self.scope_units)


@dataclass(frozen=True)
class SectionSpec:
    section_id: str
    title_vi: str
    function_name: str
    params: Callable[[ReportRange], dict[str, Any]]
    show_if: Callable[[ReportContext], bool] = lambda context: True
    top_n: int | None = None


# ── Hàm dựng tham số ──


def _range_params(report_range: ReportRange) -> dict[str, Any]:
    return report_range.params()


def _range_with_compare(report_range: ReportRange) -> dict[str, Any]:
    return {**report_range.params(), **report_range.compare_params()}


def _priority_params(report_range: ReportRange) -> dict[str, Any]:
    return {**report_range.params(), "limit": PRIORITY_TOP_N}


# ── Điều kiện hiển thị ──


def _units_visible(context: ReportContext) -> bool:
    """Phần đơn vị chỉ có nghĩa khi phạm vi có từ 2 đơn vị, hoặc admin không lọc đơn vị."""
    return context.unit_count >= 2 or (context.is_admin and context.unit_count == 0)


def _trend_visible(context: ReportContext) -> bool:
    report_range = context.report_range
    if report_range is None:
        return True
    return TREND_MIN_DAYS <= report_range.days <= TREND_MAX_DAYS


# ── Template theo loại báo cáo ──

DAILY_SECTIONS: tuple[SectionSpec, ...] = (
    SectionSpec(
        SECTION_OVERVIEW, SECTION_TITLES_VI[SECTION_OVERVIEW], "get_overview", _range_with_compare
    ),
    SectionSpec(
        SECTION_PRIORITY,
        SECTION_TITLES_VI[SECTION_PRIORITY],
        "get_priority_issues",
        _priority_params,
        top_n=PRIORITY_TOP_N,
    ),
    SectionSpec(
        SECTION_ISSUE_TYPES,
        SECTION_TITLES_VI[SECTION_ISSUE_TYPES],
        "get_issue_types",
        _range_params,
    ),
    SectionSpec(
        SECTION_BACKLOG, SECTION_TITLES_VI[SECTION_BACKLOG], "get_status_backlog", _range_params
    ),
)

PERIOD_SECTIONS: tuple[SectionSpec, ...] = (
    SectionSpec(
        SECTION_OVERVIEW, SECTION_TITLES_VI[SECTION_OVERVIEW], "get_overview", _range_with_compare
    ),
    SectionSpec(
        SECTION_TREND,
        SECTION_TITLES_VI[SECTION_TREND],
        "get_daily_trend",
        _range_params,
        show_if=_trend_visible,
    ),
    SectionSpec(
        SECTION_PRODUCTS,
        SECTION_TITLES_VI[SECTION_PRODUCTS],
        "get_products",
        _range_params,
        top_n=RANKING_TOP_N,
    ),
    SectionSpec(
        SECTION_GEOGRAPHY,
        SECTION_TITLES_VI[SECTION_GEOGRAPHY],
        "get_geography",
        _range_params,
        top_n=RANKING_TOP_N,
    ),
    SectionSpec(
        SECTION_UNITS,
        SECTION_TITLES_VI[SECTION_UNITS],
        "get_units",
        _range_params,
        show_if=_units_visible,
        top_n=RANKING_TOP_N,
    ),
    SectionSpec(
        SECTION_PRIORITY,
        SECTION_TITLES_VI[SECTION_PRIORITY],
        "get_priority_issues",
        _priority_params,
        top_n=PRIORITY_TOP_N,
    ),
)

REPORT_TEMPLATES: dict[Intent, tuple[SectionSpec, ...]] = {
    Intent.REPORT_DAILY: DAILY_SECTIONS,
    Intent.REPORT_WEEKLY: PERIOD_SECTIONS,
    Intent.REPORT_CUSTOM: PERIOD_SECTIONS,
    Intent.REPORT_EXPORT: PERIOD_SECTIONS,
}


@dataclass(frozen=True)
class ReportPlan:
    """Kết quả dựng template: các phần được giữ, bước tương ứng và ghi chú."""

    report_type: Intent
    report_range: ReportRange
    sections: tuple[SectionSpec, ...] = ()
    steps: tuple[QueryPlan, ...] = ()
    assumptions: tuple[str, ...] = ()
    notices: tuple[tuple[Reason, str], ...] = field(default_factory=tuple)


def template_for(intent: Intent) -> tuple[SectionSpec, ...]:
    return REPORT_TEMPLATES.get(intent, PERIOD_SECTIONS)


def sections_from_hints(hints: Iterable[str]) -> tuple[str, ...]:
    """Chuẩn hoá ``report_sections``/``dimension_hints`` về id phần của template."""
    found: dict[str, None] = {}
    for hint in hints:
        key = str(hint or "").strip().lower()
        if not key:
            continue
        section = DIMENSION_TO_SECTION.get(key, key if key in SECTION_TITLES_VI else None)
        if section:
            found.setdefault(section, None)
    return tuple(found)


def resolve_range(
    intent: Intent,
    *,
    today: date,
    date_range: DateRange | None = None,
    compare_range: DateRange | None = None,
) -> tuple[ReportRange | None, tuple[str, ...]]:
    """Khoảng báo cáo và kỳ so sánh; trả ``None`` khi thiếu ngày bắt buộc (design D2)."""
    assumptions: list[str] = []
    current = date_range
    if current is None:
        if intent is Intent.REPORT_DAILY:
            current = yesterday(today)
            assumptions.append(
                f"Chưa nêu ngày nên tôi lấy báo cáo của hôm qua ({format_date(current.date_from)})."
            )
        elif intent is Intent.REPORT_WEEKLY:
            current = last_full_week(today)
            assumptions.append(
                "Chưa nêu tuần nên tôi lấy tuần trước "
                f"({format_date_range(current.date_from, current.date_to)})."
            )
        else:
            return None, ()
    if current.date_to > today:
        current = DateRange(
            date_from=min(current.date_from, today), date_to=today, label=current.label
        )
        assumptions.append(
            f"Kỳ này chưa kết thúc nên số liệu chỉ tính tới {format_date(today)}."
        )
    compare = compare_range or previous_period(current)
    return ReportRange(current=current, compare=compare), tuple(assumptions)


def report_type_for_range(report_range: ReportRange) -> Intent:
    """Loại báo cáo suy ra từ khoảng ngày — dùng khi yêu cầu là "xuất báo cáo ..." (b10 D7)."""
    current = report_range.current
    if report_range.days == 1:
        return Intent.REPORT_DAILY
    if report_range.days == 7 and current.date_from.weekday() == 0:
        return Intent.REPORT_WEEKLY
    return Intent.REPORT_CUSTOM


def build_report_plan(
    intent: Intent,
    report_range: ReportRange,
    context: ReportContext,
    *,
    original_query: str = "",
    confidence: float = 1.0,
) -> ReportPlan:
    """Lọc phần theo ``show_if`` và ``report_sections``, rồi dựng bước cho từng phần."""
    context = ReportContext(
        scope_units=context.scope_units,
        is_admin=context.is_admin,
        requested_sections=context.requested_sections,
        report_range=report_range,
    )
    template = template_for(intent)
    available = {spec.section_id for spec in template}
    requested = tuple(s for s in context.requested_sections if s != SECTION_OVERVIEW)

    assumptions: list[str] = []
    notices: list[tuple[Reason, str]] = []

    unknown = [s for s in requested if s not in available]
    if unknown:
        titles = [SECTION_TITLES_VI.get(s, s) for s in unknown]
        assumptions.append(
            "Báo cáo này chưa có phần: " + ", ".join(titles) + "; tôi bỏ qua phần đó."
        )
    wanted = {s for s in requested if s in available}

    kept: list[SectionSpec] = []
    for spec in template:
        if not spec.show_if(context):
            if spec.section_id == SECTION_TREND and report_range.days > TREND_MAX_DAYS:
                notices.append(
                    (
                        Reason.TREND_RANGE_TOO_LONG,
                        render(Reason.TREND_RANGE_TOO_LONG, max_days=TREND_MAX_DAYS),
                    )
                )
            continue
        # overview luôn có mặt, kể cả khi người dùng chỉ hỏi một chiều.
        if wanted and spec.section_id != SECTION_OVERVIEW and spec.section_id not in wanted:
            continue
        kept.append(spec)

    steps = tuple(
        QueryPlan(
            pattern=QueryPattern.SQL_TEMPLATE,
            answer_shape=AnswerShape.TABLE,
            original_query=original_query,
            confidence=confidence,
            function_name=spec.function_name,
            params=spec.params(report_range),
        )
        for spec in kept
    )
    return ReportPlan(
        report_type=intent,
        report_range=report_range,
        sections=tuple(kept),
        steps=steps,
        assumptions=tuple(assumptions),
        notices=tuple(notices),
    )


def section_titles(specs: Sequence[SectionSpec]) -> tuple[str, ...]:
    return tuple(spec.title_vi for spec in specs)


def top_n_for(specs: Sequence[SectionSpec]) -> Mapping[str, int | None]:
    return {spec.section_id: spec.top_n for spec in specs}


__all__ = [
    "DAILY_SECTIONS",
    "DIMENSION_TO_SECTION",
    "PERIOD_SECTIONS",
    "PRIORITY_TOP_N",
    "RANKING_TOP_N",
    "REPORT_TEMPLATES",
    "SECTION_TITLES_VI",
    "TREND_MAX_DAYS",
    "TREND_MIN_DAYS",
    "ReportContext",
    "ReportPlan",
    "ReportRange",
    "SectionSpec",
    "build_report_plan",
    "report_type_for_range",
    "resolve_range",
    "sections_from_hints",
    "section_titles",
    "template_for",
    "top_n_for",
]
