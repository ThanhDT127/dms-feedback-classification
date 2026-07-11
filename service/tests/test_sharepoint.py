from __future__ import annotations

from pathlib import Path

import pytest
import requests

from dms.exceptions import SharePointError
from dms.sharepoint import SharePointClient


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError("http error")

    def json(self):
        return self._payload

    def iter_content(self, chunk_size=8192):
        yield b"hello"


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def _next(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return self.responses.pop(0)

    def get(self, url, **kwargs):
        return self._next("get", url, **kwargs)

    def put(self, url, **kwargs):
        return self._next("put", url, **kwargs)

    def post(self, url, **kwargs):
        return self._next("post", url, **kwargs)

    def delete(self, url, **kwargs):
        return self._next("delete", url, **kwargs)


def test_list_files_download_and_upload(settings, mock_auth_provider, tmp_path: Path):
    session = FakeSession(
        [
            FakeResponse(
                payload={"value": [{"name": "Input", "folder": {}, "id": "input-folder"}]}
            ),
            FakeResponse(
                payload={
                    "value": [
                        {"id": "1", "name": "a.xlsx", "file": {}, "size": 5},
                        {"id": "2", "name": "b.txt", "file": {}, "size": 1},
                    ]
                }
            ),
            FakeResponse(
                payload={"name": "a.xlsx", "@microsoft.graph.downloadUrl": "https://download"}
            ),
            FakeResponse(payload={}, text="ok"),
            FakeResponse(
                payload={"value": [{"name": "Output", "folder": {}, "id": "output-folder"}]}
            ),
            FakeResponse(status_code=201, payload={"id": "uploaded"}),
        ]
    )
    client = SharePointClient(mock_auth_provider, settings, session)

    files = client.list_files()
    assert files == [
        {"id": "1", "name": "a.xlsx", "size": 5, "lastModifiedDateTime": "", "eTag": ""}
    ]
    local = client.download_file("1", tmp_path / "a.xlsx")
    assert local.exists()

    upload_me = tmp_path / "upload.txt"
    upload_me.write_text("data", encoding="utf-8")
    result = client.upload_file(upload_me, settings.sp_output_folder)
    assert result["id"] == "uploaded"
    assert client.get_folder_id(settings.sp_input_folder) == "input-folder"
    assert client.get_folder_id(settings.sp_input_folder) == "input-folder"


def test_sharepoint_error_raised_on_upload_failure(settings, mock_auth_provider, tmp_path: Path):
    session = FakeSession(
        [
            FakeResponse(
                payload={"value": [{"name": "Output", "folder": {}, "id": "output-folder"}]}
            ),
            FakeResponse(status_code=500, text="boom"),
        ]
    )
    client = SharePointClient(mock_auth_provider, settings, session)
    path = tmp_path / "upload.txt"
    path.write_text("payload", encoding="utf-8")
    with pytest.raises(SharePointError):
        client.upload_file(path, settings.sp_output_folder)


def test_upload_file_uses_upload_session_for_large_file(
    settings, mock_auth_provider, tmp_path: Path, monkeypatch
):
    monkeypatch.setattr("dms.sharepoint.SIMPLE_UPLOAD_MAX_BYTES", 4)
    monkeypatch.setattr("dms.sharepoint.UPLOAD_CHUNK_SIZE", 5)
    session = FakeSession(
        [
            FakeResponse(
                payload={"value": [{"name": "Output", "folder": {}, "id": "output-folder"}]}
            ),
            FakeResponse(payload={"uploadUrl": "https://upload-session"}),
            FakeResponse(status_code=202, payload={"nextExpectedRanges": ["5-9"]}),
            FakeResponse(status_code=201, payload={"id": "uploaded-large"}),
        ]
    )
    client = SharePointClient(mock_auth_provider, settings, session)
    path = tmp_path / "large.bin"
    path.write_bytes(b"0123456789")

    result = client.upload_file(path, settings.sp_output_folder)

    assert result["id"] == "uploaded-large"
    assert [call[0] for call in session.calls] == ["get", "post", "put", "put"]
    assert session.calls[2][2]["headers"]["Content-Range"] == "bytes 0-4/10"
    assert session.calls[3][2]["headers"]["Content-Range"] == "bytes 5-9/10"


def test_upload_file_raises_when_upload_session_chunk_fails(
    settings, mock_auth_provider, tmp_path: Path, monkeypatch
):
    monkeypatch.setattr("dms.sharepoint.SIMPLE_UPLOAD_MAX_BYTES", 4)
    monkeypatch.setattr("dms.sharepoint.UPLOAD_CHUNK_SIZE", 5)
    session = FakeSession(
        [
            FakeResponse(
                payload={"value": [{"name": "Output", "folder": {}, "id": "output-folder"}]}
            ),
            FakeResponse(payload={"uploadUrl": "https://upload-session"}),
            FakeResponse(status_code=500, text="chunk boom"),
        ]
    )
    client = SharePointClient(mock_auth_provider, settings, session)
    path = tmp_path / "large.bin"
    path.write_bytes(b"0123456789")

    with pytest.raises(SharePointError):
        client.upload_file(path, settings.sp_output_folder)


def test_delete_item_calls_graph_delete(settings, mock_auth_provider):
    session = FakeSession([FakeResponse(status_code=204)])
    client = SharePointClient(mock_auth_provider, settings, session)

    client.delete_item("item-123")

    method, url, kwargs = session.calls[0]
    assert method == "delete"
    assert url.endswith("/items/item-123")
    assert "headers" in kwargs


def test_delete_item_raises_on_graph_failure(settings, mock_auth_provider):
    session = FakeSession([FakeResponse(status_code=500, text="boom")])
    client = SharePointClient(mock_auth_provider, settings, session)

    with pytest.raises(SharePointError):
        client.delete_item("item-123")


def test_list_folder_items_follows_graph_pagination(settings, mock_auth_provider):
    session = FakeSession(
        [
            FakeResponse(
                payload={"value": [{"name": "Input", "folder": {}, "id": "input-folder"}]}
            ),
            FakeResponse(
                payload={
                    "value": [{"id": "1", "name": "a.xlsx"}],
                    "@odata.nextLink": "https://graph.example/next-page",
                }
            ),
            FakeResponse(payload={"value": [{"id": "2", "name": "b.xlsx"}]}),
        ]
    )
    client = SharePointClient(mock_auth_provider, settings, session)

    items = client.list_folder_items(settings.sp_input_folder)

    assert [item["id"] for item in items] == ["1", "2"]
    assert session.calls[2][1] == "https://graph.example/next-page"


def test_list_folder_items_raises_when_later_page_fails(settings, mock_auth_provider):
    session = FakeSession(
        [
            FakeResponse(
                payload={"value": [{"name": "Input", "folder": {}, "id": "input-folder"}]}
            ),
            FakeResponse(
                payload={
                    "value": [{"id": "1", "name": "a.xlsx"}],
                    "@odata.nextLink": "https://graph.example/next-page",
                }
            ),
            FakeResponse(status_code=500, text="page exploded"),
        ]
    )
    client = SharePointClient(mock_auth_provider, settings, session)

    with pytest.raises(SharePointError):
        client.list_folder_items(settings.sp_input_folder)
