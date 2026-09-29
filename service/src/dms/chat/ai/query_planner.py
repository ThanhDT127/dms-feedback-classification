"""Query Planner — chọn intent và sinh QueryPlan (spec ``chat-query-planning``, design b02 D3–D5).

Lỗi của LLM client (timeout, SDK) được ném ra cho orchestrator (b03) xử lý; chỉ output
không hợp lệ mới đi vào vòng sửa lỗi.
"""

from __future__ import annotations

import calendar
import logging
from dataclasses import dataclass, field, replace
from datetime import date
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..contract import FUNCTION_REGISTRY_SPEC, AnswerShape, QueryPattern, QueryPlan
from .contextualizer import CODE_FENCE
from .fts_query_builder import clamp_limit
from .function_catalog import COMPARE_PARAMS, FUNCTION_CATALOG
from .intents import (
    INTENT_SPECS,
    SAMPLE_FUNCTION,
    Intent,
    PlannerState,
    is_supported,
    supported_functions,
    supports_fts,
    supports_report,
    supports_semantic,
)
from .planner_config import PlannerConfig
from .prompt_loader import load_prompt, sanitize_prompt_data
from .schema_retriever import SchemaRetriever
from .types import LLMClient, NormalizedQuery

logger = logging.getLogger("dms-chat-planner")

CALL_TYPE_PLAN = "chat_plan"
CALL_TYPE_REPAIR = "chat_plan_repair"
REPAIR_PROMPT_NAME = "planner_repair_v1"
REASON_INVALID_OUTPUT = "PLANNER_INVALID_OUTPUT"
MAX_STEPS = 3
NARRATIVE_SAMPLE_MAX_PAGE_SIZE = 5
HELP_TOPICS = frozenset({"usage", "label_definition"})
DATE_PARAMS = ("date_from", "date_to")
COMPARISON_FUNCTION = "get_comparison"
OVERVIEW_FUNCTION = "get_overview"


class PlanStepModel(BaseModel):
    model_config = ConfigDict(extra="ignore")

    pattern: str
    function_name: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    # Bước fts5_search (b08 D1): LLM chỉ đưa cụm từ nội dung; code dựng fts_query.
    search_terms: list[str] = Field(default_factory=list)
    filters: dict[str, Any] = Field(default_factory=dict)
    limit: int | None = None
    # Bước semantic_view (b09 D1): Planner mô tả yêu cầu phân tích, không viết SQL.
    analysis_request: dict[str, Any] | None = None


class PlannerOutputModel(BaseModel):
    model_config = ConfigDict(extra="ignore")

    intent: str | None = None
    system_state: str | None = None
    answer_shape: str = AnswerShape.TABLE.value
    confidence: float = Field(ge=0.0, le=1.0)
    steps: list[PlanStepModel] = Field(default_factory=list)
    # Phần báo cáo người dùng nêu rõ; chỉ lọc trong template, không dựng bước (b10 D1).
    report_sections: list[str] = Field(default_factory=list)
    help_topic: str | None = None
    help_label: str | None = None
    reason: str | None = None


@dataclass(frozen=True)
class PlannerOutput:
    intent: Intent | None
    system_state: PlannerState | None
    steps: tuple[QueryPlan, ...]
    answer_shape: AnswerShape
    confidence: float
    help_topic: str | None = None
    help_label: str | None = None
    reason: str | None = None
    report_sections: tuple[str, ...] = ()
    repairs: int = 0
    errors: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent.value if self.intent else None,
            "system_state": self.system_state.value if self.system_state else None,
            "answer_shape": self.answer_shape.value,
            "confidence": self.confidence,
            "steps": [step.to_dict() for step in self.steps],
            "help_topic": self.help_topic,
            "help_label": self.help_label,
            "report_sections": list(self.report_sections),
            "reason": self.reason,
            "repairs": self.repairs,
            "errors": list(self.errors),
        }


