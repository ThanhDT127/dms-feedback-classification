"""Unit tests for spreadsheet template and column validation enhancements in files API."""

from __future__ import annotations

import io
from pathlib import Path

import pandas as pd
import pytest
from conftest import apply_auth_overrides
from fastapi.testclient import TestClient

from dms.analytics import FeedbackAnalyticsRepository


class TestFileEnhancements:
    @pytest.fixture
    def client(self, tmp_path: Path, monkeypatch) -> TestClient:
        """Create a test client with WORK_DIR pointing to tmp_path."""
        import dms.web.api.files as files_module

        monkeypatch.setattr(files_module, "WORK_DIR", tmp_path)

        from dms.web.app import create_app

        app = create_app()
        apply_auth_overrides(app)
        repository = FeedbackAnalyticsRepository(tmp_path / "classification_jobs.db")
        app.dependency_overrides[files_module.get_feedback_analytics_repository] = lambda: (
            repository
        )
        return TestClient(app, raise_server_exceptions=True)

    def test_get_template(self, client) -> None:
        """Endpoint should dynamically generate and return template Excel file."""
        resp = client.get("/api/files/template")
        assert resp.status_code == 200
        assert (
            resp.headers["content-type"]
            == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
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
            files={
                "file": (
                    "valid.xlsx",
                    valid_content,
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
        )
        assert resp.status_code == 200
        assert resp.json()["filename"] == "valid.xlsx"

        # Verify it was saved to the input folder
        input_file = tmp_path / "input" / "valid.xlsx"
        assert input_file.is_file()

    def test_upload_ingests_valid_workbook_into_analytics(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        import dms.web.api.files as files_module
        from dms.web.app import create_app

        monkeypatch.setattr(files_module, "WORK_DIR", tmp_path)
        repository = FeedbackAnalyticsRepository(tmp_path / "classification_jobs.db")
        app = create_app()
        apply_auth_overrides(app)
        app.dependency_overrides[files_module.get_feedback_analytics_repository] = lambda: (
            repository
        )
        client = TestClient(app, raise_server_exceptions=True)
        frame = pd.DataFrame(
            {
                "Mã vấn đề": ["A-001"],
                "Nội dung vấn đề": ["Khách cần hỗ trợ"],
                "Tên đơn vị": ["Truyền thống Vùng 1"],
            }
        )
        content = io.BytesIO()
        frame.to_excel(content, index=False)

        response = client.post(
            "/api/files/upload",
            files={"file": ("feedback.xlsx", content.getvalue(), "application/octet-stream")},
        )

        assert response.status_code == 200
        assert response.json()["ingested_rows"] == 1
        assert repository.fetch_current_records()[0]["issue_code"] == "A-001"

    def test_upload_keeps_file_when_analytics_ingest_fails(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        import dms.web.api.files as files_module
        from dms.web.app import create_app

        monkeypatch.setattr(files_module, "WORK_DIR", tmp_path)
        monkeypatch.setattr(
            files_module,
            "ingest_managed_workbook",
            lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("analytics unavailable")),
            raising=False,
        )
        app = create_app()
        apply_auth_overrides(app)
        client = TestClient(app, raise_server_exceptions=True)
        content = io.BytesIO()
        pd.DataFrame({"Nội dung": ["Vẫn giữ file"]}).to_excel(content, index=False)

        response = client.post(
            "/api/files/upload",
            files={"file": ("kept.xlsx", content.getvalue(), "application/octet-stream")},
        )

        assert response.status_code == 200
        assert response.json()["ingested_rows"] == 0
        assert response.json()["ingest_error"] == "Không thể đưa file vào phân tích"
        assert "analytics unavailable" not in response.text
        assert (tmp_path / "input" / "kept.xlsx").is_file()

    def test_admin_can_ingest_existing_local_input_file(self, tmp_path: Path, monkeypatch) -> None:
        import dms.web.api.files as files_module
        from dms.web.app import create_app

        monkeypatch.setattr(files_module, "WORK_DIR", tmp_path)
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        workbook = input_dir / "existing.xlsx"
        pd.DataFrame(
            {
                "Mã vấn đề": ["EXISTING-001"],
                "Nội dung vấn đề": ["File đã có sẵn"],
            }
        ).to_excel(workbook, index=False)
        repository = FeedbackAnalyticsRepository(tmp_path / "classification_jobs.db")
        app = create_app()
        apply_auth_overrides(app)
        app.dependency_overrides[files_module.get_feedback_analytics_repository] = lambda: (
            repository
        )
        client = TestClient(app, raise_server_exceptions=True)

        response = client.post("/api/files/input/existing.xlsx/ingest")

        assert response.status_code == 200
        assert response.json()["filename"] == "existing.xlsx"
        assert response.json()["ingested_rows"] == 1
        assert repository.fetch_current_records()[0]["issue_code"] == "EXISTING-001"

    def test_ingest_existing_input_rejects_missing_file(self, client: TestClient) -> None:
        response = client.post("/api/files/input/missing.xlsx/ingest")

        assert response.status_code == 404

    def test_manual_ingest_hides_internal_exception_details(
        self, client: TestClient, monkeypatch
    ) -> None:
        import dms.web.api.files as files_module

        input_dir = files_module._work_dir() / "input"
        input_dir.mkdir(parents=True, exist_ok=True)
        pd.DataFrame({"Nội dung": ["test"]}).to_excel(input_dir / "secret.xlsx", index=False)
        monkeypatch.setattr(
            files_module,
            "ingest_managed_workbook",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                RuntimeError("C:/private/schema.db failed")
            ),
        )

        response = client.post("/api/files/input/secret.xlsx/ingest")

        assert response.status_code == 422
        assert response.json()["detail"] == "Không thể đưa file vào phân tích"
        assert "C:/private" not in response.text

    def test_upload_invalid_excel_missing_column(self, client, tmp_path: Path) -> None:
        """Excel missing the required text column should be rejected and cleaned up."""
        df = pd.DataFrame({"Tên sản phẩm": ["Sản phẩm A"]})
        out = io.BytesIO()
        with pd.ExcelWriter(out, engine="openpyxl") as writer:
            df.to_excel(writer, index=False)
        invalid_content = out.getvalue()

        resp = client.post(
            "/api/files/upload",
            files={
                "file": (
                    "invalid.xlsx",
                    invalid_content,
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
        )
        assert resp.status_code == 400
        assert "Cột dữ liệu không hợp lệ" in resp.json()["detail"]

        # Verify it was cleaned up and does not exist in target dir
        input_file = tmp_path / "input" / "invalid.xlsx"
        assert not input_file.exists()
