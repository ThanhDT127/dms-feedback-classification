"""
Notification module — Teams webhook + email via Graph API.

Sends notifications after file processing (success or error).
Gracefully skips if no notification channel is configured.

Email uses /users/{sender}/sendMail (Client Credentials flow).
"""
import json
import requests
from datetime import datetime

import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import (
    TEAMS_WEBHOOK_URL,
    NOTIFICATION_SENDER_EMAIL,
    NOTIFICATION_RECIPIENTS,
    logger,
)
from auth import get_headers as _get_graph_headers


def _send_teams_webhook(payload: dict) -> bool:
    """
    Send an Adaptive Card to Teams via Incoming Webhook.

    Returns True if sent successfully.
    """
    if not TEAMS_WEBHOOK_URL:
        return False

    try:
        resp = requests.post(
            TEAMS_WEBHOOK_URL,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=10,
        )
        if resp.status_code in (200, 202):
            logger.info("Teams notification sent successfully")
            return True
        else:
            logger.warning("Teams webhook returned %d: %s", resp.status_code, resp.text[:200])
            return False
    except Exception as e:
        logger.warning("Teams webhook error: %s", e)
        return False


def _send_email(subject: str, body: str) -> bool:
    """
    Send email via Microsoft Graph API using application-level endpoint.

    Uses /users/{sender}/sendMail (requires Mail.Send Application permission).
    Supports multiple recipients (internal + external).

    Returns True if sent successfully.
    """
    if not NOTIFICATION_SENDER_EMAIL:
        logger.warning("Email skipped: NOTIFICATION_SENDER_EMAIL not configured")
        return False

    if not NOTIFICATION_RECIPIENTS:
        logger.warning("Email skipped: NOTIFICATION_RECIPIENTS not configured")
        return False

    try:
        headers = _get_graph_headers()
        # Use /users/{sender}/sendMail — works with Client Credentials flow
        url = f"https://graph.microsoft.com/v1.0/users/{NOTIFICATION_SENDER_EMAIL}/sendMail"

        # Build recipient list
        to_recipients = [
            {"emailAddress": {"address": addr}}
            for addr in NOTIFICATION_RECIPIENTS
        ]

        mail_data = {
            "message": {
                "subject": subject,
                "body": {
                    "contentType": "HTML",
                    "content": body,
                },
                "toRecipients": to_recipients,
            },
            "saveToSentItems": False,
        }

        resp = requests.post(url, headers=headers, json=mail_data, timeout=15)
        if resp.status_code in (200, 202):
            recipients_str = ", ".join(NOTIFICATION_RECIPIENTS)
            logger.info("Email sent from %s → %s", NOTIFICATION_SENDER_EMAIL, recipients_str)
            return True
        else:
            logger.warning("Email send returned %d: %s", resp.status_code, resp.text[:300])
            return False
    except Exception as e:
        logger.warning("Email notification error: %s", e)
        return False


def _build_success_html(file_name: str, result: dict) -> str:
    """Build HTML email body for successful processing."""
    total_rows = result.get("total_rows", 0)
    duration = result.get("duration_seconds", 0)
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    return f"""
    <div style="font-family: Segoe UI, Arial, sans-serif; max-width: 600px;">
        <div style="background: #d4edda; padding: 12px 16px; border-radius: 6px; margin-bottom: 16px;">
            <strong style="color: #155724; font-size: 16px;">✅ Phân loại phản hồi hoàn tất</strong>
        </div>
        <table style="width: 100%; border-collapse: collapse;">
            <tr><td style="padding: 6px 0; color: #666;">File:</td>
                <td style="padding: 6px 0;"><strong>{file_name}</strong></td></tr>
            <tr><td style="padding: 6px 0; color: #666;">Số dòng:</td>
                <td style="padding: 6px 0;">{total_rows}</td></tr>
            <tr><td style="padding: 6px 0; color: #666;">Thời gian xử lý:</td>
                <td style="padding: 6px 0;">{duration:.0f}s</td></tr>
            <tr><td style="padding: 6px 0; color: #666;">Thời điểm:</td>
                <td style="padding: 6px 0;">{timestamp}</td></tr>
        </table>
        <p style="color: #888; font-size: 12px; margin-top: 16px;">
            Kết quả đã được upload lên SharePoint Output/
        </p>
    </div>
    """


