"""
test_pipeline.py — Chạy 1 poll cycle để test toàn bộ pipeline end-to-end.

Chạy: python scripts/test_pipeline.py

Lưu ý: Chỉ xử lý file chưa có trong seen_files.json.
Nếu muốn test lại 1 file cụ thể, dùng --file <tên file>.
"""
import sys, os, argparse, json
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, ".")

from dotenv import load_dotenv
load_dotenv()

from config import logger, SEEN_FILES_PATH
from watcher import _load_seen, _save_seen, poll_once


def main():
    parser = argparse.ArgumentParser(description="Test pipeline with 1 poll cycle")
    parser.add_argument("--force-file", metavar="NAME",
                        help="Xóa file này khỏi seen để buộc xử lý lại")
    args = parser.parse_args()

    logger.info("=" * 60)
    logger.info("PIPELINE TEST — single poll cycle")
    logger.info("=" * 60)

    seen = _load_seen()
    logger.info("Loaded %d seen files", len(seen))

    # Nếu chỉ định --force-file, tạm xóa file đó khỏi seen
    if args.force_file:
        removed = [k for k, v in seen.items() if v.get("name") == args.force_file]
        if removed:
            for k in removed:
                del seen[k]
            logger.info("Removed '%s' from seen → will reprocess", args.force_file)
        else:
            logger.warning("File '%s' not found in seen_files.json", args.force_file)

    processed = poll_once(seen)

    logger.info("=" * 60)
    if processed > 0:
        logger.info("✅ Processed %d file(s)", processed)
        print(f"\n>>> PIPELINE TEST PASSED! ({processed} file processed) <<<")
    else:
        logger.info("ℹ️  No files processed (all already done, or no new files)")
        print("\n>>> No files processed — check seen_files.json or SharePoint Input/ <<<")


if __name__ == "__main__":
    main()