@dataclass
class _Validation:
    output: PlannerOutput | None = None
    errors: list[str] = field(default_factory=list)


def _pydantic_errors(exc: ValidationError) -> str:
    parts = []
    for error in exc.errors():
        location = ".".join(str(item) for item in error.get("loc", ()))
        parts.append(f"{location}: {error.get('msg')}" if location else str(error.get("msg")))
    return "JSON không đúng định dạng: " + "; ".join(parts)


class QueryPlanner:
    def __init__(
        self,
        llm: LLMClient,
        retriever: SchemaRetriever,
        *,
        config: PlannerConfig | None = None,
    ) -> None:
        self.llm = llm
        self.retriever = retriever
        self.config = config or retriever.config

    def plan(self, query: NormalizedQuery) -> PlannerOutput:
        if not query.guard.allowed:
            raise ValueError("Planner chỉ nhận câu hỏi đã qua Input Guard")

        prompt = self.retriever.render_prompt(query)
        result = self.llm.generate_json(prompt.text, call_type=CALL_TYPE_PLAN)
        validation = self.validate(result.text, query)
        repairs = 0
        while validation.errors and repairs < self.config.max_repair:
            repairs += 1
            repair_prompt = load_prompt(
                REPAIR_PROMPT_NAME,
                {
                    "original_prompt": prompt.text,
                    "previous_output": sanitize_prompt_data(result.text, 2000),
                    "errors": "\n".join(f"- {error}" for error in validation.errors),
                },
            )
            result = self.llm.generate_json(repair_prompt.text, call_type=CALL_TYPE_REPAIR)
            validation = self.validate(result.text, query)

        if validation.errors or validation.output is None:
            logger.warning(
                "planner_invalid_output",
                extra={"request_id": query.request_id, "errors": validation.errors[:10]},
            )
            return PlannerOutput(
                intent=None,
                system_state=PlannerState.CLARIFY,
                steps=(),
                answer_shape=AnswerShape.TABLE,
                confidence=0.0,
                reason=REASON_INVALID_OUTPUT,
                repairs=repairs,
                errors=tuple(validation.errors),
            )
        logger.info(
            "planner_output",
            extra={
                "request_id": query.request_id,
                "prompt_version": prompt.version,
                "prompt_sha256": prompt.sha256,
                "intent": validation.output.intent,
                "system_state": validation.output.system_state,
                "repairs": repairs,
            },
        )
        return replace(validation.output, repairs=repairs)

    def validate(self, text: str, query: NormalizedQuery) -> _Validation:
        """Chuỗi kiểm tra theo design b02 D4; trả output hoặc danh sách lỗi cụ thể."""
        try:
            model = PlannerOutputModel.model_validate_json(CODE_FENCE.sub("", text))
        except ValidationError as exc:
            return _Validation(errors=[_pydantic_errors(exc)])

        errors: list[str] = []
        intent: Intent | None = None
        state: PlannerState | None = None
        if model.system_state is not None:
            try:
                state = PlannerState(model.system_state)
            except ValueError:
                errors.append(
                    f"system_state '{model.system_state}' không hợp lệ; chỉ nhận CLARIFY hoặc "
                    "OUT_OF_DOMAIN"
                )
        if model.intent is not None:
            try:
                intent = Intent(model.intent)
            except ValueError:
                errors.append(f"intent '{model.intent}' không thuộc 15 intent")
        if model.intent is None and model.system_state is None:
            errors.append("phải có intent hoặc system_state")
        try:
            shape = AnswerShape(model.answer_shape)
        except ValueError:
            errors.append(f"answer_shape '{model.answer_shape}' không hợp lệ")
            shape = AnswerShape.TABLE
        if model.system_state is not None and model.steps:
            errors.append("khi có system_state thì steps phải rỗng")
        if errors:
            return _Validation(errors=errors)

        base = PlannerOutput(
            intent=intent,
            system_state=state,
            steps=(),
            answer_shape=shape,
            confidence=model.confidence,
            reason=model.reason,
        )
        if state is not None:
            return _Validation(output=base)

        assert intent is not None
        milestone = self.config.milestone
        if not INTENT_SPECS[intent].needs_plan:
            return self._validate_help(model, base)
        if supports_report(intent, milestone):
            return self._validate_report(model, base)
        patterns = self.config.enabled_patterns
        if not is_supported(intent, milestone, patterns):
            if model.steps:
                return _Validation(
                    errors=[
                        f"intent {intent.value} chưa có hàm thực thi ở mốc {milestone}; "
                        "steps phải rỗng"
                    ]
                )
            return _Validation(output=base)

        if not 1 <= len(model.steps) <= MAX_STEPS:
            return _Validation(errors=[f"cần từ 1 đến {MAX_STEPS} bước, nhận {len(model.steps)}"])

        allowed = set(supported_functions(intent, milestone))
        if (
            not allowed
            and not supports_fts(intent, milestone, patterns)
            and not supports_semantic(intent, milestone, patterns)
        ):
            return _Validation(output=base)
        plans: list[QueryPlan] = []
        known_patterns = {pattern.value for pattern in QueryPattern}
        for index, step in enumerate(model.steps, start=1):
            prefix = f"bước {index}"
            if step.pattern not in known_patterns:
                errors.append(f"{prefix}: pattern '{step.pattern}' không tồn tại")
                continue
            if step.pattern not in self.config.enabled_patterns:
                errors.append(f"{prefix}: pattern '{step.pattern}' chưa được bật")
                continue
            if step.pattern == QueryPattern.SEMANTIC_VIEW.value:
                if not supports_semantic(intent, milestone, patterns):
                    errors.append(f"{prefix}: intent {intent.value} không dùng semantic_view")
                    continue
                if not step.analysis_request:
                    errors.append(f"{prefix}: semantic_view cần analysis_request")
                    continue
                plans.append(semantic_plan(step.analysis_request, shape, model.confidence, query))
                continue
            if step.pattern == QueryPattern.FTS5_SEARCH.value:
                if not supports_fts(intent, milestone, patterns):
                    errors.append(f"{prefix}: intent {intent.value} không dùng fts5_search")
                    continue
                plans.append(self._fts_plan(step, shape, model.confidence, query))
                continue
            name = step.function_name
            if not name or name not in FUNCTION_REGISTRY_SPEC:
                errors.append(f"{prefix}: function_name '{name}' không có trong danh mục hàm")
                continue
            is_sample_step = shape is AnswerShape.NARRATIVE and index > 1
            if is_sample_step and name != SAMPLE_FUNCTION:
                errors.append(
                    f"{prefix}: bước sau của câu trả lời narrative phải là {SAMPLE_FUNCTION}"
                )
                continue
            if name not in allowed and not is_sample_step:
                errors.append(f"{prefix}: hàm {name} không thuộc intent {intent.value}")
                continue

            params = dict(step.params)
            if (
                name == COMPARISON_FUNCTION
                and query.compare_range is not None
                and OVERVIEW_FUNCTION in allowed
                and not _is_previous_period(query, str(params.get("period") or "month"))
            ):
                # get_comparison chỉ so với kỳ liền trước; câu hỏi nêu hai kỳ không liền nhau
                # ("tháng 3 với tháng 6") thì phải dùng get_overview + compare_*, nếu không kỳ
                # thứ hai bị bỏ âm thầm.
                logger.info(
                    "planner_comparison_rewritten",
                    extra={"request_id": query.request_id, "to": OVERVIEW_FUNCTION},
                )
                name = OVERVIEW_FUNCTION
                params.pop("period", None)
            if is_sample_step:
                try:
                    page_size = int(params.get("page_size", NARRATIVE_SAMPLE_MAX_PAGE_SIZE))
                except (TypeError, ValueError):
                    errors.append(f"{prefix}: page_size phải là số nguyên")
                    continue
                params["page_size"] = min(max(page_size, 1), NARRATIVE_SAMPLE_MAX_PAGE_SIZE)
                params["page"] = 1
            params = self._apply_resolved_dates(params, name, query)
            plan = QueryPlan(
                pattern=QueryPattern(step.pattern),
                answer_shape=shape,
                original_query=query.rewritten_query,
                confidence=model.confidence,
                function_name=name,
                params=params,
            )
            errors.extend(f"{prefix}: {error}" for error in plan.validate())
            plans.append(plan)

        if (
            shape is AnswerShape.NARRATIVE
            and plans
            and plans[0].function_name == SAMPLE_FUNCTION
            and intent is not Intent.LOOKUP_FEEDBACK
        ):
            errors.append("bước đầu của câu trả lời narrative phải là bước thống kê")
        if errors:
            return _Validation(errors=errors)
        return _Validation(output=replace(base, steps=tuple(plans)))

    @staticmethod
    def _fts_plan(
        step: PlanStepModel, shape: AnswerShape, confidence: float, query: NormalizedQuery
    ) -> QueryPlan:
        """Gói từ khoá và bộ lọc; Plan Guard kiểm chéo từ khoá và dựng ``fts_query`` (b08 D1, D5)."""
        raw_terms = step.search_terms or step.params.get("search_terms") or []
        if isinstance(raw_terms, str):
            raw_terms = [raw_terms]
        terms = [str(term).strip() for term in raw_terms if str(term).strip()]
        filters = {**step.params, **step.filters}
        filters.pop("search_terms", None)
        limit = filters.pop("limit", None) if step.limit is None else step.limit
        for key in DATE_PARAMS:
            filters.pop(key, None)
        if query.date_range is not None:
            filters["date_from"] = query.date_range.date_from.isoformat()
            filters["date_to"] = query.date_range.date_to.isoformat()
        return QueryPlan(
            pattern=QueryPattern.FTS5_SEARCH,
            answer_shape=shape,
            original_query=query.rewritten_query,
            confidence=confidence,
            params={**filters, "search_terms": terms},
            fts_query=" ".join(terms),
            fts_limit=clamp_limit(limit),
        )

    @staticmethod
    def _validate_report(model: PlannerOutputModel, base: PlannerOutput) -> _Validation:
        """Báo cáo: bước do template dựng, Planner chỉ nêu intent và phần muốn xem (b10 D1)."""
        from .reports.templates import sections_from_hints

        if model.steps:
            return _Validation(
                errors=["intent báo cáo không tự chọn bước truy vấn; steps phải rỗng"]
            )
        return _Validation(
            output=replace(base, report_sections=sections_from_hints(model.report_sections))
        )

    @staticmethod
    def _validate_help(model: PlannerOutputModel, base: PlannerOutput) -> _Validation:
        if model.steps:
            return _Validation(errors=["intent HELP không có bước truy vấn; steps phải rỗng"])
        topic = model.help_topic or "usage"
        if topic not in HELP_TOPICS:
            return _Validation(errors=[f"help_topic '{topic}' không hợp lệ"])
        return _Validation(output=replace(base, help_topic=topic, help_label=model.help_label))

    @staticmethod
    def _apply_resolved_dates(
        params: dict[str, Any], function_name: str, query: NormalizedQuery
    ) -> dict[str, Any]:
        """Ngày luôn lấy từ DateResolver; LLM không được tự tính (design b02 D5)."""
        date_keys = (*DATE_PARAMS, *sorted(COMPARE_PARAMS))
        from_llm = {key: params[key] for key in date_keys if key in params}
        resolved: dict[str, str] = {}
        if query.date_range is not None:
            resolved["date_from"] = query.date_range.date_from.isoformat()
            resolved["date_to"] = query.date_range.date_to.isoformat()
        supports_compare = COMPARE_PARAMS <= FUNCTION_CATALOG[function_name].supported_params
        if query.compare_range is not None and supports_compare:
            resolved["compare_from"] = query.compare_range.date_from.isoformat()
            resolved["compare_to"] = query.compare_range.date_to.isoformat()

        if any(str(value) != resolved.get(key) for key, value in from_llm.items()):
            logger.info(
                "planner_date_override",
                extra={
                    "request_id": query.request_id,
                    "function_name": function_name,
                    "llm_dates": from_llm,
                    "resolved_dates": resolved,
                },
            )
        cleaned = {key: value for key, value in params.items() if key not in date_keys}
        cleaned.update(resolved)
        return cleaned


