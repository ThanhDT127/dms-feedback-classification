"""Azure AD authentication for Microsoft Graph."""

from __future__ import annotations

import logging

import msal

from .exceptions import AuthenticationError
from .settings import Settings

logger = logging.getLogger("dms-watcher")


class AuthProvider:
    """Provide Graph API tokens and headers using client credentials flow."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._msal_app: msal.ConfidentialClientApplication | None = None

    @property
    def msal_app(self) -> msal.ConfidentialClientApplication:
        if self._msal_app is None:
            authority = f"https://login.microsoftonline.com/{self.settings.azure_tenant_id}"
            self._msal_app = msal.ConfidentialClientApplication(
                client_id=self.settings.azure_client_id,
                client_credential=self.settings.azure_client_secret,
                authority=authority,
            )
            logger.info(
                "MSAL ConfidentialClientApplication initialized (tenant=%s)",
                self.settings.azure_tenant_id,
            )
        return self._msal_app

    def get_access_token(self) -> str:
        result = self.msal_app.acquire_token_for_client(scopes=self.settings.graph_scopes)
        if "access_token" not in result:
            error_desc = result.get("error_description", result.get("error", "Unknown error"))
            raise AuthenticationError(f"Token acquisition failed: {error_desc}")
        return result["access_token"]

    def get_headers(self) -> dict[str, str]:
        token = self.get_access_token()
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
