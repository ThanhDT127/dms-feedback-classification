"""Test cho stream Gemini và các ngoại lệ mới (change b04).

Toàn bộ chạy offline: SDK được thay bằng module giả qua ``monkeypatch.setitem(sys.modules, ...)``
theo đúng kiểu ``tests/test_gemini.py`` đang dùng.
"""

from __future__ import annotations

import sys
import time
import types

import pytest

from dms.exceptions import GeminiError, GeminiStreamError, GeminiStreamTimeout
from dms.gemini_client import GeminiClient


class _ForbiddenGenaiClient:
    """Dùng cho test hồi quy: đường cũ tuyệt đối không được tạo client google-genai."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        raise AssertionError("đường cũ không được tạo client google-genai")


def install_fake_sdks(monkeypatch, legacy_calls: list[dict]) -> None:
    """Cài `google.generativeai` giả (đường cũ) và `google.genai` chặn (đường mới)."""

    class FakeLegacyModel:
        def __init__(self, name: str) -> None:
            self.name = name

        def generate_content(self, prompt, generation_config=None):
            legacy_calls.append({"prompt": prompt, "generation_config": generation_config})
            return types.SimpleNamespace(text='{"ok": true}', usage_metadata=None)

    fake_legacy = types.ModuleType("google.generativeai")
    fake_legacy.configure = lambda api_key: legacy_calls.append({"configure": api_key})
    fake_legacy.GenerativeModel = FakeLegacyModel

    fake_genai = types.ModuleType("google.genai")
    fake_genai.Client = _ForbiddenGenaiClient
    fake_genai.types = types.SimpleNamespace(GenerateContentConfig=lambda **kw: kw)

    fake_google = types.ModuleType("google")
    fake_google.generativeai = fake_legacy
    fake_google.genai = fake_genai

    monkeypatch.setitem(sys.modules, "google", fake_google)
    monkeypatch.setitem(sys.modules, "google.generativeai", fake_legacy)
    monkeypatch.setitem(sys.modules, "google.genai", fake_genai)


@pytest.fixture
def apikey_settings(settings):
    settings.gemini_backend = "apikey"
    settings.gemini_api_key = "key"
    settings.max_retry = 1
    settings.base_wait = 0
    return settings


# ── 1.2 Hồi quy: đường cũ không đổi ──


def test_apikey_generate_json_stays_on_legacy_sdk(apikey_settings, monkeypatch):
    calls: list[dict] = []
    install_fake_sdks(monkeypatch, calls)

    client = GeminiClient(apikey_settings)
    response = client.generate_json("câu hỏi")

    assert response.text == '{"ok": true}'
    generate_calls = [call for call in calls if "prompt" in call]
    assert len(generate_calls) == 1
    assert generate_calls[0]["generation_config"]["response_mime_type"] == "application/json"
    assert getattr(client, "_genai_apikey_client", None) is None


def test_apikey_generate_stays_on_legacy_sdk(apikey_settings, monkeypatch):
    calls: list[dict] = []
    install_fake_sdks(monkeypatch, calls)

    client = GeminiClient(apikey_settings)
    response = client.generate("câu hỏi")

    assert response.text == '{"ok": true}'
    assert any("prompt" in call for call in calls)
    assert getattr(client, "_genai_apikey_client", None) is None


# ── 2.2 Ngoại lệ mới ──


def test_stream_errors_are_still_gemini_errors():
    with pytest.raises(GeminiError):
        raise GeminiStreamError("hỏng giữa chừng", partial=True, code="BLOCKED")
    with pytest.raises(GeminiError):
        raise GeminiStreamTimeout("hết giờ", stage="idle", partial=True)


def test_stream_error_carries_partial_and_code():
    error = GeminiStreamError("hỏng", partial=True, code="BLOCKED")
    assert error.partial is True
    assert error.code == "BLOCKED"


def test_stream_timeout_carries_stage_and_defaults():
    error = GeminiStreamTimeout("hết giờ chờ chunk đầu")
    assert error.stage == "first_chunk"
    assert error.code == "TIMEOUT"
    assert error.partial is False
    assert isinstance(error, GeminiStreamError)


def test_stream_timeout_is_catchable_as_stream_error():
    with pytest.raises(GeminiStreamError) as caught:
        raise GeminiStreamTimeout("quá tổng thời gian", stage="total", partial=True)
    assert caught.value.stage == "total"
    assert caught.value.partial is True


# ── SDK giả cho stream (design b04 D10) ──


class FakeStreamIterator:
    """Duyệt kịch bản ``(delay, text | Exception | response)``; đếm được lần close()."""

    def __init__(self, script):
        self.script = list(script)
        self.closed = 0

    def __iter__(self):
        return self

    def __next__(self):
        if not self.script:
            raise StopIteration
        delay, item = self.script.pop(0)
        if delay:
            time.sleep(delay)
        if isinstance(item, BaseException):
            raise item
        return item

    def close(self):
        self.closed += 1


class FakeGenaiModels:
    def __init__(self, scripts):
        self.scripts = list(scripts)
        self.calls: list[dict] = []
        self.iterators: list[FakeStreamIterator] = []

    def generate_content_stream(self, **kwargs):
        self.calls.append(kwargs)
        script = self.scripts.pop(0) if self.scripts else []
        iterator = FakeStreamIterator(script)
        self.iterators.append(iterator)
        return iterator

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        return types.SimpleNamespace(text='{"ok": true}', usage_metadata=None)


class FakeGenaiClient:
    def __init__(self, models):
        self.models = models


def piece(text="", *, finish_reason=None, usage=None):
    candidates = [types.SimpleNamespace(finish_reason=finish_reason)] if finish_reason else []
    metadata = types.SimpleNamespace(**usage) if usage else None
    return types.SimpleNamespace(text=text, candidates=candidates, usage_metadata=metadata)


@pytest.fixture
def stream_settings(settings):
    settings.chat_stream_first_chunk_timeout_seconds = 0.3
    settings.chat_stream_idle_timeout_seconds = 0.3
    settings.chat_stream_total_timeout_seconds = 2.0
    settings.chat_llm_max_retry = 0
    return settings


def attach(client, scripts) -> FakeGenaiModels:
    models = FakeGenaiModels(scripts)
    client._genai_stream_client = FakeGenaiClient(models)
    return models


# ── 3.x reset_clients ──


def test_reset_clients_clears_every_cached_client(settings):
    client = GeminiClient(settings)
    client._vertex_client = object()
    client._apikey_model = object()
    client._genai_apikey_client = object()
    client._genai_stream_client = object()

    client.reset_clients()

    assert client._vertex_client is None
    assert client._apikey_model is None
    assert client._genai_apikey_client is None
    assert client._genai_stream_client is None


def test_stream_client_uses_api_key_for_apikey_backend(apikey_settings, monkeypatch):
    created: list[dict] = []

    class RecordingClient:
        def __init__(self, **kwargs):
            created.append(kwargs)
            self.models = FakeGenaiModels([])

    fake_genai = types.ModuleType("google.genai")
    fake_genai.Client = RecordingClient
    fake_genai.types = types.SimpleNamespace(
        HttpOptions=lambda **kw: kw,
        GenerateContentConfig=lambda **kw: kw,
        ThinkingConfig=lambda **kw: kw,
    )
    fake_legacy = types.ModuleType("google.generativeai")
    fake_legacy.configure = lambda api_key: None
    fake_legacy.GenerativeModel = _ForbiddenGenaiClient
    fake_google = types.ModuleType("google")
    fake_google.genai = fake_genai
    fake_google.generativeai = fake_legacy
    monkeypatch.setitem(sys.modules, "google", fake_google)
    monkeypatch.setitem(sys.modules, "google.genai", fake_genai)
    monkeypatch.setitem(sys.modules, "google.generativeai", fake_legacy)

    client = GeminiClient(apikey_settings)
    client._stream_client()

    assert created and created[0]["api_key"] == "key"
    assert "http_options" in created[0]


# ── 4.x system_instruction cho generate_json ──


def test_generate_json_with_system_instruction_uses_genai(apikey_settings, monkeypatch):
    models = FakeGenaiModels([])

    class RecordingClient:
        def __init__(self, **kwargs):
            self.models = models

    fake_genai = types.ModuleType("google.genai")
    fake_genai.Client = RecordingClient
    fake_genai.types = types.SimpleNamespace(GenerateContentConfig=lambda **kw: kw)
    fake_legacy = types.ModuleType("google.generativeai")
    fake_legacy.configure = lambda api_key: None
    fake_legacy.GenerativeModel = _ForbiddenGenaiClient
    fake_google = types.ModuleType("google")
    fake_google.genai = fake_genai
    fake_google.generativeai = fake_legacy
    monkeypatch.setitem(sys.modules, "google", fake_google)
    monkeypatch.setitem(sys.modules, "google.genai", fake_genai)
    monkeypatch.setitem(sys.modules, "google.generativeai", fake_legacy)

    client = GeminiClient(apikey_settings)
    response = client.generate_json("dữ liệu", system_instruction="Luật cứng")

    assert response.text == '{"ok": true}'
    config = models.calls[0]["config"]
    assert config["system_instruction"] == "Luật cứng"
    assert config["response_mime_type"] == "application/json"


def test_generate_json_model_override_goes_through_genai(apikey_settings, monkeypatch):
    models = FakeGenaiModels([])

    class RecordingClient:
        def __init__(self, **kwargs):
            self.models = models

    fake_genai = types.ModuleType("google.genai")
    fake_genai.Client = RecordingClient
    fake_genai.types = types.SimpleNamespace(GenerateContentConfig=lambda **kw: kw)
    fake_legacy = types.ModuleType("google.generativeai")
    fake_legacy.configure = lambda api_key: None
    fake_legacy.GenerativeModel = _ForbiddenGenaiClient
    fake_google = types.ModuleType("google")
    fake_google.genai = fake_genai
    fake_google.generativeai = fake_legacy
    monkeypatch.setitem(sys.modules, "google", fake_google)
    monkeypatch.setitem(sys.modules, "google.genai", fake_genai)
    monkeypatch.setitem(sys.modules, "google.generativeai", fake_legacy)

    client = GeminiClient(apikey_settings)
    client.generate_json("dữ liệu", model="gemini-2.5-flash")

    assert models.calls[0]["model"] == "gemini-2.5-flash"


# ── 5.x stream ──


def test_stream_yields_non_empty_chunks_in_order(stream_settings):
    client = GeminiClient(stream_settings)
    attach(client, [[(0, piece("Xin ")), (0, piece("")), (0, piece("chào"))]])

    chunks = list(client.stream("hỏi"))

    assert chunks == ["Xin ", "chào"]
    assert "".join(chunks) == "Xin chào"


def test_stream_passes_generation_config(stream_settings):
    client = GeminiClient(stream_settings)
    models = attach(client, [[(0, piece("ok"))]])

    list(
        client.stream(
            "dữ liệu",
            system_instruction="Chỉ dùng số từ dữ kiện",
            temperature=0.2,
            model="gemini-2.5-flash",
        )
    )

    call = models.calls[0]
    assert call["model"] == "gemini-2.5-flash"
    config = call["config"]  # GenerateContentConfig thật của google-genai
    assert config.system_instruction == "Chỉ dùng số từ dữ kiện"
    assert config.temperature == 0.2
    assert config.thinking_config is None


def test_stream_sends_thinking_config_when_budget_is_set(stream_settings):
    stream_settings.chat_gemini_thinking_budget = 128
    client = GeminiClient(stream_settings)
    models = attach(client, [[(0, piece("ok"))]])

    list(client.stream("hỏi"))

    assert models.calls[0]["config"].thinking_config.thinking_budget == 128


def test_stream_retries_before_first_chunk(stream_settings):
    stream_settings.chat_llm_max_retry = 1
    client = GeminiClient(stream_settings)
    models = attach(
        client,
        [[(0, RuntimeError("lỗi mạng"))], [(0, piece("ok"))]],
    )

    stream = client.stream("hỏi")
    stream._retry_wait = 0.0
    chunks = list(stream)

    assert chunks == ["ok"]
    assert len(models.calls) == 2


def test_stream_does_not_retry_after_first_chunk(stream_settings):
    stream_settings.chat_llm_max_retry = 1
    client = GeminiClient(stream_settings)
    models = attach(client, [[(0, piece("Phần 1")), (0, RuntimeError("lỗi mạng"))]])

    stream = client.stream("hỏi")
    stream._retry_wait = 0.0
    received = [next(stream)]
    with pytest.raises(GeminiStreamError) as caught:
        next(stream)

    assert received == ["Phần 1"]
    assert caught.value.partial is True
    assert len(models.calls) == 1


def test_first_chunk_timeout(stream_settings):
    stream_settings.chat_stream_first_chunk_timeout_seconds = 0.1
    client = GeminiClient(stream_settings)
    attach(client, [[(1.0, piece("muộn"))]])

    started = time.monotonic()
    with pytest.raises(GeminiStreamTimeout) as caught:
        list(client.stream("hỏi"))

    assert caught.value.stage == "first_chunk"
    assert caught.value.partial is False
    assert time.monotonic() - started < 0.6


def test_idle_timeout_after_first_chunk(stream_settings):
    stream_settings.chat_stream_idle_timeout_seconds = 0.1
    client = GeminiClient(stream_settings)
    attach(client, [[(0, piece("A")), (1.0, piece("B"))]])

    stream = client.stream("hỏi")
    assert next(stream) == "A"
    with pytest.raises(GeminiStreamTimeout) as caught:
        next(stream)

    assert caught.value.stage == "idle"
    assert caught.value.partial is True


def test_total_timeout(stream_settings):
    stream_settings.chat_stream_total_timeout_seconds = 0.3
    stream_settings.chat_stream_idle_timeout_seconds = 5.0
    client = GeminiClient(stream_settings)
    attach(client, [[(0.08, piece(f"phần {i}")) for i in range(50)]])

    started = time.monotonic()
    with pytest.raises(GeminiStreamTimeout) as caught:
        list(client.stream("hỏi"))

    assert caught.value.stage == "total"
    assert time.monotonic() - started < 0.8


def test_close_cancels_and_is_idempotent(stream_settings):
    client = GeminiClient(stream_settings)
    models = attach(client, [[(0, piece("A")), (0.05, piece("B")), (0.05, piece("C"))]])

    stream = client.stream("hỏi")
    assert next(stream) == "A"
    stream.close()
    stream.close()

    assert stream.cancelled is True
    assert list(stream) == []
    assert models.iterators[0].closed >= 1


def test_context_manager_closes_on_exception(stream_settings):
    client = GeminiClient(stream_settings)
    attach(client, [[(0, piece("A")), (0, piece("B"))]])

    stream = client.stream("hỏi")
    with pytest.raises(ValueError):
        with stream as active:
            assert next(active) == "A"
            raise ValueError("lỗi phía trên")

    assert stream.cancelled is True


def test_usage_includes_thinking_tokens(stream_settings):
    client = GeminiClient(stream_settings)
    final = piece(
        "",
        usage={
            "prompt_token_count": 100,
            "candidates_token_count": 40,
            "thoughts_token_count": 10,
            "total_token_count": 150,
        },
    )
    attach(client, [[(0, piece("ok")), (0, final)]])

    stream = client.stream("hỏi")
    list(stream)

    assert stream.usage == {
        "prompt_tokens": 100,
        "completion_tokens": 50,
        "thoughts_tokens": 10,
        "total_tokens": 150,
    }
    assert stream.first_chunk_ms is not None
    assert stream.total_ms is not None
    assert stream.model == stream_settings.gemini_model


def test_blocked_without_text_raises(stream_settings):
    client = GeminiClient(stream_settings)
    attach(client, [[(0, piece("", finish_reason="SAFETY"))]])

    with pytest.raises(GeminiStreamError) as caught:
        list(client.stream("hỏi"))

    assert caught.value.code == "BLOCKED"


def test_max_tokens_is_not_an_error(stream_settings):
    client = GeminiClient(stream_settings)
    attach(client, [[(0, piece("Một phần")), (0, piece("", finish_reason="MAX_TOKENS"))]])

    stream = client.stream("hỏi")
    chunks = list(stream)

    assert chunks == ["Một phần"]
    assert stream.finish_reason == "MAX_TOKENS"


# ── 3.3 Watcher gọi reset_clients khi setting Gemini đổi ──


def test_watcher_calls_reset_clients_once(settings, monkeypatch, tmp_path):
    from test_watcher import make_watcher

    monkeypatch.setattr(settings, "keyword_dir_override", tmp_path)
    watcher = make_watcher(settings, [])

    resets: list[int] = []

    class FakeGemini:
        def reset_clients(self) -> None:
            resets.append(1)

    watcher.pipeline_runner.gemini = FakeGemini()
    new_settings = settings.model_copy(
        update={
            "gemini_backend": "apikey",
            "gemini_api_key": "some-key",
            "gemini_model": "gemini-2.5-pro",
        }
    )
    monkeypatch.setattr("dms.settings.get_settings", lambda: new_settings)

    watcher.reload_settings()

    assert resets == [1]