def _build_error_html(file_name: str, error_msg: str, retry_count: int = 0, max_retries: int = 3) -> str:
    """Build HTML email body for failed processing."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    # Truncate long error messages
    error_display = error_msg[:500] + ("..." if len(error_msg) > 500 else "")

    return f"""
    <div style="font-family: Segoe UI, Arial, sans-serif; max-width: 600px;">
        <div style="background: #f8d7da; padding: 12px 16px; border-radius: 6px; margin-bottom: 16px;">
            <strong style="color: #721c24; font-size: 16px;">❌ Phân loại phản hồi thất bại</strong>
        </div>
        <table style="width: 100%; border-collapse: collapse;">
            <tr><td style="padding: 6px 0; color: #666;">File:</td>
                <td style="padding: 6px 0;"><strong>{file_name}</strong></td></tr>
            <tr><td style="padding: 6px 0; color: #666;">Lỗi:</td>
                <td style="padding: 6px 0; color: #dc3545;">{error_display}</td></tr>
            <tr><td style="padding: 6px 0; color: #666;">Số lần thử:</td>
                <td style="padding: 6px 0;">{retry_count}/{max_retries}</td></tr>
            <tr><td style="padding: 6px 0; color: #666;">Thời điểm:</td>
                <td style="padding: 6px 0;">{timestamp}</td></tr>
        </table>
        <p style="color: #888; font-size: 12px; margin-top: 16px;">
            File đã bị đánh dấu "failed" — cần kiểm tra thủ công.
        </p>
    </div>
    """


def notify_success(file_name: str, result: dict):
    """
    Send success notification after file processing.

    Args:
        file_name: Name of the processed file.
        result: Pipeline result dict (total_rows, duration_seconds, etc.).
    """
    total_rows = result.get("total_rows", 0)
    duration = result.get("duration_seconds", 0)

    # ── Teams Adaptive Card ──
    teams_payload = {
        "type": "message",
        "attachments": [{
            "contentType": "application/vnd.microsoft.card.adaptive",
            "content": {
                "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                "type": "AdaptiveCard",
                "version": "1.4",
                "body": [
                    {
                        "type": "TextBlock",
                        "text": "✅ Phân loại phản hồi hoàn tất",
                        "weight": "Bolder",
                        "size": "Medium",
                        "color": "Good",
                    },
                    {
                        "type": "FactSet",
                        "facts": [
                            {"title": "File", "value": file_name},
                            {"title": "Số dòng", "value": str(total_rows)},
                            {"title": "Thời gian", "value": f"{duration:.0f}s"},
                        ],
                    },
                ],
            },
        }],
    }
    sent = _send_teams_webhook(teams_payload)

    # ── Email (always try, not just fallback) ──
    if not sent:
        subject = f"[DMS] ✅ Phân loại hoàn tất: {file_name}"
        body = _build_success_html(file_name, result)
        _send_email(subject, body)


def notify_error(file_name: str, error_msg: str, retry_count: int = 0, max_retries: int = 3):
    """
    Send error notification when file processing fails permanently.

    Args:
        file_name: Name of the failed file.
        error_msg: Error description.
        retry_count: Current retry count.
        max_retries: Maximum retry attempts.
    """
    # ── Teams Adaptive Card ──
    teams_payload = {
        "type": "message",
        "attachments": [{
            "contentType": "application/vnd.microsoft.card.adaptive",
            "content": {
                "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                "type": "AdaptiveCard",
                "version": "1.4",
                "body": [
                    {
                        "type": "TextBlock",
                        "text": "❌ Phân loại phản hồi thất bại",
                        "weight": "Bolder",
                        "size": "Medium",
                        "color": "Attention",
                    },
                    {
                        "type": "FactSet",
                        "facts": [
                            {"title": "File", "value": file_name},
                            {"title": "Lỗi", "value": error_msg[:200]},
                            {"title": "Retry", "value": f"{retry_count}/{max_retries}"},
                        ],
                    },
                ],
            },
        }],
    }
    sent = _send_teams_webhook(teams_payload)

    # ── Email (always try, not just fallback) ──
    if not sent:
        subject = f"[DMS] ❌ Lỗi phân loại: {file_name}"
        body = _build_error_html(file_name, error_msg, retry_count, max_retries)
        _send_email(subject, body)
