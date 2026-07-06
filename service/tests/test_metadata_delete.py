from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from dms.web.app import create_app
from dms.web.deps import get_admin_user, get_current_user


@pytest.fixture
def client(tmp_path: Path, monkeypatch) -> TestClient:
    """TestClient with isolated file directories and mocked authentication."""
    # Isolated paths
    monkeypatch.setattr("dms.settings.SERVICE_DIR", tmp_path)
    monkeypatch.setattr("dms.web.api.files.SERVICE_DIR", tmp_path, raising=False)
    monkeypatch.setattr("dms.web.api.files.WORK_DIR", tmp_path / "work")

    # Configure folders
    monkeypatch.setattr(
        "dms.web.api.files.FOLDER_MAP",
        {
            "input": [tmp_path / "work" / "input"],
            "output": [tmp_path / "work" / "output"],
        },
    )

    # Ensure dirs exist
    (tmp_path / "work" / "input").mkdir(parents=True, exist_ok=True)
    (tmp_path / "work" / "output").mkdir(parents=True, exist_ok=True)

    app = create_app()

    # Mock authentication to allow requests
    app.dependency_overrides[get_current_user] = lambda: {"username": "testuser", "role": "user"}
    app.dependency_overrides[get_admin_user] = lambda: {"username": "adminuser", "role": "admin"}

    return TestClient(app)


def test_metadata_endpoint_invalid_folder(client):
    """Querying metadata for invalid folder should return 400."""
    resp = client.get("/api/files/invalid_folder/somefile.xlsx/metadata")
    assert resp.status_code == 400
    assert "Thư mục không hợp lệ" in resp.json()["detail"]


def test_metadata_endpoint_file_not_found(client):
    """Querying metadata for missing file should return 404."""
    resp = client.get("/api/files/input/nonexistent.xlsx/metadata")
    assert resp.status_code == 404
    assert "Không tìm thấy file" in resp.json()["detail"]


def test_metadata_endpoint_excel_success(client, tmp_path):
    """Metadata endpoint should extract columns and row counts for Excel files."""
    # Create test excel file
    df = pd.DataFrame({
        "A": [1, 2, 3],
        "B": ["x", "y", "z"],
        "C": [True, False, True]
    })
    file_path = tmp_path / "work" / "input" / "test.xlsx"
    df.to_excel(file_path, index=False)

    resp = client.get("/api/files/input/test.xlsx/metadata")
    assert resp.status_code == 200
    data = resp.json()
    assert data["row_count"] == 3
    assert data["columns"] == ["A", "B", "C"]
    assert data["source"] == "local"


def test_metadata_endpoint_seen_files_sharepoint(client, tmp_path):
    """Metadata should set source=sharepoint if the file exists in seen_files.json with web_url/id."""
    # Create local file
    df = pd.DataFrame({"A": [1]})
    file_path = tmp_path / "work" / "input" / "sp_test.xlsx"
    df.to_excel(file_path, index=False)

    # Create seen_files.json marking this file as from sharepoint
    seen_data = {
        "file-id-123": {
            "name": "sp_test.xlsx",
            "status": "done",
            "web_url": "https://sharepoint.example/sp_test.xlsx",
            "id": "file-id-123"
        }
    }
    seen_path = tmp_path / "work" / "seen_files.json"
    seen_path.write_text(json.dumps(seen_data), encoding="utf-8")

    resp = client.get("/api/files/input/sp_test.xlsx/metadata")
    assert resp.status_code == 200
    data = resp.json()
    assert data["source"] == "sharepoint"


def test_bulk_delete_invalid_folder(client):
    """Bulk delete with invalid folder should return 400."""
    resp = client.post("/api/files/bulk-delete", json={
        "folder": "invalid_folder",
        "filenames": ["a.xlsx"]
    })
    assert resp.status_code == 400
    assert "Thư mục không hợp lệ" in resp.json()["detail"]


def test_bulk_delete_empty_list(client):
    """Bulk delete with empty filenames list should return 400."""
    resp = client.post("/api/files/bulk-delete", json={
        "folder": "input",
        "filenames": []
    })
    assert resp.status_code == 400
    assert "Danh sách file xóa trống" in resp.json()["detail"]


def test_bulk_delete_success(client, tmp_path):
    """Bulk delete should delete existing files and report success/failed counts."""
    # Create files to delete
    f1 = tmp_path / "work" / "input" / "f1.xlsx"
    f2 = tmp_path / "work" / "input" / "f2.xlsx"
    f1.write_text("dummy")
    f2.write_text("dummy")

    resp = client.post("/api/files/bulk-delete", json={
        "folder": "input",
        "filenames": ["f1.xlsx", "f2.xlsx", "missing.xlsx"]
    })
    assert resp.status_code == 200
    data = resp.json()

    # Check deletion status
    assert "f1.xlsx" in data["deleted"]
    assert "f2.xlsx" in data["deleted"]
    assert not f1.exists()
    assert not f2.exists()

    # Check failed items
    failed_names = [f["name"] for f in data["failed"]]
    assert "missing.xlsx" in failed_names
    assert len(data["deleted"]) == 2
    assert len(data["failed"]) == 1
    assert data["delete_scope"] == "local_cache"
    assert "local/cache" in data["message"]
    assert "SharePoint" in data["message"]


class FakeSharePointForDelete:
    def __init__(self):
        self.deleted = []

    def list_folder_items(self, folder_name):
        return [
            {"id": "sp-1", "name": "cloud.xlsx", "file": {}},
            {"id": "sp-2", "name": "other.xlsx", "file": {}},
        ]

    def delete_item(self, item_id):
        self.deleted.append(item_id)


def test_sharepoint_delete_endpoint_deletes_remote_only(client, monkeypatch):
    fake_sp = FakeSharePointForDelete()
    monkeypatch.setattr("dms.web.api.files.get_sharepoint_client", lambda: fake_sp)

    resp = client.post(
        "/api/files/sharepoint-delete",
        json={
            "folder": "input",
            "items": [{"name": "direct.xlsx", "id": "direct-id"}],
            "filenames": ["cloud.xlsx", "missing.xlsx"],
        },
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["delete_scope"] == "sharepoint"
    assert data["remote_deleted"] == ["direct.xlsx", "cloud.xlsx"]
    assert fake_sp.deleted == ["direct-id", "sp-1"]
    assert [f["name"] for f in data["failed"]] == ["missing.xlsx"]
