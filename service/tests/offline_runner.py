"""Run tests without repository credentials or external network access."""

import builtins
import io
import os
import socket
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SERVICE = ROOT / "service"
# Keep OS/toolchain environment, not inherited application/provider settings.
for name in list(os.environ):
    if name.startswith(
        (
            "GEMINI_",
            "GATEWAY_",
            "GOOGLE_",
            "GCP_",
            "AZURE_",
            "FALLBACK_",
            "SMTP_",
            "NOTIFICATION_",
            "SHAREPOINT_",
        )
    ) or name in {
        "JWT_SECRET_KEY",
        "DEFAULT_ADMIN_PASSWORD",
        "SERVICE_DIR",
        "DATA_DIR",
        "WORK_DIR",
        "LOG_DIR",
        "MODEL_DIR",
    }:
        os.environ.pop(name, None)
os.environ["JWT_SECRET_KEY"] = "offline-test-secret-not-for-production-0000"

import dotenv  # noqa: E402 — clear application environment before dependency imports
from pydantic_settings.sources import DotEnvSettingsSource  # noqa: E402

_original_read_env = DotEnvSettingsSource._read_env_file


def secret_path(value):
    try:
        path = Path(value).resolve()
    except (TypeError, ValueError, OSError):
        return False
    return path.is_relative_to(ROOT) and (
        path.name.startswith(".env") or path.name in {"sa-key.json", "testvertex.json"}
    )


def read_env(self, path):
    return {} if secret_path(path) else _original_read_env(self, path)


DotEnvSettingsSource._read_env_file = read_env
_original_load = dotenv.load_dotenv
_original_values = dotenv.dotenv_values


def safe_load(dotenv_path=None, *args, **kwargs):
    if dotenv_path is None or secret_path(dotenv_path):
        return False
    return _original_load(dotenv_path, *args, **kwargs)


def safe_values(dotenv_path=None, *args, **kwargs):
    if dotenv_path is None or secret_path(dotenv_path):
        return {}
    return _original_values(dotenv_path, *args, **kwargs)


dotenv.load_dotenv = safe_load
dotenv.dotenv_values = safe_values
_original_open = builtins.open
_original_io_open = io.open


def guarded_open(file, *args, **kwargs):
    if secret_path(file):
        raise PermissionError("Offline tests cannot open repository credential files")
    return _original_open(file, *args, **kwargs)


def guarded_io_open(file, *args, **kwargs):
    if secret_path(file):
        raise PermissionError("Offline tests cannot open repository credential files")
    return _original_io_open(file, *args, **kwargs)


builtins.open = guarded_open
io.open = guarded_io_open
_connect = socket.socket.connect
_connect_ex = socket.socket.connect_ex
_getaddrinfo = socket.getaddrinfo


def local(address):
    return isinstance(address, tuple) and address[0] in {"127.0.0.1", "::1", "localhost"}


def connect(self, address):
    if not local(address):
        raise PermissionError("Offline test blocked non-loopback network connection")
    return _connect(self, address)


def connect_ex(self, address):
    if not local(address):
        raise PermissionError("Offline test blocked non-loopback network connection")
    return _connect_ex(self, address)


def getaddrinfo(host, *args, **kwargs):
    if host not in {"127.0.0.1", "::1", "localhost", None}:
        raise PermissionError("Offline test blocked external DNS")
    return _getaddrinfo(host, *args, **kwargs)


socket.socket.connect = connect
socket.socket.connect_ex = connect_ex
socket.getaddrinfo = getaddrinfo
sys.path.insert(0, str(SERVICE / "src"))
import pytest  # noqa: E402 — install credential/network guards before test imports

if __name__ == "__main__":
    raise SystemExit(pytest.main(sys.argv[1:] or [str(SERVICE / "tests"), "-q"]))
