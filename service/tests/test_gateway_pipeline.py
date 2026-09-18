"""Offline Gateway pipeline contracts; no provider or credential access."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import Mock

import pandas as pd
import pytest

from dms.classification_jobs import ClassificationJobStore
from dms.classification_worker import ClassificationWorkerManager
from dms.exceptions import GatewayError
from dms.metrics import MetricsCollector
from dms.pipeline.issue_classifier import IssueClassifier
from dms.pipeline.rag_product import RAGProductMatcher
from dms.pipeline.runner import PipelineRunner
from dms.watcher import Watcher


class RecordingClient:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def generate(self, prompt, **kwargs):
        self.calls.append(("text", kwargs))
        return self._response()

    def generate_json(self, prompt, **kwargs):
        self.calls.append(("json", kwargs))
        return self._response()

    def _response(self):
        value = next(self.responses)
        if isinstance(value, Exception):
            raise value
        return SimpleNamespace(
            text=value, usage={"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3}
        )


@pytest.fixture
def pipeline(settings, tmp_path):
    settings.gemini_backend = "gateway"
    settings.rate_gap_sec = 0
    settings.llm_batch_size = 10
    settings.keyword_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        [{"Model": "AT10 8W", "Dòng SP": "Bulb", "Sản phẩm": "LED"}]
    ).to_excel(settings.df_products_path, index=False)
    input_path = tmp_path / "input.xlsx"
    pd.DataFrame({"Nội dung phản hồi": ["first", "second"]}).to_excel(input_path, index=False)

    def build(client):
        runner = PipelineRunner(
            gemini=client,
            rag=RAGProductMatcher(settings, client),
            metrics=MetricsCollector(tmp_path / "metrics.json"),
            settings=settings,
            usage_tracker=Mock(),
        )
        return runner, (input_path, tmp_path / "out.xlsx", tmp_path / "ckpt.json")

    return build


def test_gateway_pipeline_passes_explicit_metadata_through_json_repair(pipeline):
    client = RecordingClient([
        "1. NONE\n2. NONE",
        json.dumps([{"row_index": 0, "labels": {"Báo lỗi": True}}]),
        json.dumps([{"row_index": 0, "labels": {"Báo CL tốt": True}}]),
    ])
    runner, paths = pipeline(client)

    result = runner.run_pipeline(*paths, actor="owner", job_id="job-1")

    assert result["processed_rows"] == 2
    assert client.calls == [
        ("text", {"actor": "owner", "job_id": "job-1", "operation": "rag_extract"}),
        ("json", {"temperature": 0.0, "actor": "owner", "job_id": "job-1", "operation": "classify_batch"}),
        ("json", {"temperature": 0.0, "actor": "owner", "job_id": "job-1", "operation": "classify_batch"}),
    ]


@pytest.mark.parametrize("stage", ["rag", "json", "mini_repair", "single_repair", "runner_row"])
@pytest.mark.parametrize("category,retryable", [("auth", False), ("unreachable", True)])
def test_gateway_failure_is_not_a_neutral_completed_workbook(pipeline, stage, category, retryable):
    error = GatewayError(category, retryable=retryable)
    responses = [] if stage == "rag" else ["1. NONE\n2. NONE"]
    if stage == "mini_repair":
        responses.append(json.dumps([{"row_index": 0, "labels": {"Báo lỗi": True}}]))
    elif stage == "single_repair":
        responses.append("not json")
    responses.append(error)
    client = RecordingClient(responses)
    runner, paths = pipeline(client)
    if stage == "runner_row":
        runner.issue_classifier.classify_batch = Mock(side_effect=[ValueError("bad batch"), error])

    with pytest.raises(GatewayError) as caught:
        runner.run_pipeline(*paths, actor="owner", job_id="job-1")

    assert caught.value is error
    assert caught.value.retryable is retryable
    assert not paths[1].exists()
    assert not paths[2].exists()
    if stage == "runner_row":
        assert runner.issue_classifier.classify_batch.call_count == 2
    else:
        assert len(client.calls) == len(responses)


def test_gateway_runner_does_not_record_client_usage_again(pipeline):
    client = RecordingClient(["1. NONE\n2. NONE", '[{"labels": {}}, {"labels": {}}]'])
    runner, paths = pipeline(client)

    runner.run_pipeline(*paths, actor="owner", job_id="job-1")

    assert runner.metrics.gemini_calls == 0
    runner.usage_tracker.record.assert_not_called()


def test_legacy_helpers_accept_old_client_signatures_and_fail_soft(settings):
    class LegacyClient:
        def generate(self, prompt):
            raise RuntimeError("legacy unavailable")

        def generate_json(self, prompt, temperature=0.0):
            raise RuntimeError("legacy unavailable")

    rag = object.__new__(RAGProductMatcher)
    rag.settings = settings
    rag.gemini = LegacyClient()
    assert rag.llm_extract_batch(["row"], actor="ignored", job_id="ignored") == ["NONE"]
    result = IssueClassifier(LegacyClient(), settings).classify_batch(
        ["row"], actor="ignored", job_id="ignored"
    )
    assert result[0]["final_minors"] == ["Tin trung lập"]


@pytest.mark.parametrize("retryable", [False, True])
def test_worker_preserves_gateway_retry_policy_and_job_metadata(pipeline, settings, tmp_path, retryable):
    client = RecordingClient([GatewayError("unreachable" if retryable else "auth", retryable=retryable)])
    runner, paths = pipeline(client)
    settings.upload_input_to_sharepoint = False
    settings.classification_retry_count = 1
    store = ClassificationJobStore(tmp_path / "jobs.db")
    store.create_job(
        job_id="job-1", owner_username="owner", owner_role="user", filename="input.xlsx",
        mode="single", input_path=paths[0], output_path=paths[1],
    )
    job = store.claim_next_job(worker_id="test", global_running_limit=1, per_user_running_limit=1)
    worker = ClassificationWorkerManager(
        settings=settings, job_store=store, runner_factory=lambda: runner, sharepoint_factory=lambda: None,
    )

    worker._process_job(job)

    persisted = store.get_job("job-1", include_results=True)
    assert persisted["status"] == ("queued" if retryable else "error")
    assert persisted["retry_count"] == int(retryable)
    assert persisted["results"] == []
    assert client.calls == [
        ("text", {"actor": "owner", "job_id": "job-1", "operation": "rag_extract"})
    ]


@pytest.mark.parametrize("retryable", [False, True])
def test_watcher_gateway_failure_retry_policy(settings, retryable):
    settings.gemini_backend = "gateway"
    settings.ensure_runtime_dirs()
    pipeline = Mock()
    pipeline.run_pipeline.side_effect = GatewayError(
        "unreachable" if retryable else "outcome_unknown", retryable=retryable
    )
    file_info = {"id": "file-1", "name": "input.xlsx", "lastModifiedDateTime": "2026-01-01T00:00:00Z"}
    sharepoint = Mock()
    sharepoint.list_files.return_value = [file_info]
    watcher = Watcher(
        sharepoint, pipeline, Mock(), MetricsCollector(settings.metrics_path), settings
    )
    seen = {}

    assert watcher.poll_once(seen) == 0
    assert seen["file-1"]["status"] == ("retry" if retryable else "failed")
    if not retryable:
        assert watcher.poll_once(seen) == 0
        assert pipeline.run_pipeline.call_count == 1
    sharepoint.upload_output.assert_not_called()


@pytest.mark.parametrize("field,value", [
    ("gateway_chat_completions_url", "https://gateway.invalid/new"),
    ("gateway_api_key", "new-test-key"),
    ("gateway_model", "new-alias"),
    ("gateway_allow_insecure_http", True),
    ("fallback_enabled", True),
    ("fallback_fail_threshold", 2),
    ("fallback_retry_after_s", 90.0),
    ("gcp_project_id", "new-project"),
    ("gcp_location", "new-region"),
    ("gcp_service_account_json", "unused-test-credential.json"),
    ("gemini_model", "gemini-2.5-pro"),
    ("max_retry", 4),
    ("base_wait", 9.0),
    ("gemini_timeout_seconds", 90.0),
])
def test_watcher_gateway_reload_replaces_client_snapshot(pipeline, settings, monkeypatch, field, value):
    from dms.gemini_client import GeminiClient

    runner, _ = pipeline(GeminiClient(settings))
    old_value = getattr(settings, field)
    new_settings = settings.model_copy(update={field: value})
    watcher = Watcher(Mock(), runner, Mock(), runner.metrics, settings)
    monkeypatch.setattr("dms.settings.get_settings", lambda: new_settings)

    watcher.reload_settings()

    replacement = watcher.pipeline_runner
    assert replacement is not runner
    assert replacement.gemini is not runner.gemini
    assert getattr(replacement.gemini.settings, field) == value
    assert getattr(runner.gemini.settings, field) == old_value
    assert replacement.gemini.settings is replacement.settings
    assert replacement.rag.gemini is replacement.gemini
    assert replacement.issue_classifier.gemini is replacement.gemini
    assert replacement.usage_tracker is runner.usage_tracker


def test_gateway_asset_refresh_does_not_restore_stale_factory_client(pipeline, settings):
    from dms.gemini_client import GeminiClient

    runner, _ = pipeline(GeminiClient(settings))
    sync = Mock()
    sync.sync.return_value = SimpleNamespace(
        reload_required=True, downloaded_assets=[], errors=[], as_health_dict=lambda: {}
    )
    snapshot = settings.model_copy(update={"gateway_model": "refreshed-alias"})
    sync.get_runtime_settings.return_value = snapshot
    watcher = Watcher(
        Mock(), runner, Mock(), runner.metrics, snapshot,
        config_asset_sync=sync, runner_factory=lambda: runner,
    )

    watcher._sync_config_assets()

    assert watcher.pipeline_runner is not runner
    assert watcher.pipeline_runner.gemini is not runner.gemini
    assert watcher.pipeline_runner.gemini.settings.gateway_model == "refreshed-alias"
    assert watcher.pipeline_runner.settings is not snapshot