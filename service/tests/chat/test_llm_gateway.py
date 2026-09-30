from __future__ import annotations

import pytest

from dms.chat.ai.llm_gateway import (
    CHAT_LLM_BASE_WAIT_SECONDS,
    CHAT_LLM_TIMEOUT_SECONDS,
    GeminiChatGateway,
    GeminiJsonClient,
)
from dms.exceptions import GeminiError, GeminiStreamTimeout
from dms.gemini_client import GeminiResponse
from dms.usage_tracker import calculate_cost

STREAM_USAGE = {
    "prompt_tokens": 120,
    "completion_tokens": 60,
    "thoughts_tokens": 10,
    "total_tokens": 180,
}


class FakeGeminiStream:
    """Giả ``GeminiStream``: phát các đoạn, có thể ném lỗi sau ``error_after`` đoạn."""

    def __init__(self, chunks, *, usage=None, error=None, error_after=0, model="fake-model"):
        self._chunks = list(chunks)
        self._error = error
        self._error_after = error_after
        self._emitted = 0
        self.usage = dict(usage or {})
        self.finish_reason = "STOP"
        self.model = model
        self.first_chunk_ms = 5
        self.total_ms = 10
        self.cancelled = False
        self.close_calls = 0

    def __iter__(self):
        return self

    def __next__(self) -> str:
        if self.cancelled:
            raise StopIteration
        if self._error is not None and self._emitted == self._error_after:
            raise self._error
        if not self._chunks:
            raise StopIteration
        self._emitted += 1
        return self._chunks.pop(0)

    def close(self) -> None:
        self.close_calls += 1
        self.cancelled = True


class FakeGemini:
    def __init__(self, result: GeminiResponse | Exception | None = None, *, stream=None) -> None:
        self.result = result
        self.stream_obj = stream
        self.calls: list[tuple[str, float, dict]] = []
        self.stream_calls: list[tuple[str, dict]] = []

    def generate_json(self, prompt: str, temperature: float = 0.0, **kwargs):
        self.calls.append((prompt, temperature, kwargs))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result

    def stream(self, prompt: str, **kwargs):
        self.stream_calls.append((prompt, kwargs))
        if isinstance(self.stream_obj, Exception):
            raise self.stream_obj
        return self.stream_obj


class FakeTracker:
    def __init__(self, fail: bool = False) -> None:
        self.records: list[dict] = []
        self.fail = fail

    def record(self, **kwargs) -> None:
        if self.fail:
            raise RuntimeError("db locked")
        self.records.append(kwargs)


# ── JSON (giữ nguyên hành vi b01) ──


def test_generate_json_returns_result_and_records_usage(settings):
    usage = {"prompt_tokens": 1000, "completion_tokens": 200, "total_tokens": 1200}
    gemini = FakeGemini(GeminiResponse(text='{"ok": true}', usage=usage))
    tracker = FakeTracker()
    client = GeminiChatGateway(settings, usage_tracker=tracker, gemini=gemini)

    result = client.generate_json("prompt", call_type="chat_contextualize")

    assert result.text == '{"ok": true}'
    assert result.usage == usage
    assert result.model == client.model
    assert gemini.calls[0][0] == "prompt"
    assert gemini.calls[0][1] == 0.0
    assert len(tracker.records) == 1
    record = tracker.records[0]
    assert record["call_type"] == "chat_contextualize"
    assert record["success"] is True
    assert record["job_id"] is None
    assert record["prompt_tokens"] == 1000 and record["completion_tokens"] == 200
    assert record["estimated_cost_usd"] == pytest.approx(
        calculate_cost(client.model, 1000, 200, client._pricing)
    )


def test_failure_is_recorded_and_reraised(settings):
    tracker = FakeTracker()
    client = GeminiChatGateway(
        settings, usage_tracker=tracker, gemini=FakeGemini(GeminiError("boom"))
    )
    with pytest.raises(GeminiError):
        client.generate_json("prompt", call_type="chat_plan")
    assert len(tracker.records) == 1
    assert tracker.records[0]["success"] is False
    assert tracker.records[0]["call_type"] == "chat_plan"


def test_usage_tracker_error_does_not_break_call(settings):
    gemini = FakeGemini(GeminiResponse(text="{}", usage={}))
    client = GeminiChatGateway(settings, usage_tracker=FakeTracker(fail=True), gemini=gemini)
    assert client.generate_json("prompt", call_type="chat_plan").text == "{}"


def test_system_instruction_is_forwarded_only_when_given(settings):
    gemini = FakeGemini(GeminiResponse(text="{}", usage={}))
    client = GeminiChatGateway(settings, gemini=gemini)

    client.generate_json("a", call_type="chat_plan")
    client.generate_json("b", call_type="chat_plan", system_instruction="Luật cứng")

    assert gemini.calls[0][2] == {}
    assert gemini.calls[1][2] == {"system_instruction": "Luật cứng"}


def test_b01_alias_still_works(settings):
    assert GeminiJsonClient is GeminiChatGateway
    client = GeminiJsonClient(settings, gemini=FakeGemini(GeminiResponse(text="{}")))
    assert client.generate_json("prompt", call_type="chat_contextualize").text == "{}"


# ── Model và settings riêng cho chat ──


