"""Typed values shared by feedback analytics readers and consumers."""

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class FeedbackInputRecord:
    source_row_number: int
    raw_data: dict[str, str | None]
    content: str
    normalized_content: str
    issue_code: str | None
    issue_date: str | None
    source: str | None
    unit_name: str | None
    business_status: str | None


@dataclass(frozen=True)
class BatchClassificationResult:
    source_row_number: int
    text: str
    product: str | None
    product_line: str | None
    model: str | None
    bm25_score: float | None
    sentiment: str | None
    labels: list[str]
    brand: str | None


@dataclass(frozen=True)
class AnalyticsFilter:
    date_from: str | None = None
    date_to: str | None = None
    compare_from: str | None = None
    compare_to: str | None = None
    province: str | None = None
    district: str | None = None


@dataclass(frozen=True)
class ParsedFeedbackWorkbook:
    dataframe: pd.DataFrame
    text_column: str
    source_row_numbers: list[int]
    records: list[FeedbackInputRecord]
