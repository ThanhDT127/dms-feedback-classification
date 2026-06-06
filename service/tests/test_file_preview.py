"""Tests for multi-format file preview endpoint."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from dms.web.app import create_app
from dms.settings import SERVICE_DIR


@pytest.fixture
def client(tmp_path, monkeypatch):
    """TestClient with isolated file directories."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_BACKEND", raising=False)
    monkeypatch.delenv("GCP_PROJECT_ID", raising=False)
    monkeypatch.delenv("AZURE_TENANT_ID", raising=False)
    monkeypatch.delenv("AZURE_CLIENT_ID", raising=False)
    monkeypatch.delenv("AZURE_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("SHAREPOINT_DRIVE_ID", raising=False)
    monkeypatch.delenv("SHAREPOINT_ROOT_FOLDER_ID", raising=False)

    # Point SERVICE_DIR to tmp_path so FOLDER_MAP resolves there
    monkeypatch.setattr("dms.settings.SERVICE_DIR", tmp_path)
    monkeypatch.setattr("dms.web.api.files.SERVICE_DIR", tmp_path, raising=False)
    monkeypatch.setattr("dms.web.api.files.WORK_DIR", tmp_path / "work")
    monkeypatch.setattr("dms.web.api.files.FOLDER_MAP", {
        "input": [tmp_path / "work" / "input"],
        "keyword": [tmp_path / "Keyword"],
    })

    # Create dirs
    (tmp_path / "work" / "input").mkdir(parents=True, exist_ok=True)
    (tmp_path / "Keyword").mkdir(parents=True, exist_ok=True)

    app = create_app()
    return TestClient(app)


# ═══════════════════════════════════════════════════════════════
#  5.1 — Excel with NaN → HTTP 200
# ═══════════════════════════════════════════════════════════════

class TestExcelPreviewNaN:
    def test_excel_with_nan_returns_200(self, client, tmp_path):
        """NaN values in Excel should NOT crash with 500."""
        df = pd.DataFrame({
            "STT": [1.0, np.nan, 3.0],
            "Name": ["Alice", None, "Charlie"],
            "Score": [95.5, np.nan, np.nan],
        })
        path = tmp_path / "work" / "input" / "test_nan.xlsx"
        df.to_excel(path, index=False)

        response = client.get("/api/files/input/test_nan.xlsx/preview")
        assert response.status_code == 200
        data = response.json()
        assert data["type"] == "table"
        assert len(data["data"]) == 3
        # NaN should be replaced with empty string, not crash
        assert data["data"][1]["STT"] == ""
        assert data["data"][1]["Score"] == ""

    def test_excel_with_inf_returns_200(self, client, tmp_path):
        """Inf/-Inf values should be replaced with 'Inf' string."""
        df = pd.DataFrame({"Value": [1.0, np.inf, -np.inf, 42.0]})
        path = tmp_path / "work" / "input" / "test_inf.xlsx"
        df.to_excel(path, index=False)

        response = client.get("/api/files/input/test_inf.xlsx/preview")
        assert response.status_code == 200
        data = response.json()
        assert data["data"][1]["Value"] == "Inf"
        assert data["data"][2]["Value"] == "Inf"


# ═══════════════════════════════════════════════════════════════
#  5.2 — JSON preview
# ═══════════════════════════════════════════════════════════════

class TestJSONPreview:
    def test_json_preview(self, client, tmp_path):
        """Valid JSON file should return type='json' with parsed content."""
        content = {"labels": ["Báo lỗi", "HTPP"], "count": 2}
        path = tmp_path / "Keyword" / "test.json"
        path.write_text(json.dumps(content, ensure_ascii=False), encoding="utf-8")

        response = client.get("/api/files/keyword/test.json/preview")
        assert response.status_code == 200
        data = response.json()
        assert data["type"] == "json"
        assert data["content"]["labels"] == ["Báo lỗi", "HTPP"]
        assert data["truncated"] is False


# ═══════════════════════════════════════════════════════════════
#  5.3 — CSV preview
# ═══════════════════════════════════════════════════════════════

class TestCSVPreview:
    def test_csv_preview(self, client, tmp_path):
        """CSV file should return type='table' with parsed rows."""
        csv_content = "Name,Score\nAlice,95\nBob,88\n"
        path = tmp_path / "work" / "input" / "test.csv"
        path.write_text(csv_content, encoding="utf-8")

        response = client.get("/api/files/input/test.csv/preview")
        assert response.status_code == 200
        data = response.json()
        assert data["type"] == "table"
        assert data["columns"] == ["Name", "Score"]
        assert len(data["data"]) == 2
        assert data["data"][0]["Name"] == "Alice"


# ═══════════════════════════════════════════════════════════════
#  5.4 — Text preview
# ═══════════════════════════════════════════════════════════════

class TestTextPreview:
    def test_txt_preview(self, client, tmp_path):
        """Text file should return type='text' with raw content."""
        text = "Line 1\nLine 2\nLine 3 — tiếng Việt\n"
        path = tmp_path / "work" / "input" / "readme.txt"
        path.write_text(text, encoding="utf-8")

        response = client.get("/api/files/input/readme.txt/preview")
        assert response.status_code == 200
        data = response.json()
        assert data["type"] == "text"
        assert "tiếng Việt" in data["content"]
        assert data["truncated"] is False


# ═══════════════════════════════════════════════════════════════
#  5.5 — Unsupported file type
# ═══════════════════════════════════════════════════════════════

class TestUnsupportedPreview:
    def test_pkl_returns_unsupported(self, client, tmp_path):
        """Binary file types should return type='unsupported', not crash."""
        path = tmp_path / "work" / "input" / "model.pkl"
        path.write_bytes(b"\x80\x04\x95\x00\x00\x00\x00")

        response = client.get("/api/files/input/model.pkl/preview")
        assert response.status_code == 200
        data = response.json()
        assert data["type"] == "unsupported"
        assert data["extension"] == ".pkl"


# ═══════════════════════════════════════════════════════════════
#  5.6 — Large JSON truncation
# ═══════════════════════════════════════════════════════════════

class TestLargeFileProtection:
    def test_large_json_truncated(self, client, tmp_path):
        """JSON files > 500KB should be truncated."""
        # Create a ~600KB JSON string
        big_content = "x" * 600_000
        path = tmp_path / "Keyword" / "big.json"
        path.write_text(big_content, encoding="utf-8")

        response = client.get("/api/files/keyword/big.json/preview")
        assert response.status_code == 200
        data = response.json()
        assert data["type"] == "json"
        assert data["truncated"] is True
        # Content should be string (not parsed) since truncated
        assert isinstance(data["content"], str)
        assert data["size"] == 600_000


# ═══════════════════════════════════════════════════════════════
#  5.7 — Corrupt JSON
# ═══════════════════════════════════════════════════════════════

class TestCorruptJSON:
    def test_corrupt_json_returns_raw_with_error(self, client, tmp_path):
        """Corrupt JSON should return raw text + parse_error, not crash."""
        path = tmp_path / "Keyword" / "broken.json"
        path.write_text("{invalid json!!! [[[", encoding="utf-8")

        response = client.get("/api/files/keyword/broken.json/preview")
        assert response.status_code == 200
        data = response.json()
        assert data["type"] == "json"
        assert "parse_error" in data
        assert isinstance(data["content"], str)
        assert "{invalid json" in data["content"]


class TestFileDownload:
    def test_download_success(self, client, tmp_path):
        """Should return FileResponse and correct media headers."""
        text = "Hello Download"
        path = tmp_path / "work" / "input" / "test_dl.txt"
        path.write_text(text, encoding="utf-8")

        response = client.get("/api/files/input/test_dl.txt/download")
        assert response.status_code == 200
        assert response.text == text
        assert response.headers["content-type"] == "text/plain; charset=utf-8"

    def test_download_not_found(self, client):
        """Should return 404 for missing file."""
        response = client.get("/api/files/input/nonexistent.txt/download")
        assert response.status_code == 404

