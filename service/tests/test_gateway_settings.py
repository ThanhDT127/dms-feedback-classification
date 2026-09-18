"""Offline Gateway configuration contract; every credential is synthetic."""

import pytest
from pydantic import ValidationError

from dms.settings import Settings


def make_settings(**overrides):
    values = dict(
        _env_file=None,
        azure_tenant_id="tenant",
        azure_client_id="client",
        azure_client_secret="synthetic-azure-secret",
        sharepoint_drive_id="drive",
        sharepoint_root_folder_id="root",
        jwt_secret_key="synthetic-jwt-secret-at-least-32-characters",
        gemini_backend="gateway",
        gateway_chat_completions_url="https://gateway.example/custom/completions",
        gateway_api_key="synthetic-gateway-key",
        gateway_model="Confirmed/Alias-V1",
    )
    values.update(overrides)
    return Settings(**values)


def test_gateway_only_defaults_preserve_exact_alias_without_google_credentials():
    settings = make_settings()
    assert settings.gemini_backend == "gateway"
    assert settings.gateway_model == "Confirmed/Alias-V1"
    assert settings.gateway_chat_completions_url == "https://gateway.example/custom/completions"
    assert settings.gateway_api_key == "synthetic-gateway-key"
    assert settings.gateway_allow_insecure_http is False
    assert settings.gateway_system_user == ""
    assert settings.fallback_enabled is False
    assert settings.fallback_fail_threshold == 3
    assert settings.fallback_retry_after_s == 60
    assert settings.gcp_project_id == ""
    assert settings.gcp_service_account_json == ""


@pytest.mark.parametrize(
    "overrides",
    [
        {"gateway_chat_completions_url": ""},
        {"gateway_chat_completions_url": "http://gateway.example/exact"},
        {"gateway_chat_completions_url": "ftp://gateway.example/exact"},
        {"gateway_chat_completions_url": "https:///exact"},
        {"gateway_chat_completions_url": "https://user:password@gateway.example/exact"},
        {"gateway_chat_completions_url": "https://gateway.example/exact?key=secret"},
        {"gateway_chat_completions_url": "https://gateway.example/exact#fragment"},
        {"gateway_chat_completions_url": " https://gateway.example/exact"},
        {"gateway_chat_completions_url": "https://gateway.example:invalid/exact"},
        {"gateway_chat_completions_url": "https://gateway.example/exact\\other"},
        {"gateway_api_key": ""},
        {"gateway_api_key": "synthetic key"},
        {"gateway_api_key": "synthetic-key\n"},
        {"gateway_api_key": "${SYNTHETIC_KEY}"},
        {"gateway_api_key": "{{SYNTHETIC_KEY}}"},
        {"gateway_api_key": "<SYNTHETIC_KEY>"},
        {"gateway_model": ""},
        {"gateway_model": " Alias "},
    ],
)
def test_gateway_rejects_invalid_configuration_without_secret_disclosure(overrides):
    with pytest.raises(ValidationError) as caught:
        make_settings(**overrides)
    assert "synthetic-gateway-key" not in str(caught.value)
    assert "synthetic-azure-secret" not in str(caught.value)
    for value in overrides.values():
        if value:
            assert value not in str(caught.value)


def test_gateway_key_is_not_in_repr_or_unrelated_validation_error():
    settings = make_settings()
    assert settings.gateway_api_key not in repr(settings)
    assert settings.gateway_api_key not in str(settings)
    with pytest.raises(ValidationError) as caught:
        make_settings(jwt_secret_key="short")
    assert settings.gateway_api_key not in str(caught.value)


def test_gateway_explicit_http_and_environment_aliases(monkeypatch):
    monkeypatch.setenv("GATEWAY_SYSTEM_USER", "approved-cli-actor")
    settings = make_settings(
        gateway_allow_insecure_http=True,
        gateway_chat_completions_url="http://127.0.0.1:8001/exact",
    )
    assert settings.gateway_chat_completions_url == "http://127.0.0.1:8001/exact"
    assert settings.gateway_system_user == "approved-cli-actor"


@pytest.mark.parametrize(
    "overrides",
    [
        {"fallback_fail_threshold": 0},
        {"fallback_fail_threshold": 1.5},
        {"fallback_enabled": True, "fallback_fail_threshold": 4},
        {"fallback_retry_after_s": 0},
        {"fallback_retry_after_s": float("inf")},
        {"fallback_retry_after_s": float("nan")},
        {"max_retry": 0},
        {"base_wait": -1},
        {"base_wait": float("inf")},
        {"base_wait": float("nan")},
        {"gemini_timeout_seconds": 0},
        {"gemini_timeout_seconds": float("inf")},
        {"gemini_timeout_seconds": float("nan")},
    ],
)
def test_gateway_rejects_invalid_retry_budget(overrides):
    with pytest.raises(ValidationError):
        make_settings(**overrides)


def test_fallback_missing_direct_configuration_is_disabled_with_safe_warning(caplog):
    settings = make_settings(fallback_enabled=True)
    assert settings.fallback_enabled is False
    assert "fallback" in caplog.text.lower()
    assert "disabled" in caplog.text.lower()
    assert "synthetic-gateway-key" not in caplog.text


@pytest.mark.parametrize(
    "missing", ["gcp_project_id", "gcp_location", "gemini_model", "gcp_service_account_json"]
)
def test_fallback_requires_explicit_direct_configuration(tmp_path, missing):
    credential = tmp_path / "offline-credential.json"
    credential.write_text("not a credential; settings must never read this", encoding="utf-8")
    direct = dict(
        gcp_project_id="project",
        gcp_location="global",
        gemini_model="direct-model",
        gcp_service_account_json=str(credential),
    )
    direct[missing] = ""
    assert make_settings(fallback_enabled=True, **direct).fallback_enabled is False


def test_fallback_checks_only_file_existence_without_loading_credentials(tmp_path):
    credential = tmp_path / "offline-credential.json"
    credential.write_text("not a credential; no parsing permitted", encoding="utf-8")
    direct = dict(gcp_project_id="project", gcp_service_account_json=str(credential))
    assert make_settings(fallback_enabled=True, **direct).fallback_enabled is True
    credential.unlink()
    assert make_settings(fallback_enabled=True, **direct).fallback_enabled is False


def test_legacy_configuration_ignores_gateway_and_fallback_policy():
    settings = make_settings(
        gemini_backend="vertex",
        gcp_project_id="project",
        gateway_chat_completions_url="",
        gateway_api_key="",
        gateway_model="",
        fallback_enabled=True,
        max_retry=1,
    )
    assert settings.gemini_backend == "vertex"
    assert Settings.model_fields["gemini_backend"].default == "vertex"
