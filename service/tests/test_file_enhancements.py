"""Unit tests for spreadsheet template and column validation enhancements in files API."""

from __future__ import annotations

import io
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient


class TestFileEnhancements:
    @pytest.fixture
    def client(self, tmp_path: Path, monkeypatch) -> TestClient:
        """Create a test client with WORK_DIR pointing to tmp_path."""
        import dms.web.api.files as files_module
        monkeypatch.setattr(files_module, "WORK_DIR", tmp_path)

        from dms.web.app import create_app
        app = create_app()
        return TestClient(app, raise_server_exceptions=True)

    def test_get_template(self, client) -> None:
        """Endpoint should dynamically generate and return template Excel file."""
        resp = client.get("/api/files/template")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        assert "attachment" in resp.headers["content-disposition"]
        assert "template_dms.xlsx" in resp.headers["content-disposition"]

        # Verify it has standard columns
        df = pd.read_excel(io.BytesIO(resp.content))
        assert "Nội dung" in df.columns

    def test_upload_valid_excel(self, client, tmp_path: Path) -> None:
        """Excel with content column should upload successfully and be saved."""
        df = pd.DataFrame({"Nội dung": ["test content"]})
        out = io.BytesIO()
        with pd.ExcelWriter(out, engine="openpyxl") as writer:
            df.to_excel(writer, index=False)
        valid_content = out.getvalue()

        resp = client.post(
            "/api/files/upload",
            files={"file": ("valid.xlsx", valid_content, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
        assert resp.status_code == 200
        assert resp.json()["filename"] == "valid.xlsx"

        # Verify it was saved to the input folder
        input_file = tmp_path / "input" / "valid.xlsx"
        assert input_file.is_file()

    def test_upload_invalid_excel_missing_column(self, client, tmp_path: Path) -> None:
        """Excel missing the required text column should be rejected and cleaned up."""
        df = pd.DataFrame({"Tên sản phẩm": ["Sản phẩm A"]})
        out = io.BytesIO()
        with pd.ExcelWriter(out, engine="openpyxl") as writer:
            df.to_excel(writer, index=False)
        invalid_content = out.getvalue()

        resp = client.post(
            "/api/files/upload",
            files={"file": ("invalid.xlsx", invalid_content, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
        assert resp.status_code == 400
        assert "Cột dữ liệu không hợp lệ" in resp.json()["detail"]

        # Verify it was cleaned up and does not exist in target dir
        input_file = tmp_path / "input" / "invalid.xlsx"
        assert not input_file.exists()
