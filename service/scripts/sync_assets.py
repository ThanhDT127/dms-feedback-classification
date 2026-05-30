"""Tải kw_map.json từ SharePoint về thư mục Keyword/. Chạy: python scripts/sync_assets.py"""

import json
import os
import sys

# --- Cài deps nếu chưa có ---
try:
    import msal
    import requests
except ImportError:
    print("Đang cài msal, requests...")
    os.system(f"{sys.executable} -m pip install msal requests -q")
    import msal
    import requests

# --- Đọc .env ---
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SERVICE_DIR = os.path.join(SCRIPT_DIR, "..")
ENV_FILE = os.path.join(SERVICE_DIR, ".env")

env = {}
if os.path.isfile(ENV_FILE):
    for line in open(ENV_FILE, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")

TENANT = env.get("AZURE_TENANT_ID", "")
CLIENT_ID = env.get("AZURE_CLIENT_ID", "")
CLIENT_SECRET = env.get("AZURE_CLIENT_SECRET", "")
DRIVE_ID = env.get("SHAREPOINT_DRIVE_ID", "")
SP_KEYWORD_FOLDER = env.get("SP_KEYWORD_FOLDER", "Keyword")
KEYWORD_DIR = os.path.join(SERVICE_DIR, "Keyword")

if not all([TENANT, CLIENT_ID, CLIENT_SECRET, DRIVE_ID]):
    print("Thiếu config trong .env (AZURE_TENANT_ID, CLIENT_ID, CLIENT_SECRET, SHAREPOINT_DRIVE_ID)")
    sys.exit(1)

# --- Auth ---
print(f"Đang xác thực Azure AD...")
app = msal.ConfidentialClientApplication(
    CLIENT_ID,
    authority=f"https://login.microsoftonline.com/{TENANT}",
    client_credential=CLIENT_SECRET,
)
token_resp = app.acquire_token_for_client(scopes=["https://graph.microsoft.com/.default"])
if "access_token" not in token_resp:
    print(f"Lỗi auth: {token_resp.get('error_description', token_resp)}")
    sys.exit(1)

TOKEN = token_resp["access_token"]
HEADERS = {"Authorization": f"Bearer {TOKEN}"}
GRAPH = "https://graph.microsoft.com/v1.0"

# --- Tìm folder Keyword trên SharePoint ---
print(f"Đang tìm thư mục '{SP_KEYWORD_FOLDER}' trên SharePoint...")
resp = requests.get(f"{GRAPH}/drives/{DRIVE_ID}/root:/{SP_KEYWORD_FOLDER}:/children", headers=HEADERS)
if resp.status_code != 200:
    print(f"Lỗi: {resp.status_code} - {resp.text[:300]}")
    sys.exit(1)

files = resp.json().get("value", [])
print(f"Tìm thấy {len(files)} file trong {SP_KEYWORD_FOLDER}/")

# --- Tải về ---
os.makedirs(KEYWORD_DIR, exist_ok=True)
downloaded = 0

for f in files:
    name = f["name"]
    size = f.get("size", 0)
    download_url = f.get("@microsoft.graph.downloadUrl")

    if not download_url:
        # Lấy download URL
        item_resp = requests.get(f"{GRAPH}/drives/{DRIVE_ID}/items/{f['id']}", headers=HEADERS)
        download_url = item_resp.json().get("@microsoft.graph.downloadUrl")

    if not download_url:
        print(f"  ⚠ {name}: không lấy được URL tải")
        continue

    dest = os.path.join(KEYWORD_DIR, name)
    print(f"  📥 {name} ({size:,} bytes)...", end=" ")
    content = requests.get(download_url).content
    with open(dest, "wb") as out:
        out.write(content)
    print("OK")
    downloaded += 1

print(f"\n✅ Đã tải {downloaded} file về {KEYWORD_DIR}/")

# Kiểm tra kw_map.json
kw_path = os.path.join(KEYWORD_DIR, "kw_map.json")
if os.path.isfile(kw_path):
    print(f"✅ kw_map.json: {os.path.getsize(kw_path):,} bytes")
else:
    print("❌ kw_map.json: KHÔNG TÌM THẤY trong thư mục SharePoint")
