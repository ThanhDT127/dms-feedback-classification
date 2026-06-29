"""Notification services for Teams and email."""

from __future__ import annotations

import logging
from datetime import datetime

import requests

from .auth import AuthProvider
from .settings import Settings

logger = logging.getLogger("dms-watcher")


class NotificationService:
    """Send success and failure notifications."""

    def __init__(
        self,
        auth: AuthProvider,
        settings: Settings,
        session: requests.Session,
    ) -> None:
        self.auth = auth
        self.settings = settings
        self.session = session

    def _send_teams_webhook(self, payload: dict) -> bool:
        if not self.settings.teams_webhook_url:
            return False
        try:
            response = self.session.post(
                self.settings.teams_webhook_url,
                json=payload,
                headers={"Content-Type": "application/json"},
            )
        except Exception as exc:
            logger.warning("Teams webhook error: %s", exc)
            return False
        if response.status_code in (200, 202):
            logger.info("Teams notification sent successfully")
            return True
        logger.warning(
            "Teams webhook returned %d: %s",
            response.status_code,
            response.text[:200],
        )
        return False

    def _send_email(self, subject: str, body: str) -> bool:
        if not self.settings.notification_sender_email:
            logger.warning("Email skipped: NOTIFICATION_SENDER_EMAIL not configured")
            return False
        recipients = self.settings.notification_recipients
        if not recipients:
            logger.warning("Email skipped: NOTIFICATION_RECIPIENTS not configured")
            return False

        url = f"{self.settings.graph_base}/users/{self.settings.notification_sender_email}/sendMail"
        to_recipients = [{"emailAddress": {"address": addr}} for addr in recipients]
        payload = {
            "message": {
                "subject": subject,
                "body": {"contentType": "HTML", "content": body},
                "toRecipients": to_recipients,
            },
            "saveToSentItems": False,
        }
        try:
            response = self.session.post(
                url,
                headers=self.auth.get_headers(),
                json=payload,
            )
        except Exception as exc:
            logger.warning("Email notification error: %s", exc)
            return False
        if response.status_code in (200, 202):
            logger.info(
                "Email sent from %s -> %s",
                self.settings.notification_sender_email,
                ", ".join(recipients),
            )
            return True
        logger.warning(
            "Email send returned %d: %s",
            response.status_code,
            response.text[:300],
        )
        return False

    @staticmethod
    def _build_success_html(file_name: str, result: dict) -> str:
        total_rows = result.get("total_rows", 0)
        duration = result.get("duration_seconds", 0)
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return f"""
        <div style="font-family: Segoe UI, Arial, sans-serif; max-width: 600px;">
            <div style="background: #d4edda; padding: 12px 16px; border-radius: 6px; margin-bottom: 16px;">
                <strong style="color: #155724; font-size: 16px;">Hoan tat phan loai phan hoi</strong>
            </div>
            <table style="width: 100%; border-collapse: collapse;">
                <tr><td style="padding: 6px 0; color: #666;">File:</td><td style="padding: 6px 0;"><strong>{file_name}</strong></td></tr>
                <tr><td style="padding: 6px 0; color: #666;">So dong:</td><td style="padding: 6px 0;">{total_rows}</td></tr>
                <tr><td style="padding: 6px 0; color: #666;">Thoi gian xu ly:</td><td style="padding: 6px 0;">{duration:.0f}s</td></tr>
                <tr><td style="padding: 6px 0; color: #666;">Thoi diem:</td><td style="padding: 6px 0;">{timestamp}</td></tr>
            </table>
            <p style="color: #888; font-size: 12px; margin-top: 16px;">
                Ket qua da duoc upload len SharePoint Output/
            </p>
        </div>
        """

    @staticmethod
    def _build_error_html(
        file_name: str,
        error_msg: str,
        retry_count: int = 0,
        max_retries: int = 3,
    ) -> str:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        error_display = error_msg[:500] + ("..." if len(error_msg) > 500 else "")
        return f"""
        <div style="font-family: Segoe UI, Arial, sans-serif; max-width: 600px;">
            <div style="background: #f8d7da; padding: 12px 16px; border-radius: 6px; margin-bottom: 16px;">
                <strong style="color: #721c24; font-size: 16px;">Phan loai phan hoi that bai</strong>
            </div>
            <table style="width: 100%; border-collapse: collapse;">
                <tr><td style="padding: 6px 0; color: #666;">File:</td><td style="padding: 6px 0;"><strong>{file_name}</strong></td></tr>
                <tr><td style="padding: 6px 0; color: #666;">Loi:</td><td style="padding: 6px 0; color: #dc3545;">{error_display}</td></tr>
                <tr><td style="padding: 6px 0; color: #666;">So lan thu:</td><td style="padding: 6px 0;">{retry_count}/{max_retries}</td></tr>
                <tr><td style="padding: 6px 0; color: #666;">Thoi diem:</td><td style="padding: 6px 0;">{timestamp}</td></tr>
            </table>
            <p style="color: #888; font-size: 12px; margin-top: 16px;">
                File da bi danh dau failed, can kiem tra thu cong.
            </p>
        </div>
        """

    def send_success(self, file_name: str, result: dict) -> None:
        total_rows = result.get("total_rows", 0)
        duration = result.get("duration_seconds", 0)
        teams_payload = {
            "type": "message",
            "attachments": [
                {
                    "contentType": "application/vnd.microsoft.card.adaptive",
                    "content": {
                        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                        "type": "AdaptiveCard",
                        "version": "1.4",
                        "body": [
                            {
                                "type": "TextBlock",
                                "text": "Phan loai phan hoi hoan tat",
                                "weight": "Bolder",
                                "size": "Medium",
                                "color": "Good",
                            },
                            {
                                "type": "FactSet",
                                "facts": [
                                    {"title": "File", "value": file_name},
                                    {"title": "So dong", "value": str(total_rows)},
                                    {"title": "Thoi gian", "value": f"{duration:.0f}s"},
                                ],
                            },
                        ],
                    },
                }
            ],
        }
        if not self._send_teams_webhook(teams_payload):
            subject = f"[DMS] Hoan tat phan loai: {file_name}"
            body = self._build_success_html(file_name, result)
            self._send_email(subject, body)

    def send_error(
        self,
        file_name: str,
        error_msg: str,
        retry_count: int = 0,
        max_retries: int = 3,
    ) -> None:
        teams_payload = {
            "type": "message",
            "attachments": [
                {
                    "contentType": "application/vnd.microsoft.card.adaptive",
                    "content": {
                        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                        "type": "AdaptiveCard",
                        "version": "1.4",
                        "body": [
                            {
                                "type": "TextBlock",
                                "text": "Phan loai phan hoi that bai",
                                "weight": "Bolder",
                                "size": "Medium",
                                "color": "Attention",
                            },
                            {
                                "type": "FactSet",
                                "facts": [
                                    {"title": "File", "value": file_name},
                                    {"title": "Loi", "value": error_msg[:200]},
                                    {"title": "Retry", "value": f"{retry_count}/{max_retries}"},
                                ],
                            },
                        ],
                    },
                }
            ],
        }
        if not self._send_teams_webhook(teams_payload):
            subject = f"[DMS] Loi phan loai: {file_name}"
            body = self._build_error_html(
                file_name,
                error_msg,
                retry_count=retry_count,
                max_retries=max_retries,
            )
            self._send_email(subject, body)
