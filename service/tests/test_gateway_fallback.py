from __future__ import annotations

import pytest
from test_gateway import Audit, gateway_settings

from dms.exceptions import GatewayError
from dms.gemini_client import GeminiClient, GeminiResponse


def fallback_client(tmp_path, monkeypatch, **overrides):
    credential = tmp_path / "dummy.json"
    credential.write_text("{}")
    options = dict(fallback_enabled=True, gcp_project_id="dummy-project",
                   gcp_service_account_json=str(credential))
    options.update(overrides)
    audit = Audit()
    client = GeminiClient(gateway_settings(tmp_path, **options), usage_tracker=audit)
    direct = []

    def direct_request(*args):
        direct.append(args)
        return GeminiResponse("direct", {"prompt_tokens": 2}, route="direct_vertex",
                              model_actual="direct-model")

    monkeypatch.setattr(client, "_direct_request", direct_request, raising=False)
    return client, audit, direct


def unreachable(*args):
    raise GatewayError("unreachable", retryable=True)


@pytest.mark.parametrize("helper", ["generate", "generate_json"])
def test_threshold_on_final_attempt_rescues_same_helper(tmp_path, monkeypatch, helper):
    client, audit, direct = fallback_client(tmp_path, monkeypatch)
    monkeypatch.setattr(client, "_gateway_request", unreachable)
    result = getattr(client, helper)("prompt", actor="alice")
    assert result.text == "direct" and len(direct) == 1
    starts = [e for e in audit.events if e["event_kind"] == "attempt_started"]
    assert [e["route"] for e in starts] == ["gateway"] * 3 + ["direct_vertex"]
    assert len({e["operation_id"] for e in starts}) == 1
    assert starts[-1]["incident_id"]
    getattr(client, helper)("second", actor="alice")
    assert len(direct) == 2
    assert client._direct_attempted == client._direct_succeeded == 2


def test_direct_failure_is_one_attempt_per_new_helper(tmp_path, monkeypatch):
    client, audit, direct = fallback_client(tmp_path, monkeypatch)
    monkeypatch.setattr(client, "_gateway_request", unreachable)
    def fail(*args):
        direct.append(1)
        raise RuntimeError("secret direct error")
    monkeypatch.setattr(client, "_direct_request", fail)
    for _ in range(2):
        with pytest.raises(GatewayError) as caught:
            client.generate("prompt", actor="alice")
        assert not caught.value.retryable
    assert len(direct) == 2
    assert client._direct_attempted == 2 and client._direct_succeeded == 0


@pytest.mark.parametrize("overrides", [
    {"fallback_fail_threshold": 0}, {"fallback_fail_threshold": 4},
    {"fallback_retry_after_s": float("inf")}, {"fallback_retry_after_s": 0},
])
def test_runtime_fallback_threshold_validation(tmp_path, monkeypatch, overrides):
    client, audit, direct = fallback_client(tmp_path, monkeypatch, **overrides)
    monkeypatch.setattr(client, "_gateway_request", unreachable)
    with pytest.raises(GatewayError) as caught:
        client.generate("prompt", actor="alice")
    assert caught.value.category == "configuration"
    assert not audit.events and not direct


def test_missing_direct_file_disables_only_fallback(tmp_path, monkeypatch, caplog):
    client, audit, direct = fallback_client(tmp_path, monkeypatch,
                                          gcp_service_account_json=str(tmp_path / "missing.json"))
    monkeypatch.setattr(client, "_gateway_request", unreachable)
    with pytest.raises(GatewayError) as caught:
        client.generate("prompt", actor="alice")
    assert caught.value.category == "unreachable" and not direct
    assert "fallback" in caplog.text.lower()


