from __future__ import annotations

import pytest
from pydantic import ValidationError

from dms.exceptions import ConfigurationError
from dms.settings import Settings, get_settings


def test_settings_validation_defaults_and_paths(tmp_path):
    settings = Settings(
        azure_tenant_id="tenant",
        azure_client_id="client",
        azure_client_secret="secret",
        sharepoint_drive_id="drive",
        sharepoint_root_folder_id="root",
        gemini_backend="vertex",
        gcp_project_id="project",
        poll_interval_seconds="123",
        data_dir=tmp_path / "data",
        work_dir=tmp_path / "work",
        log_dir=tmp_path / "logs",
    )
    assert settings.poll_interval_seconds == 123
    assert settings.keyword_dir == tmp_path / "data" / "Keyword"
    assert settings.model_dir == tmp_path / "data" / "Model"
    assert settings.df_products_path.name.endswith(".xlsx")


def test_settings_model_dir_override(tmp_path):
    settings = Settings(
        azure_tenant_id="tenant",
        azure_client_id="client",
        azure_client_secret="secret",
        sharepoint_drive_id="drive",
        sharepoint_root_folder_id="root",
        gemini_backend="vertex",
        gcp_project_id="project",
        data_dir=tmp_path / "data",
        model_dir_override=tmp_path / "custom-model",
    )
    assert settings.model_dir == tmp_path / "custom-model"


def test_settings_missing_required_fields_raise_validation_error():
    with pytest.raises(ValidationError):
        Settings(
            azure_tenant_id="",
            azure_client_id="client",
            azure_client_secret="secret",
            sharepoint_drive_id="drive",
            sharepoint_root_folder_id="root",
            gemini_backend="vertex",
            gcp_project_id="project",
        )


def test_settings_apikey_requires_api_key():
    with pytest.raises(ValidationError):
        Settings(
            azure_tenant_id="tenant",
            azure_client_id="client",
            azure_client_secret="secret",
            sharepoint_drive_id="drive",
            sharepoint_root_folder_id="root",
            gemini_backend="apikey",
            gemini_api_key="",
        )


def test_notification_recipients_fallback():
    settings = Settings(
        azure_tenant_id="tenant",
        azure_client_id="client",
        azure_client_secret="secret",
        sharepoint_drive_id="drive",
        sharepoint_root_folder_id="root",
        gemini_backend="vertex",
        gcp_project_id="project",
        notification_recipients_raw="",
        notification_email="legacy@example.com",
    )
    assert settings.notification_recipients == ["legacy@example.com"]


def test_get_settings_wraps_validation_error(monkeypatch):
    get_settings.cache_clear()

    class BrokenSettings:
        def __init__(self):
            raise ValidationError.from_exception_data(
                "Settings",
                [
                    {
                        "type": "missing",
                        "loc": ("AZURE_TENANT_ID",),
                        "msg": "missing input",
                        "input": None,
                    }
                ],
            )

    monkeypatch.setattr("dms.settings.Settings", BrokenSettings)
    with pytest.raises(ConfigurationError):
        get_settings()
    get_settings.cache_clear()
