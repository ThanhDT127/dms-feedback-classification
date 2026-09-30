"""Sinh SQL từ ``analysis_request`` và sửa theo lỗi (design b09 D1, D3, D6).

Prompt chỉ gồm schema, yêu cầu, giá trị được phép (bộ lọc đã chuẩn hoá + ngày đã giải) và ví dụ.
**Không** có thông tin phân quyền của người hỏi.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from ...settings import Settings
from .contextualizer import CODE_FENCE
from .prompt_loader import load_prompt, sanitize_prompt_data
from .semantic_schema import render_schema_vi
from .sql_example_retriever import select_examples
from .types import LLMClient

logger = logging.getLogger("dms-chat-sql")

CALL_TYPE_SQL = "chat_sql"
CALL_TYPE_SQL_REPAIR = "chat_sql_repair"
GENERATE_PROMPT = "sql_generate_v1"
REPAIR_PROMPT = "sql_repair_v1"
MAX_ERROR_IN_PROMPT = 300
MAX_SQL_IN_PROMPT = 2000


class SqlDraftError(ValueError):
    """LLM không trả JSON đúng dạng ``{sql, output_columns}`` (coi là lỗi sửa được)."""


@dataclass(frozen=True)
class SqlDraft:
    sql: str
    output_columns: tuple[Mapping[str, str], ...] = ()
    prompt: str = field(default="", repr=False)


@dataclass(frozen=True)
class SqlGeneratorConfig:
    examples_top_k: int = 4
    max_joins: int = 2
    max_subquery_depth: int = 3

    @classmethod
    def from_settings(cls, settings: Settings) -> SqlGeneratorConfig:
        return cls(
            examples_top_k=int(settings.chat_sql_examples_top_k),
            max_joins=int(settings.chat_sql_max_joins),
            max_subquery_depth=int(settings.chat_sql_max_subquery_depth),
        )


class SqlGenerator:
    def __init__(self, llm: LLMClient, *, config: SqlGeneratorConfig | None = None) -> None:
        self.llm = llm
        self.config = config or SqlGeneratorConfig()

    def system_instruction(self) -> str:
        return load_prompt(
            GENERATE_PROMPT,
            {
                "schema": render_schema_vi(),
                "max_joins": str(self.config.max_joins),
                "max_depth": str(self.config.max_subquery_depth),
            },
        ).text

    def render_user_prompt(
        self, request: Mapping[str, Any], filters: Mapping[str, Any], *, question: str
    ) -> str:
        allowed = [
            f"- {key} = {value}"
            for key, value in sorted(filters.items())
            if value not in (None, "")
        ]
        examples = select_examples(question, request, top_k=self.config.examples_top_k)
        lines = [
            f"<cau_hoi>{sanitize_prompt_data(question, 500)}</cau_hoi>",
            "<yeu_cau>",
            sanitize_prompt_data(json.dumps(dict(request), ensure_ascii=False), 1500),
            "</yeu_cau>",
            "GIÁ TRỊ ĐƯỢC PHÉP",
            *(allowed or ["- (không có bộ lọc)"]),
        ]
        if examples:
            lines.append("VÍ DỤ")
            for example in examples:
                lines.append(f"Hỏi: {example.question}")
                lines.append(f"SQL: {example.sql}")
        lines.append("Viết SQL:")
        return "\n".join(lines)

    def generate(
        self, request: Mapping[str, Any], filters: Mapping[str, Any], *, question: str
    ) -> SqlDraft:
        prompt = self.render_user_prompt(request, filters, question=question)
        result = self.llm.generate_json(
            prompt, call_type=CALL_TYPE_SQL, system_instruction=self.system_instruction()
        )
        return _parse(result.text, prompt)

    def repair(self, draft: SqlDraft, *, error_code: str, error_detail: str) -> SqlDraft:
        prompt = load_prompt(
            REPAIR_PROMPT,
            {
                "previous_sql": sanitize_prompt_data(draft.sql, MAX_SQL_IN_PROMPT),
                "error_code": error_code,
                "error_detail": sanitize_prompt_data(error_detail, MAX_ERROR_IN_PROMPT),
                "original_prompt": draft.prompt,
            },
        ).text
        result = self.llm.generate_json(
            prompt, call_type=CALL_TYPE_SQL_REPAIR, system_instruction=self.system_instruction()
        )
        return _parse(result.text, draft.prompt)


def _parse(text: str, prompt: str) -> SqlDraft:
    try:
        data = json.loads(CODE_FENCE.sub("", text or ""))
    except ValueError:
        # Không phải JSON: để SQL Guard báo SQL_PARSE và vòng sửa xử lý.
        return SqlDraft(sql=str(text or ""), prompt=prompt)
    if not isinstance(data, dict) or not isinstance(data.get("sql"), str):
        return SqlDraft(sql="", prompt=prompt)
    columns = data.get("output_columns") or []
    parsed: Sequence[Mapping[str, str]] = [
        {"alias": str(c.get("alias", "")), "meaning_vi": str(c.get("meaning_vi", ""))}
        for c in columns
        if isinstance(c, dict)
    ]
    return SqlDraft(sql=data["sql"], output_columns=tuple(parsed), prompt=prompt)
