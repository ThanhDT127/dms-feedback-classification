"""Kiểu dữ liệu nội bộ của Dev B cho bước hiểu câu hỏi (khối [1] INGEST/QUERY).

Các kiểu này không nằm trong ``contract.py`` vì chỉ Dev B dùng (design b01 D1).
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Protocol

from ..contract import QueryPlan, QueryResult, UserScope
from .intents import Intent

if TYPE_CHECKING:  # tránh vòng import: query_planner đã import module này
    from .query_planner import PlannerOutput

# ═══════════════════════════════════════════════════════════════════
# INPUT GUARD
# ═══════════════════════════════════════════════════════════════════


class GuardAction(StrEnum):
    ALLOW = "allow"
    REFUSE = "refuse"


class GuardReason(StrEnum):
    WRITE_REQUEST = "WRITE_REQUEST"
    SECRET_REQUEST = "SECRET_REQUEST"
    PROMPT_INJECTION = "PROMPT_INJECTION"
    INVALID_INPUT = "INVALID_INPUT"
    INPUT_TOO_LONG = "INPUT_TOO_LONG"


class GuardLayer(StrEnum):
    RAW = "raw"  # lớp 1: câu gốc, trước mọi lần gọi LLM
    REWRITTEN = "rewritten"  # lớp 2: câu đã được Contextualizer viết lại


@dataclass(frozen=True)
class GuardDecision:
    action: GuardAction
    reason_code: GuardReason | None = None
    message: str | None = None
    layer: GuardLayer = GuardLayer.RAW
    rule_id: str | None = None  # chỉ để ghi log, không bao giờ đưa ra người dùng

    @property
    def allowed(self) -> bool:
        return self.action is GuardAction.ALLOW

    @classmethod
    def allow(cls, layer: GuardLayer = GuardLayer.RAW) -> GuardDecision:
        return cls(action=GuardAction.ALLOW, layer=layer)

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action.value,
            "reason_code": self.reason_code.value if self.reason_code else None,
            "message": self.message,
            "layer": self.layer.value,
        }


# ═══════════════════════════════════════════════════════════════════
# NGÀY THÁNG, ENTITY, SLOT
# ═══════════════════════════════════════════════════════════════════


class QueryIssue(StrEnum):
    INVALID_DATE = "INVALID_DATE"


class Dimension(StrEnum):
    UNIT = "unit"
    PROVINCE = "province"
    DISTRICT = "district"
    PRODUCT = "product"
    LABEL = "label"
    SENTIMENT = "sentiment"
    STATUS = "status"
    SOURCE = "source"


@dataclass(frozen=True)
class DateRange:
    date_from: date
    date_to: date
    label: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "date_from": self.date_from.isoformat(),
            "date_to": self.date_to.isoformat(),
            "label": self.label,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> DateRange:
        return cls(
            date_from=date.fromisoformat(str(data["date_from"])),
            date_to=date.fromisoformat(str(data["date_to"])),
            label=str(data.get("label") or ""),
        )


@dataclass(frozen=True)
class EntityCandidate:
    """Ứng viên entity thô. Normalizer không chốt giá trị; Plan Guard (b03) mới chốt."""

    dimension: str
    mention: str
    value: str
    score: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "dimension": self.dimension,
            "mention": self.mention,
            "value": self.value,
            "score": round(self.score, 4),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> EntityCandidate:
        return cls(
            dimension=str(data["dimension"]),
            mention=str(data.get("mention") or ""),
            value=str(data["value"]),
            score=float(data.get("score") or 0.0),
        )


EntityMap = Mapping[str, tuple[EntityCandidate, ...]]


@dataclass(frozen=True)
class SessionSlots:
    """Bộ lọc hiện hành của phiên; là nguồn chuẩn cho bộ lọc (design b01 D7)."""

    date_range: DateRange | None = None
    compare_range: DateRange | None = None
    entities: EntityMap = field(default_factory=dict)

    def is_empty(self) -> bool:
        return self.date_range is None and self.compare_range is None and not self.entities

    def to_dict(self) -> dict[str, Any]:
        return {
            "date_range": self.date_range.to_dict() if self.date_range else None,
            "compare_range": self.compare_range.to_dict() if self.compare_range else None,
            "entities": {
                dim: [c.to_dict() for c in candidates] for dim, candidates in self.entities.items()
            },
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> SessionSlots:
        if not data:
            return cls()
        date_range = data.get("date_range")
        compare_range = data.get("compare_range")
        entities = data.get("entities") or {}
        return cls(
            date_range=DateRange.from_dict(date_range) if date_range else None,
            compare_range=DateRange.from_dict(compare_range) if compare_range else None,
            entities={
                str(dim): tuple(EntityCandidate.from_dict(c) for c in candidates)
                for dim, candidates in entities.items()
            },
        )


@dataclass(frozen=True)
class NormalizedQuery:
    """Kết quả của bước hiểu câu hỏi, đầu vào của Planner (b02) và Plan Guard (b03)."""

    request_id: str
    original_query: str
    rewritten_query: str
    match_text: str
    guard: GuardDecision
    is_follow_up: bool = False
    contextualization_failed: bool = False
    date_range: DateRange | None = None
    compare_range: DateRange | None = None
    entities: EntityMap = field(default_factory=dict)
    dimension_hints: tuple[str, ...] = ()
    slots: SessionSlots = field(default_factory=SessionSlots)
    assumptions: tuple[str, ...] = ()
    issues: tuple[QueryIssue, ...] = ()
    # Đổi chủ đề (b11 D6): ``reset_only`` = câu chỉ có cụm reset, không có gì để truy vấn.
    topic_reset: bool = False
    reset_only: bool = False
    # Đơn vị bị bỏ khỏi slot kế thừa vì user không còn quyền (b11 D7).
    dropped_slot_units: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "original_query": self.original_query,
            "rewritten_query": self.rewritten_query,
            "match_text": self.match_text,
            "guard": self.guard.to_dict(),
            "is_follow_up": self.is_follow_up,
            "contextualization_failed": self.contextualization_failed,
            "date_range": self.date_range.to_dict() if self.date_range else None,
            "compare_range": self.compare_range.to_dict() if self.compare_range else None,
            "entities": {
                dim: [c.to_dict() for c in candidates] for dim, candidates in self.entities.items()
            },
            "dimension_hints": list(self.dimension_hints),
            "slots": self.slots.to_dict(),
            "assumptions": list(self.assumptions),
            "issues": [issue.value for issue in self.issues],
            "topic_reset": self.topic_reset,
            "reset_only": self.reset_only,
            "dropped_slot_units": list(self.dropped_slot_units),
        }


# ═══════════════════════════════════════════════════════════════════
# INTERFACE (có fake trong tests/chat/ai_fakes.py)
# ═══════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class LLMResult:
    text: str
    usage: Mapping[str, int] = field(default_factory=dict)
    latency_ms: int = 0
    model: str = ""


class TextStream(Protocol):
    """Luồng văn bản do LLM sinh; cài đặt thật là ``llm_gateway.LLMStream`` (b04)."""

    @property
    def usage(self) -> Mapping[str, int]: ...

    @property
    def finish_reason(self) -> str | None: ...

    def __iter__(self) -> Iterator[str]: ...
    def __next__(self) -> str: ...
    def close(self) -> None: ...


class LLMClient(Protocol):
    def generate_json(
        self, prompt: str, *, call_type: str, system_instruction: str | None = None
    ) -> LLMResult: ...

    def stream(
        self,
        prompt: str,
        *,
        call_type: str,
        system_instruction: str | None = None,
        temperature: float | None = None,
        max_output_tokens: int | None = None,
    ) -> TextStream: ...


@dataclass(frozen=True)
class HistoryTurn:
    question: str
    answer_summary: str

    def char_size(self) -> int:
        return len(self.question) + len(self.answer_summary)


@dataclass(frozen=True)
class HistoryWindow:
    """Ngữ cảnh đưa vào Contextualizer: tóm tắt phiên + các lượt gần nhất (b11 D5)."""

    summary: str = ""
    turns: tuple[HistoryTurn, ...] = ()

    def __bool__(self) -> bool:
        return bool(self.summary or self.turns)

    def __len__(self) -> int:
        return len(self.turns)

    def __iter__(self) -> Iterator[HistoryTurn]:
        return iter(self.turns)

    def char_size(self) -> int:
        return len(self.summary) + sum(turn.char_size() for turn in self.turns)

    @classmethod
    def of(cls, value: HistoryWindow | Sequence[HistoryTurn] | None) -> HistoryWindow:
        """Nhận cả danh sách lượt cũ lẫn ``HistoryWindow`` để code gọi không phải đổi hết."""
        if value is None:
            return cls()
        if isinstance(value, HistoryWindow):
            return value
        return cls(turns=tuple(value))


class ConversationHistory(Protocol):
    def last_turns(self, session_id: str, n: int) -> Sequence[HistoryTurn]: ...

    def window(self, session_id: str, *, max_turns: int, budget_chars: int) -> HistoryWindow: ...


class MetadataProvider(Protocol):
    def valid_values(self) -> Mapping[str, Sequence[str]]:
        """Trả ``{units, provinces, districts, products, statuses}``."""
        ...


# ═══════════════════════════════════════════════════════════════════
# PLAN GUARD & ORCHESTRATOR (b03, design D1)
# ═══════════════════════════════════════════════════════════════════


class Decision(StrEnum):
    RUN = "run"
    CLARIFY = "clarify"
    REFUSE = "refuse"
    HELP = "help"
    NOT_SUPPORTED = "not_supported"


class Reason(StrEnum):
    """Lý do của một quyết định; mỗi giá trị phải có template trong ``refusal_templates``."""

    UNAUTHORIZED_SCOPE = "UNAUTHORIZED_SCOPE"
    OUT_OF_DOMAIN = "OUT_OF_DOMAIN"
    INVALID_PLAN = "INVALID_PLAN"
    LOW_CONFIDENCE = "LOW_CONFIDENCE"
    INVALID_DATE = "INVALID_DATE"
    ENTITY_AMBIGUOUS = "ENTITY_AMBIGUOUS"
    ENTITY_NOT_FOUND = "ENTITY_NOT_FOUND"
    FILTER_NOT_SUPPORTED = "FILTER_NOT_SUPPORTED"
    INTENT_NOT_SUPPORTED = "INTENT_NOT_SUPPORTED"
    MULTI_UNIT_FILTER_UNAVAILABLE = "MULTI_UNIT_FILTER_UNAVAILABLE"
    PARTIAL_REFUSAL = "PARTIAL_REFUSAL"
    TIMEOUT = "TIMEOUT"
    INTERNAL = "INTERNAL"
    SAMPLE_UNAVAILABLE = "SAMPLE_UNAVAILABLE"
    HELP = "HELP"
    NO_DATA = "NO_DATA"
    COMMENTARY_UNAVAILABLE = "COMMENTARY_UNAVAILABLE"
    SINGLE_KPI = "SINGLE_KPI"
    # Tra cứu FTS (b08)
    NO_MATCH = "NO_MATCH"
    NO_SEARCH_TERMS = "NO_SEARCH_TERMS"
    RELAXED_SEARCH = "RELAXED_SEARCH"
    LOOKUP_BY_CODE_UNAVAILABLE = "LOOKUP_BY_CODE_UNAVAILABLE"
    FILE_LOOKUP_UNAVAILABLE = "FILE_LOOKUP_UNAVAILABLE"
    SIMILAR_REFERENCE_UNKNOWN = "SIMILAR_REFERENCE_UNKNOWN"
    # Pattern 2 (b09)
    SQL_GENERATION_FAILED = "SQL_GENERATION_FAILED"
    NO_DATA_FILTERED = "NO_DATA_FILTERED"
    # Trí nhớ hội thoại (b11)
    TOPIC_RESET = "TOPIC_RESET"
    # Báo cáo và xuất file (b10)
    REPORT_RANGE_REQUIRED = "REPORT_RANGE_REQUIRED"
    TREND_RANGE_TOO_LONG = "TREND_RANGE_TOO_LONG"
    SECTION_UNAVAILABLE = "SECTION_UNAVAILABLE"
    EXPORT_NO_SOURCE = "EXPORT_NO_SOURCE"
    EXPORT_FAILED = "EXPORT_FAILED"
    EXPORT_TOO_LARGE = "EXPORT_TOO_LARGE"


class StepState(StrEnum):
    OK = "ok"
    NO_DATA = "no_data"
    FORBIDDEN = "forbidden"
    ERROR = "error"


class ErrorCode(StrEnum):
    """Mã lỗi nội bộ do ``ResultInterpreter`` gán (contract v1.0 chưa có ``error_code``)."""

    SCOPE_VIOLATION = "SCOPE_VIOLATION"
    PARAM_INVALID = "PARAM_INVALID"
    TIMEOUT = "TIMEOUT"
    INTERNAL = "INTERNAL"
    SQL_INVALID = "SQL_INVALID"  # lỗi cú pháp/cột của SQL Pattern 2 — vòng sửa lỗi xử lý (b09 D6)


FLAG_COMPARISON_DISALLOWED = "comparison_disallowed"
FLAG_NEEDS_SEMANTIC_FALLBACK = "needs_semantic_fallback"
# Yêu cầu xuất lại câu trả lời trước: bước lấy từ ``metadata.plans`` của lượt cũ (b10 D7).
FLAG_EXPORT_REPLAY = "export_replay"


# ── Chế độ báo cáo (b10 D3) ──


class TurnMode(StrEnum):
    NORMAL = "normal"
    REPORT = "report"


class SectionStatus(StrEnum):
    OK = "ok"
    NO_DATA = "no_data"
    ERROR = "error"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class ReportSection:
    """Một phần của báo cáo và trạng thái lấy dữ liệu của nó."""

    section_id: str
    title: str
    index: int
    step_index: int = 0
    status: SectionStatus = SectionStatus.OK

    def to_dict(self) -> dict[str, Any]:
        return {
            "section_id": self.section_id,
            "title": self.title,
            "index": self.index,
            "step_index": self.step_index,
            "status": self.status.value,
        }

    def event_ref(self) -> dict[str, Any]:
        """Trường ``section`` gắn vào ``data_block``/``commentary`` (design D4)."""
        return {"id": self.section_id, "title": self.title, "index": self.index}


@dataclass(frozen=True)
class DroppedEntity:
    """Giá trị bị bỏ khỏi plan, vd. đơn vị người dùng không có quyền xem."""

    dimension: str
    value: str
    reason: Reason = Reason.UNAUTHORIZED_SCOPE

    def to_dict(self) -> dict[str, Any]:
        return {"dimension": self.dimension, "value": self.value, "reason": self.reason.value}


@dataclass(frozen=True)
class Notice:
    """Thông báo kèm theo câu trả lời; b05 hiển thị, không phải lỗi."""

    kind: Reason
    message: str
    details: Mapping[str, Any] = field(default_factory=dict)  # vd. dropped_terms của RELAXED_SEARCH

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"kind": self.kind.value, "message": self.message}
        if self.details:
            data["details"] = dict(self.details)
        return data


@dataclass(frozen=True)
class StepStatus:
    state: StepState
    code: ErrorCode | None = None

    @property
    def ok(self) -> bool:
        return self.state is StepState.OK

    def to_dict(self) -> dict[str, Any]:
        return {"state": self.state.value, "code": self.code.value if self.code else None}


@dataclass(frozen=True)
class StepResult:
    index: int
    function_name: str | None
    status: StepStatus
    result: QueryResult | None = None
    duration_ms: int = 0
    is_sample: bool = False
    # Pattern 2: SQL đã qua guard (chỉ cho audit/metadata, không bao giờ vào event) và alias kết quả.
    sql: str | None = None
    output_columns: tuple[Mapping[str, str], ...] = ()
    sql_repairs: int = 0

    def to_dict(self) -> dict[str, Any]:
        data = {
            "index": self.index,
            "function_name": self.function_name,
            "status": self.status.to_dict(),
            "result": self.result.to_dict() if self.result is not None else None,
            "duration_ms": self.duration_ms,
            "is_sample": self.is_sample,
        }
        if self.sql is not None:
            data["sql"] = self.sql
            data["sql_repairs"] = self.sql_repairs
        return data


@dataclass(frozen=True)
class ValidatedPlan:
    """Kết quả của Plan Guard. Giữ nguyên ``planner_output`` để audit (design b03 D1)."""

    planner_output: PlannerOutput
    decision: Decision
    reason: Reason | None = None
    intent: Intent | None = None
    intent_downgraded_from: Intent | None = None
    steps: tuple[QueryPlan, ...] = ()
    scope_units: tuple[str, ...] = ()  # rỗng = admin, không giới hạn
    dropped_entities: tuple[DroppedEntity, ...] = ()
    flags: frozenset[str] = frozenset()
    notices: tuple[Notice, ...] = ()
    clarify_options: tuple[str, ...] = ()
    message: str | None = None
    # Mỗi bước fts5_search: các lần thử theo thứ tự nới (FtsAttempt); bước khác là () (b08 D4).
    fts_attempts: tuple[tuple[Any, ...], ...] = ()
    # ── Chế độ báo cáo (b10 D1, D3) ──
    report_type: Intent | None = None
    report_range: DateRange | None = None
    report_compare: DateRange | None = None
    report_sections: tuple[ReportSection, ...] = ()
    export_request: bool = False
    assumptions: tuple[str, ...] = ()  # giả định do Plan Guard thêm (vd. khoảng mặc định)

    @property
    def is_terminal(self) -> bool:
        return self.decision is not Decision.RUN

    def to_dict(self) -> dict[str, Any]:
        return {
            "planner_output": self.planner_output.to_dict(),
            "decision": self.decision.value,
            "reason": self.reason.value if self.reason else None,
            "intent": self.intent.value if self.intent else None,
            "intent_downgraded_from": (
                self.intent_downgraded_from.value if self.intent_downgraded_from else None
            ),
            "steps": [step.to_dict() for step in self.steps],
            "scope_units": list(self.scope_units),
            "dropped_entities": [entity.to_dict() for entity in self.dropped_entities],
            "flags": sorted(self.flags),
            "notices": [notice.to_dict() for notice in self.notices],
            "clarify_options": list(self.clarify_options),
            "message": self.message,
            "report_type": self.report_type.value if self.report_type else None,
            "report_range": self.report_range.to_dict() if self.report_range else None,
            "report_sections": [section.to_dict() for section in self.report_sections],
            "export_request": self.export_request,
        }


class TurnCancelled(Exception):
    """Người dùng huỷ lượt giữa các giai đoạn (design b06 D6); runner phát ``done(cancelled)``."""

    def __init__(self, stage: str) -> None:
        super().__init__(stage)
        self.stage = stage


@dataclass(frozen=True)
class TurnRequest:
    question: str
    scope: UserScope
    session_id: str = ""
    # Danh sách lượt (b01) hoặc ``HistoryWindow`` có tóm tắt phiên (b11 D5).
    history: HistoryWindow | Sequence[HistoryTurn] = ()
    previous_slots: SessionSlots | None = None
    request_id: str | None = None
    # Trích dẫn (payload khối quote) của câu trả lời trước, cho LOOKUP_SIMILAR (b08 D6).
    previous_quotes: Sequence[Mapping[str, Any]] = ()
    # ``metadata.plans`` của câu trả lời trước có dữ liệu, cho REPORT_EXPORT (b10 D7).
    previous_plans: Sequence[Mapping[str, Any]] = ()
    # Ai đang hỏi, dùng cho tên file và sheet "Thông tin" của bản xuất.
    display_name: str = ""


@dataclass(frozen=True)
class TurnOutcome:
    """Kết quả một lượt, đầu vào của Response Shaper (b05). Không chứa văn bản trả lời."""

    request_id: str
    original_query: str
    decision: Decision
    rewritten_query: str = ""
    intent: Intent | None = None
    intent_downgraded_from: Intent | None = None
    reason: Reason | None = None
    validated_plan: ValidatedPlan | None = None
    step_results: tuple[StepResult, ...] = ()
    scope_units: tuple[str, ...] = ()
    dropped_entities: tuple[DroppedEntity, ...] = ()
    notices: tuple[Notice, ...] = ()
    assumptions: tuple[str, ...] = ()
    clarify_options: tuple[str, ...] = ()
    message: str | None = None
    guard: GuardDecision | None = None  # quyết định của Input Guard khi lượt bị chặn sớm
    slots: SessionSlots = field(default_factory=SessionSlots)
    timings_ms: Mapping[str, int] = field(default_factory=dict)
    llm_usage: Mapping[str, Mapping[str, int]] = field(default_factory=dict)
    # ── Chế độ báo cáo (b10 D3) ──
    mode: TurnMode = TurnMode.NORMAL
    report_type: Intent | None = None
    report_range: DateRange | None = None
    compare_range: DateRange | None = None
    sections: tuple[ReportSection, ...] = ()
    export_request: bool = False
    # Ai hỏi lượt này; cần cho tên file và sheet "Thông tin" của bản xuất (b10 D8).
    username: str = ""
    display_name: str = ""
    is_admin: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "original_query": self.original_query,
            "rewritten_query": self.rewritten_query,
            "decision": self.decision.value,
            "intent": self.intent.value if self.intent else None,
            "intent_downgraded_from": (
                self.intent_downgraded_from.value if self.intent_downgraded_from else None
            ),
            "reason": self.reason.value if self.reason else None,
            "validated_plan": (
                self.validated_plan.to_dict() if self.validated_plan is not None else None
            ),
            "step_results": [step.to_dict() for step in self.step_results],
            "scope_units": list(self.scope_units),
            "dropped_entities": [entity.to_dict() for entity in self.dropped_entities],
            "notices": [notice.to_dict() for notice in self.notices],
            "assumptions": list(self.assumptions),
            "clarify_options": list(self.clarify_options),
            "message": self.message,
            "guard": self.guard.to_dict() if self.guard is not None else None,
            "slots": self.slots.to_dict(),
            "timings_ms": dict(self.timings_ms),
            "llm_usage": {key: dict(value) for key, value in self.llm_usage.items()},
            "mode": self.mode.value,
            "report_type": self.report_type.value if self.report_type else None,
            "report_range": self.report_range.to_dict() if self.report_range else None,
            "sections": [section.to_dict() for section in self.sections],
        }


class QueryExecutor(Protocol):
    """Khớp cả ``MockQueryExecutor`` lẫn ``SecureQueryExecutor`` của Dev A."""

    def execute(self, plan: QueryPlan, scope: UserScope) -> QueryResult: ...
