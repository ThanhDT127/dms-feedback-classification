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
    os.system(f"{sys.executable} -m pip install msal requests --trusted-host pypi.org --trusted-host files.pythonhosted.org -q")
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
ROOT_FOLDER_ID = env.get("SHAREPOINT_ROOT_FOLDER_ID", "")
KEYWORD_FOLDER_NAME = env.get("SHAREPOINT_KEYWORD_FOLDER", "Keyword")
KEYWORD_DIR = os.path.join(SERVICE_DIR, "Keyword")

if not all([TENANT, CLIENT_ID, CLIENT_SECRET, DRIVE_ID, ROOT_FOLDER_ID]):
    print("Thiếu config trong .env:")
    for k in ["AZURE_TENANT_ID", "AZURE_CLIENT_ID", "AZURE_CLIENT_SECRET", "SHAREPOINT_DRIVE_ID", "SHAREPOINT_ROOT_FOLDER_ID"]:
        print(f"  {k}: {'OK' if env.get(k) else 'THIẾU'}")
    sys.exit(1)

# --- Auth ---
print("Đang xác thực Azure AD...")
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

# --- Tìm subfolder Keyword trong root folder ---
print(f"Đang tìm subfolder '{KEYWORD_FOLDER_NAME}' trong root folder...")
resp = requests.get(
    f"{GRAPH}/drives/{DRIVE_ID}/items/{ROOT_FOLDER_ID}/children",
    headers=HEADERS,
)
if resp.status_code != 200:
    print(f"Lỗi list root folder: {resp.status_code} - {resp.text[:300]}")
    sys.exit(1)

keyword_folder_id = None
for item in resp.json().get("value", []):
    if item.get("name") == KEYWORD_FOLDER_NAME and "folder" in item:
        keyword_folder_id = item["id"]
        break

if not keyword_folder_id:
    print(f"Không tìm thấy subfolder '{KEYWORD_FOLDER_NAME}' trong root folder!")
    print("Các folder có sẵn:")
    for item in resp.json().get("value", []):
        if "folder" in item:
            print(f"  📁 {item['name']}")
    sys.exit(1)

print(f"Tìm thấy folder ID: {keyword_folder_id[:20]}...")

# --- List files trong Keyword folder ---
resp = requests.get(
    f"{GRAPH}/drives/{DRIVE_ID}/items/{keyword_folder_id}/children",
    headers=HEADERS,
)
files = resp.json().get("value", [])
print(f"Có {len(files)} file trong {KEYWORD_FOLDER_NAME}/")

# --- Tải về ---
os.makedirs(KEYWORD_DIR, exist_ok=True)
downloaded = 0

for f in files:
    if "folder" in f:
        continue
    name = f["name"]
    size = f.get("size", 0)

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

kw_path = os.path.join(KEYWORD_DIR, "kw_map.json")
if os.path.isfile(kw_path):
    print(f"✅ kw_map.json: {os.path.getsize(kw_path):,} bytes")
else:
    print("❌ kw_map.json không có trong SharePoint Keyword/")
