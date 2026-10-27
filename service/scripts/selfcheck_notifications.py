"""Kiem tra nhanh: job cua web co gui mail khong.

    python scripts/selfcheck_notifications.py            # chi assert (khong gui mail)
    python scripts/selfcheck_notifications.py --send     # gui 1 mail that qua Graph

Chay duoc trong container: docker exec dms-feedback-web python /app/scripts/selfcheck_notifications.py
"""
from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from dms.classification_worker import ClassificationWorkerManager
from dms.web import deps


class FakeNotify:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def send_success(self, file_name, result):
        self.calls.append(("success", file_name, result))

    def send_error(self, file_name, error, retry_count=0, max_retries=3):
        self.calls.append(("error", file_name, error, retry_count))


class BoomNotify:
    def send_success(self, *args, **kwargs):
        raise RuntimeError("graph down")


def _manager(settings, notification_factory):
    return ClassificationWorkerManager(
        settings=settings,
        job_store=None,
        runner_factory=None,
        sharepoint_factory=None,
        notification_factory=notification_factory,
    )


def main() -> int:
    manager = deps.get_classification_worker_manager()
    assert manager is not None, "Khong tao duoc worker manager"
    assert manager.notification_factory is not None, "notification_factory chua duoc wire vao worker"
    settings = manager.settings

    fake = FakeNotify()
    m = _manager(settings, lambda: fake)
    m._notify("success", {"filename": "a.xlsx"}, result={"total_rows": 1})
    m._notify("error", {"filename": "b.xlsx"}, error="boom", retry_count=2)
    assert fake.calls == [
        ("success", "a.xlsx", {"total_rows": 1}),
        ("error", "b.xlsx", "boom", 2),
    ], fake.calls

    # loi thong bao khong duoc lam chet job
    _manager(settings, lambda: BoomNotify())._notify("success", {"filename": "c.xlsx"}, result={})

    print("SELFCHECK_OK: worker goi ca send_success va send_error, loi thong bao bi nuot")

    if "--send" in sys.argv:
        service = manager.notification_factory()
        manager._notify(
            "success",
            {"filename": "selfcheck.xlsx"},
            result={"total_rows": 7, "duration_seconds": 42},
        )
        print("SENT via", type(service).__name__, "->", settings.notification_recipients)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
