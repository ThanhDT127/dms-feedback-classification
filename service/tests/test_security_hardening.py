from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from starlette.websockets import WebSocketDisconnect

from dms.jwt_utils import create_token, decode_token
from dms.notification import NotificationService
from dms.settings import Settings
from dms.token_blacklist import clear, is_revoked, revoke
from dms.user_store import UserStore
from dms.web import deps
from dms.web.app import create_app
from dms.web.rate_limit import limiter
from dms.web.ws.connection_limiter import WS_LIMIT_CLOSE_CODE, ws_connection_limiter

SECRET = "test-secret-key-that-is-at-least-32-bytes-long"


@pytest.fixture(autouse=True)
def reset_security_state():
    clear()
    limiter.reset()
    ws_connection_limiter.reset()
    deps.reset()
    yield
    clear()
    limiter.reset()
    ws_connection_limiter.reset()
    deps.reset()


def _settings(tmp_path: Path, *, environment: str = "development") -> Settings:
    return Settings(
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
        environment=environment,
    )


def _client(monkeypatch, settings: Settings, store: UserStore | None = None) -> TestClient:
    monkeypatch.setattr(deps, "get_settings", lambda: settings)
    if store is not None:
        monkeypatch.setattr(deps, "get_user_store", lambda: store)
    return TestClient(create_app())


def test_settings_require_strong_jwt_secret(monkeypatch):
    monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
    with pytest.raises(ValidationError):
        Settings(
            azure_tenant_id="tenant",
            azure_client_id="client",
            azure_client_secret="secret",
            sharepoint_drive_id="drive",
            sharepoint_root_folder_id="root",
            gemini_backend="vertex",
            gcp_project_id="project",
            _env_file=None,
        )

    with pytest.raises(ValidationError, match="JWT_SECRET_KEY must be at least 32"):
        Settings(
            azure_tenant_id="tenant",
            azure_client_id="client",
            azure_client_secret="secret",
            sharepoint_drive_id="drive",
            sharepoint_root_folder_id="root",
            gemini_backend="vertex",
            gcp_project_id="project",
            jwt_secret_key="weak",
            _env_file=None,
        )


def test_user_store_random_admin_requires_password_change(tmp_path):
    store = UserStore(tmp_path / "users.json", default_admin_password="")
    admin = store.get_user("admin")
    assert admin is not None
    assert admin["must_change_password"] is True
    assert store.authenticate("admin", "admin123") is None

    assert store.update_password("admin", "new-password")
    updated = store.get_user("admin")
    assert updated is not None
    assert updated["must_change_password"] is False


def test_blacklisted_access_token_is_rejected():
    token = create_token(
        subject="alice",
        token_type="access",
        secret_key=SECRET,
        expires_minutes=30,
    )
    payload = decode_token(token, SECRET, expected_type="access")
    revoke(payload["jti"], payload["exp"])

    with pytest.raises(ValueError, match="revoked"):
        decode_token(token, SECRET, expected_type="access")


def test_blacklist_ignores_expired_entries():
    revoke("expired-token", time.time() - 1)
    assert is_revoked("expired-token") is False


