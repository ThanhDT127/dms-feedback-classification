"""Tests for SharePoint real data sync and ingestion."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pandas as pd

from dms.analytics.repository import FeedbackAnalyticsRepository
from dms.sharepoint_sync import (
    SharePointSyncService,
    SyncStats,
    parse_output_results,
)


def _create_sample_output_excel(path: Path) -> None:
    """Create a 2-row header output workbook like the pipeline produces."""
    # Row 0: Major categories and subheaders
    # Row 1: Column headers
    # Row 2+: Data
    data = [
        # Row 0:
        [
            "",
            "",
            "",
            "",
            "",
            "",
            "Báo lỗi",
            "Báo CL tốt",
            "Y/c cải tiến",
            "Đề xuất SPM",
            "Bảng giá, Catalogue",
            "Bảng biển",
            "Kệ bóng, thử đèn,…",
            "Khác",
            "Tốt/ ko tốt",
            "Trả thưởng",
            "Đề xuất",
            "Bảo hành",
            "HTPP",
            "Hàng hoá",
            "Hàng giả",
            "Website",
            "Hãng",
            "Hoạt động",
            "CTKM, giá, cơ chế",
            "TT SP",
            "Tin trung lập",
            "",
            "",
        ],
        # Row 1:
        [
            "Mã vấn đề",
            "Ngày",
            "Tên đơn vị",
            "Sản phẩm",
            "Dòng SP",
            "Model",
            "Sản phẩm",
            "Sản phẩm",
            "Sản phẩm",
            "Sản phẩm",
            "Yêu cầu",
            "Yêu cầu",
            "Yêu cầu",
            "Yêu cầu",
            "Giá",
            "Giá",
            "Giá",
            "Dịch vụ",
            "Dịch vụ",
            "Dịch vụ",
            "Hàng giả",
            "Website",
            "Đối thủ",
            "Đối thủ",
            "Đối thủ",
            "Đối thủ",
            "Tin trung lập",
            "Sentiment",
            "BM25_Score",
        ],
        # Row 2 (Record 1):
        [
            "10001",
            "2026-03-01",
            "Đơn vị A",
            "Đèn LED Bulb",
            "LED-A60",
            "A60/9W",
            "x",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "Tiêu cực",
            "0.85",
        ],
        # Row 3 (Record 2):
        [
            "10002",
            "2026-03-02",
            "Đơn vị B",
            "Phích nước",
            "RD-1040",
            "1040 ST1",
            "",
            "x",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "Tích cực",
            "0.92",
        ],
    ]
    df = pd.DataFrame(data)
    df.to_excel(path, header=False, index=False)


def test_parse_output_results(tmp_path: Path):
    excel_path = tmp_path / "test_output.xlsx"
    _create_sample_output_excel(excel_path)

    code_to_row = {"10001": 5, "10002": 6}
    results = parse_output_results(excel_path, code_to_row)

    assert len(results) == 2
    r1 = results[0]
    assert r1.source_row_number == 5
    assert r1.product == "Đèn LED Bulb"
    assert r1.product_line == "LED-A60"
    assert r1.model == "A60/9W"
    assert r1.sentiment == "Tiêu cực"
    assert r1.bm25_score == 0.85
    assert "Báo lỗi" in r1.labels

    r2 = results[1]
    assert r2.source_row_number == 6
    assert r2.product == "Phích nước"
    assert r2.sentiment == "Tích cực"
    assert "Báo CL tốt" in r2.labels


def test_download_folder_files_skips_existing(tmp_path: Path):
    mock_client = MagicMock()
    mock_settings = MagicMock()
    mock_repo = MagicMock()

    local_dir = tmp_path / "input"
    local_dir.mkdir(parents=True, exist_ok=True)

    # Pre-create an existing file
    existing_file = local_dir / "existing.xlsx"
    existing_file.write_bytes(b"12345")

    mock_client.list_folder_items.return_value = [
        {"id": "id-1", "name": "existing.xlsx", "size": 5},
        {"id": "id-2", "name": "new.xlsx", "size": 10},
    ]

    service = SharePointSyncService(
        sp_client=mock_client,
        settings=mock_settings,
        analytics_repo=mock_repo,
    )
    stats = SyncStats()

    paths = service.download_folder_files("Input", local_dir, stats, is_input=True)

    assert len(paths) == 2
    assert stats.skipped_inputs == 1
    assert stats.downloaded_inputs == 1
    # download_file should only have been called for id-2
    mock_client.download_file.assert_called_once_with("id-2", local_dir / "new.xlsx")


def test_ingest_local_files_with_output(tmp_path: Path):
    db_path = tmp_path / "jobs.db"
    repo = FeedbackAnalyticsRepository(db_path)

    work_dir = tmp_path / "work"
    input_dir = work_dir / "input"
    output_dir = work_dir / "output"
    input_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Create input excel
    in_path = input_dir / "sample_data.xlsx"
    in_df = pd.DataFrame(
        {
            "Mã vấn đề": ["10001", "10002"],
            "Ngày": ["2026-03-01", "2026-03-02"],
            "Tên đơn vị": ["Đơn vị A", "Đơn vị B"],
            "Nội dung vấn đề": ["Đèn hỏng không sáng", "Phích giữ nhiệt tốt"],
        }
    )
    in_df.to_excel(in_path, index=False)

    # Create output excel
    out_path = output_dir / "sample_data_output.xlsx"
    _create_sample_output_excel(out_path)

    mock_client = MagicMock()
    mock_settings = MagicMock()
    mock_settings.work_dir = work_dir

    service = SharePointSyncService(
        sp_client=mock_client,
        settings=mock_settings,
        analytics_repo=repo,
    )
    stats = SyncStats()
    service.ingest_local_files(stats)

    assert stats.ingested_files == 1
    assert stats.classified_files == 1

    # Check repository rows
    rows = repo.fetch_analytics_rows()
    assert len(rows) == 2
    codes = {r["issue_code"]: r for r in rows}
    assert "10001" in codes
    assert codes["10001"]["classification_state"] == "completed"
    assert codes["10001"]["product"] == "Đèn LED Bulb"
    assert codes["10001"]["sentiment"] == "Tiêu cực"

    assert "10002" in codes
    assert codes["10002"]["classification_state"] == "completed"
    assert codes["10002"]["product"] == "Phích nước"
