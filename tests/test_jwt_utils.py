"""Tests for JWT token utilities."""
import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "service" / "src"))

from dms.jwt_utils import create_token, decode_token

SECRET = "test-secret-key-for-testing-32-bytes-min"
WRONG_SECRET = "wrong-secret-key-for-testing-32-bytes"


def test_create_access_token():
    token = create_token(subject="testuser", token_type="access", secret_key=SECRET)
    assert isinstance(token, str)
    assert len(token) > 10


def test_decode_access_token():
    token = create_token(subject="testuser", token_type="access", secret_key=SECRET)
    payload = decode_token(token, SECRET, expected_type="access")
    assert payload["sub"] == "testuser"
    assert payload["type"] == "access"


def test_create_refresh_token():
    token = create_token(
        subject="testuser",
        token_type="refresh",
        secret_key=SECRET,
        expires_minutes=60 * 24 * 7,
    )
    payload = decode_token(token, SECRET, expected_type="refresh")
    assert payload["type"] == "refresh"
    assert payload["sub"] == "testuser"


def test_wrong_token_type():
    token = create_token(subject="testuser", token_type="access", secret_key=SECRET)
    with pytest.raises(ValueError, match="Expected refresh token"):
        decode_token(token, SECRET, expected_type="refresh")


def test_invalid_signature():
    import jwt
    token = create_token(subject="testuser", token_type="access", secret_key=SECRET)
    with pytest.raises(jwt.InvalidSignatureError):
        decode_token(token, WRONG_SECRET, expected_type="access")


def test_expired_token():
    import jwt as pyjwt
    from datetime import datetime, timedelta, timezone

    payload = {
        "sub": "testuser",
        "type": "access",
        "iat": datetime.now(timezone.utc) - timedelta(hours=2),
        "exp": datetime.now(timezone.utc) - timedelta(hours=1),
    }
    token = pyjwt.encode(payload, SECRET, algorithm="HS256")
    with pytest.raises(pyjwt.ExpiredSignatureError):
        decode_token(token, SECRET, expected_type="access")
