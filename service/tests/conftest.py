from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dms.settings import Settings


class DummySession:
    def __init__(self):
        self.calls = []

    def get(self, *args, **kwargs):
        self.calls.append(("get", args, kwargs))
        raise NotImplementedError

    def post(self, *args, **kwargs):
        self.calls.append(("post", args, kwargs))
        raise NotImplementedError

    def put(self, *args, **kwargs):
        self.calls.append(("put", args, kwargs))
        raise NotImplementedError


class DummyAuthProvider:
    def get_headers(self):
        return {"Authorization": "Bearer test-token", "Content-Type": "application/json"}


@pytest.fixture
def settings(tmp_path: Path, monkeypatch) -> Settings:
    s = Settings(
        azure_tenant_id="tenant",
        azure_client_id="client",
        azure_client_secret="secret",
        sharepoint_drive_id="drive",
        sharepoint_root_folder_id="root",
        gemini_backend="vertex",
        gcp_project_id="project",
        data_dir=tmp_path / "data",
        work_dir=tmp_path / "work",
        log_dir=tmp_path / "logs",
        notification_recipients_raw="alpha@example.com,beta@example.com",
        notification_sender_email="sender@example.com",
    )
    monkeypatch.setattr("dms.settings.get_settings", lambda: s)
    return s


@pytest.fixture
def mock_session() -> DummySession:
    return DummySession()


@pytest.fixture
def mock_auth_provider() -> DummyAuthProvider:
    return DummyAuthProvider()