def test_chat_settings_copy_leaves_pipeline_settings_untouched(settings):
    original_retry = settings.max_retry
    original_wait = settings.base_wait
    client = GeminiChatGateway(settings, gemini=FakeGemini(GeminiResponse(text="{}")))

    # Pipeline đếm tổng số lần gọi; chat đếm số lần thử lại.
    assert client.settings.max_retry == settings.chat_llm_max_retry + 1
    assert client.settings.base_wait == pytest.approx(CHAT_LLM_BASE_WAIT_SECONDS)
    assert client.settings.gemini_timeout_seconds <= CHAT_LLM_TIMEOUT_SECONDS
    assert settings.max_retry == original_retry
    assert settings.base_wait == original_wait


def test_chat_uses_its_own_model_without_changing_the_pipeline_model(settings):
    settings.gemini_model = "gemini-2.5-flash-lite"
    settings.chat_gemini_model = "gemini-2.5-flash"

    client = GeminiChatGateway(settings, gemini=FakeGemini(GeminiResponse(text="{}")))

    assert client.model == "gemini-2.5-flash"
    assert settings.gemini_model == "gemini-2.5-flash-lite"


def test_chat_falls_back_to_pipeline_model(settings):
    settings.gemini_model = "gemini-2.5-flash-lite"
    settings.chat_gemini_model = ""

    client = GeminiChatGateway(settings, gemini=FakeGemini(GeminiResponse(text="{}")))

    assert client.model == "gemini-2.5-flash-lite"


# ── Stream: usage đúng một lần ──


def test_stream_records_usage_once_on_success(settings):
    tracker = FakeTracker()
    stream = FakeGeminiStream(["Xin ", "chào"], usage=STREAM_USAGE)
    client = GeminiChatGateway(settings, usage_tracker=tracker, gemini=FakeGemini(stream=stream))

    handle = client.stream("prompt", call_type="chat_synthesis")
    chunks = list(handle)

    assert chunks == ["Xin ", "chào"]
    assert len(tracker.records) == 1
    record = tracker.records[0]
    assert record["call_type"] == "chat_synthesis"
    assert record["success"] is True
    assert record["prompt_tokens"] == 120
    assert record["completion_tokens"] == 60
    assert handle.usage == STREAM_USAGE
    assert handle.finish_reason == "STOP"


def test_stream_failure_before_first_chunk_records_one_failed_call(settings):
    tracker = FakeTracker()
    stream = FakeGeminiStream(
        ["không tới đây"],
        error=GeminiStreamTimeout("hết giờ", stage="first_chunk"),
        error_after=0,
    )
    client = GeminiChatGateway(settings, usage_tracker=tracker, gemini=FakeGemini(stream=stream))

    handle = client.stream("prompt", call_type="chat_synthesis")
    with pytest.raises(GeminiStreamTimeout):
        list(handle)

    assert len(tracker.records) == 1
    assert tracker.records[0]["success"] is False


def test_cancel_after_one_chunk_records_success_once(settings):
    tracker = FakeTracker()
    stream = FakeGeminiStream(["A", "B", "C"], usage=STREAM_USAGE)
    client = GeminiChatGateway(settings, usage_tracker=tracker, gemini=FakeGemini(stream=stream))

    handle = client.stream("prompt", call_type="chat_synthesis")
    assert next(handle) == "A"
    handle.close()
    handle.close()

    assert len(tracker.records) == 1
    assert tracker.records[0]["success"] is True
    assert handle.cancelled is True
    assert stream.close_calls >= 1


def test_context_manager_records_once(settings):
    tracker = FakeTracker()
    stream = FakeGeminiStream(["A", "B"], usage=STREAM_USAGE)
    client = GeminiChatGateway(settings, usage_tracker=tracker, gemini=FakeGemini(stream=stream))

    with client.stream("prompt", call_type="chat_synthesis") as handle:
        assert next(handle) == "A"

    assert len(tracker.records) == 1


def test_stream_open_failure_is_recorded(settings):
    tracker = FakeTracker()
    client = GeminiChatGateway(
        settings, usage_tracker=tracker, gemini=FakeGemini(stream=GeminiError("không mở được"))
    )

    with pytest.raises(GeminiError):
        client.stream("prompt", call_type="chat_synthesis")

    assert len(tracker.records) == 1
    assert tracker.records[0]["success"] is False


@pytest.mark.parametrize(("max_retry", "expected_calls"), [(0, 1), (1, 2), (2, 3)])
def test_json_retry_count_means_retries_not_attempts(
    settings, monkeypatch, max_retry, expected_calls
):
    from dms.exceptions import GeminiError
    from dms.gemini_client import GeminiClient

    chat_settings = GeminiChatGateway(settings, gemini=object(), max_retry=max_retry).settings
    real = GeminiClient(chat_settings)
    calls: list[int] = []

    def failing(*args, **kwargs):
        calls.append(1)
        raise RuntimeError("429 RESOURCE_EXHAUSTED")

    monkeypatch.setattr(real, "_generate_genai_json", failing)
    monkeypatch.setattr("time.sleep", lambda _s: None)
    gateway = GeminiChatGateway(settings, gemini=real, max_retry=max_retry)

    with pytest.raises(GeminiError, match="429"):
        gateway.generate_json("prompt", call_type="chat_plan", system_instruction="sys")

    assert len(calls) == expected_calls
