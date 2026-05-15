"""Quick integration checks against the refactored package."""

from __future__ import annotations

import base64
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, "src")

from dms.auth import AuthProvider
from dms.gemini_client import GeminiClient
from dms.http_client import create_session
from dms.settings import get_settings
from dms.sharepoint import SharePointClient

settings = get_settings()
auth = AuthProvider(settings)
gemini = GeminiClient(settings)
sharepoint = SharePointClient(auth=auth, settings=settings, session=create_session())


def test_azure_auth() -> bool:
    print("\n" + "=" * 60)
    print("TEST 1: Azure AD Authentication (Client Credentials)")
    print("=" * 60)
    token = auth.get_access_token()
    parts = token.split(".")
    pad = parts[1] + "=" * (4 - len(parts[1]) % 4)
    payload = json.loads(base64.urlsafe_b64decode(pad))
    print(f"  App: {payload.get('app_displayname', '?')}")
    print(f"  Roles: {payload.get('roles', [])}")
    print(f"  Token: {token[:10]}...(truncated for security)")
    print("  OK")
    return True


def test_gemini() -> bool:
    print("\n" + "=" * 60)
    print("TEST 2: Gemini LLM (Vertex AI)")
    print("=" * 60)
    resp = gemini.generate("Trả lời đúng 1 từ: 1+1=?")
    print(f"  Response: {resp.strip()}")
    print("  OK")
    return True


def test_sharepoint() -> bool:
    print("\n" + "=" * 60)
    print("TEST 3: SharePoint (List Input folder)")
    print("=" * 60)
    files = sharepoint.list_files()
    print(f"  Found {len(files)} files in Input/:")
    for file_info in files[:5]:
        print(f"    - {file_info['name']} ({file_info.get('size', '?')} bytes)")
    if len(files) > 5:
        print(f"    ... and {len(files) - 5} more")
    print("  OK")
    return True


if __name__ == "__main__":
    print("SharePoint & Services Integration Test")
    print(f"Working dir: {os.getcwd()}")

    results = {}
    for name, fn in [
        ("Azure Auth", test_azure_auth),
        ("Gemini", test_gemini),
        ("SharePoint", test_sharepoint),
    ]:
        try:
            results[name] = fn()
        except Exception as exc:
            print(f"  FAILED: {exc}")
            results[name] = False

    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    all_ok = True
    for name, ok in results.items():
        print(f"  {name}: {'OK' if ok else 'FAILED'}")
        if not ok:
            all_ok = False

    if all_ok:
        print("\n  ALL TESTS PASSED")
    else:
        print("\n  Some tests failed. Check logs above.")
        sys.exit(1)
