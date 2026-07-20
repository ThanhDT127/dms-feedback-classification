"""Reconstruct seen_files.json and metrics.json from SharePoint history."""

from __future__ import annotations

import logging
import sys
import tempfile
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import requests

# Add src/ to PYTHONPATH
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import json

from dms.auth import AuthProvider
from dms.settings import get_settings
from dms.sharepoint import SharePointClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("reconstruct-history")


def main() -> None:
    settings = get_settings()
    settings.ensure_runtime_dirs()

    session = requests.Session()
    auth = AuthProvider(settings)
    sp_client = SharePointClient(auth, settings, session)

    logger.info("Connecting to SharePoint Graph API...")

    seen_path = settings.seen_files_path
    metrics_path = settings.metrics_path

    seen = {}
    if seen_path.is_file():
        try:
            seen = json.loads(seen_path.read_text(encoding="utf-8"))
            logger.info("Loaded %d files from local seen_files.json", len(seen))
        except Exception as exc:
            logger.warning("Failed to load local seen_files.json: %s", exc)

    # 1. Fetch input files metadata from SharePoint to restore lastModifiedDateTime
    logger.info("Fetching Input folder items from SharePoint...")
    try:
        input_files = sp_client.list_files()
        logger.info("Found %d files in SharePoint Input/", len(input_files))
        input_map = {f.get("name"): f for f in input_files if f.get("name")}
    except Exception as exc:
        logger.error("Failed to list SharePoint Input/ files: %s", exc)
        return

    updated_seen_count = 0
    for fid, entry in list(seen.items()):
        name = entry.get("name")
        if name in input_map:
            sp_f = input_map[name]
            last_mod = sp_f.get("lastModifiedDateTime", "")
            if last_mod and entry.get("lastModifiedDateTime") != last_mod:
                entry["lastModifiedDateTime"] = last_mod
                updated_seen_count += 1
        elif name:
            # Fallback check: if name matches but ID differs (e.g. self-healed files)
            for sp_name, sp_f in input_map.items():
                if sp_name.lower() == name.lower():
                    last_mod = sp_f.get("lastModifiedDateTime", "")
                    if last_mod and entry.get("lastModifiedDateTime") != last_mod:
                        entry["lastModifiedDateTime"] = last_mod
                        updated_seen_count += 1
                    break

    logger.info("Restored lastModifiedDateTime for %d files in seen_files.json", updated_seen_count)

    # 2. Reconstruct label distribution by downloading and scanning output files
    logger.info("Fetching Output folder items from SharePoint...")
    try:
        output_files = sp_client.list_folder_items(settings.sp_output_folder)
        logger.info("Found %d files in SharePoint Output/", len(output_files))
    except Exception as exc:
        logger.error("Failed to list SharePoint Output/ files: %s", exc)
        return

    import pandas as pd

    from dms.pipeline.issue_classifier import MINOR_ORDER

    label_distribution = defaultdict(int)
    scanned_count = 0

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        for out_f in output_files:
            name = out_f.get("name", "")
            if not name.endswith(".xlsx") or "folder" in out_f:
                continue

            logger.info("Downloading and scanning: %s", name)
            local_file = tmp_path / name
            try:
                sp_client.download_file(out_f["id"], local_file)
                # Read output file starting at header row 1
                df = pd.read_excel(local_file, header=1)
                for col in MINOR_ORDER:
                    if col in df.columns:
                        col_series = df[col].dropna()
                        count = sum(1 for val in col_series if str(val).strip() != "")
                        label_distribution[col] += count
                scanned_count += 1
            except Exception as exc:
                logger.warning("Failed to scan output file %s: %s", name, exc)

    logger.info("Scanned %d output files from SharePoint.", scanned_count)
    logger.info("Reconstructed label distribution: %s", dict(label_distribution))

    # 3. Reconstruct metrics
    metrics = {
        "start_time": datetime.now().isoformat(timespec="seconds"),
        "uptime_seconds": 0,
        "total_polls": 1,
        "files_processed": len(seen),
        "files_failed": 0,
        "files_skipped": 0,
        "label_distribution": dict(label_distribution),
        "success_rate_pct": 100.0,
    }

    # Save reconstructed state locally
    seen_path.write_text(json.dumps(seen, indent=2, ensure_ascii=False), encoding="utf-8")
    metrics_path.write_text(json.dumps(metrics, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("Saved local seen_files.json and metrics.json")

    # 4. Upload to SharePoint Check_Point/
    logger.info("Uploading states to SharePoint Check_Point/...")
    try:
        sp_client.upload_file(
            seen_path, settings.sp_checkpoint_folder, remote_filename="seen_files.json"
        )
        sp_client.upload_file(
            metrics_path, settings.sp_checkpoint_folder, remote_filename="metrics.json"
        )
        logger.info(
            "Successfully uploaded reconstructed seen_files.json and metrics.json to SharePoint!"
        )
    except Exception as exc:
        logger.error("Failed to upload states to SharePoint Check_Point/: %s", exc)


if __name__ == "__main__":
    main()
