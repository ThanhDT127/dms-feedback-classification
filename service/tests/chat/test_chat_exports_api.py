"""``GET /api/chat/exports/{export_id}`` (spec ``chat-report-export``, b10 task 5.1, 5.2)."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from dms.chat.ai.export.export_store import EXPORT_DIR_NAME, ExportStore
from dms.jwt_utils import create_token
from dms.settings import Settings
from dms.token_blacklist import clear
from dms.web import deps
from dms.web.app import create_app
from dms.web.rate_limit import limiter

from .test_chat_ws import SECRET, FakeUserStore

# Endpoint dùng đồng hồ thật để kiểm hạn, nên file mẫu cũng tạo theo giờ thật.
NOW = datetime.now(UTC)


@pytest.fixture(autouse=True)
def reset_state():
    clear()
    limiter.reset()
    yield
    clear()
    limiter.reset()


class Env:
    def __init__(self, tmp_path, monkeypatch, **overrides) -> None:
        values = dict(
            azure_tenant_id="tenant",
            azure_client_id="client",
            azure_client_secret="secret-value",
            sharepoint_drive_id="drive",
            sharepoint_root_folder_id="root",
            gemini_backend="vertex",
            gcp_project_id="project",
            data_dir=tmp_path / "data",
            work_dir=tmp_path / "work",
            log_dir=tmp_path / "logs",
            jwt_secret_key=SECRET,
            default_admin_password="admin-password",
            chat_enabled=True,
        )
        values.update(overrides)
        self.settings = Settings(**values)
        self.store = ExportStore(
            self.settings.work_dir / EXPORT_DIR_NAME,
            ttl_hours=int(self.settings.chat_export_ttl_hours),
        )
        monkeypatch.setattr(deps, "get_settings", lambda: self.settings)
        monkeypatch.setattr(deps, "get_user_store", lambda: FakeUserStore())
        monkeypatch.setattr(deps, "get_chat_services", lambda: object())
        self.client = TestClient(create_app())

    def headers(self, username="alice"):
        token = create_token(username, "access", SECRET, expires_minutes=30)
        return {"Authorization": f"Bearer {token}"}

    def make_export(self, owner="alice", *, filename="báo-cáo tuần.xlsx", now=NOW) -> str:
        export_id, path = self.store.reserve(owner)
        path.write_bytes(b"fake-xlsx-bytes")
        self.store.save_metadata(owner, export_id, filename=filename, size_bytes=15, now=now)
        return export_id


@pytest.fixture
def env(tmp_path, monkeypatch):
    return Env(tmp_path, monkeypatch)


def get(env: Env, export_id: str, username: str = "alice"):
    return env.client.get(f"/api/chat/exports/{export_id}", headers=env.headers(username))


def test_owner_downloads_the_file(env: Env):
    export_id = env.make_export()
    response = get(env, export_id)
    assert response.status_code == 200
    assert response.content == b"fake-xlsx-bytes"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "no-store"
    disposition = response.headers["content-disposition"]
    assert disposition.startswith("attachment;")
    assert "filename*=utf-8''" in disposition


def test_other_user_gets_404(env: Env):
    export_id = env.make_export(owner="alice")
    assert get(env, export_id, username="bob").status_code == 404


def test_admin_also_gets_404(env: Env):
    export_id = env.make_export(owner="alice")
    assert get(env, export_id, username="admin").status_code == 404


@pytest.mark.parametrize(
    "bad",
    ["..%2F..%2Fsettings", "not-hex", "0" * 31, "0" * 33, "ABCDEF0123456789ABCDEF0123456789"],
)
def test_malformed_export_id_gets_404(env: Env, bad: str):
    response = env.client.get(f"/api/chat/exports/{bad}", headers=env.headers())
    assert response.status_code == 404


def test_path_traversal_reads_nothing_outside_the_export_dir(env: Env, tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("top secret", encoding="utf-8")
    response = env.client.get("/api/chat/exports/..%2F..%2Fsecret.txt", headers=env.headers())
    assert response.status_code == 404
    assert b"top secret" not in response.content


def test_expired_export_gets_404(env: Env):
    export_id = env.make_export(now=NOW - timedelta(days=3))
    assert get(env, export_id).status_code == 404


def test_missing_file_gets_404(env: Env):
    export_id = env.make_export()
    env.store.file_path("alice", export_id).unlink()
    assert get(env, export_id).status_code == 404


def test_login_required(env: Env):
    export_id = env.make_export()
    assert env.client.get(f"/api/chat/exports/{export_id}").status_code in (401, 403)


def test_download_is_audited(env: Env, caplog):
    export_id = env.make_export()
    with caplog.at_level(logging.INFO, logger="dms-chat-audit"):
        assert get(env, export_id).status_code == 200
    assert any(record.message == "chat_export_downloaded" for record in caplog.records)
