"""Quick manual email-notification checks against the refactored package."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from dms.auth import AuthProvider
from dms.http_client import create_session
from dms.notification import NotificationService
from dms.settings import get_settings


class TestableNotificationService(NotificationService):
    def send_email_direct(self, subject: str, body: str) -> bool:
        return self._send_email(subject, body)


def main() -> None:
    settings = get_settings()
    auth = AuthProvider(settings)
    service = TestableNotificationService(
        auth=auth,
        settings=settings,
        session=create_session(default_timeout=settings.http_timeout_seconds),
    )

    print("=" * 60)
    print("EMAIL NOTIFICATION TEST")
    print("=" * 60)
    print(f"\n  Sender:     {settings.notification_sender_email or '(not configured)'}")
    print(
        f"  Recipients: {', '.join(settings.notification_recipients) or '(not configured)'}"
    )

    if not settings.notification_sender_email:
        print("\n  NOTIFICATION_SENDER_EMAIL not set in .env")
        return
    if not settings.notification_recipients:
        print("\n  NOTIFICATION_RECIPIENTS not set in .env")
        return

    print("\n--- Test 1: Simple email ---")
    ok = service.send_email_direct(
        subject="[DMS] Test email from DMS Service",
        body="""
        <div style="font-family: Segoe UI, Arial, sans-serif;">
            <h2>Test Email</h2>
            <p>This is a test email from the DMS Feedback Classification Service.</p>
        </div>
        """,
    )
    print(f"  Result: {'Sent' if ok else 'Failed'}")

    print("\n--- Test 2: Success notification ---")
    service.send_success(
        "TEST_FILE_demo.xlsx",
        {"total_rows": 150, "duration_seconds": 45.3},
    )
    print("  send_success() called")

    print("\n--- Test 3: Error notification ---")
    service.send_error(
        "TEST_FILE_demo.xlsx",
        "ValueError: Cannot detect header row - file may have unexpected format",
        retry_count=3,
        max_retries=3,
    )
    print("  send_error() called")

    print("\n" + "=" * 60)
    print("Check inbox of:", ", ".join(settings.notification_recipients))
    print("You should see 3 emails.")
    print("=" * 60)


if __name__ == "__main__":
    main()
