from __future__ import annotations

from dms.http_client import TimeoutSession, create_session


def test_create_session_uses_timeout_session():
    session = create_session(default_timeout=12.5)
    assert isinstance(session, TimeoutSession)
    assert session.default_timeout == 12.5


def test_retry_adapter_is_mounted():
    session = create_session()
    https_adapter = session.adapters["https://"]
    retries = https_adapter.max_retries
    assert retries.total == 3
    assert 429 in retries.status_forcelist
    assert 503 in retries.status_forcelist


def test_timeout_session_sets_default_timeout(monkeypatch):
    captured = {}

    def fake_request(self, method, url, **kwargs):
        captured["timeout"] = kwargs.get("timeout")
        return object()

    monkeypatch.setattr("requests.Session.request", fake_request)
    session = TimeoutSession(default_timeout=9.0)
    session.request("GET", "https://example.com")
    assert captured["timeout"] == 9.0
