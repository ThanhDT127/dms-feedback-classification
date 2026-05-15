"""
test_sharepoint.py — Kiểm tra kết nối SharePoint + Vertex AI + Azure AD.

Chạy: python scripts/test_sharepoint.py
"""
import sys, os
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, ".")

from dotenv import load_dotenv
load_dotenv()

import json
from config import logger
from auth import get_access_token
from sharepoint import list_input_files
from gemini_client import generate


def test_azure_auth():
    print("\n" + "=" * 60)
    print("TEST 1: Azure AD Authentication (Client Credentials)")
    print("=" * 60)
    token = get_access_token()
    # Decode payload để kiểm tra roles
    import base64
    parts = token.split(".")
    pad = parts[1] + "=" * (4 - len(parts[1]) % 4)
    payload = json.loads(base64.urlsafe_b64decode(pad))
    roles = payload.get("roles", [])
    print(f"  App: {payload.get('app_displayname', '?')}")
    print(f"  Roles: {roles}")
    print(f"  Token: {token[:10]}...(truncated for security)")
    print("  ✅ Azure Auth OK")
    return True


def test_gemini():
    print("\n" + "=" * 60)
    print("TEST 2: Gemini LLM (Vertex AI)")
    print("=" * 60)
    resp = generate("Trả lời đúng 1 từ: 1+1=?")
    print(f"  Response: {resp.strip()}")
    print("  ✅ Gemini OK")
    return True


def test_sharepoint():
    print("\n" + "=" * 60)
    print("TEST 3: SharePoint (List Input folder)")
    print("=" * 60)
    files = list_input_files()
    print(f"  Found {len(files)} files in Input/:")
    for f in files[:5]:
        print(f"    - {f['name']} ({f.get('size', '?')} bytes)")
    if len(files) > 5:
        print(f"    ... and {len(files) - 5} more")
    print("  ✅ SharePoint OK")
    return True


if __name__ == "__main__":
    print("🚀 SharePoint & Services Integration Test")
    print(f"   Working dir: {os.getcwd()}")

    results = {}
    for name, fn in [("Azure Auth", test_azure_auth),
                     ("Gemini", test_gemini),
                     ("SharePoint", test_sharepoint)]:
        try:
            results[name] = fn()
        except Exception as e:
            print(f"  ❌ FAILED: {e}")
            results[name] = False

    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    all_ok = True
    for name, ok in results.items():
        icon = "✅" if ok else "❌"
        print(f"  {name}: {icon}")
        if not ok:
            all_ok = False

    if all_ok:
        print("\n  🎉 ALL TESTS PASSED!")
    else:
        print("\n  ❌ Some tests failed. Check logs above.")
        sys.exit(1)
