"""Tests for auth API endpoints.

These tests require the FastAPI test client.
Run with: pytest tests/test_auth_api.py -v
"""
import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "service" / "src"))

# Note: Full integration tests require FastAPI TestClient setup
# which depends on the full app configuration.
# These are placeholder tests that document expected behavior.


def test_login_endpoint_exists():
    """Login endpoint should be registered at POST /api/auth/login."""
    from dms.web.api.auth_api import router
    routes = [r.path for r in router.routes]
    assert "/api/auth/login" in routes


def test_refresh_endpoint_exists():
    from dms.web.api.auth_api import router
    routes = [r.path for r in router.routes]
    assert "/api/auth/refresh" in routes


def test_me_endpoint_exists():
    from dms.web.api.auth_api import router
    routes = [r.path for r in router.routes]
    assert "/api/auth/me" in routes


def test_change_password_endpoint_exists():
    """Change-password endpoint should be registered at POST /api/auth/change-password."""
    from dms.web.api.auth_api import router
    routes = [r.path for r in router.routes]
    assert "/api/auth/change-password" in routes
