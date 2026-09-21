"""Nạp cặp file Excel input/output có sẵn vào DB analytics (không chạy lại AI).

Dùng khi đã có kết quả phân loại trong các file ``*_output.xlsx``: script đọc file input để
tạo bản ghi, rồi áp nhãn/cảm xúc/sản phẩm từ file output tương ứng.

Ví dụ:
    uv run python scripts/ingest_excel_archive.py --input-dir work/input --output-dir work/output
    uv run python scripts/ingest_excel_archive.py --dry-run
"""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path

from dms.analytics import FeedbackAnalyticsRepository
from dms.analytics.ingest import ingest_managed_workbook
from dms.analytics.input_reader import read_feedback_workbook, sha256_file
from dms.pipeline.issue_classifier import MINOR_TO_MAJOR
from dms.settings import Settings
from dms.sharepoint_sync import parse_output_results

logger = logging.getLogger("ingest-excel-archive")


@dataclass
class ArchiveStats:
    files: int = 0
    ingested_rows: int = 0
    classified_files: int = 0
    classified_rows: int = 0
    deactivated_rows: int = 0
    indexed_rows: int = 0
    without_output: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def find_output(input_path: Path, output_dir: Path) -> Path | None:
    """Output của pipeline là ``<tên input>_output.xlsx``; chấp nhận vài biến thể tên."""
    candidates = (
        output_dir / f"{input_path.stem}_output.xlsx",
        output_dir / f"{input_path.stem}.xlsx",
        output_dir / input_path.name,
    )
    return next((path for path in candidates if path.is_file()), None)


def ingest_pair(
    repository: FeedbackAnalyticsRepository,
    input_path: Path,
    output_path: Path | None,
    stats: ArchiveStats,
) -> None:
    result = ingest_managed_workbook(repository, input_path)
    stats.files += 1
    stats.ingested_rows += result.persisted_rows
    if output_path is None:
        stats.without_output.append(input_path.name)
        logger.info("%s: %d dòng (chưa có file output)", input_path.name, result.persisted_rows)
        return

    parsed = read_feedback_workbook(input_path)
    code_to_row = {
        str(record.issue_code): record.source_row_number
        for record in parsed.records
        if record.issue_code
    }
    results = parse_output_results(output_path, code_to_row)
    if not results:
        stats.without_output.append(input_path.name)
        logger.warning(
            "%s: không khớp được dòng nào với %s (thiếu 'Mã vấn đề'?)",
            input_path.name,
            output_path.name,
        )
        return

    job_id = f"analytics-ingest-{sha256_file(input_path)}"
    repository.apply_batch_results(
        job_id=job_id, results=results, minor_to_major=MINOR_TO_MAJOR
    )
    stats.classified_files += 1
    stats.classified_rows += len(results)
    logger.info(
        "%s: %d dòng, áp %d kết quả phân loại từ %s",
        input_path.name,
        result.persisted_rows,
        len(results),
        output_path.name,
    )


def deactivate_empty_rows(repository: FeedbackAnalyticsRepository) -> int:
    """Bỏ kích hoạt dòng trống (dòng phân cách ngay dưới header của mỗi file).

    Các dòng này không có nội dung, mã vấn đề hay đơn vị nên không bao giờ là phản hồi thật;
    để ``is_active = 1`` thì mọi thống kê đều lệch thêm một dòng cho mỗi file.
    """
    import sqlite3

    with sqlite3.connect(repository.db_path) as conn:
        cursor = conn.execute(
            """
            UPDATE feedback_records
            SET is_active = 0
            WHERE is_active = 1
              AND TRIM(COALESCE(content, '')) = ''
              AND issue_code IS NULL
              AND unit_name IS NULL
            """
        )
        return int(cursor.rowcount or 0)


def sync_chat_search_index(repository: FeedbackAnalyticsRepository) -> int:
    """Dựng lại chỉ mục FTS5 của chatbot sau khi dữ liệu thay đổi (b08)."""
    import sqlite3

    from dms.chat.db.migrations import apply_chat_migrations, sync_fts_index

    with sqlite3.connect(repository.db_path) as conn:
        apply_chat_migrations(conn)
        return sync_fts_index(conn)


def run(
    input_dir: Path,
    output_dir: Path,
    *,
    dry_run: bool = False,
    skip_cleanup: bool = False,
    skip_fts: bool = False,
) -> ArchiveStats:
    settings = Settings()
    stats = ArchiveStats()
    inputs = sorted(input_dir.glob("*.xlsx"))
    if not inputs:
        logger.warning("Không có file .xlsx nào trong %s", input_dir)
        return stats
    if dry_run:
        for path in inputs:
            output = find_output(path, output_dir)
            stats.files += 1
            if output is None:
                stats.without_output.append(path.name)
            logger.info("%s → %s", path.name, output.name if output else "(không có output)")
        return stats

    repository = FeedbackAnalyticsRepository(settings.classification_jobs_db_path)
    logger.info("DB: %s", settings.classification_jobs_db_path)
    for path in inputs:
        try:
            ingest_pair(repository, path, find_output(path, output_dir), stats)
        except Exception as exc:  # một file hỏng không dừng cả lô
            message = f"{path.name}: {type(exc).__name__}: {exc}"
            stats.errors.append(message)
            logger.error("Lỗi khi nạp %s", message)

    if not skip_cleanup:
        stats.deactivated_rows = deactivate_empty_rows(repository)
        logger.info("Bỏ kích hoạt %d dòng trống", stats.deactivated_rows)
    if not skip_fts:
        stats.indexed_rows = sync_chat_search_index(repository)
        logger.info("Đồng bộ %d bản ghi vào chỉ mục tìm kiếm của chat", stats.indexed_rows)
    return stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, default=Path("work/input"))
    parser.add_argument("--output-dir", type=Path, default=Path("work/output"))
    parser.add_argument("--dry-run", action="store_true", help="chỉ liệt kê cặp file")
    parser.add_argument(
        "--skip-cleanup", action="store_true", help="giữ nguyên các dòng trống trong DB"
    )
    parser.add_argument(
        "--skip-fts", action="store_true", help="không dựng lại chỉ mục tìm kiếm của chat"
    )
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.WARNING if args.quiet else logging.INFO,
        format="%(levelname)s %(message)s",
    )
    stats = run(
        args.input_dir.resolve(),
        args.output_dir.resolve(),
        dry_run=args.dry_run,
        skip_cleanup=args.skip_cleanup,
        skip_fts=args.skip_fts,
    )
    print(
        f"\nĐã xử lý {stats.files} file input, {stats.ingested_rows} dòng; "
        f"áp kết quả phân loại cho {stats.classified_files} file ({stats.classified_rows} dòng)."
    )
    if stats.deactivated_rows:
        print(f"Bỏ kích hoạt {stats.deactivated_rows} dòng trống.")
    if stats.indexed_rows:
        print(f"Chỉ mục tìm kiếm của chat: {stats.indexed_rows} bản ghi.")
    if stats.without_output:
        print(f"Không có/không khớp output ({len(stats.without_output)}): "
              + ", ".join(stats.without_output[:10]))
    if stats.errors:
        print(f"Lỗi ({len(stats.errors)}):")
        for message in stats.errors[:10]:
            print("  -", message)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
