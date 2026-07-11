"""SharePoint Graph API client."""

from __future__ import annotations

import logging
from pathlib import Path

import requests

from .auth import AuthProvider
from .exceptions import SharePointError
from .settings import Settings

logger = logging.getLogger("dms-watcher")

SIMPLE_UPLOAD_MAX_BYTES = 4 * 1024 * 1024
UPLOAD_CHUNK_SIZE = 5 * 1024 * 1024


class SharePointClient:
    """SharePoint operations for listing, downloading, and uploading files."""

    def __init__(
        self,
        auth: AuthProvider,
        settings: Settings,
        session: requests.Session,
    ) -> None:
        self.auth = auth
        self.settings = settings
        self.session = session
        self._folder_ids: dict[str, str] = {}

    def _drive_url(self, path: str = "") -> str:
        return (
            f"{self.settings.graph_base}/drives/{self.settings.sharepoint_drive_id}/{path}".rstrip(
                "/"
            )
        )

    def _raise_for_error(self, response: requests.Response, message: str) -> None:
        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            detail = response.text[:300]
            raise SharePointError(f"{message} ({response.status_code}): {detail}") from exc

    def _get_subfolder_id(self, parent_id: str, folder_name: str) -> str:
        url = self._drive_url(f"items/{parent_id}/children")
        response = self.session.get(url, headers=self.auth.get_headers())
        self._raise_for_error(response, "Cannot access folder children")
        data = response.json()
        if "error" in data:
            raise SharePointError(f"Cannot access folder children: {data['error']['message']}")
        for item in data.get("value", []):
            if item.get("name") == folder_name and "folder" in item:
                return item["id"]
        raise SharePointError(f"Subfolder not found: {folder_name!r} in parent {parent_id}")

    def get_folder_id(self, folder_name: str) -> str:
        if folder_name not in self._folder_ids:
            self._folder_ids[folder_name] = self._get_subfolder_id(
                self.settings.sharepoint_root_folder_id, folder_name
            )
            logger.info(
                "Resolved folder '%s' -> %s",
                folder_name,
                self._folder_ids[folder_name],
            )
        return self._folder_ids[folder_name]

    def list_folder_items(self, folder_name: str) -> list[dict]:
        folder_id = self.get_folder_id(folder_name)
        url = self._drive_url(f"items/{folder_id}/children")
        items: list[dict] = []
        while url:
            response = self.session.get(url, headers=self.auth.get_headers())
            self._raise_for_error(response, f"Cannot list folder {folder_name}")
            data = response.json()
            if "error" in data:
                raise SharePointError(
                    f"Cannot list folder {folder_name}: {data['error']['message']}"
                )
            items.extend(data.get("value", []))
            url = data.get("@odata.nextLink")
        return items

    def list_files(self) -> list[dict]:
        data = self.list_folder_items(self.settings.sp_input_folder)
        files = []
        for item in data:
            name = item.get("name", "")
            if "file" in item and name.lower().endswith(".xlsx"):
                files.append(
                    {
                        "id": item["id"],
                        "name": name,
                        "size": item.get("size", 0),
                        "lastModifiedDateTime": item.get("lastModifiedDateTime", ""),
                        "eTag": item.get("eTag", ""),
                    }
                )
        logger.info("Found %d .xlsx file(s) in Input/", len(files))
        return files

    def download_file(self, file_id: str, local_path: str | Path) -> Path:
        url = self._drive_url(f"items/{file_id}")
        response = self.session.get(url, headers=self.auth.get_headers())
        self._raise_for_error(response, f"Cannot fetch metadata for item {file_id}")
        item_data = response.json()
        dl_url = item_data.get("@microsoft.graph.downloadUrl")
        if not dl_url:
            raise SharePointError(f"No download URL for item {file_id}")

        local_path = Path(local_path)
        local_path.parent.mkdir(parents=True, exist_ok=True)
        dl_response = self.session.get(dl_url, stream=True)
        self._raise_for_error(dl_response, f"Cannot download item {file_id}")
        with local_path.open("wb") as handle:
            for chunk in dl_response.iter_content(chunk_size=8192):
                handle.write(chunk)
        logger.info(
            "Downloaded %s (%d bytes) -> %s",
            item_data.get("name", "?"),
            local_path.stat().st_size,
            local_path,
        )
        return local_path

    def upload_file(
        self,
        local_path: str | Path,
        remote_folder: str,
        remote_filename: str | None = None,
    ) -> dict:
        local_path = Path(local_path)
        if local_path.stat().st_size <= SIMPLE_UPLOAD_MAX_BYTES:
            return self._upload_file_simple(local_path, remote_folder, remote_filename)
        return self._upload_file_session(local_path, remote_folder, remote_filename)

    def _upload_file_simple(
        self,
        local_path: Path,
        remote_folder: str,
        remote_filename: str | None = None,
    ) -> dict:
        from urllib.parse import quote

        filename = remote_filename or local_path.name
        folder_id = self.get_folder_id(remote_folder)
        url = self._drive_url(f"items/{folder_id}:/{quote(filename)}:/content")
        headers = self.auth.get_headers()
        upload_headers = {k: v for k, v in headers.items() if k != "Content-Type"}
        with local_path.open("rb") as handle:
            response = self.session.put(url, headers=upload_headers, data=handle)
        if response.status_code not in (200, 201):
            raise SharePointError(
                f"Upload failed ({response.status_code}) for {filename}: {response.text[:300]}"
            )
        logger.info("Uploaded %s as %s -> %s/", local_path.name, filename, remote_folder)
        return response.json()

    def _upload_file_session(
        self,
        local_path: Path,
        remote_folder: str,
        remote_filename: str | None = None,
    ) -> dict:
        from urllib.parse import quote

        filename = remote_filename or local_path.name
        folder_id = self.get_folder_id(remote_folder)
        create_url = self._drive_url(f"items/{folder_id}:/{quote(filename)}:/createUploadSession")
        response = self.session.post(
            create_url,
            headers=self.auth.get_headers(),
            json={"item": {"@microsoft.graph.conflictBehavior": "replace", "name": filename}},
        )
        if response.status_code not in (200, 201):
            raise SharePointError(
                f"Upload session creation failed ({response.status_code}) for {filename}: {response.text[:300]}"
            )
        upload_url = response.json().get("uploadUrl")
        if not upload_url:
            raise SharePointError(f"Upload session response missing uploadUrl for {filename}")

        total_size = local_path.stat().st_size
        final_response = None
        with local_path.open("rb") as handle:
            start = 0
            while start < total_size:
                chunk = handle.read(UPLOAD_CHUNK_SIZE)
                if not chunk:
                    break
                end = start + len(chunk) - 1
                headers = {
                    "Content-Length": str(len(chunk)),
                    "Content-Range": f"bytes {start}-{end}/{total_size}",
                }
                chunk_response = self.session.put(upload_url, headers=headers, data=chunk)
                if chunk_response.status_code not in (200, 201, 202):
                    raise SharePointError(
                        f"Upload session chunk failed ({chunk_response.status_code}) for {filename}: {chunk_response.text[:300]}"
                    )
                final_response = chunk_response
                start = end + 1

        if final_response is None or final_response.status_code not in (200, 201):
            raise SharePointError(f"Upload session did not complete for {filename}")
        logger.info("Uploaded %s as %s -> %s/ via upload session", local_path.name, filename, remote_folder)
        return final_response.json()

    def upload_output(self, local_path: str | Path) -> dict:
        return self.upload_file(local_path, self.settings.sp_output_folder)

    def upload_checkpoint(self, local_path: str | Path) -> dict:
        return self.upload_file(local_path, self.settings.sp_checkpoint_folder)

    def delete_item(self, item_id: str) -> None:
        """Delete a SharePoint drive item by Microsoft Graph item id."""
        if not item_id:
            raise SharePointError("Delete failed: missing SharePoint item id")
        url = self._drive_url(f"items/{item_id}")
        response = self.session.delete(url, headers=self.auth.get_headers())
        if response.status_code not in (200, 202, 204):
            raise SharePointError(
                f"Delete failed ({response.status_code}) for item {item_id}: "
                f"{response.text[:300]}"
            )
        logger.info("Deleted SharePoint item %s", item_id)
