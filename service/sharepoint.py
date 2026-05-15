"""
SharePoint integration module — Graph API file operations.

Refactored from notebook cells for download_folder_recursive, upload_file, etc.
Uses client credentials auth instead of device flow.
"""
import os
import requests
from typing import Optional

from auth import get_headers
from config import (
    SHAREPOINT_DRIVE_ID,
    SHAREPOINT_ROOT_FOLDER_ID,
    GRAPH_BASE,
    SP_INPUT_FOLDER,
    SP_OUTPUT_FOLDER,
    SP_CHECKPOINT_FOLDER,
    logger,
)


class SharePointError(Exception):
    """Raised when a SharePoint/Graph API operation fails."""
    pass


def _drive_url(path: str = "") -> str:
    """Build Graph API URL for the configured drive."""
    return f"{GRAPH_BASE}/drives/{SHAREPOINT_DRIVE_ID}/{path}".rstrip("/")


def _get_subfolder_id(parent_id: str, folder_name: str) -> str:
    """
    Find a subfolder ID by name within a parent folder.

    Args:
        parent_id: SharePoint item ID of the parent folder.
        folder_name: Name of the subfolder to find.

    Returns:
        SharePoint item ID of the subfolder.

    Raises:
        SharePointError: If folder not found or API error.
    """
    url = _drive_url(f"items/{parent_id}/children")
    headers = get_headers()
    resp = requests.get(url, headers=headers)
    resp.raise_for_status()
    data = resp.json()

    if "error" in data:
        raise SharePointError(f"Cannot access folder children: {data['error']['message']}")

    for item in data.get("value", []):
        if item.get("name") == folder_name and "folder" in item:
            return item["id"]

    raise SharePointError(f"Subfolder not found: '{folder_name}' in parent {parent_id}")


# ── Cached folder IDs ───────────────────────────────────────────────────────
_folder_ids: dict[str, str] = {}


def _get_folder_id(folder_name: str) -> str:
    """Get (and cache) the ID of a known subfolder under the root."""
    if folder_name not in _folder_ids:
        _folder_ids[folder_name] = _get_subfolder_id(SHAREPOINT_ROOT_FOLDER_ID, folder_name)
        logger.info("Resolved folder '%s' → %s", folder_name, _folder_ids[folder_name])
    return _folder_ids[folder_name]


def list_input_files() -> list[dict]:
    """
    List .xlsx files in the SharePoint Input/ folder.

    Returns:
        List of dicts with keys: id, name, size, lastModifiedDateTime
    """
    input_folder_id = _get_folder_id(SP_INPUT_FOLDER)
    url = _drive_url(f"items/{input_folder_id}/children")
    headers = get_headers()
    resp = requests.get(url, headers=headers)
    resp.raise_for_status()
    data = resp.json()

    if "error" in data:
        raise SharePointError(f"Cannot list Input folder: {data['error']['message']}")

    files = []
    for item in data.get("value", []):
        name = item.get("name", "")
        if "file" in item and name.lower().endswith(".xlsx"):
            files.append({
                "id": item["id"],
                "name": name,
                "size": item.get("size", 0),
                "lastModifiedDateTime": item.get("lastModifiedDateTime", ""),
            })

    logger.info("Found %d .xlsx file(s) in Input/", len(files))
    return files


def download_file(file_id: str, local_path: str) -> str:
    """
    Download a file from SharePoint to a local path.

    Args:
        file_id: SharePoint item ID of the file.
        local_path: Local filesystem path to save the file.

    Returns:
        The local_path where the file was saved.

    Raises:
        SharePointError: If download fails.
    """
    # Get download URL
    url = _drive_url(f"items/{file_id}")
    headers = get_headers()
    resp = requests.get(url, headers=headers)
    resp.raise_for_status()
    item_data = resp.json()

    dl_url = item_data.get("@microsoft.graph.downloadUrl")
    if not dl_url:
        raise SharePointError(f"No download URL for item {file_id}")

    # Download content
    os.makedirs(os.path.dirname(local_path) or ".", exist_ok=True)
    dl_resp = requests.get(dl_url, stream=True)
    dl_resp.raise_for_status()

    with open(local_path, "wb") as f:
        for chunk in dl_resp.iter_content(chunk_size=8192):
            f.write(chunk)

    file_size = os.path.getsize(local_path)
    logger.info("Downloaded %s (%d bytes) → %s", item_data.get("name", "?"), file_size, local_path)
    return local_path


def upload_file(local_path: str, remote_folder: str) -> dict:
    """
    Upload a local file to a SharePoint folder.

    Uses simple upload (PUT) for files < 4MB, which is suitable for
    Excel outputs and JSON checkpoints.

    Args:
        local_path: Local filesystem path of the file to upload.
        remote_folder: Name of the target folder (e.g. "Output", "Check_Point").

    Returns:
        Graph API response dict for the uploaded item.

    Raises:
        SharePointError: If upload fails.
    """
    folder_id = _get_folder_id(remote_folder)
    file_name = os.path.basename(local_path)
    url = _drive_url(f"items/{folder_id}:/{file_name}:/content")

    headers = get_headers()
    # Remove Content-Type json header for file upload
    upload_headers = {k: v for k, v in headers.items() if k != "Content-Type"}

    with open(local_path, "rb") as f:
        resp = requests.put(url, headers=upload_headers, data=f)

    if resp.status_code not in (200, 201):
        raise SharePointError(
            f"Upload failed ({resp.status_code}) for {file_name}: {resp.text[:300]}"
        )

    logger.info("Uploaded %s → %s/", file_name, remote_folder)
    return resp.json()


def upload_output(local_path: str) -> dict:
    """Upload a file to the Output/ folder."""
    return upload_file(local_path, SP_OUTPUT_FOLDER)


def upload_checkpoint(local_path: str) -> dict:
    """Upload a file to the Check_Point/ folder."""
    return upload_file(local_path, SP_CHECKPOINT_FOLDER)
