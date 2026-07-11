"""In-memory JWT token blacklist."""
from __future__ import annotations

import threading
import time

_lock = threading.Lock()
_blacklist: dict[str, float] = {}  # jti -> exp timestamp


def revoke(jti: str, exp: float) -> None:
    """Add a token's JTI to the blacklist so it is rejected on future use."""
    with _lock:
        _blacklist[jti] = exp


def is_revoked(jti: str) -> bool:
    """Return True if the given JTI has been revoked."""
    now = time.time()
    with _lock:
        exp = _blacklist.get(jti)
        if exp is None:
            return False
        if exp < now:
            _blacklist.pop(jti, None)
            return False
        return True


def cleanup() -> None:
    """Remove entries whose ``exp`` timestamp has already passed."""
    now = time.time()
    with _lock:
        expired = [jti for jti, exp in _blacklist.items() if exp < now]
        for jti in expired:
            del _blacklist[jti]


def clear() -> None:
    """Clear the blacklist (for testing)."""
    with _lock:
        _blacklist.clear()


def size() -> int:
    """Return the number of entries currently in the blacklist."""
    with _lock:
        return len(_blacklist)
