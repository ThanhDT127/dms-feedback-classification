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


def test_service_log_filters_gateway_attempt_events_and_prefers_service_jsonl(
    tmp_path, monkeypatch
):
    from dms.web.api import metrics_api

    logging_config.setup_logging(tmp_path)
    logging_config.ensure_gateway_logging(tmp_path)

    gw_logger = logging.getLogger("dms.gateway")
    gw_logger.info("attempt_started", extra={"route": "gateway", "outcome": "started"})
    gw_logger.info("attempt_finished", extra={"route": "gateway", "outcome": "failed"})
    logging.getLogger("dms-watcher").warning("AI request failed: route=gateway category=auth")

    service_log = tmp_path / "dms-service.jsonl"
    gateway_log = tmp_path / f"dms-gateway-{os.getpid()}.jsonl"

    service_text = service_log.read_text(encoding="utf-8")
    gateway_text = gateway_log.read_text(encoding="utf-8")

    assert "attempt_started" not in service_text
    assert "attempt_finished" not in service_text
    assert "AI request failed: route=gateway category=auth" in service_text
    assert "attempt_started" in gateway_text
    assert "attempt_finished" in gateway_text

    monkeypatch.setattr(metrics_api, "_log_dir", lambda: tmp_path)
    assert metrics_api._find_latest_log() == service_log
    assert (
        metrics_api._parse_log_line('{"ts":"2026-10-26T00:00:00","level":"INFO","module":"dms.gateway","msg":"attempt_started"}')
        is None
    )
