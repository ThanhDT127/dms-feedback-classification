"""Ghép bước hiểu câu hỏi: IG1 → CTX → NRM → IG2 (b01 task 7.1)."""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime

from ...settings import Settings
from ..guardrails.input_guard import InputGuard
from .contextualizer import Contextualizer, merge_slots
from .date_resolver import DEFAULT_TIMEZONE, DateResolver
from .memory.topic_reset import RESET_ASSUMPTION, detect_topic_reset
from .query_normalizer import QueryNormalizer
from .text_match import normalize_match_text
from .types import (
    Dimension,
    EntityCandidate,
    GuardLayer,
    HistoryTurn,
    HistoryWindow,
    LLMClient,
    MetadataProvider,
    NormalizedQuery,
    QueryIssue,
    SessionSlots,
)

NO_TIME_ASSUMPTION = "Câu hỏi không nêu mốc thời gian nên dùng toàn bộ dữ liệu."
DROPPED_UNIT_ASSUMPTION = "Bỏ đơn vị {units} khỏi ngữ cảnh vì bạn không còn quyền xem."


@dataclass(frozen=True)
class UnderstandingConfig:
    timezone: str = DEFAULT_TIMEZONE
    max_question_chars: int = 1000
    history_turns: int = 5
    fuzzy_match_threshold: float = 0.88

    @classmethod
    def from_settings(cls, settings: Settings) -> UnderstandingConfig:
        return cls(
            timezone=settings.chat_timezone,
            max_question_chars=settings.chat_max_question_chars,
            history_turns=settings.chat_history_turns,
            fuzzy_match_threshold=settings.chat_fuzzy_match_threshold,
        )


def understand_query(
    question: str,
    *,
    session_history: HistoryWindow | Sequence[HistoryTurn],
    previous_slots: SessionSlots | None,
    llm: LLMClient,
    metadata: MetadataProvider,
    clock: Callable[[], datetime] | None = None,
    config: UnderstandingConfig | None = None,
    request_id: str | None = None,
    scope_units: Sequence[str] | None = None,
) -> NormalizedQuery:
    """``scope_units`` = đơn vị user đang được xem; ``None`` = admin, không giới hạn (b11 D7)."""
    cfg = config or UnderstandingConfig()
    rid = request_id or uuid.uuid4().hex
    guard = InputGuard(max_chars=cfg.max_question_chars)

    raw_decision = guard.check(question, layer=GuardLayer.RAW)
    if not raw_decision.allowed:
        return NormalizedQuery(
            request_id=rid,
            original_query=question,
            rewritten_query=question,
            match_text=normalize_match_text(question),
            guard=raw_decision,
            slots=previous_slots or SessionSlots(),
        )

    # Đổi chủ đề: coi như lượt đầu — không Contextualize, không kế thừa slot (b11 D6).
    reset = detect_topic_reset(question)
    if reset:
        session_history = HistoryWindow()
        previous_slots = None

    # Slot kế thừa chỉ giữ đơn vị user còn quyền xem (b11 D7).
    previous_slots, dropped_units = scope_slots(previous_slots, scope_units)

    context = Contextualizer(llm, max_turns=cfg.history_turns).rewrite(question, session_history)
    rewritten = context.rewritten_query

    normalizer = QueryNormalizer(
        metadata,
        date_resolver=DateResolver(clock, cfg.timezone),
        fuzzy_threshold=cfg.fuzzy_match_threshold,
    )
    normalized = normalizer.normalize(rewritten)
    if dropped_units:
        # Đơn vị user nhắc thẳng ở lượt này vẫn để Plan Guard xét như cũ; đơn vị chỉ đến từ
        # ngữ cảnh cũ (Contextualizer có thể chép lại) thì bỏ, không sinh refusal (b11 D7).
        own_units = _unit_values(normalizer.normalize(question).entities)
        dropped_units = tuple(unit for unit in dropped_units if unit not in own_units)
        normalized = _without_units(normalized, set(dropped_units))

    rewritten_decision = guard.check(rewritten, layer=GuardLayer.REWRITTEN)
    if not rewritten_decision.allowed:
        return NormalizedQuery(
            request_id=rid,
            original_query=question,
            rewritten_query=rewritten,
            match_text=normalized.match_text,
            guard=rewritten_decision,
            is_follow_up=context.is_follow_up,
            contextualization_failed=context.failed,
            slots=previous_slots or SessionSlots(),
        )

    dates = normalized.dates
    invalid_date = QueryIssue.INVALID_DATE in dates.issues
    current_slots = SessionSlots(
        date_range=dates.date_range,
        compare_range=dates.compare_range,
        entities=normalized.entities,
    )
    slots = merge_slots(previous_slots, current_slots, is_follow_up=context.is_follow_up)

    date_range = None if invalid_date else slots.date_range
    compare_range = None if invalid_date else slots.compare_range
    assumptions = list(dates.assumptions)
    if reset:
        assumptions.insert(0, RESET_ASSUMPTION)
    if dropped_units:
        assumptions.append(DROPPED_UNIT_ASSUMPTION.format(units=", ".join(dropped_units)))
    if date_range is None and not invalid_date and not reset.only_reset:
        assumptions.append(NO_TIME_ASSUMPTION)

    return NormalizedQuery(
        request_id=rid,
        original_query=question,
        rewritten_query=rewritten,
        match_text=normalized.match_text,
        guard=rewritten_decision,
        is_follow_up=context.is_follow_up,
        contextualization_failed=context.failed,
        date_range=date_range,
        compare_range=compare_range,
        entities=slots.entities,
        dimension_hints=normalized.dimension_hints,
        # Plan Guard vẫn thấy mọi đơn vị của lượt này (để từ chối nếu cần), nhưng slot được lưu
        # cho lượt sau chỉ giữ đơn vị trong phạm vi (b11 D7).
        slots=scope_slots(slots, scope_units)[0] or slots,
        assumptions=tuple(assumptions),
        issues=dates.issues,
        topic_reset=bool(reset),
        reset_only=reset.only_reset,
        dropped_slot_units=tuple(dropped_units),
    )


