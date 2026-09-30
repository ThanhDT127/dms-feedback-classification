"""Chọn ví dụ SQL cho prompt sinh SQL, không dùng embedding (design b09 D3)."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from rapidfuzz import fuzz

from .text_match import normalize_match_text

EXAMPLES_PATH = Path(__file__).resolve().parent / "data" / "sql_examples.jsonl"
QUESTION_WEIGHT = 0.6
SHAPE_WEIGHT = 0.4


@dataclass(frozen=True)
class SqlExample:
    id: str
    question: str
    analysis_request: Mapping[str, Any]
    sql: str
    features: frozenset[str] = field(default_factory=frozenset)

    @property
    def shape(self) -> tuple[tuple[str, ...], tuple[str, ...]]:
        return (
            tuple(sorted(self.analysis_request.get("dimensions") or [])),
            tuple(sorted(self.analysis_request.get("measures") or [])),
        )


def request_features(request: Mapping[str, Any]) -> frozenset[str]:
    return frozenset(
        [f"d:{d}" for d in request.get("dimensions") or []]
        + [f"m:{m}" for m in request.get("measures") or []]
    )


@lru_cache(maxsize=1)
def load_examples(path: Path = EXAMPLES_PATH) -> tuple[SqlExample, ...]:
    examples = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        raw = json.loads(line)
        examples.append(
            SqlExample(
                id=raw["id"],
                question=raw["question"],
                analysis_request=raw["analysis_request"],
                sql=raw["sql"],
                features=request_features(raw["analysis_request"]),
            )
        )
    return tuple(examples)


def select_examples(
    question: str,
    request: Mapping[str, Any],
    *,
    top_k: int = 4,
    examples: Sequence[SqlExample] | None = None,
) -> list[SqlExample]:
    """Điểm = 0.6 × token_set_ratio(câu hỏi) + 0.4 × Jaccard(chiều ∪ độ đo); không trùng hình dạng."""
    if top_k <= 0:
        return []
    pool = examples if examples is not None else load_examples()
    target = request_features(request)
    normalized = normalize_match_text(question)

    def score(example: SqlExample) -> float:
        text = fuzz.token_set_ratio(normalized, normalize_match_text(example.question)) / 100
        union = target | example.features
        jaccard = len(target & example.features) / len(union) if union else 0.0
        return QUESTION_WEIGHT * text + SHAPE_WEIGHT * jaccard

    chosen: list[SqlExample] = []
    shapes: set[tuple[tuple[str, ...], tuple[str, ...]]] = set()
    for example in sorted(pool, key=lambda e: (-score(e), e.id)):
        if example.shape in shapes:
            continue
        chosen.append(example)
        shapes.add(example.shape)
        if len(chosen) >= top_k:
            break
    return chosen
