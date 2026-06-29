from __future__ import annotations

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
