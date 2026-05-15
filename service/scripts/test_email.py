"""
Quick test: Send a test email notification.

Run from service/ directory:
    python scripts/test_email.py

Requires:
- .env with NOTIFICATION_SENDER_EMAIL and NOTIFICATION_RECIPIENTS
- Azure AD App has Mail.Send (Application permission + admin consent)
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from config import (
    NOTIFICATION_SENDER_EMAIL,
    NOTIFICATION_RECIPIENTS,
    logger,
)
from notification import notify_success, notify_error, _send_email


def main():
    print("=" * 60)
    print("EMAIL NOTIFICATION TEST")
    print("=" * 60)

    print(f"\n  Sender:     {NOTIFICATION_SENDER_EMAIL or '(not configured)'}")
    print(f"  Recipients: {', '.join(NOTIFICATION_RECIPIENTS) or '(not configured)'}")

    if not NOTIFICATION_SENDER_EMAIL:
        print("\n  ❌ NOTIFICATION_SENDER_EMAIL not set in .env")
        return

    if not NOTIFICATION_RECIPIENTS:
        print("\n  ❌ NOTIFICATION_RECIPIENTS not set in .env")
        return

    # ── Test 1: Simple email ──
    print("\n─── Test 1: Simple email ───")
    ok = _send_email(
        subject="[DMS] 🧪 Test email từ DMS Service",
        body="""
        <div style="font-family: Segoe UI, Arial, sans-serif;">
            <h2>🧪 Test Email</h2>
            <p>Đây là email test từ DMS Feedback Classification Service.</p>
            <p>Nếu bạn nhận được email này, notification đã hoạt động đúng!</p>
        </div>
        """,
    )
    print(f"  Result: {'✅ Sent!' if ok else '❌ Failed'}")

    # ── Test 2: Success notification ──
    print("\n─── Test 2: Success notification ───")
    notify_success("TEST_FILE_demo.xlsx", {
        "total_rows": 150,
        "duration_seconds": 45.3,
    })
    print("  ✅ notify_success() called")

    # ── Test 3: Error notification ──
    print("\n─── Test 3: Error notification ───")
    notify_error(
        "TEST_FILE_demo.xlsx",
        "ValueError: Cannot detect header row — file may have unexpected format",
        retry_count=3,
        max_retries=3,
    )
    print("  ✅ notify_error() called")

    print("\n" + "=" * 60)
    print("Check inbox of:", ", ".join(NOTIFICATION_RECIPIENTS))
    print("You should see 3 emails.")
    print("=" * 60)


if __name__ == "__main__":
    main()
