"""Adapter TẠM THỜI cho ``MetadataProvider`` (design b01 D9).

Đọc giá trị hợp lệ từ ``FeedbackAnalyticsService``. Sẽ được thay bằng MetadataCache của
Dev A (review C09) khi có; ``filter_options()`` nạp toàn bộ dòng nên phải bọc
``CachedMetadataProvider``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from ...analytics.models import AnalyticsFilter
from ...analytics.service import FeedbackAnalyticsService


class AnalyticsMetadataAdapter:
    def __init__(self, service: FeedbackAnalyticsService) -> None:
        self._service = service

    def valid_values(self) -> Mapping[str, Sequence[str]]:
        everything = AnalyticsFilter()
        geography = self._service.filter_options(everything)
        issue_options = self._service.issue_filter_options(everything)
        sources: list[str] = []
        try:
            with self._service.repository._conn() as conn:
                rows = conn.execute(
                    "SELECT DISTINCT source FROM feedback_records "
                    "WHERE is_active = 1 AND source IS NOT NULL AND trim(source) != '' "
                    "ORDER BY source"
                ).fetchall()
                sources = [str(r[0]).strip() for r in rows if r[0] and str(r[0]).strip()]
        except Exception:
            try:
                src_data = self._service.sources(everything)
                sources = [
                    str(item["label"]).strip()
                    for item in src_data.get("items", [])
                    if item.get("label")
                ]
            except Exception:
                sources = []
        return {
            "units": list(geography.get("units", [])),
            "provinces": list(geography.get("provinces", [])),
            "districts": list(geography.get("districts", [])),
            "products": list(issue_options.get("products", [])),
            "statuses": list(issue_options.get("statuses", [])),
            "sources": sources,
        }

    def raw_key_catalog(self) -> tuple[str, ...]:
        """Danh mục key thật trong ``raw_data_json`` cho Pattern 4 (b09 D9)."""
        return load_raw_key_catalog(self._service)


def load_document_frequencies(service: FeedbackAnalyticsService):
    """Bảng tần suất tài liệu cho độ đặc trưng của token FTS (design b08 D4)."""
    from .fts_query_builder import DocumentFrequencies

    with service.repository._conn() as conn:
        rows = conn.execute(
            "SELECT normalized_content FROM feedback_records WHERE is_active = 1"
        ).fetchall()
    return DocumentFrequencies.from_texts(str(row[0] or "") for row in rows)


RAW_KEY_SAMPLE_ROWS = 5000


def load_raw_key_catalog(service: FeedbackAnalyticsService) -> tuple[str, ...]:
    """Key cấp 1 của ``raw_data_json`` trên tối đa 5.000 dòng gần nhất (đủ phủ cột ổn định)."""
    with service.repository._conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT j.key FROM (SELECT raw_data_json FROM feedback_records "
            "WHERE is_active = 1 ORDER BY feedback_id DESC LIMIT ?) r, json_each(r.raw_data_json) j",
            (RAW_KEY_SAMPLE_ROWS,),
        ).fetchall()
    return tuple(sorted(str(row[0]) for row in rows if row[0]))
