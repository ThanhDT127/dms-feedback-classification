"""Unit tests for path traversal guard and upload size limit in files API."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from conftest import apply_auth_overrides

# ---------------------------------------------------------------------------
# _validate_safe_path tests (unit-level)
# ---------------------------------------------------------------------------


class TestValidateSafePath:
    def _get_fn(self):
        from dms.web.api.files import _validate_safe_path

        return _validate_safe_path

    def test_normal_filename_passes(self, tmp_path: Path) -> None:
        fn = self._get_fn()
        result = fn(tmp_path, "report.xlsx")
        assert result == (tmp_path / "report.xlsx").resolve()

    def test_strips_directory_traversal(self, tmp_path: Path) -> None:
        fn = self._get_fn()
        # Path.name strips the directory part: '../../evil.xlsx' → 'evil.xlsx'
        result = fn(tmp_path, "../../evil.xlsx")
        assert result == (tmp_path / "evil.xlsx").resolve()
        assert str(result).startswith(str(tmp_path.resolve()))

    def test_empty_filename_raises(self, tmp_path: Path) -> None:
        fn = self._get_fn()
        with pytest.raises(HTTPException) as exc_info:
            fn(tmp_path, "")
        assert exc_info.value.status_code == 400

    def test_dot_only_raises(self, tmp_path: Path) -> None:
        fn = self._get_fn()
        with pytest.raises(HTTPException) as exc_info:
            fn(tmp_path, ".")
        assert exc_info.value.status_code == 400

    def test_normal_subdirectory_stripping(self, tmp_path: Path) -> None:
        fn = self._get_fn()
        # Even 'subdir/file.xlsx' gets stripped to 'file.xlsx'
        result = fn(tmp_path, "subdir/file.xlsx")
        assert result.name == "file.xlsx"
        assert str(result).startswith(str(tmp_path.resolve()))


# ---------------------------------------------------------------------------
# Upload size limit tests (via TestClient)
# ---------------------------------------------------------------------------


class TestUploadSizeLimit:
    @pytest.fixture
    def client(self, tmp_path: Path, monkeypatch):
        """Create a test client with WORK_DIR pointing to tmp_path."""
        import dms.web.api.files as files_module

        monkeypatch.setattr(files_module, "WORK_DIR", tmp_path)

        from dms.web.app import create_app

        app = create_app()
        apply_auth_overrides(app)
        return TestClient(app, raise_server_exceptions=True)

    def test_upload_within_limit(self, client, tmp_path) -> None:
        """Small file should upload successfully."""
        small_content = b"PK\x03\x04" + b"\x00" * 100  # fake xlsx header
        # Note: endpoint checks .xlsx extension
        resp = client.post(
            "/api/files/upload",
            files={"file": ("test.xlsx", small_content, "application/octet-stream")},
        )
        # May fail with 422 if openpyxl validation, but NOT 413
        assert resp.status_code != 413

    def test_upload_exceeds_limit(self, client, tmp_path) -> None:
        """File > 50MB should be rejected with 413."""
        MAX = 50 * 1024 * 1024
        huge_content = b"x" * (MAX + 1)
        resp = client.post(
            "/api/files/upload",
            files={"file": ("large.xlsx", huge_content, "application/octet-stream")},
        )
        assert resp.status_code == 413
        assert "50MB" in resp.json().get("detail", "")
