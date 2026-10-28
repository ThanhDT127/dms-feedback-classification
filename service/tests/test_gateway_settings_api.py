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


def test_available_models_include_gemini_3_5_and_3_8_and_pricing():
    from dms.usage_tracker import DEFAULT_MODEL_PRICING, calculate_cost

    models = asyncio.run(settings_api.get_available_models(admin={"role": "admin"}))
    model_ids = {m["id"] for m in models}
    for expected in (
        "gemini-3.8-flash-lite",
        "gemini-3.8-flash",
        "gemini-3.8-pro",
        "gemini-3.5-flash-lite",
        "gemini-3.5-flash",
        "gemini-3.5-pro",
        "gemini-2.5-flash-lite",
        "gemini-2.5-flash",
    ):
        assert expected in model_ids
        assert calculate_cost(expected, 1000, 500, DEFAULT_MODEL_PRICING) > 0


def test_gateway_save_preserves_direct_fallback_gemini_model(tmp_path, monkeypatch):
    captured = {}
    monkeypatch.setattr(settings_api, "SERVICE_DIR", tmp_path)
    monkeypatch.setattr(settings_api, "update_env_file", lambda updates: captured.update(updates))
    monkeypatch.setattr(settings_api.deps, "reset", lambda: None)
    monkeypatch.setattr(
        settings_api,
        "get_settings_provider",
        lambda: SimpleNamespace(reload=lambda: SimpleNamespace(gemini_backend="gateway")),
    )
    asyncio.run(
        settings_api.update_settings(
            {
                "backend": "gateway",
                "model": "gemini-flash-lite",
                "gateway_model": "gemini-flash-lite",
            },
            admin={"role": "admin"},
        )
    )
    assert captured.get("GATEWAY_MODEL") == "gemini-flash-lite"
    assert "GEMINI_MODEL" not in captured
