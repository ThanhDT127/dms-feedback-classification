from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from dms.web.api import settings_api


@pytest.mark.parametrize("key", ["gateway_api_key", "GATEWAY_API_KEY"])
def test_gateway_key_is_masked_in_settings_and_raw_fallback(monkeypatch, key):
    monkeypatch.setattr(settings_api.deps, "get_settings_partial", lambda: {key: "dummy-secret"})
    result = asyncio.run(settings_api.get_settings(admin={"role": "admin"}))
    assert result[key] != "dummy-secret"
    assert "dummy-secret" not in str(result)


def test_gateway_save_does_not_claim_inference_verified(tmp_path, monkeypatch):
    monkeypatch.setattr(settings_api, "SERVICE_DIR", tmp_path)
    monkeypatch.setattr(settings_api, "update_env_file", lambda updates: None)
    monkeypatch.setattr(settings_api.deps, "reset", lambda: None)
    monkeypatch.setattr(
        settings_api,
        "get_settings_provider",
        lambda: SimpleNamespace(reload=lambda: SimpleNamespace(gemini_backend="gateway")),
    )
    result = asyncio.run(
        settings_api.update_settings({"llm_batch_size": 5}, admin={"role": "admin"})
    )
    assert result["success"] is True
    assert result["connection_verified"] is False


def test_connection_reports_direct_route_not_gateway_recovery(monkeypatch):
    response = SimpleNamespace(text="ok", usage={}, route="direct_vertex")
    client = SimpleNamespace(
        settings=SimpleNamespace(gemini_backend="gateway"),
        generate=lambda *args, **kwargs: response,
    )
    monkeypatch.setattr(settings_api.deps, "get_gemini", lambda: client)
    result = asyncio.run(settings_api.test_connection(admin={"role": "admin"}))
    assert result["route"] == "direct_vertex"
    assert result["gateway_verified"] is False