def test_logout_revokes_current_access_token(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    store = UserStore(tmp_path / "users.json", default_admin_password="admin-password")
    token = create_token("admin", "access", settings.jwt_secret_key, expires_minutes=30)
    client = _client(monkeypatch, settings, store)

    response = client.post("/api/auth/logout", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200

    rejected = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert rejected.status_code == 401


def test_change_password_revokes_old_token_and_returns_new_tokens(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    store = UserStore(tmp_path / "users.json", default_admin_password="old-password")
    token = create_token("admin", "access", settings.jwt_secret_key, expires_minutes=30)
    client = _client(monkeypatch, settings, store)

    response = client.post(
        "/api/auth/change-password",
        json={"old_password": "old-password", "new_password": "new-password"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["access_token"]
    assert body["refresh_token"]
    assert store.authenticate("admin", "new-password") is not None

    rejected = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert rejected.status_code == 401


def test_login_rate_limit_returns_retry_after(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    store = UserStore(tmp_path / "users.json", default_admin_password="admin-password")
    client = _client(monkeypatch, settings, store)

    last_response = None
    for _ in range(6):
        last_response = client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "wrong-password"},
        )

    assert last_response is not None
    assert last_response.status_code == 429
    assert last_response.headers["Retry-After"] == "60"


def test_login_success_and_failure_contract(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    store = UserStore(tmp_path / "users.json", default_admin_password="admin-password")
    client = _client(monkeypatch, settings, store)

    ok = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "admin-password"},
    )
    assert ok.status_code == 200
    body = ok.json()
    assert body["access_token"]
    assert body["refresh_token"]
    assert body["token_type"] == "bearer"
    assert body["user"]["username"] == "admin"

    failed = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "wrong-password"},
    )
    assert failed.status_code == 401


def test_refresh_success_and_failure_contract(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    store = UserStore(tmp_path / "users.json", default_admin_password="admin-password")
    client = _client(monkeypatch, settings, store)
    refresh = create_token("admin", "refresh", settings.jwt_secret_key, expires_minutes=30)

    ok = client.post("/api/auth/refresh", json={"refresh_token": refresh})
    assert ok.status_code == 200
    assert ok.json()["access_token"]

    failed = client.post("/api/auth/refresh", json={"refresh_token": "not-a-token"})
    assert failed.status_code == 401


@pytest.mark.parametrize("path", ["/ws/logs", "/ws/classify/job-1"])
def test_websocket_rejects_missing_token(tmp_path, monkeypatch, path):
    settings = _settings(tmp_path)
    client = _client(monkeypatch, settings)

    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect(path):
            pass

    assert exc_info.value.code == 4001
    assert exc_info.value.reason == "Authentication required"


@pytest.mark.parametrize("path", ["/ws/logs", "/ws/classify/job-1"])
def test_websocket_rejects_invalid_token(tmp_path, monkeypatch, path):
    settings = _settings(tmp_path)
    client = _client(monkeypatch, settings)

    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect(f"{path}?token=not-a-jwt"):
            pass

    assert exc_info.value.code == 4001
    assert exc_info.value.reason == "Invalid or expired token"


@pytest.mark.parametrize("path", ["/ws/logs", "/ws/classify/job-1"])
def test_websocket_rejects_expired_token(tmp_path, monkeypatch, path):
    settings = _settings(tmp_path)
    token = create_token("admin", "access", settings.jwt_secret_key, expires_minutes=-1)
    client = _client(monkeypatch, settings)

    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect(f"{path}?token={token}"):
            pass

    assert exc_info.value.code == 4001
    assert exc_info.value.reason == "Invalid or expired token"


@pytest.mark.parametrize("path", ["/ws/logs", "/ws/classify/job-1"])
def test_websocket_rejects_missing_server_settings(monkeypatch, path):
    monkeypatch.setattr(deps, "get_settings", lambda: None)
    client = TestClient(create_app())

    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect(f"{path}?token=anything"):
            pass

    assert exc_info.value.code == 4001
    assert exc_info.value.reason == "Server configuration unavailable"


def test_websocket_connection_limit_exceeded(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    token = create_token("admin", "access", settings.jwt_secret_key, expires_minutes=30)
    client = _client(monkeypatch, settings)
    monkeypatch.setattr(ws_connection_limiter, "max_per_identity", 1)

    with client.websocket_connect(f"/ws/logs?token={token}"):
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect(f"/ws/logs?token={token}"):
                pass

    assert exc_info.value.code == WS_LIMIT_CLOSE_CODE
    assert exc_info.value.reason == "WebSocket connection limit exceeded"


def test_username_validation_on_create_and_update(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    store = UserStore(tmp_path / "users.json", default_admin_password="admin-password")
    client = _client(monkeypatch, settings, store)
    token = create_token("admin", "access", settings.jwt_secret_key, expires_minutes=30)
    headers = {"Authorization": f"Bearer {token}"}

    valid = client.post(
        "/api/users",
        json={"username": "valid_user-1", "password": "password123", "role": "user"},
        headers=headers,
    )
    assert valid.status_code == 200

    for username in ("ab", "bad user", "a" * 51):
        invalid = client.post(
            "/api/users",
            json={"username": username, "password": "password123", "role": "user"},
            headers=headers,
        )
        assert invalid.status_code == 400

    update_invalid = client.put(
        "/api/users/valid_user-1",
        json={"username": "bad/user"},
        headers=headers,
    )
    assert update_invalid.status_code == 400


def test_settings_api_masks_secrets_and_removes_secret_endpoint(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    monkeypatch.setattr(deps, "get_settings_partial", lambda: settings.model_dump())
    app = create_app()
    app.dependency_overrides[deps.get_admin_user] = lambda: {"username": "admin", "role": "admin"}
    client = TestClient(app)

    response = client.get("/api/settings")
    assert response.status_code == 200
    body = response.json()
    assert body["azure_client_secret"] == "••••••••••••"
    assert body["jwt_secret_key"] == "••••••••••••"
    assert SECRET not in str(body)

    assert client.get("/api/settings/secret/jwt_secret_key").status_code == 404


def test_notification_html_escapes_user_controlled_fields():
    html = NotificationService._build_error_html(
        '<img src=x onerror="alert(1)">.xlsx',
        '<script>alert("x")</script>',
    )

    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "&lt;img" in html
    assert "&quot;alert(1)&quot;" in html


def test_security_headers_and_cors_defaults(tmp_path, monkeypatch):
    settings = _settings(tmp_path, environment="production")
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.delenv("CORS_ALLOWED_ORIGINS", raising=False)
    client = _client(monkeypatch, settings)

    response = client.get("/", headers={"Origin": "http://localhost:8501"})
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Strict-Transport-Security"].startswith("max-age=")
    assert response.headers["access-control-allow-origin"] == "http://localhost:8501"
