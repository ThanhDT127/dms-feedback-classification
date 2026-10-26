"""Tests for Milestone 2: QueryNormalizer, unit aliases, 21 labels, text_match, and cache revisions."""

from __future__ import annotations

from datetime import UTC, datetime

from dms.chat.ai.date_resolver import DateResolver
from dms.chat.ai.fts_query_builder import CachedTermStats
from dms.chat.ai.query_normalizer import (
    CachedMetadataProvider,
    QueryNormalizer,
    load_synonyms,
    synonym_canonical_values,
)
from dms.chat.ai.text_match import find_candidates, mention_score
from dms.chat.cache.query_cache import QueryCache
from dms.chat.contract import (
    CLASSIFICATION_LABELS,
    MAJOR_GROUPS,
    AnswerShape,
    QueryPattern,
    QueryPlan,
    QueryResult,
    UserScope,
)

from .ai_fakes import FixedClock, StaticMetadataProvider

CLOCK = FixedClock(datetime(2026, 9, 15, 3, 0, tzinfo=UTC))


def make_normalizer(values=None) -> QueryNormalizer:
    return QueryNormalizer(
        StaticMetadataProvider(values),
        date_resolver=DateResolver(CLOCK),
        fuzzy_threshold=0.88,
    )


def test_short_unit_aliases_resolved_in_normalize():
    normalizer = make_normalizer()

    # tv1 -> Truyền thống Vùng 1
    res_tv1 = normalizer.normalize("số phản hồi của tv1")
    unit_candidates = res_tv1.entities.get("unit", ())
    assert len(unit_candidates) >= 1
    assert unit_candidates[0].value == "Truyền thống Vùng 1"

    # nt -> Nha Trang
    res_nt = normalizer.normalize("tình hình ở nt tháng 8")
    assert any(c.value == "Nha Trang" for c in res_nt.entities.get("unit", ()))

    # bh -> Biên Hòa
    res_bh = normalizer.normalize("báo cáo cho bh")
    assert any(c.value == "Biên Hòa" for c in res_bh.entities.get("unit", ()))

    # sg -> Hồ Chí Minh
    res_sg = normalizer.normalize("khách hàng sg phàn nàn")
    assert any(c.value == "Hồ Chí Minh" for c in res_sg.entities.get("unit", ()))

    # hn -> Hà Nội (province)
    res_hn = normalizer.normalize("đơn vị hn phản hồi")
    assert any(c.value == "Hà Nội" for c in res_hn.entities.get("province", ()))


def test_single_token_labels_recognized():
    normalizer = make_normalizer()

    # HTPP
    res_htpp = normalizer.normalize("vấn đề về HTPP gần đây")
    labels_htpp = [c.value for c in res_htpp.entities.get("label", ())]
    assert "HTPP" in labels_htpp

    # Hãng
    res_hang = normalizer.normalize("thông tin về Hãng đối thủ")
    labels_hang = [c.value for c in res_hang.entities.get("label", ())]
    assert "Hãng" in labels_hang

    # Khác
    res_khac = normalizer.normalize("phân loại nhóm Khác")
    labels_khac = [c.value for c in res_khac.entities.get("label", ())]
    assert "Khác" in labels_khac


def test_all_21_labels_and_8_major_groups_in_synonyms():
    synonyms = load_synonyms()
    canonical = synonym_canonical_values(synonyms)

    # 21/21 labels exist in canonical["label"]
    labels = canonical.get("label", set())
    for label in CLASSIFICATION_LABELS:
        assert label in labels, f"Nhãn {label} thiếu trong synonyms_vi.json"
    assert len(labels) == 21

    # 8 major groups exist in canonical["major_group"]
    groups = canonical.get("major_group", set())
    expected_groups = set(MAJOR_GROUPS.values())
    for group in expected_groups:
        assert group in groups, f"Nhóm {group} thiếu trong synonyms_vi.json"
    assert len(groups) == 8


