"""Read feedback workbooks with stable source rows and metadata aliases."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import numpy as np
import pandas as pd
from unidecode import unidecode

from .models import FeedbackInputRecord, ParsedFeedbackWorkbook

TEXT_ALIASES = [
    "nội dung",
    "noi dung",
    "nội dung vấn đề",
    "noi dung van de",
    "nội dung phản hồi",
    "noi dung phan hoi",
]

METADATA_ALIASES = {
    "issue_code": ("Mã vấn đề", "Ma van de"),
    "issue_date": ("Ngày ghi nhận", "Ngày", "Date"),
    "source": ("Nguồn", "Source"),
    "unit_name": ("Tên đơn vị", "Đơn vị", "Unit"),
    "business_status": ("Trạng thái", "Status"),
}


def _canon_lower(s: str) -> str:
    return re.sub(r"\s+", " ", unidecode(str(s or "")).lower().strip())


def normalize_text(value: object) -> str | None:
    value = "" if pd.isna(value) else str(value).strip()
    return value or None


def normalize_duplicate_content(value: str) -> str:
    return re.sub(r"\s+", " ", value).casefold().strip()


def _is_numeric_like(value: object) -> bool:
    if value is None:
        return True
    text = str(value).strip()
    if not text:
        return True
    return bool(re.fullmatch(r"[\d\-\./:, ]+", text))


def _score_textiness(values: list[object]) -> float:
    text_values = [str(value) for value in values if str(value).strip()]
    if not text_values:
        return -1.0
    lengths = [len(value) for value in text_values]
    return float(
        (np.mean(lengths) if lengths else 0) * 0.7
        + np.mean([1.0 if " " in value else 0.0 for value in text_values]) * 20
        + np.mean([0.0 if _is_numeric_like(value) else 1.0 for value in text_values]) * 30
    )


def _detect_header_and_textcol_with_row(
    raw_df: pd.DataFrame, scan_rows: int = 10
) -> tuple[pd.DataFrame, str, int | None]:
    """Return normalized rows, text column, and zero-based header row when found."""
    n_scan = min(scan_rows, len(raw_df))
    best_row_idx, best_col_idx = None, None
    for row_idx in range(n_scan):
        for col_idx, value in enumerate(raw_df.iloc[row_idx, :].tolist()):
            canonical = _canon_lower(value)
            if any(
                alias in canonical and len(canonical) <= len(alias) + 20 for alias in TEXT_ALIASES
            ):
                best_row_idx, best_col_idx = row_idx, col_idx
                break
        if best_row_idx is not None:
            break

    if best_row_idx is not None:
        header_values = [
            str(value).strip() if str(value).strip() else f"col_{index}"
            for index, value in enumerate(raw_df.iloc[best_row_idx, :].tolist())
        ]
        dataframe = raw_df.iloc[best_row_idx + 1 :, :].copy()
        dataframe.columns = header_values
        text_column = next(
            (
                column
                for column in dataframe.columns
                if any(alias in _canon_lower(column) for alias in TEXT_ALIASES)
            ),
            dataframe.columns[best_col_idx],
        )
        return dataframe.reset_index(drop=True), text_column, best_row_idx

    dataframe = raw_df.copy().reset_index(drop=True)
    dataframe.columns = [f"col_{index}" for index in range(dataframe.shape[1])]
    scores = {
        index: -1.0
        if all(
            (str(value).strip() == "" or pd.isna(value)) for value in dataframe.iloc[:n_scan, index]
        )
        else _score_textiness(dataframe.iloc[:n_scan, index].tolist())
        for index in range(dataframe.shape[1])
    }
    best_index = max(scores, key=lambda index: scores[index]) if scores else 0
    return dataframe.copy(), dataframe.columns[best_index], None


def detect_header_and_textcol(
    raw_df: pd.DataFrame, scan_rows: int = 10
) -> tuple[pd.DataFrame, str]:
    """Auto-detect the header row and main text column."""
    dataframe, text_column, _ = _detect_header_and_textcol_with_row(raw_df, scan_rows)
    return dataframe, text_column


def _find_metadata_columns(columns: list[str]) -> dict[str, str | None]:
    return {
        field: next(
            (
                column
                for column in columns
                if any(_canon_lower(alias) == _canon_lower(column) for alias in aliases)
            ),
            None,
        )
        for field, aliases in METADATA_ALIASES.items()
    }


def _parse_issue_date(value: object) -> str | None:
    if normalize_text(value) is None:
        return None
    parsed = pd.to_datetime(value, dayfirst=True, errors="coerce")
    return None if pd.isna(parsed) else parsed.date().isoformat()


def read_feedback_workbook(input_path: Path) -> ParsedFeedbackWorkbook:
    raw_dataframe = pd.read_excel(input_path, header=None, dtype=object)
    dataframe, text_column, header_row_index = _detect_header_and_textcol_with_row(raw_dataframe)
    metadata_columns = _find_metadata_columns(list(dataframe.columns))
    first_source_row = 1 if header_row_index is None else header_row_index + 2
    source_row_numbers = list(range(first_source_row, first_source_row + len(dataframe)))
    records = []
    for source_row_number, (_, row) in zip(source_row_numbers, dataframe.iterrows(), strict=True):
        raw_data = {
            str(column): None if pd.isna(value) else str(value) for column, value in row.items()
        }
        content = normalize_text(row[text_column]) or ""
        metadata = {
            field: normalize_text(row[column]) if column is not None else None
            for field, column in metadata_columns.items()
        }
        records.append(
            FeedbackInputRecord(
                source_row_number=source_row_number,
                raw_data=raw_data,
                content=content,
                normalized_content=normalize_duplicate_content(content),
                issue_code=metadata["issue_code"],
                issue_date=_parse_issue_date(row[metadata_columns["issue_date"]])
                if metadata_columns["issue_date"] is not None
                else None,
                source=metadata["source"],
                unit_name=metadata["unit_name"],
                business_status=metadata["business_status"],
            )
        )
    return ParsedFeedbackWorkbook(dataframe, text_column, source_row_numbers, records)


def sha256_file(input_path: Path) -> str:
    digest = hashlib.sha256()
    with input_path.open("rb") as file_handle:
        for chunk in iter(lambda: file_handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
