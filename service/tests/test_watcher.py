from __future__ import annotations

import logging

from dms.metrics import MetricsCollector
from dms.watcher import Watcher


class FakeSharePoint:
    def __init__(self, files):
        self.files = files
        self.downloads = []
        self.uploaded = []

    def list_files(self):
        return self.files

    def download_file(self, file_id, local_path):
        self.downloads.append((file_id, local_path))
        local_path.write_text("dummy", encoding="utf-8")
        return local_path

    def upload_output(self, path):
        self.uploaded.append(("output", path))

    def upload_checkpoint(self, path):
        self.uploaded.append(("checkpoint", path))


class FakeSharePointMetricsUploadFails(FakeSharePoint):
    def __init__(self, files, metrics_path):
        super().__init__(files)
        self.metrics_path = metrics_path

    def upload_checkpoint(self, path):
        if path == self.metrics_path:
            raise RuntimeError("metrics upload failed")
        super().upload_checkpoint(path)


class FakePipeline:
    def __init__(self, should_fail=False, label="base"):
        self.should_fail = should_fail
        self.label = label
        self.calls = []

    def run_pipeline(self, input_path, output_path, ckpt_path):
        self.calls.append(self.label)
        if self.should_fail:
            raise RuntimeError("boom")
        output_path.write_text("out", encoding="utf-8")
        ckpt_path.write_text("{}", encoding="utf-8")
        return {"total_rows": 3, "duration_seconds": 1.2}


class FakeNotifications:
    def __init__(self):
        self.success = []
        self.error = []

    def send_success(self, file_name, result):
        self.success.append((file_name, result))

    def send_error(self, file_name, error_msg, retry_count=0, max_retries=3):
        self.error.append((file_name, error_msg, retry_count, max_retries))


class FakeSyncResult:
    def __init__(self, reload_required=False, downloaded_assets=None, errors=None):
        self.reload_required = reload_required
        self.downloaded_assets = downloaded_assets or []
        self.errors = errors or []
        self.changed_assets = list(self.downloaded_assets)
        self.checked_at = "2026-05-16T10:00:00"

    def as_health_dict(self):
        return {
            "checked_at": self.checked_at,
            "reload_required": self.reload_required,
            "changed_assets": self.changed_assets,
            "downloaded_assets": self.downloaded_assets,
            "errors": self.errors,
        }


class FakeConfigSync:
    def __init__(self, results=None, error=None):
        self.results = list(results or [])
        self.error = error

    def sync(self):
        if self.error is not None:
            raise self.error
        if self.results:
            return self.results.pop(0)
        return FakeSyncResult()


def make_watcher(settings, files, should_fail=False, config_sync=None, runner_factory=None):
    settings.ensure_runtime_dirs()
    metrics = MetricsCollector(settings.metrics_path)
    return Watcher(
        sharepoint_client=FakeSharePoint(files),
        pipeline_runner=FakePipeline(should_fail=should_fail),
        notification_service=FakeNotifications(),
        metrics=metrics,
        settings=settings,
        config_asset_sync=config_sync,
        runner_factory=runner_factory,
    )


def test_watcher_marks_success_and_skips_done(settings):
    settings.enable_runtime_cleanup = True
    watcher = make_watcher(settings, [{"id": "1", "name": "a.xlsx"}], should_fail=False)
    seen = {}
    processed = watcher.poll_once(seen)
    assert processed == 1
    assert seen["1"]["status"] == "done"
    assert not (settings.work_dir / "input" / "a.xlsx").exists()
    assert not (settings.work_dir / "output" / "a_output.xlsx").exists()
    assert not (settings.work_dir / "checkpoint" / "a.json").exists()


def test_watcher_marks_retry_and_terminal_failure(settings):
    watcher = make_watcher(settings, [{"id": "1", "name": "a.xlsx"}], should_fail=True)
    seen = {}
    assert watcher.poll_once(seen) == 0
    assert seen["1"]["status"] == "retry"
    assert watcher.poll_once(seen) == 0
    assert watcher.poll_once(seen) == 0
    assert seen["1"]["status"] == "failed"


def test_watcher_reloads_runner_after_asset_sync(settings):
    runs = []

    def runner_factory():
        label = f"reload-{len(runs) + 1}"
        runs.append(label)
        return FakePipeline(label=label)

    sync = FakeConfigSync(
        results=[FakeSyncResult(reload_required=True, downloaded_assets=["keyword/kw_map.json"])]
    )
    watcher = Watcher(
        sharepoint_client=FakeSharePoint([{"id": "1", "name": "a.xlsx"}]),
        pipeline_runner=FakePipeline(label="initial"),
        notification_service=FakeNotifications(),
        metrics=MetricsCollector(settings.metrics_path),
        settings=settings,
        config_asset_sync=sync,
        runner_factory=runner_factory,
    )
    settings.ensure_runtime_dirs()

    seen = {}
    processed = watcher.poll_once(seen)
    assert processed == 1
    assert watcher.pipeline_runner.label == "reload-1"