_PERIOD_MONTHS = {"month": 1, "quarter": 3, "year": 12}


def _is_previous_period(query: NormalizedQuery, period: str) -> bool:
    """Kỳ get_comparison tự lùi có trùng đúng kỳ so sánh câu hỏi nêu không.

    Analytics lùi từng mốc theo tháng và kẹp ngày (30/04 → 30/03, không phải 31/03), nên kỳ
    so sánh là trọn tháng/quý thì phải khớp cả ngày cuối; kỳ đang diễn ra ("tháng này" tới hôm
    nay) chỉ cần khớp mốc đầu, vì so cùng số ngày với kỳ trước là chủ ý của get_comparison.
    """
    months = _PERIOD_MONTHS.get(period)
    if months is None or query.date_range is None or query.compare_range is None:
        return False
    current, compare = query.date_range, query.compare_range
    if compare.date_from != _shift_months(current.date_from, months):
        return False
    if not _is_month_end(current.date_to):
        return True
    return compare.date_to == _shift_months(current.date_to, months)


def _shift_months(value: date, months: int) -> date:
    """Cùng quy tắc với ``FeedbackAnalyticsService.comparison``: lùi tháng, kẹp ngày."""
    year, month_index = divmod(value.year * 12 + value.month - 1 - months, 12)
    month = month_index + 1
    return value.replace(
        year=year, month=month, day=min(value.day, calendar.monthrange(year, month)[1])
    )


