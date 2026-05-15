"""Initialize directories and seed seen_files for a fresh deployment."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, "src")

from dms.auth import AuthProvider
from dms.http_client import create_session
from dms.settings import get_settings
from dms.sharepoint import SharePointClient

settings = get_settings()
sharepoint = SharePointClient(
    auth=AuthProvider(settings),
    settings=settings,
    session=create_session(default_timeout=settings.http_timeout_seconds),
)


def check_required_files():
    print("\nChecking required files...")
    errors = []
    checks = [
        (".env", Path(".env")),
        (
            "Service Account JSON",
            Path(settings.gcp_service_account_json) if settings.gcp_service_account_json else None,
        ),
        ("Product Catalog", settings.df_products_path),
    ]
    for name, path in checks:
        if path is None:
            print(f"  {name}: not configured")
            errors.append(name)
        elif path.exists():
            size_kb = path.stat().st_size // 1024
            print(f"  OK {name}: {path} ({size_kb} KB)")
        else:
            print(f"  MISSING {name}: {path}")
            errors.append(name)
    return errors


def create_work_dirs():
    print("\nCreating work directories...")
    for directory in (
        settings.work_dir,
        settings.work_dir / "input",
        settings.work_dir / "output",
        settings.work_dir / "checkpoint",
    ):
        directory.mkdir(parents=True, exist_ok=True)
        print(f"  OK {directory}")


def seed_seen_files(process_all: bool = False):
    print("\nConnecting to SharePoint...")
    try:
        files = sharepoint.list_files()
    except Exception as exc:
        print(f"  Cannot connect to SharePoint: {exc}")
        return False

    print(f"  Found {len(files)} file(s) in Input/")

    if process_all:
        settings.seen_files_path.parent.mkdir(parents=True, exist_ok=True)
        settings.seen_files_path.write_text("{}", encoding="utf-8")
        print(f"  Empty seen_files.json created at {settings.seen_files_path}")
        return True

    existing_seen = {}
    if settings.seen_files_path.exists():
        existing_seen = json.loads(settings.seen_files_path.read_text(encoding="utf-8"))
        print(f"  Found existing seen_files.json ({len(existing_seen)} entries)")

    seen = dict(existing_seen)
    newly_seeded = 0
    for file_info in files:
        if file_info["id"] not in seen:
            seen[file_info["id"]] = {
                "name": file_info["name"],
                "status": "done",
                "processed_at": datetime.now().isoformat(),
                "note": "pre-seeded on deployment",
            }
            newly_seeded += 1

    settings.seen_files_path.parent.mkdir(parents=True, exist_ok=True)
    settings.seen_files_path.write_text(
        json.dumps(seen, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    done_count = sum(1 for value in seen.values() if value.get("status") == "done")
    print(
        f"  Saved seen_files.json: {len(seen)} entries "
        f"({done_count} done, {newly_seeded} newly seeded)"
    )
    print(f"  Location: {settings.seen_files_path}")
    return True


def main():
    parser = argparse.ArgumentParser(description="Setup deployment and seed seen_files")
    parser.add_argument(
        "--process-all",
        action="store_true",
        help="Do not seed seen_files; process all existing files",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("DMS Feedback Classification - Deployment Setup")
    print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    errors = check_required_files()
    if errors:
        print(f"\nMissing required files: {errors}")
        sys.exit(1)

    create_work_dirs()
    ok = seed_seen_files(process_all=args.process_all)
    if not ok:
        print("\nSharePoint seeding failed - service may process all files on first run")

    print("\n" + "=" * 60)
    print("Setup complete! Ready to run:")
    print("  Docker: docker-compose up -d")
    print("  Local:  python -m dms")
    print("=" * 60)
