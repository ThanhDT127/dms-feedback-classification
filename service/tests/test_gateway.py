from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from dms.gemini_client import GeminiClient


def gateway_settings(tmp_path, url="http://127.0.0.1:1/chat", **overrides):
    values = dict(gemini_backend="gateway", gateway_chat_completions_url=url,
                  gateway_api_key="dummy-key", gateway_model="Alias-EXACT",
                  gateway_allow_insecure_http=True, max_retry=3, base_wait=0,
                  gemini_timeout_seconds=0.3, fallback_enabled=False,
                  fallback_fail_threshold=3, fallback_retry_after_s=60,
                  gcp_project_id="", gcp_location="global",
                  gcp_service_account_json="", gemini_model="direct-model",
                  work_dir=tmp_path, log_dir=tmp_path / "logs")
    values.update(overrides)
    return SimpleNamespace(**values)


class Audit:
    def __init__(self):
        self.events = []

    def record_attempt(self, **event):
        self.events.append(event)


@contextmanager
def endpoint(responses):
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            seen.append((self.path, dict(self.headers),
                         json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
            status, body, headers = responses.pop(0)
            self.send_response(status)
            for key, value in headers.items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(json.dumps(body).encode())

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/exact/chat", seen
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def completion(text="hello"):
    return {"id": "response-1", "model": "actual-model",
            "choices": [{"message": {"content": text}}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5,
                      "completion_tokens_details": {"reasoning_tokens": 1}}}


@pytest.mark.parametrize("helper", ["generate", "generate_json"])
def test_wire_identity_and_attempt_audit(tmp_path, helper):
    audit = Audit()
    with endpoint([(200, completion(), {})]) as (url, seen):
        client = GeminiClient(gateway_settings(tmp_path, url), usage_tracker=audit)
        response = getattr(client, helper)("private prompt", temperature=0.2,
                                           actor="alice", job_id="job-1", operation="classify")
    path, headers, body = seen[0]
    assert path == "/exact/chat"
    assert headers["Authorization"] == "Bearer dummy-key"
    assert headers["X-User"] == body["user"] == "alice"
    assert body["model"] == "Alias-EXACT"
    assert body["messages"] == [{"role": "user", "content": "private prompt"}]
    assert body["temperature"] == 0.2
    assert body.get("response_format") == ({"type": "json_object"} if helper == "generate_json" else None)
    assert response.text == "hello"
    assert response.usage == completion()["usage"]
    assert response.route == "gateway"
    assert [e["event_kind"] for e in audit.events] == ["attempt_started", "attempt_finished"]
    start, finish = audit.events
    assert start["operation_id"] == finish["operation_id"]
    assert start["attempt_id"] == finish["attempt_id"]
    assert finish["actor"] == "alice" and finish["job_id"] == "job-1"
    assert finish["call_type"] == "classify" and finish["outcome"] == "success"
    assert finish["response_id"] == "response-1"
    assert finish["model_requested"] == "Alias-EXACT"
    assert finish["model_actual"] == "actual-model"
    assert "private prompt" not in repr(audit.events)


@pytest.mark.parametrize("status,category,unknown", [
    (400, "request", False), (401, "auth", False), (403, "auth", False),
    (404, "request", False), (422, "request", False), (302, "redirect", False),
    (500, "outcome_unknown", True), (503, "outcome_unknown", True),
    (200, "response", True),
])
@pytest.mark.parametrize("helper", ["generate", "generate_json"])
def test_unsafe_errors_never_replay_and_are_sanitized(tmp_path, status, category, unknown, helper):
    from dms import exceptions
    error_type = getattr(exceptions, "GatewayError", exceptions.GeminiError)
    audit = Audit()
    with endpoint([(status, {"private": "secret-response"}, {"Location": "/leak"})]) as (url, seen):
        client = GeminiClient(gateway_settings(tmp_path, url), usage_tracker=audit)
        with pytest.raises(error_type) as caught:
            getattr(client, helper)("secret-prompt", actor="alice")
    assert caught.value.category == category
    assert caught.value.outcome_unknown is unknown
    assert not caught.value.retryable
    assert len(seen) == 1
    assert len(audit.events) == 2
    assert audit.events[-1]["error_category"] == category
    assert "secret" not in str(caught.value) + repr(audit.events)


@pytest.mark.parametrize("overrides,actor,category", [
    ({}, None, "identity"), ({}, "bad\r\nactor", "identity"),
    ({"gateway_api_key": " key "}, "alice", "configuration"),
    ({"gateway_api_key": "${TOKEN}"}, "alice", "configuration"),
    ({"gateway_allow_insecure_http": False}, "alice", "configuration"),
    ({"gateway_chat_completions_url": "http://user:pw@127.0.0.1/chat"}, "alice", "configuration"),
    ({"gateway_model": ""}, "alice", "configuration"),
    ({"max_retry": 0}, "alice", "configuration"),
    ({"gemini_timeout_seconds": float("nan")}, "alice", "configuration"),
])
def test_validation_blocks_before_network(tmp_path, monkeypatch, overrides, actor, category):
    from dms import exceptions
    error_type = getattr(exceptions, "GatewayError", exceptions.GeminiError)
    client = GeminiClient(gateway_settings(tmp_path, **overrides), usage_tracker=Audit())
    calls = []
    monkeypatch.setattr(client, "_gateway_request", lambda *a: calls.append(a))
    with pytest.raises(error_type) as caught:
        client.generate("secret", actor=actor)
    assert caught.value.category == category
    assert not calls


@pytest.mark.parametrize("failure_event", ["attempt_started", "attempt_finished"])
def test_audit_failure_never_replays(tmp_path, failure_event):
    from dms import exceptions
    error_type = getattr(exceptions, "GatewayError", exceptions.GeminiError)

    class BrokenAudit(Audit):
        def record_attempt(self, **event):
            if event["event_kind"] == failure_event:
                raise OSError("secret disk error")
            super().record_attempt(**event)

    with endpoint([(200, completion(), {})]) as (url, seen):
        client = GeminiClient(gateway_settings(tmp_path, url), usage_tracker=BrokenAudit())
        with pytest.raises(error_type) as caught:
            client.generate("secret", actor="alice")
    assert caught.value.category == "accounting"
    assert caught.value.outcome_unknown is (failure_event == "attempt_finished")
    assert len(seen) == (failure_event == "attempt_finished")


@pytest.mark.parametrize("helper", ["generate", "generate_json"])
def test_quota_retries_are_bounded_with_unique_attempts(tmp_path, monkeypatch, helper):
    from dms.exceptions import GatewayError
    waits = []
    monkeypatch.setattr("dms.gemini_client.time.sleep", waits.append)
    audit = Audit()
    with endpoint([(429, {}, {"Retry-After": "99999999"})] * 3) as (url, seen):
        client = GeminiClient(gateway_settings(tmp_path, url), usage_tracker=audit)
        with pytest.raises(GatewayError) as caught:
            getattr(client, helper)("prompt", actor="alice")
    assert caught.value.category == "quota" and caught.value.retryable
    assert len(seen) == 3 and waits == [60, 60]
    assert len({e["operation_id"] for e in audit.events}) == 1
    assert len({e["attempt_id"] for e in audit.events}) == 3


def test_real_refused_connection_has_presend_budget(tmp_path, monkeypatch):
    import socket

    from dms.exceptions import GatewayError
    waits = []
    monkeypatch.setattr("dms.gemini_client.time.sleep", waits.append)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        url = f"http://127.0.0.1:{sock.getsockname()[1]}/chat"
        audit = Audit()
        client = GeminiClient(gateway_settings(tmp_path, url), usage_tracker=audit)
        with pytest.raises(GatewayError) as caught:
            client.generate("prompt", actor="alice")
    assert caught.value.category == "unreachable" and caught.value.retryable
    assert not caught.value.outcome_unknown
    assert len(audit.events) == 6 and len(waits) == 2


@pytest.mark.parametrize("kind,category,retryable", [
    ("ConnectTimeout", "unreachable", True), ("ReadTimeout", "outcome_unknown", False),
    ("ConnectionError", "outcome_unknown", False), ("SSLError", "configuration", False),
    ("ProxyError", "configuration", False),
])
def test_transport_fault_classification(tmp_path, monkeypatch, kind, category, retryable):
    import requests

    from dms.exceptions import GatewayError
    calls = []

    def fail(*args, **kwargs):
        calls.append(1)
        raise getattr(requests.exceptions, kind)("secret connection refused")

    monkeypatch.setattr(requests.Session, "post", fail)
    client = GeminiClient(gateway_settings(tmp_path), usage_tracker=Audit())
    with pytest.raises(GatewayError) as caught:
        client.generate("prompt", actor="alice")
    assert caught.value.category == category
    assert caught.value.retryable is retryable
    assert len(calls) == (3 if retryable else 1)


def test_parse_failure_keeps_valid_usage(tmp_path):
    from dms.exceptions import GatewayError
    body = completion()
    body["choices"] = []
    audit = Audit()
    with endpoint([(200, body, {})]) as (url, seen):
        client = GeminiClient(gateway_settings(tmp_path, url), usage_tracker=audit)
        with pytest.raises(GatewayError):
            client.generate("prompt", actor="alice")
    assert len(seen) == 1
    assert audit.events[-1]["usage"] == body["usage"]
    assert audit.events[-1]["response_id"] == "response-1"


def test_required_logging_failure_blocks_request(tmp_path, monkeypatch):
    import dms.logging_config as logging_config
    from dms.exceptions import GatewayError
    def broken(*args):
        raise OSError("secret path")
    monkeypatch.setattr(logging_config, "ensure_gateway_logging", broken)
    client = GeminiClient(gateway_settings(tmp_path), usage_tracker=Audit())
    calls = []
    monkeypatch.setattr(client, "_gateway_request", lambda *a: calls.append(1))
    with pytest.raises(GatewayError) as caught:
        client.generate("prompt", actor="alice")
    assert caught.value.category == "accounting" and not calls


def test_standalone_usage_is_persisted_in_existing_database(tmp_path):
    import sqlite3
    with endpoint([(200, completion(), {})]) as (url, seen):
        client = GeminiClient(gateway_settings(tmp_path, url))
        client.generate("prompt", actor="alice", job_id="job-1")
    with sqlite3.connect(tmp_path / "classification_jobs.db") as db:
        rows = db.execute("SELECT event_kind, actor, route, response_id FROM gemini_usage_log ORDER BY id").fetchall()
    assert rows == [("attempt_started", "alice", "gateway", None),
                    ("attempt_finished", "alice", "gateway", "response-1")]


@pytest.mark.parametrize("failed_message", ["attempt_started", "attempt_finished"])
def test_log_write_failure_is_typed_and_never_replayed(tmp_path, monkeypatch, failed_message):
    from dms.exceptions import GatewayError
    from dms.gemini_client import gateway_logger
    original = gateway_logger.info
    def log(message, **kwargs):
        if message == failed_message:
            raise OSError("private disk failure")
        original(message, **kwargs)
    monkeypatch.setattr(gateway_logger, "info", log)
    with endpoint([(200, completion(), {})]) as (url, seen):
        client = GeminiClient(gateway_settings(tmp_path, url), usage_tracker=Audit())
        with pytest.raises(GatewayError) as caught:
            client.generate("prompt", actor="alice")
    assert caught.value.category == "accounting"
    assert caught.value.outcome_unknown is (failed_message == "attempt_finished")
    assert len(seen) == (failed_message == "attempt_finished")


def test_finished_callback_uses_same_persisted_event(tmp_path):
    callbacks, audit = [], Audit()
    with endpoint([(200, completion(), {})]) as (url, seen):
        client = GeminiClient(gateway_settings(tmp_path, url), usage_tracker=audit,
                              on_attempt=callbacks.append)
        client.generate("prompt", actor="alice")
    assert callbacks == [audit.events[-1]]


def test_unknown_usage_and_response_shape_fail_closed(tmp_path):
    body = completion()
    del body["usage"]
    audit = Audit()
    with endpoint([(200, body, {})]) as (url, seen):
        client = GeminiClient(gateway_settings(tmp_path, url), usage_tracker=audit)
        response = client.generate("prompt", actor="alice")
    assert response.usage == {} and audit.events[-1]["usage"] is None