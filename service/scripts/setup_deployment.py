"""
setup_deployment.py — Script khởi tạo môi trường khi deploy lên máy ảo mới.

Chức năng:
  1. Đánh dấu tất cả file hiện có trên SharePoint Input/ là 'done'
     → Service sẽ chỉ xử lý file MỚI được upload sau khi deploy
  2. Tạo thư mục work/ cần thiết
  3. Kiểm tra các file cấu hình bắt buộc

Chạy DUY NHẤT 1 LẦN khi deploy lên máy ảo mới:
  python scripts/setup_deployment.py

Nếu muốn xử lý lại toàn bộ file cũ, thêm flag:
  python scripts/setup_deployment.py --process-all
"""
import sys, os, json, argparse
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, ".")

from dotenv import load_dotenv
load_dotenv()

from datetime import datetime
from pathlib import Path
from config import (
    WORK_DIR, SEEN_FILES_PATH, KEYWORD_DIR, DATA_DIR,
    DF_PRODUCTS_PATH, GCP_SERVICE_ACCOUNT_JSON, logger
)


def check_required_files():
    """Kiểm tra các file bắt buộc phải có trước khi chạy service."""
    print("\n📋 Checking required files...")
    errors = []

    checks = [
        (".env", Path(".env"), "File cấu hình môi trường"),
        ("Service Account JSON", Path(GCP_SERVICE_ACCOUNT_JSON) if GCP_SERVICE_ACCOUNT_JSON else None, "GCP credentials"),
        ("Product Catalog", DF_PRODUCTS_PATH, "Catalog sản phẩm cho RAG"),
    ]

    for name, path, desc in checks:
        if path is None:
            print(f"  ⚠️  {name}: GCP_SERVICE_ACCOUNT_JSON chưa được cấu hình trong .env")
            errors.append(name)
        elif path.exists():
            size_kb = path.stat().st_size // 1024
            print(f"  ✅ {name}: {path} ({size_kb} KB)")
        else:
            print(f"  ❌ {name}: {path} — KHÔNG TÌM THẤY")
            errors.append(name)

    return errors


def create_work_dirs():
    """Tạo các thư mục làm việc cần thiết."""
    print("\n📁 Creating work directories...")
    dirs = [
        WORK_DIR,
        WORK_DIR / "input",
        WORK_DIR / "output",
        WORK_DIR / "checkpoint",
    ]
    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)
        print(f"  ✅ {d}")


def seed_seen_files(process_all: bool = False):
    """Đánh dấu file cũ là done (hoặc bỏ qua nếu --process-all)."""
    from sharepoint import list_input_files

    print("\n☁️  Connecting to SharePoint...")
    try:
        files = list_input_files()
    except Exception as e:
        print(f"  ❌ Cannot connect to SharePoint: {e}")
        return False

    print(f"  Found {len(files)} file(s) in Input/")

    if process_all:
        print("  ℹ️  --process-all flag set → NOT seeding seen_files (will process everything)")
        # Tạo file trống
        SEEN_FILES_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(SEEN_FILES_PATH, "w", encoding="utf-8") as f:
            json.dump({}, f)
        print(f"  ✅ Empty seen_files.json created at {SEEN_FILES_PATH}")
        return True

    # Load existing seen (nếu có)
    existing_seen = {}
    if SEEN_FILES_PATH.exists():
        with open(SEEN_FILES_PATH, "r", encoding="utf-8") as f:
            existing_seen = json.load(f)
        print(f"  ℹ️  Found existing seen_files.json ({len(existing_seen)} entries)")

    # Đánh dấu tất cả file hiện tại là done
    seen = dict(existing_seen)
    newly_seeded = 0
    for f in files:
        if f["id"] not in seen:
            seen[f["id"]] = {
                "name": f["name"],
                "status": "done",
                "processed_at": datetime.now().isoformat(),
                "note": "pre-seeded on deployment",
            }
            newly_seeded += 1

    SEEN_FILES_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(SEEN_FILES_PATH, "w", encoding="utf-8") as f:
        json.dump(seen, f, ensure_ascii=False, indent=2)

    done_count = sum(1 for v in seen.values() if v.get("status") == "done")
    print(f"  ✅ Saved seen_files.json: {len(seen)} entries ({done_count} done, {newly_seeded} newly seeded)")
    print(f"  📍 Location: {SEEN_FILES_PATH}")
    print(f"\n  Service sẽ chỉ xử lý file được upload SAU thời điểm này.")
    return True


def main():
    parser = argparse.ArgumentParser(
        description="Setup deployment — seed seen_files and verify environment"
    )
    parser.add_argument(
        "--process-all",
        action="store_true",
        help="Không seed seen_files → xử lý toàn bộ file cũ (dùng khi muốn reprocess)"
    )
    args = parser.parse_args()

    print("=" * 60)
    print("DMS Feedback Classification — Deployment Setup")
    print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    # Step 1: Check required files
    errors = check_required_files()
    if errors:
        print(f"\n❌ Missing required files: {errors}")
        print("   Fix these before running the service!")
        sys.exit(1)

    # Step 2: Create work directories
    create_work_dirs()

    # Step 3: Seed seen_files.json
    ok = seed_seen_files(process_all=args.process_all)
    if not ok:
        print("\n⚠️  SharePoint seeding failed — service will process ALL files on first run")

    print("\n" + "=" * 60)
    print("✅ Setup complete! Ready to run:")
    print("   Docker: docker-compose up -d")
    print("   Local:  python watcher.py")
    print("=" * 60)


if __name__ == "__main__":
    main()