def test_text_match_prefix_infix_and_token_subset():
    products = [
        "Đèn LED âm trần Downlight AT04 9W",
        "Đèn LED bàn học RD-RL-20",
    ]

    # Prefix match: "đèn led"
    prefix_matches = find_candidates("phản hồi đèn led", products, threshold=0.88)
    assert len(prefix_matches) == 2
    assert all(m.score >= 0.88 for m in prefix_matches)

    # Infix match: "âm trần"
    infix_matches = find_candidates("lỗi âm trần", products, threshold=0.88)
    assert len(infix_matches) == 1
    assert infix_matches[0].value == "Đèn LED âm trần Downlight AT04 9W"

    # Infix match: "bàn học"
    infix_matches_2 = find_candidates("hỏng bàn học", products, threshold=0.88)
    assert len(infix_matches_2) == 1
    assert infix_matches_2[0].value == "Đèn LED bàn học RD-RL-20"

    # Token subset match: "đèn downlight"
    subset_matches = find_candidates("phản hồi đèn downlight", products, threshold=0.88)
    assert len(subset_matches) >= 1
    assert subset_matches[0].value == "Đèn LED âm trần Downlight AT04 9W"
    assert subset_matches[0].score >= 0.88

    # mention_score supports token subset
    assert mention_score("đèn downlight", "Đèn LED âm trần Downlight AT04 9W") >= 0.88


def test_query_cache_invalidation_on_revision_change():
    rev = [1]
    cache = QueryCache(default_ttl_seconds=300, revision_getter=lambda: rev[0])

    plan = QueryPlan(
        pattern=QueryPattern.SQL_TEMPLATE,
        answer_shape=AnswerShape.NUMBER,
        original_query="Tổng quan",
        function_name="overview",
    )
    scope = UserScope(username="admin", role="admin")

    # Set cache at revision 1
    cache.set(plan, scope, QueryResult.ok(data=[{"total": 42}]))
    cached = cache.get(plan, scope)
    assert cached is not None
    assert cached.data == [{"total": 42}]

    # Invalidate by updating revision to 2
    rev[0] = 2
    assert cache.get(plan, scope) is None
    assert cache.stats()["size"] == 0


def test_cached_metadata_provider_invalidation_on_revision_change():
    class DynamicMetadata:
        def __init__(self):
            self.calls = 0

        def valid_values(self):
            self.calls += 1
            return {"units": [f"Unit_{self.calls}"]}

    inner = DynamicMetadata()
    rev = [1]
    cached_provider = CachedMetadataProvider(inner, revision_getter=lambda: rev[0])

    val1 = cached_provider.valid_values()
    assert val1 == {"units": ["Unit_1"]}
    assert inner.calls == 1

    # Calling again at same revision -> cache hit
    val2 = cached_provider.valid_values()
    assert val2 == {"units": ["Unit_1"]}
    assert inner.calls == 1

    # Revision increments -> invalidates and reloads
    rev[0] = 2
    val3 = cached_provider.valid_values()
    assert val3 == {"units": ["Unit_2"]}
    assert inner.calls == 2


def test_cached_term_stats_invalidation_on_revision_change():
    class FakeTermStats:
        def __init__(self, token: str, score: float):
            self.token = token
            self.score = score

        def idf(self, token: str) -> float | None:
            return self.score if token == self.token else None

    calls = [0]

    def loader():
        calls[0] += 1
        return FakeTermStats(f"token_{calls[0]}", 1.5)

    rev = [1]
    cached_stats = CachedTermStats(loader, revision_getter=lambda: rev[0])

    assert cached_stats.idf("token_1") == 1.5
    assert calls[0] == 1

    # Same revision -> cache hit
    assert cached_stats.idf("token_1") == 1.5
    assert calls[0] == 1

    # Increment revision -> invalidates and calls loader again
    rev[0] = 2
    assert cached_stats.idf("token_2") == 1.5
    assert calls[0] == 2
