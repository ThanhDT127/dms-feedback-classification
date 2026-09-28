from __future__ import annotations

import logging
import os

import pytest

from dms import logging_config


def test_gateway_log_is_file_backed_and_process_specific(tmp_path):
    setup = getattr(logging_config, "ensure_gateway_logging", None)
    assert callable(setup), "Gateway runtime needs a dedicated durable log"
    setup(tmp_path)
    logger = logging.getLogger("dms.gateway")
    before = len(logger.handlers)
    setup(tmp_path)
    assert len(logger.handlers) == before
    logger.warning("fallback event attempt=dummy")
    path = tmp_path / f"dms-gateway-{os.getpid()}.jsonl"
    assert "fallback event attempt=dummy" in path.read_text(encoding="utf-8")


def test_gateway_log_failure_does_not_silently_use_console(tmp_path):
    setup = getattr(logging_config, "ensure_gateway_logging", None)
    assert callable(setup)
    blocked = tmp_path / "not-a-directory"
    blocked.write_text("file", encoding="utf-8")
    with pytest.raises(OSError):
        setup(blocked)
