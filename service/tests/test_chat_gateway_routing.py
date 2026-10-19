from __future__ import annotations

import pytest
from test_gateway import Audit, completion, endpoint

from dms.chat.ai.budget.request_context import ChatRequestContext, use_context
from dms.chat.ai.llm_gateway import GeminiChatGateway
from dms.exceptions import GatewayError, GeminiStreamError
from dms.gemini_client import GeminiClient, GeminiResponse
from dms.settings import Settings


def chat_client(tmp_path, url, **updates):
    settings = Settings(
        _env_file=None,
        azure_tenant_id="tenant",
        azure_client_id="client",
        azure_client_secret="secret",
        sharepoint_drive_id="drive",
        sharepoint_root_folder_id="root",
        gemini_backend="gateway",
        gateway_chat_completions_url=url,
        gateway_api_key="synthetic-gateway-key",
        gateway_model="gateway-alias",
        gateway_allow_insecure_http=True,
        gateway_system_user="cli-worker",
        gemini_api_key="synthetic-direct-key",
        gemini_model="direct-model",
        fallback_enabled=False,
        work_dir=tmp_path,
        log_dir=tmp_path / "logs",
        jwt_secret_key="test-secret-key-that-is-at-least-32-bytes-long",
    ).model_copy(update=updates)
    client = GeminiChatGateway(settings)
    audit = Audit()
    client._gemini = GeminiClient(client.settings, usage_tracker=audit)

    def no_direct_sdk():
        raise AssertionError("Gateway chat must not call Google's streaming SDK")

    client._gemini._stream_client = no_direct_sdk
    return client, audit


def test_chat_json_passes_authenticated_actor_and_system_instruction(tmp_path):
    with endpoint([(200, completion('{"ok":true}'), {})]) as (url, seen):
        client, audit = chat_client(tmp_path, url)
        with use_context(ChatRequestContext(username="alice", request_id="turn-1")):
            result = client.generate_json(
                "Question", call_type="chat_plan", system_instruction="Planner rules"
            )
    body = seen[0][2]
    assert seen[0][1]["X-User"] == body["user"] == "alice"
    assert body["messages"] == [
        {"role": "system", "content": "Planner rules"},
        {"role": "user", "content": "Question"},
    ]
    assert body["response_format"] == {"type": "json_object"}
    assert result.text == '{"ok":true}'
    assert result.usage == {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}
    assert result.model == "actual-model"
    assert audit.events[0]["call_type"] == "chat_plan"
    assert client.model == "gateway-alias"


def test_chat_cli_uses_explicit_system_actor(tmp_path):
    with endpoint([(200, completion("{}"), {})]) as (url, seen):
        client, _ = chat_client(tmp_path, url)
        with use_context(None):
            client.generate_json("Question", call_type="chat_plan")
    assert seen[0][2]["user"] == "cli-worker"


def test_chat_missing_actor_fails_without_inference(tmp_path):
    client, audit = chat_client(tmp_path, "http://127.0.0.1:1/chat", gateway_system_user="")
    with use_context(None), pytest.raises(GatewayError) as caught:
        client.generate_json("Question", call_type="chat_plan")
    assert caught.value.category == "identity"
    assert not audit.events


def test_chat_stream_uses_gateway_and_captures_actor_before_reader_thread(tmp_path, monkeypatch):
    with endpoint([(200, completion("Gateway answer"), {})]) as (url, seen):
        client, audit = chat_client(tmp_path, url)

        def no_direct_sdk():
            pytest.fail("Gateway chat must not call Google's streaming SDK")

        monkeypatch.setattr(client._gemini, "_stream_client", no_direct_sdk)
        with use_context(ChatRequestContext(username="alice", request_id="turn-2")):
            stream = client.stream(
                "Question",
                call_type="chat_synthesis",
                system_instruction="Answer rules",
                max_output_tokens=80,
            )
        with use_context(ChatRequestContext(username="bob", request_id="turn-3")):
            with stream:
                assert "".join(stream) == "Gateway answer"
    assert seen[0][2]["user"] == "alice"
    assert seen[0][2]["max_tokens"] == 80
    assert seen[0][2]["messages"][0] == {"role": "system", "content": "Answer rules"}
    assert "response_format" not in seen[0][2]
    assert stream.usage["total_tokens"] > 0
    assert stream.finish_reason == "STOP"
    assert len([e for e in audit.events if e["event_kind"] == "attempt_started"]) == 1
    assert audit.events[0]["call_type"] == "chat_synthesis"


