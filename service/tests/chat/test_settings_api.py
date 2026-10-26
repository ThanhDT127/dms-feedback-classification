"""Tests for Chatbot configuration fields in settings API (GET, PUT, PATCH)."""

import asyncio
import os

import pytest
from conftest import apply_auth_overrides
from dotenv import dotenv_values
from fastapi.testclient import TestClient

from dms.settings import Settings, get_settings
from dms.web import deps
from dms.web.api import settings_api
from dms.web.app import create_app


@pytest.fixture(autouse=True)
def isolate_chat_env(monkeypatch):
    """Ensure tests in this module do not leak CHAT_* env vars to subsequent test suites."""
    chat_keys = [
        "CHAT_ENABLED",
        "CHAT_GEMINI_MODEL",
        "CHAT_MILESTONE",
        "CHAT_ENABLED_PATTERNS",
    ]
    orig_env = {k: os.environ.get(k) for k in chat_keys}
    for k in chat_keys:
        monkeypatch.delenv(k, raising=False)
    try:
        yield
    finally:
        for k, v in orig_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        get_settings.cache_clear()
        deps.reset()


@pytest.fixture
def client(tmp_path, monkeypatch):
    # Ensure complete isolation from host environment variables
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_BACKEND", raising=False)
    monkeypatch.delenv("GCP_PROJECT_ID", raising=False)
    monkeypatch.delenv("AZURE_TENANT_ID", raising=False)
    monkeypatch.delenv("AZURE_CLIENT_ID", raising=False)
    monkeypatch.delenv("AZURE_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("SHAREPOINT_DRIVE_ID", raising=False)
    monkeypatch.delenv("SHAREPOINT_ROOT_FOLDER_ID", raising=False)
    monkeypatch.delenv("CHAT_ENABLED", raising=False)
    monkeypatch.delenv("CHAT_GEMINI_MODEL", raising=False)
    monkeypatch.delenv("CHAT_MILESTONE", raising=False)
    monkeypatch.delenv("CHAT_ENABLED_PATTERNS", raising=False)

    # Set up mock .env file with valid settings so it passes baseline validation
    mock_env = tmp_path / ".env"
    mock_env.write_text(
        "AZURE_TENANT_ID=tenant\n"
        "AZURE_CLIENT_ID=client\n"
        "AZURE_CLIENT_SECRET=secret\n"
        "SHAREPOINT_DRIVE_ID=drive\n"
        "SHAREPOINT_ROOT_FOLDER_ID=root\n"
        "GEMINI_BACKEND=vertex\n"
        "GCP_PROJECT_ID=project\n"
        "CHAT_ENABLED=true\n"
        "CHAT_MILESTONE=M5\n"
        "CHAT_ENABLED_PATTERNS=sql_template,semantic_view,fts5_search,json_extract\n",
        encoding="utf-8",
    )

    new_config = dict(Settings.model_config)
    new_config["env_file"] = str(mock_env)
    monkeypatch.setattr(Settings, "model_config", new_config)

    kw_dir = tmp_path / "Keyword"
    kw_dir.mkdir()

    monkeypatch.setattr("dms.settings.SERVICE_DIR", tmp_path)
    monkeypatch.setattr("dms.web.api.settings_api.SERVICE_DIR", tmp_path)

    # Clear cache and reset deps
    get_settings.cache_clear()
    deps.reset()

    app = create_app()
    apply_auth_overrides(app)
    return TestClient(app)


def test_get_settings_contains_chat_defaults(client):
    """GET /api/settings returns chatbot configuration with default values."""
    response = client.get("/api/settings")
    assert response.status_code == 200
    data = response.json()

    assert "chat_enabled" in data
    assert data["chat_enabled"] is True
    assert data.get("chat_milestone") == "M5"
    assert "chat_gemini_model" in data

    patterns = data.get("chat_enabled_patterns", "")
    assert "sql_template" in patterns
    assert "semantic_view" in patterns
    assert "fts5_search" in patterns
    assert "json_extract" in patterns


def test_patch_settings_chat_fields_success(client, tmp_path):
    """PATCH /api/settings successfully updates chatbot fields and persists to .env."""
    payload = {
        "chat_enabled": False,
        "chat_gemini_model": "gemini-2.5-pro",
        "chat_milestone": "M4",
        "chat_enabled_patterns": "sql_template,fts5_search",
    }
    response = client.patch("/api/settings", json=payload)
    assert response.status_code == 200
    result = response.json()
    assert result["success"] is True

    # Verify .env has been modified
    parsed = dotenv_values(tmp_path / ".env")
    assert parsed["CHAT_ENABLED"] == "false"
    assert parsed["CHAT_GEMINI_MODEL"] == "gemini-2.5-pro"
    assert parsed["CHAT_MILESTONE"] == "M4"
    assert parsed["CHAT_ENABLED_PATTERNS"] == "sql_template,fts5_search"

    # Re-apply auth overrides after deps.reset()
    apply_auth_overrides(client.app)
    get_res = client.get("/api/settings")
    assert get_res.status_code == 200
    updated_data = get_res.json()
    assert updated_data["chat_enabled"] is False
    assert updated_data["chat_gemini_model"] == "gemini-2.5-pro"
    assert updated_data["chat_milestone"] == "M4"
    assert updated_data["chat_enabled_patterns"] == "sql_template,fts5_search"


def test_patch_settings_chat_patterns_as_list(client, tmp_path):
    """PATCH /api/settings accepts chat_enabled_patterns as a list."""
    payload = {
        "chat_enabled_patterns": ["sql_template", "semantic_view"],
    }
    response = client.patch("/api/settings", json=payload)
    assert response.status_code == 200

    parsed = dotenv_values(tmp_path / ".env")
    assert parsed["CHAT_ENABLED_PATTERNS"] == "sql_template,semantic_view"

    apply_auth_overrides(client.app)
    get_res = client.get("/api/settings")
    assert get_res.json()["chat_enabled_patterns"] == "sql_template,semantic_view"


def test_put_settings_chat_fields_backward_compatible(client, tmp_path):
    """PUT /api/settings also updates chat configuration (backward compatibility)."""
    payload = {
        "chat_milestone": "M3",
    }
    response = client.put("/api/settings", json=payload)
    assert response.status_code == 200

    parsed = dotenv_values(tmp_path / ".env")
    assert parsed["CHAT_MILESTONE"] == "M3"

    apply_auth_overrides(client.app)
    get_res = client.get("/api/settings")
    assert get_res.json()["chat_milestone"] == "M3"


def test_patch_settings_reset_chat_gemini_model(client, tmp_path):
    """Setting chat_gemini_model to empty string clears the field to inherit pipeline model."""
    client.patch("/api/settings", json={"chat_gemini_model": "gemini-2.5-flash"})
    parsed1 = dotenv_values(tmp_path / ".env")
    assert parsed1["CHAT_GEMINI_MODEL"] == "gemini-2.5-flash"

    # Reset to empty string
    client.patch("/api/settings", json={"chat_gemini_model": ""})
    parsed2 = dotenv_values(tmp_path / ".env")
    assert parsed2.get("CHAT_GEMINI_MODEL") in ("", None)

    apply_auth_overrides(client.app)
    get_res = client.get("/api/settings")
    assert get_res.json()["chat_gemini_model"] == ""


def test_patch_settings_invalid_milestone_validation_error(client, tmp_path):
    """PATCH /api/settings with invalid milestone triggers validation error and rolls back."""
    old_env = (tmp_path / ".env").read_text(encoding="utf-8")
    payload = {"chat_milestone": "M99"}
    response = client.patch("/api/settings", json=payload)
    assert response.status_code == 400
    assert "Cấu hình không hợp lệ" in response.json()["detail"]

    # Verify rollback: .env remains unchanged
    new_env = (tmp_path / ".env").read_text(encoding="utf-8")
    assert new_env == old_env


def test_patch_settings_invalid_patterns_validation_error(client, tmp_path):
    """PATCH /api/settings with invalid pattern triggers validation error and rolls back."""
    old_env = (tmp_path / ".env").read_text(encoding="utf-8")
    payload = {"chat_enabled_patterns": "invalid_pattern_xyz"}
    response = client.patch("/api/settings", json=payload)
    assert response.status_code == 400
    assert "Cấu hình không hợp lệ" in response.json()["detail"]

    # Verify rollback
    new_env = (tmp_path / ".env").read_text(encoding="utf-8")
    assert new_env == old_env


def test_get_settings_fallback_reads_chat_keys(monkeypatch):
    """When get_settings_partial falls back to raw .env, chat keys are mapped to lowercase."""
    raw_env_fallback = {
        "CHAT_ENABLED": "true",
        "CHAT_MILESTONE": "M5",
        "CHAT_GEMINI_MODEL": "gemini-2.5-flash",
        "CHAT_ENABLED_PATTERNS": "sql_template,fts5_search",
    }
    monkeypatch.setattr(settings_api.deps, "get_settings_partial", lambda: raw_env_fallback)
    result = asyncio.run(settings_api.get_settings(admin={"role": "admin"}))

    assert result["chat_enabled"] is True
    assert result["chat_milestone"] == "M5"
    assert result["chat_gemini_model"] == "gemini-2.5-flash"
    assert result["chat_enabled_patterns"] == "sql_template,fts5_search"
