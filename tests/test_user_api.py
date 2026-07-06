"""Tests for user management API endpoints.

Run with: pytest tests/test_user_api.py -v
"""
import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "service" / "src"))


def test_user_list_endpoint_exists():
    from dms.web.api.auth_api import user_router
    paths = [r.path for r in user_router.routes]
    assert "/api/users" in paths  # GET root path = list users


def test_user_create_endpoint_exists():
    from dms.web.api.auth_api import user_router
    paths = [r.path for r in user_router.routes]
    assert "/api/users" in paths  # POST to root


def test_user_delete_endpoint_exists():
    from dms.web.api.auth_api import user_router
    paths = [r.path for r in user_router.routes]
    assert "/api/users/{username}" in paths  # DELETE by username
