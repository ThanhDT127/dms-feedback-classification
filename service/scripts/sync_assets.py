"""Tải config assets (kw_map.json, Excel, Model) từ SharePoint về local."""

from __future__ import annotations

import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, "src")

from dms.auth import AuthProvider
from dms.config_assets import ConfigAssetSyncer
from dms.http_client import create_session
from dms.settings import get_settings
from dms.sharepoint import SharePointClient

if __name__ == "__main__":
    print("=== Sync Config Assets từ SharePoint ===")
    print(f"Working dir: {os.getcwd()}")

    settings = get_settings()
    print(f"Keyword dir: {settings.keyword_dir}")
    print(f"Model dir:   {settings.model_dir}")

    auth = AuthProvider(settings)
    session = create_session()
    sp = SharePointClient(auth=auth, settings=settings, session=session)
    syncer = ConfigAssetSyncer(settings=settings, sharepoint=sp)

    print("\nĐang sync...")
    result = syncer.sync()

    print(f"\nKết quả: {result}")
    print(f"\nKiểm tra kw_map.json: ", end="")
    if settings.kw_map_path.is_file():
        size = settings.kw_map_path.stat().st_size
        print(f"OK ({size:,} bytes)")
    else:
        print("KHÔNG TÌM THẤY")
