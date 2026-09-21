"""Plan & Param Guard (spec ``chat-plan-guard``, design b03 D2–D6).

Lớp Python thuần giữa Planner và Executor: chốt tham số theo dữ liệu thật, áp phạm vi đơn vị
(kể cả từ chối riêng phần), và quyết định ``run | clarify | refuse | help | not_supported``.
**Không gọi LLM.**
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime

from ...settings import Settings
from ..ai.date_resolver import DEFAULT_TIMEZONE, DateResolver
from ..ai.fts_query_builder import FTS_FILTER_KEYS, FtsQueryBuilder
from ..ai.function_catalog import FUNCTION_CATALOG, PARAM_LABELS_VI
from ..ai.intents import (
    INTENT_SPECS,
    REPORT_INTENTS,
    SAMPLE_FUNCTION,
    Intent,
    PlannerState,
    is_supported,
    supports_fts,
    supports_report,
    supports_semantic,
)
from ..ai.intents import supported_functions as intent_functions
from ..ai.query_planner import ANALYSIS_REQUEST_PARAM, PlannerOutput
from ..ai.refusal_templates import format_options, join_vi, render
from ..ai.reports.templates import (
    ReportContext,
    build_report_plan,
    report_type_for_range,
    resolve_range,
)
from ..ai.text_match import match_tokens, normalize_match_text, phrase_starts, rank_values
from ..ai.types import (
    FLAG_COMPARISON_DISALLOWED,
    FLAG_EXPORT_REPLAY,
    FLAG_NEEDS_SEMANTIC_FALLBACK,
    Decision,
    Dimension,
    DroppedEntity,
    MetadataProvider,
    NormalizedQuery,
    Notice,
    QueryIssue,
    Reason,
    ReportSection,
    SectionStatus,
    ValidatedPlan,
)
from ..ai.unit_aliases import DEFAULT_ALIASES, AliasTable
from ..contract import (
    CLASSIFICATION_LABELS,
    SENTIMENT_LABELS,
    QueryPattern,
    QueryPlan,
    UserScope,
)

logger = logging.getLogger("dms-chat-plan-guard")

MAX_CLARIFY_OPTIONS = 5
SEMANTIC_VIEW_PATTERN = "semantic_view"
SEARCH_TERMS_PARAM = "search_terms"
# Bộ lọc Pattern 2 hỗ trợ (cột phân loại của semantic schema + ngày); province/district chưa có cột.
SEMANTIC_FILTER_KEYS = frozenset(
    {
        "date_from",
        "date_to",
        "compare_from",
        "compare_to",
        "unit_name",
        "sentiment",
        "business_status",
        "product",
        "source",
        "label",
    }
)
# Hàm Pattern 1 → chiều tương ứng khi chuyển sang Pattern 2 (needs_semantic_fallback).
FUNCTION_DIMENSIONS: dict[str, list[str]] = {
    "get_overview": [],
    "get_products": ["product"],
    "get_units": ["unit_name"],
    "get_sources": ["source"],
    "get_status_backlog": ["business_status"],
    "get_issue_types": ["label"],
    "get_groups": ["major_group"],
    "get_daily_trend": ["issue_date"],
}
ISSUE_CODE_PATTERN = re.compile(r"\b[A-Za-z][A-Za-z0-9]{1,5}-\d{3,}\b")
# "liệt kê phản hồi trong file X.xlsx" — chưa có bộ lọc source_file_name (b08 D7).
_FILE_LISTING = re.compile(r"\.(xlsx|xlsm|xls|csv)\b|\btrong (file|tep)\b")

# Tham số lọc → (khoá của MetadataProvider, chiều alias). None = dùng hằng số của contract.
PARAM_DIMENSIONS: dict[str, tuple[str | None, str | None]] = {
    "unit_name": ("units", Dimension.UNIT.value),
    "province": ("provinces", Dimension.PROVINCE.value),
    "district": ("districts", None),
    "product": ("products", None),
    "business_status": ("statuses", None),
    "label": (None, None),
    "sentiment": (None, None),
}
_CONSTANT_VALUES: dict[str, tuple[str, ...]] = {
    "label": tuple(CLASSIFICATION_LABELS),
    "sentiment": tuple(SENTIMENT_LABELS),
}
DIMENSION_LABELS_VI: dict[str, str] = {
    "unit_name": "đơn vị",
    "province": "tỉnh/thành",
    "district": "quận/huyện",
    "product": "sản phẩm",
    "business_status": "trạng thái xử lý",
    "label": "nhãn",
    "sentiment": "cảm xúc",
}


@dataclass(frozen=True)
class PlanGuardConfig:
    min_confidence: float = 0.6
    fuzzy_threshold: float = 0.88
    ambiguity_margin: float = 0.05
    milestone: str = "M1"
    enabled_patterns: frozenset[str] = frozenset({"sql_template"})
    fts_max_relax: int = 2
    fts_max_terms: int = 6
    fts_default_limit: int = 20
    timezone: str = DEFAULT_TIMEZONE

    @classmethod
    def from_settings(cls, settings: Settings) -> PlanGuardConfig:
        return cls(
            min_confidence=settings.chat_plan_min_confidence,
            fuzzy_threshold=settings.chat_fuzzy_match_threshold,
            ambiguity_margin=settings.chat_fuzzy_ambiguity_margin,
            milestone=settings.chat_milestone,
            enabled_patterns=frozenset(
                part.strip() for part in settings.chat_enabled_patterns.split(",") if part.strip()
            ),
            fts_max_relax=settings.chat_fts_max_relax,
            fts_max_terms=settings.chat_fts_max_terms,
            fts_default_limit=settings.chat_fts_default_limit,
            timezone=settings.chat_timezone,
        )


@dataclass
class _Resolution:
    """Kết quả chốt giá trị cho một tham số."""

    value: str | None = None
    reason: Reason | None = None
    options: tuple[str, ...] = ()
    mention: str = ""
    param: str = ""


class PlanGuard:
    def __init__(
        self,
        metadata: MetadataProvider,
        *,
        config: PlanGuardConfig | None = None,
        aliases: AliasTable = DEFAULT_ALIASES,
        fts_builder: FtsQueryBuilder | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.metadata = metadata
        self.config = config or PlanGuardConfig()
        self.aliases = aliases
        # Khoảng mặc định của báo cáo neo theo hôm nay ở múi giờ Việt Nam (b10 D2).
        self.dates = DateResolver(clock, tz_name=self.config.timezone)
        self.fts_builder = fts_builder or FtsQueryBuilder(
            max_terms=self.config.fts_max_terms, default_limit=self.config.fts_default_limit
        )

    # ── API ──

    def check(
        self, output: PlannerOutput, query: NormalizedQuery, scope: UserScope
    ) -> ValidatedPlan:
        """Chạy 10 bước của design D2; dừng ở bước đầu tiên cho quyết định kết thúc."""
        # Bước 1–3
        terminal = self._check_state_and_intent(output)
        if terminal is not None:
            return terminal

        assert output.intent is not None
        intent = output.intent

        # Báo cáo: bước do template dựng sau khi áp phạm vi, không từ Planner (b10 D1).
        if intent in REPORT_INTENTS and supports_report(intent, self.config.milestone):
            return self._check_report(output, intent, query, scope)

        # Bước 4–6
        for step_check in (
            self._check_structure(output, intent),
            self._check_confidence(output),
            self._check_dates(output, query),
        ):
            if step_check is not None:
                return step_check

        # Bước 7
        resolved_steps, resolution = self._resolve_entities(output.steps)
        if resolution is not None:
            return self._clarify_entity(output, intent, resolution)

        # Bước 8
        scoped = self._apply_scope(output, intent, query, scope, resolved_steps)
        if scoped.decision is not Decision.RUN:
            return scoped

        # Bước 9–10
        checked = self._check_params(scoped)
        if checked.decision is not Decision.RUN:
            return checked
        return self._build_fts(checked, query)

    # ── Bước 1–3 ──

    def _check_state_and_intent(self, output: PlannerOutput) -> ValidatedPlan | None:
        if output.system_state is PlannerState.OUT_OF_DOMAIN:
            return self._terminal(output, Decision.REFUSE, Reason.OUT_OF_DOMAIN)
        if output.system_state is PlannerState.CLARIFY:
            return self._terminal(output, Decision.CLARIFY, Reason.INVALID_PLAN)
        if output.intent is None:
            return self._terminal(output, Decision.CLARIFY, Reason.INVALID_PLAN)
        if not INTENT_SPECS[output.intent].needs_plan:
            return self._terminal(output, Decision.HELP, Reason.HELP, intent=output.intent)
        if not is_supported(output.intent, self.config.milestone, self.config.enabled_patterns):
            return self._terminal(
                output, Decision.NOT_SUPPORTED, Reason.INTENT_NOT_SUPPORTED, intent=output.intent
            )
        return None

    # ── Bước 4–6 ──

    def _check_structure(self, output: PlannerOutput, intent: Intent) -> ValidatedPlan | None:
        if not output.steps:
            return self._terminal(output, Decision.CLARIFY, Reason.INVALID_PLAN, intent=intent)

        allowed = set(intent_functions(intent, self.config.milestone))
        semantic_steps = sum(1 for s in output.steps if s.pattern is QueryPattern.SEMANTIC_VIEW)
        if semantic_steps > 1:
            return self._invalid_plan(output, intent, "tối đa một bước semantic_view")
        for index, step in enumerate(output.steps, start=1):
            if step.pattern is QueryPattern.SEMANTIC_VIEW:
                if not supports_semantic(
                    intent, self.config.milestone, self.config.enabled_patterns
                ):
                    return self._invalid_plan(
                        output, intent, f"bước {index}: semantic_view chưa bật cho {intent.value}"
                    )
                continue
            if step.pattern is QueryPattern.FTS5_SEARCH:
                if not supports_fts(intent, self.config.milestone, self.config.enabled_patterns):
                    return self._invalid_plan(
                        output, intent, f"bước {index}: fts5_search chưa bật cho {intent.value}"
                    )
                continue
            name = step.function_name
            is_sample = index > 1 and name == SAMPLE_FUNCTION
            if not name or name not in FUNCTION_CATALOG:
                return self._invalid_plan(output, intent, f"bước {index}: hàm '{name}' không có")
            if name not in allowed and not is_sample:
                return self._invalid_plan(
                    output, intent, f"bước {index}: {name} không thuộc {intent.value}"
                )
            if step.pattern.value not in self.config.enabled_patterns:
                return self._invalid_plan(
                    output, intent, f"bước {index}: pattern {step.pattern.value} chưa bật"
                )
            errors = step.validate()
            if errors:
                return self._invalid_plan(output, intent, f"bước {index}: {'; '.join(errors)}")
        return None

    def _check_confidence(self, output: PlannerOutput) -> ValidatedPlan | None:
        if output.confidence < self.config.min_confidence:
            return self._terminal(
                output, Decision.CLARIFY, Reason.LOW_CONFIDENCE, intent=output.intent
            )
        return None

    def _check_dates(self, output: PlannerOutput, query: NormalizedQuery) -> ValidatedPlan | None:
        if QueryIssue.INVALID_DATE in query.issues:
            return self._terminal(
                output, Decision.CLARIFY, Reason.INVALID_DATE, intent=output.intent
            )
        for step in output.steps:
            date_from = step.params.get("date_from")
            date_to = step.params.get("date_to")
            if date_from and date_to and str(date_from) > str(date_to):
                return self._terminal(
                    output, Decision.CLARIFY, Reason.INVALID_DATE, intent=output.intent
                )
        return None

    def recheck_steps(
        self,
        output: PlannerOutput,
        steps: Sequence[QueryPlan],
        query: NormalizedQuery,
        scope: UserScope,
    ) -> ValidatedPlan:
        """Chạy lại các bước đã lưu qua phạm vi hiện tại (xuất câu trả lời trước — b10 D7).

        Quyền có thể đã đổi từ lúc hỏi, mà file xuất là dữ liệu rời khỏi hệ thống, nên không
        bao giờ dùng lại quyết định phạm vi cũ.
        """
        intent = output.intent or Intent.REPORT_EXPORT
        resolved, resolution = self._resolve_entities(steps)
        if resolution is not None:
            return self._clarify_entity(output, intent, resolution)
        scoped = self._apply_scope(output, intent, query, scope, resolved)
        if scoped.decision is not Decision.RUN:
            return scoped
        checked = self._check_params(replace(scoped, export_request=True))
        logger.info(
            "plan_guard_export_recheck",
            extra={"steps": len(checked.steps), "decision": checked.decision.value},
        )
        return checked

    # ── Báo cáo (b10 D1, D2) ──

    def _check_report(
        self,
        output: PlannerOutput,
        intent: Intent,
        query: NormalizedQuery,
        scope: UserScope,
    ) -> ValidatedPlan:
        invalid_date = self._check_dates(output, query)
        if invalid_date is not None:
            return invalid_date

        scoped = self._apply_scope(output, intent, query, scope, ())
        if scoped.decision is not Decision.RUN:
            return scoped
        unit = self._report_unit(query, scope, scoped)
        export = intent is Intent.REPORT_EXPORT

        report_range, assumptions = resolve_range(
            intent,
            today=self.dates.today(),
            date_range=query.date_range,
            compare_range=query.compare_range,
        )
        if report_range is None:
            if export:
                # "Xuất kết quả vừa rồi": orchestrator chạy lại plan của câu trả lời trước.
                logger.info("plan_guard_export_replay")
                return replace(
                    scoped,
                    steps=(),
                    flags=scoped.flags | {FLAG_EXPORT_REPLAY},
                    export_request=True,
                )
            return self._terminal(
                output, Decision.CLARIFY, Reason.REPORT_RANGE_REQUIRED, intent=intent
            )

        context = ReportContext(
            scope_units=(unit,) if unit else tuple(scoped.scope_units),
            is_admin=scope.is_admin,
            requested_sections=output.report_sections,
        )
        report_type = report_type_for_range(report_range) if export else intent
        plan = build_report_plan(
            report_type,
            report_range,
            context,
            original_query=query.rewritten_query,
            confidence=output.confidence,
        )
        steps = self._with_unit(plan.steps, unit)
        sections = tuple(
            ReportSection(
                section_id=spec.section_id,
                title=spec.title_vi,
                index=index,
                step_index=index,
                status=SectionStatus.OK,
            )
            for index, spec in enumerate(plan.sections, start=1)
        )
        notices = tuple(scoped.notices) + tuple(
            Notice(kind=kind, message=message) for kind, message in plan.notices
        )
        checked = self._check_params(
            replace(
                scoped,
                steps=steps,
                notices=notices,
                report_type=report_type,
                report_range=report_range.current,
                report_compare=report_range.compare,
                report_sections=sections,
                export_request=export,
                assumptions=tuple(assumptions) + plan.assumptions,
            )
        )
        logger.info(
            "plan_guard_report",
            extra={
                "intent": intent.value,
                "sections": [section.section_id for section in sections],
                "steps": len(steps),
            },
        )
        return checked

    def _report_unit(
        self, query: NormalizedQuery, scope: UserScope, scoped: ValidatedPlan
    ) -> str | None:
        """Đơn vị áp vào mọi bước của báo cáo: admin theo đơn vị được nhắc, user theo phạm vi."""
        if scope.is_admin:
            mentioned = self._mentioned_units(query, ())
            return mentioned[0] if mentioned else None
        units = tuple(scoped.scope_units)
        return units[0] if len(units) == 1 else None

    # ── Bước 7: chuẩn hoá giá trị ──

    def _resolve_entities(
        self, steps: Sequence[QueryPlan]
    ) -> tuple[tuple[QueryPlan, ...], _Resolution | None]:
        resolved: list[QueryPlan] = []
        for step in steps:
            params = dict(step.params)
            for param in list(params):
                if param not in PARAM_DIMENSIONS or not params[param]:
                    continue
                outcome = self._resolve_value(param, str(params[param]))
                if outcome.reason is not None:
                    return (), outcome
                params[param] = outcome.value
            resolved.append(replace(step, params=params))
        return tuple(resolved), None

    def _resolve_value(self, param: str, mention: str) -> _Resolution:
        values = self._values_for(param)
        if not values:
            return _Resolution(value=mention, param=param, mention=mention)

        _, alias_dimension = PARAM_DIMENSIONS[param]
        if alias_dimension:
            aliased = self.aliases.resolve(alias_dimension, mention)
            if aliased:
                return _Resolution(value=aliased, param=param, mention=mention)

        normalized = normalize_match_text(mention)
        for value in values:
            if normalize_match_text(value) == normalized:
                return _Resolution(value=value, param=param, mention=mention)

        matches = rank_values(
            mention, values, self.config.fuzzy_threshold, limit=MAX_CLARIFY_OPTIONS
        )
        if not matches:
            return _Resolution(reason=Reason.ENTITY_NOT_FOUND, param=param, mention=mention)
        if len(matches) > 1 and matches[0].score - matches[1].score < self.config.ambiguity_margin:
            return _Resolution(
                reason=Reason.ENTITY_AMBIGUOUS,
                options=tuple(match.value for match in matches),
                param=param,
                mention=mention,
            )
        return _Resolution(value=matches[0].value, param=param, mention=mention)

    def _values_for(self, param: str) -> tuple[str, ...]:
        if param in _CONSTANT_VALUES:
            return _CONSTANT_VALUES[param]
        metadata_key, _ = PARAM_DIMENSIONS[param]
        if metadata_key is None:
            return ()
        return tuple(self.metadata.valid_values().get(metadata_key, ()))

    def _clarify_entity(
        self, output: PlannerOutput, intent: Intent, resolution: _Resolution
    ) -> ValidatedPlan:
        label = DIMENSION_LABELS_VI.get(resolution.param, resolution.param)
        if resolution.reason is Reason.ENTITY_AMBIGUOUS:
            message = render(Reason.ENTITY_AMBIGUOUS, options=format_options(resolution.options))
        else:
            message = render(
                Reason.ENTITY_NOT_FOUND, dimension_label=label, mention=resolution.mention
            )
        return ValidatedPlan(
            planner_output=output,
            decision=Decision.CLARIFY,
            reason=resolution.reason,
            intent=intent,
            clarify_options=resolution.options,
            message=message,
        )

    # ── Bước 8: phạm vi đơn vị (D4, D5) ──

    def _apply_scope(
        self,
        output: PlannerOutput,
        intent: Intent,
        query: NormalizedQuery,
        scope: UserScope,
        steps: tuple[QueryPlan, ...],
    ) -> ValidatedPlan:
        mentioned = self._mentioned_units(query, steps)

        if scope.is_admin:
            if len(mentioned) >= 2:
                return ValidatedPlan(
                    planner_output=output,
                    decision=Decision.NOT_SUPPORTED,
                    reason=Reason.MULTI_UNIT_FILTER_UNAVAILABLE,
                    intent=intent,
                    message=render(
                        Reason.MULTI_UNIT_FILTER_UNAVAILABLE, options=format_options(mentioned)
                    ),
                )
            steps = self._with_unit(steps, mentioned[0] if mentioned else None)
            return ValidatedPlan(
                planner_output=output, decision=Decision.RUN, intent=intent, steps=steps
            )

        allowed = tuple(scope.unit_ids)
        if not allowed:
            return ValidatedPlan(
                planner_output=output,
                decision=Decision.REFUSE,
                reason=Reason.UNAUTHORIZED_SCOPE,
                intent=intent,
                message=render(
                    Reason.UNAUTHORIZED_SCOPE,
                    dropped_units=join_vi(mentioned) or "đơn vị này",
                    allowed_units="chưa có đơn vị nào được phân công",
                ),
            )

        allowed_lookup = {normalize_match_text(unit): unit for unit in allowed}
        inside = tuple(
            allowed_lookup[normalize_match_text(unit)]
            for unit in mentioned
            if normalize_match_text(unit) in allowed_lookup
        )
        outside = tuple(
            unit for unit in mentioned if normalize_match_text(unit) not in allowed_lookup
        )

        if mentioned and not inside:
            return ValidatedPlan(
                planner_output=output,
                decision=Decision.REFUSE,
                reason=Reason.UNAUTHORIZED_SCOPE,
                intent=intent,
                dropped_entities=tuple(
                    DroppedEntity(Dimension.UNIT.value, unit) for unit in outside
                ),
                message=render(
                    Reason.UNAUTHORIZED_SCOPE,
                    dropped_units=join_vi(outside),
                    allowed_units=join_vi(allowed),
                ),
            )

        effective = inside or allowed
        notices: list[Notice] = []
        dropped: list[DroppedEntity] = []
        flags: set[str] = set()
        downgraded_from: Intent | None = None

        if outside:
            dropped = [DroppedEntity(Dimension.UNIT.value, unit) for unit in outside]
            notices.append(
                Notice(
                    kind=Reason.PARTIAL_REFUSAL,
                    message=render(
                        Reason.PARTIAL_REFUSAL,
                        dropped_units=join_vi(outside),
                        allowed_units=join_vi(effective),
                    ),
                )
            )
            if intent is Intent.COMPARISON and query.compare_range is None and len(inside) <= 1:
                downgraded_from = Intent.COMPARISON
                intent = Intent.OVERVIEW
                flags.add(FLAG_COMPARISON_DISALLOWED)

        steps = self._with_unit(steps, effective[0] if len(effective) == 1 else None)
        return ValidatedPlan(
            planner_output=output,
            decision=Decision.RUN,
            intent=intent,
            intent_downgraded_from=downgraded_from,
            steps=steps,
            scope_units=tuple(effective),
            dropped_entities=tuple(dropped),
            flags=frozenset(flags),
            notices=tuple(notices),
        )

    def _mentioned_units(
        self, query: NormalizedQuery, steps: Sequence[QueryPlan]
    ) -> tuple[str, ...]:
        """Đơn vị được nhắc: entity của Normalizer + alias trong câu + tham số của plan."""
        mentions: dict[str, None] = {}
        # Đơn vị đã bị bỏ khỏi ngữ cảnh vì hết quyền, và user không nhắc lại ở lượt này (b11 D7).
        ignored = {normalize_match_text(unit) for unit in query.dropped_slot_units}
        # Mã vấn đề như "NT-0106" không phải là nhắc tới đơn vị (tránh lộ đơn vị ở b08 D6).
        code_tokens = {
            token
            for code in ISSUE_CODE_PATTERN.findall(query.rewritten_query)
            for token in match_tokens(code)
        }
        candidates = query.entities.get(Dimension.UNIT.value, ())
        # Normalizer trả mọi ứng viên gần giống ("Vùng 1" khớp 0.95 cả "Vùng 2", "Vùng 3");
        # với mỗi cụm chỉ ứng viên điểm cao nhất mới là đơn vị được nhắc.
        best_score: dict[str, float] = {}
        for candidate in candidates:
            best_score[candidate.mention] = max(
                best_score.get(candidate.mention, 0.0), candidate.score
            )
        for candidate in candidates:
            if candidate.score < best_score[candidate.mention]:
                continue
            if code_tokens and set(match_tokens(candidate.mention)) <= code_tokens:
                continue
            mentions.setdefault(candidate.value, None)

        text_without_codes = ISSUE_CODE_PATTERN.sub(" ", query.rewritten_query)
        tokens = match_tokens(text_without_codes) if code_tokens else match_tokens(query.match_text)
        for alias, canonical in self.aliases.by_dimension.get(Dimension.UNIT.value, {}).items():
            if phrase_starts(tokens, tuple(alias.split())):
                mentions.setdefault(canonical, None)

        for step in steps:
            unit = step.params.get("unit_name")
            if unit:
                mentions.setdefault(str(unit), None)
        return tuple(unit for unit in mentions if normalize_match_text(unit) not in ignored)

    @staticmethod
    def _with_unit(steps: Sequence[QueryPlan], unit: str | None) -> tuple[QueryPlan, ...]:
        """Đặt (hoặc bỏ) ``unit_name`` trên bản sao của từng bước; không sửa plan gốc."""
        rebuilt: list[QueryPlan] = []
        for step in steps:
            params = dict(step.params)
            if unit:
                params["unit_name"] = unit
            else:
                params.pop("unit_name", None)
            rebuilt.append(replace(step, params=params))
        return tuple(rebuilt)

    # ── Bước 9–10: tham số được hàm hỗ trợ (D6) ──

    def _check_params(self, plan: ValidatedPlan) -> ValidatedPlan:
        unsupported: dict[str, None] = {}
        for step in plan.steps:
            if step.pattern is QueryPattern.SEMANTIC_VIEW:
                for param in step.params:
                    if param not in SEMANTIC_FILTER_KEYS and param != ANALYSIS_REQUEST_PARAM:
                        unsupported.setdefault(param, None)
                continue
            if step.pattern is QueryPattern.FTS5_SEARCH:
                for param in step.params:
                    if param not in FTS_FILTER_KEYS and param != SEARCH_TERMS_PARAM:
                        unsupported.setdefault(param, None)
                continue
            info = FUNCTION_CATALOG[str(step.function_name)]
            for param in step.params:
                if param not in info.supported_params:
                    unsupported.setdefault(param, None)

        if not unsupported:
            return plan

        fallback = self._semantic_fallback(plan, unsupported)
        if fallback is not None:
            return fallback

        names = [PARAM_LABELS_VI.get(param, param) for param in unsupported]
        logger.info("plan_guard_filter_not_supported", extra={"params": list(unsupported)})
        return replace(
            plan,
            decision=Decision.NOT_SUPPORTED,
            reason=Reason.FILTER_NOT_SUPPORTED,
            steps=(),
            message=render(Reason.FILTER_NOT_SUPPORTED, filter_name=join_vi(names)),
        )

    # ── Pattern 2 (b09 D7): chuyển bộ lọc Pattern 1 không hỗ trợ thành bước semantic_view ──

    def _semantic_fallback(
        self, plan: ValidatedPlan, unsupported: Mapping[str, None]
    ) -> ValidatedPlan | None:
        intent = plan.intent
        if intent is None or not supports_semantic(
            intent, self.config.milestone, self.config.enabled_patterns
        ):
            return None
        if any(param not in SEMANTIC_FILTER_KEYS for param in unsupported):
            return None  # vd. province: view chưa có cột
        first = plan.steps[0]
        if (
            first.pattern is not QueryPattern.SQL_TEMPLATE
            or first.function_name not in FUNCTION_DIMENSIONS
        ):
            return None
        # Mọi tham số (kể cả tham số Pattern 1 hỗ trợ như province) phải có cột ở view, nếu không
        # bộ lọc sẽ bị bỏ mất âm thầm khi chuyển sang Pattern 2.
        passthrough = {"limit", "page_size", "page"}
        if any(k not in SEMANTIC_FILTER_KEYS and k not in passthrough for k in first.params):
            return None
        params = dict(first.params)
        limit = params.pop("limit", None) or params.pop("page_size", None)
        params.pop("page", None)
        filters = {k: v for k, v in params.items() if k in SEMANTIC_FILTER_KEYS}
        request = {
            "goal_vi": plan.planner_output.reason or "Phân tích theo yêu cầu",
            "measures": ["so_van_de"],
            "dimensions": FUNCTION_DIMENSIONS[str(first.function_name)],
            "order": "so_van_de desc" if FUNCTION_DIMENSIONS[str(first.function_name)] else None,
            "limit": limit,
        }
        step = QueryPlan(
            pattern=QueryPattern.SEMANTIC_VIEW,
            answer_shape=first.answer_shape,
            original_query=first.original_query,
            confidence=first.confidence,
            params={
                **filters,
                ANALYSIS_REQUEST_PARAM: {k: v for k, v in request.items() if v is not None},
            },
            sql="",
        )
        logger.info("plan_guard_semantic_fallback", extra={"params": list(unsupported)})
        return replace(plan, steps=(step,), flags=plan.flags | {FLAG_NEEDS_SEMANTIC_FALLBACK})

    # ── Bước FTS (b08 D5): dựng token, fts_query và các lần thử nới ──

    def _build_fts(self, plan: ValidatedPlan, query: NormalizedQuery) -> ValidatedPlan:
        if not any(step.pattern is QueryPattern.FTS5_SEARCH for step in plan.steps):
            return plan
        if plan.intent is Intent.LOOKUP_FILE and _FILE_LISTING.search(query.match_text):
            logger.info("plan_guard_file_listing_not_supported")
            return replace(
                plan,
                decision=Decision.NOT_SUPPORTED,
                reason=Reason.FILTER_NOT_SUPPORTED,
                steps=(),
                message=render(Reason.FILTER_NOT_SUPPORTED, filter_name="tên file"),
            )
        builder = self.fts_builder
        steps: list[QueryPlan] = []
        attempts: list[tuple[object, ...]] = []
        for step in plan.steps:
            if step.pattern is not QueryPattern.FTS5_SEARCH:
                steps.append(step)
                attempts.append(())
                continue
            filters = {k: v for k, v in step.params.items() if k in FTS_FILTER_KEYS}
            terms = step.params.get(SEARCH_TERMS_PARAM) or []
            spec = builder.build(
                [str(t) for t in terms],
                query.rewritten_query,
                filters=filters,
                limit=step.fts_limit,
            )
            # LOOKUP_SIMILAR lấy từ khoá từ phản hồi gốc ở orchestrator, không từ câu hỏi.
            if spec.is_empty and plan.intent is not Intent.LOOKUP_SIMILAR:
                return replace(
                    plan,
                    decision=Decision.CLARIFY,
                    reason=Reason.NO_SEARCH_TERMS,
                    steps=(),
                    message=render(Reason.NO_SEARCH_TERMS),
                )
            steps.append(
                replace(
                    step,
                    params={**filters, SEARCH_TERMS_PARAM: list(spec.terms)},
                    fts_query=spec.fts_query,
                    fts_filters=dict(spec.filters),
                    fts_limit=spec.limit,
                )
            )
            attempts.append(
                tuple(builder.attempts(spec.tokens, max_relax=self.config.fts_max_relax))
            )
        return replace(plan, steps=tuple(steps), fts_attempts=tuple(attempts))

    # ── Tiện ích ──

    @staticmethod
    def _terminal(
        output: PlannerOutput,
        decision: Decision,
        reason: Reason,
        *,
        intent: Intent | None = None,
        **values: object,
    ) -> ValidatedPlan:
        return ValidatedPlan(
            planner_output=output,
            decision=decision,
            reason=reason,
            intent=intent,
            message=render(reason, **values),
        )

    def _invalid_plan(self, output: PlannerOutput, intent: Intent, detail: str) -> ValidatedPlan:
        logger.info("plan_guard_invalid_plan", extra={"detail": detail, "intent": intent.value})
        return self._terminal(output, Decision.CLARIFY, Reason.INVALID_PLAN, intent=intent)


def iter_unsupported_params(steps: Iterable[QueryPlan]) -> Mapping[str, str]:
    """Tham số không được hàm hỗ trợ → tên tiếng Việt (dùng cho log và b05)."""
    found: dict[str, str] = {}
    for step in steps:
        info = FUNCTION_CATALOG.get(str(step.function_name))
        if info is None:
            continue
        for param in step.params:
            if param not in info.supported_params:
                found[param] = PARAM_LABELS_VI.get(param, param)
    return found
