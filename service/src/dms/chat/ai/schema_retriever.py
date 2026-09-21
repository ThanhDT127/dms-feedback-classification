"""Schema & Example Retriever — dựng ngữ cảnh prompt cho Planner (design b02 D6–D7).

Chỉ đọc từ ``function_catalog``, ``intents``, ``MetadataProvider`` (có cache) và tập few-shot;
không truy vấn DB. Không nhận ``UserScope``: phân quyền do Plan Guard (b03) quyết định.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ...prompt_renderer import RenderedPrompt
from ..contract import CLASSIFICATION_LABELS
from .function_catalog import FUNCTION_CATALOG, render_function_line
from .intents import (
    INTENT_SPECS,
    SAMPLE_FUNCTION,
    Intent,
    functions_for_milestone,
    is_supported,
    supported_functions,
    supports_fts,
    supports_report,
    supports_semantic,
)
from .planner_config import PlannerConfig
from .prompt_loader import load_prompt, sanitize_prompt_data
from .query_normalizer import CachedMetadataProvider
from .types import DateRange, MetadataProvider, NormalizedQuery

PROMPT_NAME = "planner_v1"
FTS_PROMPT_NAME = "planner_v2"
REPORT_PROMPT_NAME = "planner_v4"  # thêm luật báo cáo (b10); chỉ khi REPORT_* đã bật
SEMANTIC_PROMPT_NAME = "planner_v3"  # thêm luật Pattern 2 (b09); chỉ khi semantic_view đã bật  # có luật từ khoá FTS (b08); chỉ dùng khi fts5_search đã bật
FEWSHOT_PATH = Path(__file__).resolve().parent / "data" / "planner_fewshot.jsonl"
PROMPT_FEWSHOT_LIMIT = (
    10  # 10 ví dụ đầu của file phải phủ đủ loại (có dữ liệu, chưa hỗ trợ, HELP, trạng thái)
)
CHARS_PER_TOKEN = 3  # ước lượng thận trọng cho tiếng Việt có dấu
MAX_PROMPT_TOKENS = 4000
MAX_QUESTION_CHARS_IN_PROMPT = 2000


@dataclass(frozen=True)
class FewShotExample:
    id: str
    question: str
    dates: str
    output: Mapping[str, Any]


def load_fewshot(path: Path = FEWSHOT_PATH) -> tuple[FewShotExample, ...]:
    examples: list[FewShotExample] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        raw = json.loads(line)
        examples.append(
            FewShotExample(
                id=str(raw["id"]),
                question=str(raw["question"]),
                dates=str(raw.get("dates") or "không có"),
                output=raw["output"],
            )
        )
    return tuple(examples)


def estimate_tokens(text: str) -> int:
    return math.ceil(len(text) / CHARS_PER_TOKEN)


def _range_text(value: DateRange | None) -> str | None:
    if value is None:
        return None
    return f"{value.date_from.isoformat()} → {value.date_to.isoformat()} ({value.label})"


class SchemaRetriever:
    def __init__(
        self,
        metadata: MetadataProvider,
        *,
        config: PlannerConfig,
        fewshot: Sequence[FewShotExample] | None = None,
    ) -> None:
        self.config = config
        self.metadata: MetadataProvider = (
            metadata
            if isinstance(metadata, CachedMetadataProvider)
            else CachedMetadataProvider(metadata, ttl_seconds=config.metadata_ttl_seconds)
        )
        self.fewshot = tuple(fewshot) if fewshot is not None else load_fewshot()

    def render_prompt(self, query: NormalizedQuery) -> RenderedPrompt:
        return load_prompt(self.prompt_name, self.prompt_variables(query))

    @property
    def prompt_name(self) -> str:
        if any(supports_report(intent, self.config.milestone) for intent in Intent):
            return REPORT_PROMPT_NAME
        if any(
            supports_semantic(intent, self.config.milestone, self.config.enabled_patterns)
            for intent in Intent
        ):
            return SEMANTIC_PROMPT_NAME
        if any(
            supports_fts(intent, self.config.milestone, self.config.enabled_patterns)
            for intent in Intent
        ):
            return FTS_PROMPT_NAME
        return PROMPT_NAME

    def prompt_variables(self, query: NormalizedQuery) -> dict[str, str]:
        units = self.metadata.valid_values().get("units", ())
        return {
            "intents": self._intents_section(),
            "functions": self._functions_section(),
            "units": "; ".join(units) if units else "không có",
            "labels": "; ".join(CLASSIFICATION_LABELS),
            "fewshot": self._fewshot_section(),
            "date_facts": self._date_facts(query),
            "candidates": self._candidates(query),
            "hints": ", ".join(query.dimension_hints) or "không có",
            "assumptions": "; ".join(query.assumptions) or "không có",
            "question": sanitize_prompt_data(query.rewritten_query, MAX_QUESTION_CHARS_IN_PROMPT),
        }

    def _intents_section(self) -> str:
        lines = []
        for intent in Intent:
            spec = INTENT_SPECS[intent]
            if not spec.needs_plan:
                functions = "không cần hàm"
            elif supports_report(intent, self.config.milestone):
                # Bước của báo cáo do template Python dựng, không do Planner chọn (b10 D1).
                functions = "hệ thống tự dựng các phần, steps = []"
            elif is_supported(intent, self.config.milestone, self.config.enabled_patterns):
                names = list(supported_functions(intent, self.config.milestone))
                if supports_fts(intent, self.config.milestone, self.config.enabled_patterns):
                    names.append("fts5_search (tìm theo nội dung)")
                if supports_semantic(intent, self.config.milestone, self.config.enabled_patterns):
                    names.append("semantic_view (chỉ khi không hàm nào đáp ứng)")
                functions = ", ".join(names)
            else:
                functions = "chưa hỗ trợ ở mốc này, steps = []"
            lines.append(
                f"- {intent.value} [{spec.group}]: {spec.description_vi} "
                f"Phân biệt: {spec.disambiguation_vi} Hàm: {functions}."
            )
        return "\n".join(lines)

    def _functions_section(self) -> str:
        names = dict.fromkeys(functions_for_milestone(self.config.milestone))
        names.setdefault(SAMPLE_FUNCTION, None)
        lines = [
            render_function_line(FUNCTION_CATALOG[name])
            for name in names
            if FUNCTION_CATALOG[name].pattern in self.config.enabled_patterns
        ]
        return "\n".join(lines) or "không có"

    def _fewshot_section(self) -> str:
        blocks = []
        for example in self.fewshot[:PROMPT_FEWSHOT_LIMIT]:
            # Bỏ khoá null cho gọn; mẫu JSON đầy đủ đã có ở cuối prompt.
            compact = {k: v for k, v in example.output.items() if v is not None}
            output = json.dumps(compact, ensure_ascii=False, separators=(",", ":"))
            blocks.append(f"Hỏi: {example.question}\nNgày: {example.dates}\nJSON: {output}")
        return "\n\n".join(blocks)

    @staticmethod
    def _date_facts(query: NormalizedQuery) -> str:
        parts = []
        if current := _range_text(query.date_range):
            parts.append(f"date_range {current}")
        if compare := _range_text(query.compare_range):
            parts.append(f"compare_range {compare}")
        return "; ".join(parts) or "không có"

    @staticmethod
    def _candidates(query: NormalizedQuery) -> str:
        parts = [
            f"{dimension}: " + ", ".join(candidate.value for candidate in candidates)
            for dimension, candidates in query.entities.items()
            if candidates
        ]
        return "; ".join(parts) or "không có"
