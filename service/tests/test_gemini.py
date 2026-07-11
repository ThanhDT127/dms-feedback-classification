from __future__ import annotations

import sys
import types

import pytest

from dms.exceptions import GeminiError
from dms.gemini_client import GeminiClient, GeminiResponse


def test_gemini_vertex_initialization(settings, monkeypatch):
    class FakeModelService:
        def generate_content(self, **kwargs):
            return types.SimpleNamespace(text="vertex-ok", usage_metadata=None)

    class FakeClient:
        def __init__(self):
            self.models = FakeModelService()

    fake_google = types.ModuleType("google")
    fake_genai = types.ModuleType("google.genai")
    fake_genai.Client = FakeClient
    fake_genai.types = types.SimpleNamespace(GenerateContentConfig=lambda **kw: kw)
    fake_google.genai = fake_genai
    monkeypatch.setitem(sys.modules, "google", fake_google)
    monkeypatch.setitem(sys.modules, "google.genai", fake_genai)

    client = GeminiClient(settings)
    assert client.generate("hello").text == "vertex-ok"


def test_gemini_apikey_initialization(settings, monkeypatch):
    settings.gemini_backend = "apikey"
    settings.gemini_api_key = "key"

    configured = {}

    class FakeModel:
        def __init__(self, name):
            self.name = name

        def generate_content(self, prompt, generation_config=None):
            return types.SimpleNamespace(text="apikey-ok", usage_metadata=None)

    fake_google = sys.modules.get("google") or types.ModuleType("google")
    fake_module = types.ModuleType("google.generativeai")
    fake_module.configure = lambda api_key: configured.setdefault("api_key", api_key)
    fake_module.GenerativeModel = FakeModel
    fake_google.generativeai = fake_module
    monkeypatch.setitem(sys.modules, "google", fake_google)
    monkeypatch.setitem(sys.modules, "google.generativeai", fake_module)

    client = GeminiClient(settings)
    assert client.generate("hello").text == "apikey-ok"
    assert configured["api_key"] == "key"


def test_gemini_wraps_errors(settings, monkeypatch):
    client = GeminiClient(settings)
    monkeypatch.setattr(
        client,
        "_generate_vertex",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("bad")),
    )
    with pytest.raises(GeminiError):
        client.generate("hello")


def test_gemini_retries_once_at_provider_boundary(settings, monkeypatch):
    settings.max_retry = 3
    settings.base_wait = 0
    client = GeminiClient(settings)
    calls = []

    def flaky_generate(*args, **kwargs):
        calls.append((args, kwargs))
        if len(calls) < 3:
            raise RuntimeError("transient")
        return GeminiResponse(text="ok", usage={"prompt_tokens": 1, "completion_tokens": 1})

    monkeypatch.setattr(client, "_generate_vertex", flaky_generate)

    response = client.generate_json("hello")

    assert response.text == "ok"
    assert len(calls) == settings.max_retry
