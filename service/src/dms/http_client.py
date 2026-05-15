"""HTTP client factory with retries and default timeout."""

from __future__ import annotations

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


class TimeoutSession(requests.Session):
    """Session that applies a default timeout unless explicitly overridden."""

    def __init__(self, default_timeout: float = 30.0) -> None:
        super().__init__()
        self.default_timeout = default_timeout

    def request(self, *args, **kwargs):
        kwargs.setdefault("timeout", self.default_timeout)
        return super().request(*args, **kwargs)


def create_session(default_timeout: float = 30.0) -> requests.Session:
    """Create a requests session with retry adapters and default timeout."""
    retry = Retry(
        total=3,
        backoff_factor=1.0,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["DELETE", "GET", "HEAD", "OPTIONS", "POST", "PUT"],
        respect_retry_after_header=True,
    )
    adapter = HTTPAdapter(max_retries=retry)

    session = TimeoutSession(default_timeout=default_timeout)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session
