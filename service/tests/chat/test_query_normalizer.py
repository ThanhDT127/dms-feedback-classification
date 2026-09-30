from __future__ import annotations

from datetime import UTC, datetime

from dms.chat.ai.date_resolver import DateResolver
from dms.chat.ai.metadata_adapter import AnalyticsMetadataAdapter
from dms.chat.ai.query_normalizer import (
    KNOWN_CANONICAL_VALUES,
    CachedMetadataProvider,
    QueryNormalizer,
    load_synonyms,
    synonym_canonical_values,
)

from .ai_fakes import FixedClock, StaticMetadataProvider

CLOCK = FixedClock(datetime(2026, 9, 15, 3, 0, tzinfo=UTC))


def make_normalizer(values=None) -> QueryNormalizer:
    return QueryNormalizer(
        StaticMetadataProvider(values),
        date_resolver=DateResolver(CLOCK),
        fuzzy_threshold=0.88,
    )


def values_of(result, dimension: str) -> list[str]:
    return [c.value for c in result.entities.get(dimension, ())]


def test_match_text_keeps_original():
    text = "Phản hồi về Rạng  Đông"
    result = make_normalizer().normalize(text)
    assert result.match_text == "phan hoi ve rang dong"
    assert text == "Phản hồi về Rạng  Đông"


def test_complaint_synonym_is_negative_sentiment_and_date_resolved():
    result = make_normalizer().normalize("Có bao nhiêu khách phàn nàn tháng 8?")
    assert values_of(result, "sentiment") == ["Tiêu cực"]
    assert result.dates.date_range is not None
    assert result.dates.date_range.date_from.isoformat() == "2026-08-01"


def test_longer_phrase_wins_for_negation():
    result = make_normalizer().normalize("khách không hài lòng về đèn")
    assert values_of(result, "sentiment") == ["Tiêu cực"]


def test_label_synonym():
    result = make_normalizer().normalize("đèn bị hỏng sau 2 ngày")
    assert "Báo lỗi" in values_of(result, "label")


def test_full_label_name_is_recognised():
    result = make_normalizer().normalize("phản hồi về hàng giả")
    assert "Hàng giả" in values_of(result, "label")


def test_region_word_hints_province_dimension():
    result = make_normalizer().normalize("Khu vực nào phản hồi nhiều nhất?")
    assert "province" in result.dimension_hints


def test_hint_exclusions_avoid_false_dimensions():
    result = make_normalizer().normalize("tình trạng xử lý của nhân viên")
    assert "province" not in result.dimension_hints
    assert "label" not in result.dimension_hints


def test_unaccented_province_candidate():
    result = make_normalizer().normalize("phan hoi o ha noi")
    candidates = result.entities["province"]
    assert candidates[0].value == "Hà Nội"
    assert candidates[0].score >= 0.88


def test_multiple_unit_candidates_are_kept():
    values = {"units": ["Truyền thống Vùng 1", "Vùng 1 Duyên hải", "Nha Trang"]}
    result = make_normalizer(values).normalize("tổng quan vùng 1 tháng 8")
    assert set(values_of(result, "unit")) == {"Truyền thống Vùng 1", "Vùng 1 Duyên hải"}


def test_at_most_three_candidates_per_dimension():
    values = {"products": ["Đèn LED A", "Đèn LED B", "Đèn LED C", "Đèn LED D"]}
    normalizer = QueryNormalizer(
        StaticMetadataProvider(values), date_resolver=DateResolver(CLOCK), fuzzy_threshold=0.8
    )
    result = normalizer.normalize("phản hồi đèn led")
    assert len(result.entities.get("product", ())) <= 3


def test_synonym_canonical_values_exist_in_contract():
    canonical = synonym_canonical_values(load_synonyms())
    for dimension, values in canonical.items():
        assert dimension in KNOWN_CANONICAL_VALUES
        assert values <= KNOWN_CANONICAL_VALUES[dimension], (
            values - KNOWN_CANONICAL_VALUES[dimension]
        )


def test_cached_metadata_provider_respects_ttl():
    inner = StaticMetadataProvider()
    now = [0.0]
    cached = CachedMetadataProvider(inner, ttl_seconds=300, now=lambda: now[0])
    cached.valid_values()
    now[0] = 299
    cached.valid_values()
    assert inner.calls == 1
    now[0] = 300
    cached.valid_values()
    assert inner.calls == 2


def test_metadata_adapter_maps_service_options():
    class FakeService:
        def filter_options(self, analytics_filter):
            return {"units": ["Nha Trang"], "provinces": ["Khánh Hòa"], "districts": ["Cam Ranh"]}

        def issue_filter_options(self, analytics_filter):
            return {"units": ["Nha Trang"], "products": ["Đèn LED"], "statuses": ["Đã xử lý"]}

    values = AnalyticsMetadataAdapter(FakeService()).valid_values()  # type: ignore[arg-type]
    assert values == {
        "units": ["Nha Trang"],
        "provinces": ["Khánh Hòa"],
        "districts": ["Cam Ranh"],
        "products": ["Đèn LED"],
        "statuses": ["Đã xử lý"],
    }