@pytest.mark.parametrize("streaming", [False, True])
def test_chat_gateway_refusal_triggers_existing_fallback(tmp_path, monkeypatch, streaming):
    client, audit = chat_client(
        tmp_path,
        "http://127.0.0.1:1/chat",
        fallback_enabled=True,
        fallback_fail_threshold=3,
        chat_llm_max_retry=2,
    )
    client._gemini.settings.base_wait = 0
    direct = []

    def direct_request(config, prompt, temperature, json_mode, **options):
        direct.append((json_mode, options))
        return GeminiResponse(
            text="Fallback answer",
            usage={"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5},
            route="direct_apikey",
            model_actual="direct-model",
        )

    monkeypatch.setattr(client._gemini, "_direct_request", direct_request)
    with use_context(ChatRequestContext(username="alice", request_id="turn-4")):
        if streaming:
            with client.stream(
                "Question", call_type="chat_synthesis", system_instruction="Rules"
            ) as stream:
                assert "".join(stream) == "Fallback answer"
            assert stream.model == "direct-model"
            assert stream.usage["total_tokens"] == 5
        else:
            result = client.generate_json(
                "Question", call_type="chat_plan", system_instruction="Rules"
            )
            assert result.text == "Fallback answer"
            assert result.model == "direct-model"
    starts = [e for e in audit.events if e["event_kind"] == "attempt_started"]
    assert [e["route"] for e in starts] == ["gateway"] * 3 + ["direct_apikey"]
    assert {e["actor"] for e in starts} == {"alice"}
    assert direct == [(not streaming, {"system_instruction": "Rules"})]


def test_gateway_stream_auth_failure_does_not_retry_or_fallback(tmp_path):
    with endpoint([(403, {"error": "denied"}, {})]) as (url, seen):
        client, audit = chat_client(tmp_path, url, fallback_enabled=True, chat_llm_max_retry=2)
        with client.stream("Question", call_type="chat_synthesis") as stream:
            with pytest.raises(GeminiStreamError):
                list(stream)
    assert len(seen) == 1
    finishes = [e for e in audit.events if e["event_kind"] == "attempt_finished"]
    assert len(finishes) == 1
    assert finishes[0]["error_category"] == "auth"


def test_closing_gateway_stream_before_read_does_not_send_request(tmp_path):
    client, audit = chat_client(tmp_path, "http://127.0.0.1:1/chat")
    stream = client.stream("Question", call_type="chat_synthesis")
    stream.close()
    assert list(stream) == []
    assert not audit.events


@pytest.mark.parametrize("streaming", [False, True])
def test_chat_timeout_uses_fallback_on_first_attempt(tmp_path, monkeypatch, streaming):
    import requests

    client, audit = chat_client(
        tmp_path,
        "http://127.0.0.1:1/chat",
        fallback_enabled=True,
        fallback_fail_threshold=3,
        chat_llm_max_retry=2,
    )

    def timeout(*args, **kwargs):
        raise requests.exceptions.ReadTimeout("synthetic timeout")

    monkeypatch.setattr(requests.Session, "post", timeout)
    monkeypatch.setattr(
        client._gemini,
        "_direct_request",
        lambda *args, **kwargs: GeminiResponse(
            "Fallback answer", route="direct_apikey", model_actual="direct-model"
        ),
    )
    with use_context(ChatRequestContext(username="alice", request_id="timeout-turn")):
        if streaming:
            with client.stream("Question", call_type="chat_synthesis") as stream:
                assert "".join(stream) == "Fallback answer"
        else:
            assert client.generate_json("Question", call_type="chat_plan").text == "Fallback answer"
    starts = [e for e in audit.events if e["event_kind"] == "attempt_started"]
    assert [e["route"] for e in starts] == ["gateway", "direct_apikey"]
    assert {e["actor"] for e in starts} == {"alice"}