def test_watcher_keeps_current_runner_when_sync_fails(settings):
    current = FakePipeline(label="stable")
    watcher = Watcher(
        sharepoint_client=FakeSharePoint([{"id": "1", "name": "a.xlsx"}]),
        pipeline_runner=current,
        notification_service=FakeNotifications(),
        metrics=MetricsCollector(settings.metrics_path),
        settings=settings,
        config_asset_sync=FakeConfigSync(error=RuntimeError("sync boom")),
        runner_factory=lambda: FakePipeline(label="should-not-reload"),
    )
    settings.ensure_runtime_dirs()

    seen = {}
    processed = watcher.poll_once(seen)
    assert processed == 1
    assert watcher.pipeline_runner is current


def test_watcher_settings_hot_reloads(settings, monkeypatch, tmp_path):
    # Mock settings
    monkeypatch.setattr(settings, "keyword_dir_override", tmp_path)

    watcher = make_watcher(settings, [])

    # Check current values
    assert watcher.settings.gemini_backend == "vertex"

    # Mock get_settings to return new Settings
    mock_new_settings = settings.model_copy(
        update={
            "gemini_backend": "apikey",
            "gemini_api_key": "some-key",
            "gemini_model": "gemini-2.5-pro",
            "notify_on_success": False,
        }
    )

    # Mock get_settings in dms.settings module
    monkeypatch.setattr("dms.settings.get_settings", lambda: mock_new_settings)

    watcher.reload_settings()

    # Verify in-place attributes are updated
    assert watcher.settings.gemini_backend == "apikey"
    assert watcher.settings.gemini_model == "gemini-2.5-pro"
    assert watcher.settings.notify_on_success is False


def test_watcher_error_logging_keeps_original_failure_when_metrics_upload_fails(
    settings, caplog
):
    settings.ensure_runtime_dirs()
    watcher = Watcher(
        sharepoint_client=FakeSharePointMetricsUploadFails(
            [{"id": "1", "name": "a.xlsx"}],
            settings.metrics_path,
        ),
        pipeline_runner=FakePipeline(should_fail=True),
        notification_service=FakeNotifications(),
        metrics=MetricsCollector(settings.metrics_path),
        settings=settings,
    )
    seen = {}

    with caplog.at_level(logging.WARNING):
        processed = watcher.poll_once(seen)

    assert processed == 0
    assert seen["1"]["last_error"] == "RuntimeError: boom"
    assert watcher.metrics.last_error["error"] == "boom"
    assert "metrics upload failed" in caplog.text


def test_watcher_auto_resets_failed_file_on_sharepoint_change(settings):
    """Task 9.4: poll_once() auto-resets failed file when lastModifiedDateTime changes."""
    files = [{"id": "1", "name": "a.xlsx", "lastModifiedDateTime": "2026-07-17T10:00:00Z"}]
    watcher = make_watcher(settings, files, should_fail=True)

    # Fail the file 3 times to reach "failed" status
    seen = {}
    watcher.poll_once(seen)
    watcher.poll_once(seen)
    watcher.poll_once(seen)
    assert seen["1"]["status"] == "failed"

    # Now simulate SharePoint returning a newer lastModifiedDateTime
    watcher.sharepoint_client.files = [
        {"id": "1", "name": "a.xlsx", "lastModifiedDateTime": "2026-07-17T12:00:00Z"}
    ]

    # Store the old modified time in seen
    seen["1"]["lastModifiedDateTime"] = "2026-07-17T10:00:00Z"

    # poll_once should auto-reset the file
    watcher.poll_once(seen)
    assert seen["1"]["status"] in ("retry", "failed")  # retry then processed (fails again)
    assert seen["1"]["failures"] <= 1  # reset to 0 then incremented once


def test_watcher_record_retry_failure_is_final_on_max_retries(settings):
    """Task 9.5: _process_file() calls record_retry_failure(is_final=True) when retries exhausted."""
    watcher = make_watcher(
        settings, [{"id": "1", "name": "a.xlsx"}], should_fail=True
    )
    seen = {}

    # First failure - not final
    watcher.poll_once(seen)
    assert seen["1"]["failures"] == 1
    assert watcher.metrics.total_retries == 1
    assert watcher.metrics.files_failed == 0  # Not final yet

    # Second failure - not final
    watcher.poll_once(seen)
    assert seen["1"]["failures"] == 2
    assert watcher.metrics.total_retries == 2
    assert watcher.metrics.files_failed == 0  # Not final yet

    # Third failure - final (MAX_FILE_RETRIES = 3)
    watcher.poll_once(seen)
    assert seen["1"]["failures"] == 3
    assert seen["1"]["status"] == "failed"
    assert watcher.metrics.total_retries == 3
    assert watcher.metrics.files_failed == 1  # Final failure counted

