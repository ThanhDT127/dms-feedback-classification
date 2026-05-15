from __future__ import annotations

import pytest

from dms.auth import AuthProvider
from dms.exceptions import AuthenticationError


class FakeMsalApp:
    def __init__(self, result):
        self.result = result
        self.scopes = None

    def acquire_token_for_client(self, scopes):
        self.scopes = scopes
        return self.result


def test_auth_provider_returns_token_and_headers(settings):
    provider = AuthProvider(settings)
    fake_app = FakeMsalApp({"access_token": "abc123"})
    provider._msal_app = fake_app
    assert provider.get_access_token() == "abc123"
    assert fake_app.scopes == settings.graph_scopes
    headers = provider.get_headers()
    assert headers["Authorization"] == "Bearer abc123"


def test_auth_provider_raises_typed_error(settings):
    provider = AuthProvider(settings)
    provider._msal_app = FakeMsalApp({"error": "invalid_client", "error_description": "nope"})
    with pytest.raises(AuthenticationError):
        provider.get_access_token()
