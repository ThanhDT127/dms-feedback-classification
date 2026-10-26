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

from ..contract import CLASSIFICATION_LABELS, MAJOR_GROUPS, SENTIMENT_LABELS
from .date_resolver import DateResolution, DateResolver
from .text_match import find_candidates, match_tokens, normalize_match_text, phrase_starts
from .types import Dimension, EntityCandidate, MetadataProvider
from .unit_aliases import DEFAULT_ALIASES

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
    """Cache ``valid_values()`` trong tiến trình theo TTL và revision (design b01 D9)."""

    def __init__(
        self,
        inner: MetadataProvider,
        *,
        ttl_seconds: float = DEFAULT_METADATA_TTL_SECONDS,
        now: Callable[[], float] = time.monotonic,
        revision_getter: Callable[[], int] | None = None,
    ) -> None:
        self._inner = inner
        self._ttl = ttl_seconds
        self._now = now
        self._revision_getter = revision_getter
        self._cached: Mapping[str, Sequence[str]] | None = None
        self._loaded_at = 0.0
        self._last_revision: int | None = None

    def raw_key_catalog(self) -> tuple[str, ...]:
        """Chuyển tiếp tới provider gốc nếu có (Pattern 4, b09 D9); không có thì rỗng."""
        loader = getattr(self._inner, "raw_key_catalog", None)
        return tuple(loader()) if callable(loader) else ()

    def valid_values(self) -> Mapping[str, Sequence[str]]:
        current = self._now()
        current_rev: int | None = None
        if self._revision_getter is not None:
            try:
                current_rev = self._revision_getter()
            except Exception:
                current_rev = None
        elif hasattr(self._inner, "get_cache_revision"):
            try:
                current_rev = self._inner.get_cache_revision()
            except Exception:
                current_rev = None
        elif hasattr(self._inner, "_service") and hasattr(self._inner._service, "repository"):
            try:
                with self._inner._service.repository._conn() as conn:
                    row = conn.execute(
                        "SELECT revision FROM analytics_cache_revision WHERE singleton = 1"
                    ).fetchone()
                    if row:
                        current_rev = int(row[0])
            except Exception:
                current_rev = None

        rev_changed = (
            current_rev is not None
            and self._last_revision is not None
            and current_rev != self._last_revision
        )

        if self._cached is None or rev_changed or current - self._loaded_at >= self._ttl:
            self._cached = self._inner.valid_values()
            self._loaded_at = current
            self._last_revision = current_rev
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
        self._major_group_phrases = self.synonyms.value_synonyms.get("major_group", ())
        # Đồng nghĩa nhãn + tên nhãn đầy đủ (nhãn 1 token thông dụng như "Khác", "Hãng" được kích hoạt qua cụm ghép trong synonyms_vi.json để tránh hiểu nhầm "chủ đề khác", "hàng tốt")
        self._label_phrases = _longest_first(
            [
                p
                for p in self.synonyms.value_synonyms.get(Dimension.LABEL.value, ())
                if len(p[0]) >= 2 or p[1] in ("HTPP", "Website")
            ]
            + [
                (tuple(match_tokens(label)), label)
                for label in CLASSIFICATION_LABELS
                if len(match_tokens(label)) >= 2 or label in ("HTPP", "Website")
            ]
        )

    def normalize(self, text: str) -> NormalizationResult:
        tokens = match_tokens(text)
        entities: dict[str, tuple[EntityCandidate, ...]] = {}

        for dimension_key, phrases in (
            (Dimension.SENTIMENT.value, self._sentiment_phrases),
            (Dimension.LABEL.value, self._label_phrases),
            ("major_group", self._major_group_phrases),
        ):
            found = self._phrase_entities(tokens, dimension_key, phrases)
            if found:
                entities[dimension_key] = found

        # Áp bảng alias tra tắt (tv1, v1, nt, bh, sg, hn...)
        alias_candidates: dict[str, list[EntityCandidate]] = {}
        for dim_name, dim_enum in (("unit", Dimension.UNIT), ("province", Dimension.PROVINCE)):
            seen_values: set[str] = set()
            candidates_list: list[EntityCandidate] = []
            n = len(tokens)
            used_spans: set[int] = set()
            for window_len in (3, 2, 1):
                for start in range(n - window_len + 1):
                    span = set(range(start, start + window_len))
                    if span & used_spans:
                        continue
                    mention = " ".join(tokens[start : start + window_len])
                    resolved = DEFAULT_ALIASES.resolve(dim_name, mention)
                    if resolved and resolved not in seen_values:
                        seen_values.add(resolved)
                        used_spans |= span
                        candidates_list.append(
                            EntityCandidate(dim_enum.value, mention, resolved, 1.0)
                        )
            if candidates_list:
                alias_candidates[dim_enum.value] = candidates_list

        values = self.metadata.valid_values()
        for key, dimension in METADATA_DIMENSIONS:
            matches = find_candidates(
                text, values.get(key, ()), self.fuzzy_threshold, limit=self.max_candidates
            )
            candidates: list[EntityCandidate] = [
                EntityCandidate(dimension.value, m.mention, m.value, m.score) for m in matches
            ]
            matched_values = {m.value for m in matches}
            for alias_c in alias_candidates.get(dimension.value, []):
                if alias_c.value not in matched_values:
                    candidates.append(alias_c)
                    matched_values.add(alias_c.value)
            if candidates:
                entities[dimension.value] = tuple(candidates[: self.max_candidates])

        return NormalizationResult(
            match_text=normalize_match_text(text),
            dates=self.date_resolver.resolve(text),
            entities=entities,
            dimension_hints=self._dimension_hints(tokens),
        )

    def _phrase_entities(
        self,
        tokens: Sequence[str],
        dimension: Dimension | str,
        phrases: Sequence[tuple[Phrase, str]],
    ) -> tuple[EntityCandidate, ...]:
        """Cụm dài khớp trước và chiếm token, nên "không hài lòng" không sinh thêm "hài lòng"."""
        used: set[int] = set()
        found: dict[str, EntityCandidate] = {}
        dim_str = dimension.value if isinstance(dimension, Dimension) else str(dimension)
        for phrase, value in phrases:
            for start in phrase_starts(tokens, phrase):
                span = set(range(start, start + len(phrase)))
                if span & used:
                    continue
                used |= span
                found.setdefault(
                    value, EntityCandidate(dim_str, " ".join(phrase), value, 1.0)
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
    "major_group": frozenset(MAJOR_GROUPS.values()),
}
