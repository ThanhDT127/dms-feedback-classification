"""DMS feedback classification service."""

from __future__ import annotations

import tomllib
from functools import lru_cache
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _package_version
from pathlib import Path

__all__ = ["__version__", "get_version"]

__version__ = "0.1.0"


def _read_pyproject_version(pyproject_path: Path | None = None) -> str | None:
    target = pyproject_path or (Path(__file__).resolve().parents[2] / "pyproject.toml")
    if not target.is_file():
        return None
    try:
        with target.open("rb") as handle:
            data = tomllib.load(handle)
        project = data.get("project")
        if isinstance(project, dict):
            raw_version = project.get("version")
            if isinstance(raw_version, str) and raw_version.strip():
                return raw_version.strip()
    except Exception:
        return None
    return None


@lru_cache(maxsize=1)
def get_version() -> str:
    """Resolve canonical package version from metadata or pyproject.toml."""
    try:
        installed = _package_version("dms-feedback-classifier").strip()
        if installed:
            return installed
    except PackageNotFoundError:
        pass

    pyproject_ver = _read_pyproject_version()
    if pyproject_ver:
        return pyproject_ver
    return __version__
