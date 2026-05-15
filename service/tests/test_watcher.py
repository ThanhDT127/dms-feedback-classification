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
    def __init__(self, should_fail=False):
        self.should_fail = should_fail

    def run_pipeline(self, input_path, output_path, ckpt_path):
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


def make_watcher(settings, files, should_fail=False):
    settings.ensure_runtime_dirs()
    metrics = MetricsCollector(settings.metrics_path)
    return Watcher(
        sharepoint_client=FakeSharePoint(files),
        pipeline_runner=FakePipeline(should_fail=should_fail),
        notification_service=FakeNotifications(),
        metrics=metrics,
        settings=settings,
    )


def test_watcher_marks_success_and_skips_done(settings):
    watcher = make_watcher(settings, [{"id": "1", "name": "a.xlsx"}], should_fail=False)
    seen = {}
    processed = watcher.poll_once(seen)
    assert processed == 1
    assert seen["1"]["status"] == "done"


def test_watcher_marks_retry_and_terminal_failure(settings):
    watcher = make_watcher(settings, [{"id": "1", "name": "a.xlsx"}], should_fail=True)
    seen = {}
    assert watcher.poll_once(seen) == 0
    assert seen["1"]["status"] == "retry"
    assert watcher.poll_once(seen) == 0
    assert watcher.poll_once(seen) == 0
    assert seen["1"]["status"] == "failed"
