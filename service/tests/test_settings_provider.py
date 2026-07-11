from __future__ import annotations

from dms.settings import Settings
from dms.web import deps


def _set_required_env(monkeypatch, tmp_path, *, data_dir=None):
    monkeypatch.setenv("AZURE_TENANT_ID", "tenant")
    monkeypatch.setenv("AZURE_CLIENT_ID", "client")
    monkeypatch.setenv("AZURE_CLIENT_SECRET", "secret")
    monkeypatch.setenv("SHAREPOINT_DRIVE_ID", "drive")
    monkeypatch.setenv("SHAREPOINT_ROOT_FOLDER_ID", "root")
    monkeypatch.setenv("GEMINI_BACKEND", "vertex")
    monkeypatch.setenv("GCP_PROJECT_ID", "project")
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-key-that-is-at-least-32-bytes-long")
    monkeypatch.setenv("DATA_DIR", str(data_dir or tmp_path / "data"))
    monkeypatch.setenv("WORK_DIR", str(tmp_path / "work"))
    monkeypatch.setenv("LOG_DIR", str(tmp_path / "logs"))


def test_web_deps_settings_does_not_cache_none_forever(monkeypatch, tmp_path):
    isolated_env = tmp_path / ".env"
    isolated_env.write_text("", encoding="utf-8")
    new_config = dict(Settings.model_config)
    new_config["env_file"] = str(isolated_env)
    monkeypatch.setattr(Settings, "model_config", new_config)
    deps.reset()

    assert deps.get_settings() is None

    _set_required_env(monkeypatch, tmp_path)

    settings = deps.get_settings()
    assert settings is not None
    assert settings.azure_tenant_id == "tenant"
    deps.reset()


def test_deps_reset_rebuilds_settings_dependent_singletons(monkeypatch, tmp_path):
    first_data = tmp_path / "data-1"
    second_data = tmp_path / "data-2"
    _set_required_env(monkeypatch, tmp_path, data_dir=first_data)
    deps.reset()

    first_store = deps.get_user_store()
    assert first_store is not None
    assert first_store.db_path == first_data / "users.json"

    monkeypatch.setenv("DATA_DIR", str(second_data))
    deps.reset()

    second_store = deps.get_user_store()
    assert second_store is not None
    assert second_store.db_path == second_data / "users.json"
    assert second_store is not first_store
    deps.reset()