def _is_month_end(value: date) -> bool:
    return value.day == calendar.monthrange(value.year, value.month)[1]


ANALYSIS_KEYS = ("goal_vi", "measures", "dimensions", "filters", "order", "limit")
ANALYSIS_REQUEST_PARAM = "analysis_request"


def semantic_plan(
    request: dict[str, Any],
    shape: AnswerShape,
    confidence: float,
    query: NormalizedQuery,
    *,
    extra_filters: dict[str, Any] | None = None,
) -> QueryPlan:
    """Bước Pattern 2: bộ lọc nằm trong ``params`` để Plan Guard chuẩn hoá và áp phạm vi như
    Pattern 1; ``analysis_request`` giữ mô tả cho SqlGenerator. SQL do orchestrator sinh sau guard."""
    cleaned = {key: request.get(key) for key in ANALYSIS_KEYS if request.get(key) is not None}
    for key in ("measures", "dimensions"):
        value = cleaned.get(key) or []
        cleaned[key] = [str(v) for v in (value if isinstance(value, list) else [value])]
    filters = dict(request.get("filters") or {})
    filters.update(extra_filters or {})
    for key in (*DATE_PARAMS, *sorted(COMPARE_PARAMS)):
        filters.pop(key, None)
    if query.date_range is not None:
        filters["date_from"] = query.date_range.date_from.isoformat()
        filters["date_to"] = query.date_range.date_to.isoformat()
    if query.compare_range is not None:
        filters["compare_from"] = query.compare_range.date_from.isoformat()
        filters["compare_to"] = query.compare_range.date_to.isoformat()
    cleaned.pop("filters", None)
    return QueryPlan(
        pattern=QueryPattern.SEMANTIC_VIEW,
        answer_shape=shape,
        original_query=query.rewritten_query,
        confidence=confidence,
        params={**filters, ANALYSIS_REQUEST_PARAM: cleaned},
        sql="",
    )