# ── Slot theo phạm vi hiện tại (b11 D7) ──


def scope_slots(
    slots: SessionSlots | None, scope_units: Sequence[str] | None
) -> tuple[SessionSlots | None, tuple[str, ...]]:
    """Giao đơn vị trong slot kế thừa với phạm vi hiện tại; trả slot mới và đơn vị bị bỏ."""
    if slots is None or scope_units is None:
        return slots, ()
    candidates = slots.entities.get(Dimension.UNIT.value, ())
    if not candidates:
        return slots, ()
    allowed = {normalize_match_text(unit) for unit in scope_units}
    kept = tuple(c for c in candidates if normalize_match_text(c.value) in allowed)
    dropped = tuple(
        dict.fromkeys(c.value for c in candidates if normalize_match_text(c.value) not in allowed)
    )
    if not dropped:
        return slots, ()
    entities = {k: v for k, v in slots.entities.items() if k != Dimension.UNIT.value}
    if kept:
        entities[Dimension.UNIT.value] = kept
    return (
        SessionSlots(
            date_range=slots.date_range,
            compare_range=slots.compare_range,
            entities=entities,
        ),
        dropped,
    )


def _unit_values(entities: Mapping[str, Sequence[EntityCandidate]]) -> set[str]:
    return {c.value for c in entities.get(Dimension.UNIT.value, ())}


def _without_units(normalized, units: set[str]):
    if not units:
        return normalized
    candidates = normalized.entities.get(Dimension.UNIT.value, ())
    kept = tuple(c for c in candidates if c.value not in units)
    entities = {k: v for k, v in normalized.entities.items() if k != Dimension.UNIT.value}
    if kept:
        entities[Dimension.UNIT.value] = kept
    return replace(normalized, entities=entities)
