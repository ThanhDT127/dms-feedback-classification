"""Query Normalizer (spec ``chat-query-normalization``, design b01 D9–D10).

Chuẩn hoá văn bản, gom đồng nghĩa, trích entity thô và giải mốc thời gian.
Không chốt giá trị entity; việc đó thuộc Plan Guard (b03).
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..contract import CLASSIFICATION_LABELS, SENTIMENT_LABELS
from .date_resolver import DateResolution, DateResolver
from .text_match import find_candidates, match_tokens, normalize_match_text, phrase_starts
from .types import Dimension, EntityCandidate, MetadataProvider

SYNONYMS_PATH = Path(__file__).resolve().parent / "data" / "synonyms_vi.json"
DEFAULT_METADATA_TTL_SECONDS = 300.0
MAX_CANDIDATES_PER_DIMENSION = 3

Phrase = tuple[str, ...]

# Khoá của MetadataProvider.valid_values() → chiều dữ liệu.
METADATA_DIMENSIONS: tuple[tuple[str, Dimension], ...] = (
    ("units", Dimension.UNIT),
    ("provinces", Dimension.PROVINCE),
    ("districts", Dimension.DISTRICT),
    ("products", Dimension.PRODUCT),
    ("statuses", Dimension.STATUS),
)


@dataclass(frozen=True)
class Synonyms:
    dimension_hints: tuple[tuple[Phrase, str], ...]
    hint_exclusions: tuple[Phrase, ...]
    value_synonyms: Mapping[str, tuple[tuple[Phrase, str], ...]]


def _longest_first(pairs: list[tuple[Phrase, str]]) -> tuple[tuple[Phrase, str], ...]:
    return tuple(sorted((p for p in pairs if p[0]), key=lambda item: -len(item[0])))


def load_synonyms(path: Path = SYNONYMS_PATH) -> Synonyms:
    raw: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    hints = _longest_first(
        [(tuple(match_tokens(k)), str(v)) for k, v in raw["dimension_hints"].items()]
    )
    exclusions = tuple(
        p for p in (tuple(match_tokens(x)) for x in raw.get("dimension_hint_exclusions", [])) if p
    )
    values: dict[str, tuple[tuple[Phrase, str], ...]] = {}
    for dimension, mapping in raw["value_synonyms"].items():
        pairs = [
            (tuple(match_tokens(phrase)), str(canonical))
            for canonical, phrases in mapping.items()
            for phrase in [canonical, *phrases]
        ]
        values[str(dimension)] = _longest_first(pairs)
    return Synonyms(dimension_hints=hints, hint_exclusions=exclusions, value_synonyms=values)


@dataclass(frozen=True)
class NormalizationResult:
    match_text: str
    dates: DateResolution
    entities: Mapping[str, tuple[EntityCandidate, ...]]
    dimension_hints: tuple[str, ...]


class CachedMetadataProvider:
    """Cache ``valid_values()`` trong tiến trình theo TTL (design b01 D9)."""

    def __init__(
        self,
        inner: MetadataProvider,
        *,
        ttl_seconds: float = DEFAULT_METADATA_TTL_SECONDS,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self._inner = inner
        self._ttl = ttl_seconds
        self._now = now
        self._cached: Mapping[str, Sequence[str]] | None = None
        self._loaded_at = 0.0

    def raw_key_catalog(self) -> tuple[str, ...]:
        """Chuyển tiếp tới provider gốc nếu có (Pattern 4, b09 D9); không có thì rỗng."""
        loader = getattr(self._inner, "raw_key_catalog", None)
        return tuple(loader()) if callable(loader) else ()

    def valid_values(self) -> Mapping[str, Sequence[str]]:
        current = self._now()
        if self._cached is None or current - self._loaded_at >= self._ttl:
            self._cached = self._inner.valid_values()
            self._loaded_at = current
        return self._cached


class QueryNormalizer:
    def __init__(
        self,
        metadata: MetadataProvider,
        *,
        date_resolver: DateResolver,
        fuzzy_threshold: float = 0.88,
        synonyms: Synonyms | None = None,
        max_candidates: int = MAX_CANDIDATES_PER_DIMENSION,
    ) -> None:
        self.metadata = metadata
        self.date_resolver = date_resolver
        self.fuzzy_threshold = fuzzy_threshold
        self.synonyms = synonyms or load_synonyms()
        self.max_candidates = max_candidates
        self._sentiment_phrases = self.synonyms.value_synonyms.get(Dimension.SENTIMENT.value, ())
        # Đồng nghĩa nhãn + tên nhãn đầy đủ từ 2 token; tên 1 token như "Khác" dễ khớp nhầm.
        self._label_phrases = _longest_first(
            list(self.synonyms.value_synonyms.get(Dimension.LABEL.value, ()))
            + [
                (tuple(match_tokens(label)), label)
                for label in CLASSIFICATION_LABELS
                if len(match_tokens(label)) >= 2
            ]
        )

    def normalize(self, text: str) -> NormalizationResult:
        tokens = match_tokens(text)
        entities: dict[str, tuple[EntityCandidate, ...]] = {}

        for dimension, phrases in (
            (Dimension.SENTIMENT, self._sentiment_phrases),
            (Dimension.LABEL, self._label_phrases),
        ):
            found = self._phrase_entities(tokens, dimension, phrases)
            if found:
                entities[dimension.value] = found

        values = self.metadata.valid_values()
        for key, dimension in METADATA_DIMENSIONS:
            matches = find_candidates(
                text, values.get(key, ()), self.fuzzy_threshold, limit=self.max_candidates
            )
            if matches:
                entities[dimension.value] = tuple(
                    EntityCandidate(dimension.value, m.mention, m.value, m.score) for m in matches
                )

        return NormalizationResult(
            match_text=normalize_match_text(text),
            dates=self.date_resolver.resolve(text),
            entities=entities,
            dimension_hints=self._dimension_hints(tokens),
        )

    def _phrase_entities(
        self,
        tokens: Sequence[str],
        dimension: Dimension,
        phrases: Sequence[tuple[Phrase, str]],
    ) -> tuple[EntityCandidate, ...]:
        """Cụm dài khớp trước và chiếm token, nên "không hài lòng" không sinh thêm "hài lòng"."""
        used: set[int] = set()
        found: dict[str, EntityCandidate] = {}
        for phrase, value in phrases:
            for start in phrase_starts(tokens, phrase):
                span = set(range(start, start + len(phrase)))
                if span & used:
                    continue
                used |= span
                found.setdefault(
                    value, EntityCandidate(dimension.value, " ".join(phrase), value, 1.0)
                )
        return tuple(found.values())[: self.max_candidates]

    def _dimension_hints(self, tokens: Sequence[str]) -> tuple[str, ...]:
        masked = list(tokens)
        for phrase in self.synonyms.hint_exclusions:
            for start in phrase_starts(tokens, phrase):
                for i in range(start, start + len(phrase)):
                    masked[i] = ""
        used: set[int] = set()
        hints: list[str] = []
        for phrase, dimension in self.synonyms.dimension_hints:
            for start in phrase_starts(masked, phrase):
                span = set(range(start, start + len(phrase)))
                if span & used:
                    continue
                used |= span
                if dimension not in hints:
                    hints.append(dimension)
        return tuple(hints)


def synonym_canonical_values(synonyms: Synonyms) -> dict[str, set[str]]:
    """Giá trị chuẩn trong bảng đồng nghĩa, để test đối chiếu với contract."""
    return {dim: {value for _, value in pairs} for dim, pairs in synonyms.value_synonyms.items()}


KNOWN_CANONICAL_VALUES: dict[str, frozenset[str]] = {
    Dimension.LABEL.value: frozenset(CLASSIFICATION_LABELS),
    Dimension.SENTIMENT.value: frozenset(SENTIMENT_LABELS),
}