@pytest.mark.parametrize("helper", ["generate", "generate_json"])
@pytest.mark.parametrize("probe_error", [None, "unreachable", "auth", "quota", "outcome_unknown"])
def test_probe_policy_and_second_incident(tmp_path, monkeypatch, helper, probe_error):
    clock = [10.0]
    monkeypatch.setattr("dms.gemini_client.time.monotonic", lambda: clock[0])
    client, audit, direct = fallback_client(tmp_path, monkeypatch)
    monkeypatch.setattr(client, "_gateway_request", unreachable)
    def call():
        return getattr(client, helper)("prompt", actor="alice")
    call()
    first = client._incident_id
    clock[0] = 69
    call()
    assert len(direct) == 2
    clock[0] = 70
    probes = []
    def probe(*args):
        probes.append(args)
        if probe_error:
            raise GatewayError(probe_error, retryable=probe_error in {"quota", "unreachable"},
                               outcome_unknown=probe_error == "outcome_unknown")
        return GeminiResponse("recovered", route="gateway")
    monkeypatch.setattr(client, "_gateway_request", probe)
    if probe_error in {"auth", "quota", "outcome_unknown"}:
        with pytest.raises(GatewayError):
            call()
    else:
        assert call().text == ("direct" if probe_error else "recovered")
    assert len(probes) == 1
    assert len(direct) == (3 if probe_error == "unreachable" else 2)
    if probe_error == "unreachable":
        assert client._incident_id == first
        call()
        assert len(probes) == 1
    else:
        assert client._incident_id is None and client._unreachable == 0
        monkeypatch.setattr(client, "_gateway_request", unreachable)
        call()
        assert client._incident_id != first
        assert client._direct_attempted == client._direct_succeeded == 1


