import sys
from pathlib import Path
import json

sys.path.insert(0, str(Path(__file__).parent.parent / "service" / "src"))
from dms.web import deps

settings = deps.get_settings()
sp_client = deps.get_sharepoint_client()

if not settings or not sp_client:
    print("Failed to initialize settings or SharePoint client.")
    sys.exit(1)

seen_path = settings.seen_files_path
if not seen_path.exists():
    print(f"seen_files.json not found at {seen_path}")
    sys.exit(1)

with seen_path.open("r", encoding="utf-8") as f:
    seen = json.load(f)

# Insert the entry for DMST0426-16-17.xlsx
file_id = "01GQCGF52SSCQLHFC3ZRD2H76HIFZINIRW"
seen[file_id] = {
    "name": "DMST0426-16-17.xlsx",
    "status": "done",
    "processed_at": "2026-06-06T08:08:00.000000",
    "lastModifiedDateTime": "2026-04-17T10:00:00Z",
    "total_rows": 0,
    "duration_seconds": 0.0,
    "label_distribution": {},
}

print("Saving seen_files.json locally...")
with seen_path.open("w", encoding="utf-8") as f:
    json.dump(seen, f, ensure_ascii=False, indent=2)

print("Uploading seen_files.json to SharePoint Check_Point...")
try:
    sp_client.upload_checkpoint(seen_path)
    print("Upload complete!")
except Exception as e:
    print(f"Failed to upload to SharePoint: {e}")
