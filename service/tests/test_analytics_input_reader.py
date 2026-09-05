from pathlib import Path

import pandas as pd

from dms.analytics.input_reader import read_feedback_workbook


def test_reader_keeps_source_rows_aliases_and_null_metadata(tmp_path: Path):
    """A reader regression must not discard physical row identity or fill metadata."""
    input_path = tmp_path / "feedback.xlsx"
    pd.DataFrame(
        [
            ["exported", None, None, None],
            ["Mã vấn đề", "Ngày", "Nguồn", "Nội dung phản hồi"],
            ["MA-1", "15/08/2026", "CRM", "Đèn không sáng"],
            [None, None, None, "Cần catalogue"],
        ]
    ).to_excel(input_path, index=False, header=False)

    parsed = read_feedback_workbook(input_path)

    assert parsed.source_row_numbers == [3, 4]
    assert parsed.records[0].issue_code == "MA-1"
    assert parsed.records[0].issue_date == "2026-08-15"
    assert parsed.records[1].source is None
    assert parsed.records[1].content == "Cần catalogue"


def test_reader_normalizes_duplicate_content_without_inventing_metadata(tmp_path: Path):
    """Normalization must preserve missing business metadata as null values."""
    input_path = tmp_path / "feedback.xlsx"
    pd.DataFrame({"Nội dung phản hồi": ["  Lỗi   ĐÈN "]}).to_excel(input_path, index=False)

    record = read_feedback_workbook(input_path).records[0]

    assert record.normalized_content == "lỗi đèn"
    assert record.issue_code is None
    assert record.source is None
