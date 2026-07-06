"""JWT token creation and validation utilities."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import jwt

ACCESS_TOKEN_EXPIRE_MINUTES = 30
REFRESH_TOKEN_EXPIRE_DAYS = 7


def create_token(
    subject: str,
    token_type: str,
    secret_key: str,
    expires_minutes: int = 30,
) -> str:
    """Create a JWT token with standard claims.

    Args:
        subject: The token subject (typically a username).
        token_type: Token type — ``'access'`` or ``'refresh'``.
        secret_key: HMAC signing key.
        expires_minutes: Lifetime of the token in minutes.

    Returns:
        Encoded JWT string.
    """
    if not secret_key:
        raise ValueError("JWT secret key must not be empty")
    now = datetime.now(timezone.utc)
    payload = {
        "sub": subject,
        "type": token_type,
        "iat": now,
        "exp": now + timedelta(minutes=expires_minutes),
    }
    return jwt.encode(payload, secret_key, algorithm="HS256")


def decode_token(token: str, secret_key: str, expected_type: str = "access") -> dict:
    """Decode and validate a JWT token.

    Raises:
        jwt.ExpiredSignatureError: Token has expired.
        jwt.InvalidTokenError: Invalid token.
        ValueError: Wrong token type.
    """
    if not secret_key:
        raise ValueError("JWT secret key must not be empty")
    payload = jwt.decode(token, secret_key, algorithms=["HS256"])

    if payload.get("type") != expected_type:
        raise ValueError(f"Expected {expected_type} token, got {payload.get('type')}")

    return payload