def test_single_flight_probe_does_not_hold_network_lock(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    clock = [0.0]
    monkeypatch.setattr("dms.gemini_client.time.monotonic", lambda: clock[0])
    client, audit, direct = fallback_client(tmp_path, monkeypatch)
    monkeypatch.setattr(client, "_gateway_request", unreachable)
    client.generate("open", actor="alice")
    clock[0] = 60
    entered, release = Event(), Event()
    probes = []
    def probe(*args):
        probes.append(1)
        entered.set()
        assert release.wait(5)
        return GeminiResponse("recovered", route="gateway")
    monkeypatch.setattr(client, "_gateway_request", probe)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(client.generate, "probe", actor="alice")
        try:
            assert entered.wait(2)
            second = pool.submit(client.generate, "concurrent", actor="alice")
            assert second.result(2).text == "direct"
        finally:
            release.set()
        assert first.result(2).text == "recovered"
    assert len(probes) == 1 and client._incident_id is None


def test_late_gateway_success_cannot_close_new_incident(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    client, audit, direct = fallback_client(tmp_path, monkeypatch)
    entered, release = Event(), Event()
    def request(config, prompt, *args):
        if prompt == "late":
            entered.set()
            assert release.wait(5)
            return GeminiResponse("late", route="gateway")
        return unreachable()
    monkeypatch.setattr(client, "_gateway_request", request)
    with ThreadPoolExecutor(max_workers=1) as pool:
        late = pool.submit(client.generate, "late", actor="alice")
        try:
            assert entered.wait(2)
            client.generate("open", actor="alice")
            incident = client._incident_id
        finally:
            release.set()
        assert late.result(2).text == "late"
    assert client._incident_id == incident and client._unreachable == 3


@pytest.mark.parametrize("json_mode", [False, True])
def test_direct_sdk_lazy_singleton_explicit_credentials_and_no_retries(tmp_path, monkeypatch, json_mode):
    from types import SimpleNamespace

    client, audit, direct = fallback_client(tmp_path, monkeypatch)
    # Exercise the real direct boundary, not the fault-injection seam.
    monkeypatch.delattr(client, "_direct_request")
    initialized, requests_seen = [], []
    credentials = object()
    def generate_content(**kwargs):
        requests_seen.append(kwargs)
        return SimpleNamespace(text="direct-text", model_version="direct-actual",
                               usage_metadata=SimpleNamespace(prompt_token_count=3,
                                   candidates_token_count=2, total_token_count=8,
                                   thoughts_token_count=3, cached_content_token_count=1))
    def construct(**kwargs):
        initialized.append(kwargs)
        return SimpleNamespace(models=SimpleNamespace(generate_content=generate_content))
    import google.genai
    import google.oauth2.service_account
    monkeypatch.setattr(google.genai, "Client", construct)
    monkeypatch.setattr(google.oauth2.service_account.Credentials, "from_service_account_file",
                        lambda *a, **kw: credentials)
    monkeypatch.setattr(client, "_gateway_request", unreachable)
    helper = client.generate_json if json_mode else client.generate
    for _ in range(2):
        response = helper("prompt", temperature=0.2, actor="alice")
        assert response.route == "direct_vertex" and response.response_id is None
    assert len(initialized) == 1 and len(requests_seen) == 2
    options = initialized[0]
    assert options["vertexai"] is True and options["credentials"] is credentials
    assert options["project"] == "dummy-project" and options["location"] == "global"
    assert options["http_options"].retry_options.attempts == 1
    assert options["http_options"].timeout == 300
    assert requests_seen[0]["model"] == "direct-model"
    assert requests_seen[0]["config"].temperature == 0.2
    assert requests_seen[0]["config"].response_mime_type == ("application/json" if json_mode else None)
    assert response.usage["completion_tokens"] == 2
    assert response.usage["completion_tokens_details"]["reasoning_tokens"] == 3
    assert response.usage["prompt_tokens_details"]["cached_tokens"] == 1
    assert audit.events[-1]["model_requested"] == "Alias-EXACT"
    assert audit.events[-1]["model_actual"] == "direct-actual"


def test_late_direct_response_does_not_count_in_next_incident(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    clock = [0.0]
    monkeypatch.setattr("dms.gemini_client.time.monotonic", lambda: clock[0])
    client, audit, direct = fallback_client(tmp_path, monkeypatch)
    monkeypatch.setattr(client, "_gateway_request", unreachable)
    client.generate("open", actor="alice")
    old = client._incident_id
    entered, release = Event(), Event()
    def delayed(config, prompt, *args):
        if prompt == "late":
            entered.set()
            assert release.wait(5)
        return GeminiResponse("direct", route="direct_vertex")
    monkeypatch.setattr(client, "_direct_request", delayed)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(client.generate, "late", actor="alice")
        try:
            assert entered.wait(2)
            clock[0] = 60
            monkeypatch.setattr(client, "_gateway_request", lambda *a: GeminiResponse("recovered", route="gateway"))
            client.generate("recover", actor="alice")
            monkeypatch.setattr(client, "_gateway_request", unreachable)
            client.generate("reopen", actor="alice")
            assert client._incident_id != old
        finally:
            release.set()
        future.result(2)
    assert client._direct_attempted == client._direct_succeeded == 1


def test_simultaneous_threshold_opens_one_incident(tmp_path, monkeypatch, caplog):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    client, audit, direct = fallback_client(tmp_path, monkeypatch, fallback_fail_threshold=1)
    barrier = Barrier(3)
    def fail(*args):
        barrier.wait(3)
        unreachable()
    monkeypatch.setattr(client, "_gateway_request", fail)
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(client.generate, "prompt", actor="alice") for _ in range(3)]
        assert [f.result(5).text for f in futures] == ["direct"] * 3
    assert len({e["incident_id"] for e in audit.events if e["route"] == "direct_vertex"}) == 1
    assert sum(r.message == "fallback_open" for r in caplog.records) == 1
    assert client._direct_attempted == client._direct_succeeded == 3


def test_quota_between_unreachable_attempts_does_not_reset_counter(tmp_path, monkeypatch):
    client, audit, direct = fallback_client(tmp_path, monkeypatch)
    faults = iter([GatewayError("unreachable", retryable=True), GatewayError("quota", retryable=True),
                   GatewayError("unreachable", retryable=True), GatewayError("unreachable", retryable=True)])
    def request(*args):
        raise next(faults)
    monkeypatch.setattr(client, "_gateway_request", request)
    with pytest.raises(GatewayError):
        client.generate("first", actor="alice")
    assert client._unreachable == 2 and not direct
    assert client.generate("second", actor="alice").route == "direct_vertex"


def test_direct_response_parse_failure_preserves_usage(tmp_path, monkeypatch):
    client, audit, direct = fallback_client(tmp_path, monkeypatch)
    monkeypatch.setattr(client, "_gateway_request", unreachable)
    def malformed(*args):
        error = GatewayError("response", outcome_unknown=True)
        error.usage = {"prompt_tokens": 4}
        raise error
    monkeypatch.setattr(client, "_direct_request", malformed)
    with pytest.raises(GatewayError):
        client.generate("prompt", actor="alice")
    assert audit.events[-1]["usage"] == {"prompt_tokens": 4}