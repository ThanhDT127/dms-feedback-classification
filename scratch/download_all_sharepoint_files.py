import sys
from pathlib import Path
import requests

# Reconfigure stdout to use UTF-8 to prevent 'charmap' encoding issues in Windows console/logs
sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')

# Add service/src to python path
SERVICE_SRC = Path(r"d:\Works\DMS\service\src")
if str(SERVICE_SRC) not in sys.path:
    sys.path.insert(0, str(SERVICE_SRC))

from dms.settings import get_settings
from dms.auth import AuthProvider
from dms.sharepoint import SharePointClient

def main():
    settings = get_settings()
    auth = AuthProvider(settings)
    
    session = requests.Session()
    client = SharePointClient(auth, settings, session)
    
    # Target folders to list and download from
    folders = ["Input", "Output", "Check_Point", "Keyword", "Model"]
    
    cache_base = Path(r"d:\Works\DMS\scratch\sharepoint_cache")
    cache_base.mkdir(parents=True, exist_ok=True)
    
    print("Connecting to SharePoint and listing folders...")
    for folder in folders:
        print(f"\n--- Folder: {folder} ---")
        try:
            items = client.list_folder_items(folder)
            print(f"Found {len(items)} items in '{folder}'.")
            
            folder_cache_dir = cache_base / folder
            folder_cache_dir.mkdir(parents=True, exist_ok=True)
            
            for item in items:
                name = item.get("name")
                item_id = item.get("id")
                # check if it's a file
                if "file" in item:
                    print(f"  Downloading: {name} ({item_id})")
                    local_path = folder_cache_dir / name
                    client.download_file(item_id, local_path)
                elif "folder" in item:
                    print(f"  [Folder] {name} ({item_id}) - skipping nested folders for now")
        except Exception as e:
            import traceback
            print(f"Error processing folder '{folder}': {e}")
            traceback.print_exc()

if __name__ == "__main__":
    main()
