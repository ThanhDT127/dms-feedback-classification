"""
Authentication module — MSAL Client Credentials Flow for headless Graph API access.

Uses Azure AD App Registration with Application permissions (not delegated).
Required permissions: Sites.ReadWrite.All, Files.ReadWrite.All
"""
import msal
from config import (
    AZURE_TENANT_ID,
    AZURE_CLIENT_ID,
    AZURE_CLIENT_SECRET,
    GRAPH_SCOPES,
    logger,
)


class AuthError(Exception):
    """Raised when authentication fails or is misconfigured."""
    pass


def _validate_config():
    """Raise AuthError if any required Azure AD env vars are missing."""
    missing = []
    if not AZURE_TENANT_ID:
        missing.append("AZURE_TENANT_ID")
    if not AZURE_CLIENT_ID:
        missing.append("AZURE_CLIENT_ID")
    if not AZURE_CLIENT_SECRET:
        missing.append("AZURE_CLIENT_SECRET")
    if missing:
        raise AuthError(
            f"Missing required environment variables: {', '.join(missing)}. "
            "Please configure your .env file. See .env.example for reference."
        )


# ── Lazy singleton MSAL app ─────────────────────────────────────────────────
_msal_app: msal.ConfidentialClientApplication | None = None


def _get_msal_app() -> msal.ConfidentialClientApplication:
    """Create or return cached MSAL ConfidentialClientApplication."""
    global _msal_app
    if _msal_app is None:
        _validate_config()
        authority = f"https://login.microsoftonline.com/{AZURE_TENANT_ID}"
        _msal_app = msal.ConfidentialClientApplication(
            client_id=AZURE_CLIENT_ID,
            client_credential=AZURE_CLIENT_SECRET,
            authority=authority,
        )
        logger.info("MSAL ConfidentialClientApplication initialized (tenant=%s)", AZURE_TENANT_ID)
    return _msal_app


def get_access_token() -> str:
    """
    Acquire an access token using client_credentials flow.
    MSAL handles token caching and auto-renewal internally.

    Returns:
        Access token string.

    Raises:
        AuthError: If token acquisition fails.
    """
    app = _get_msal_app()
    result = app.acquire_token_for_client(scopes=GRAPH_SCOPES)

    if "access_token" not in result:
        error_desc = result.get("error_description", result.get("error", "Unknown error"))
        raise AuthError(f"Token acquisition failed: {error_desc}")

    logger.debug("Access token acquired successfully")
    return result["access_token"]


def get_headers() -> dict:
    """
    Return HTTP headers with Bearer token for Microsoft Graph API calls.

    Returns:
        Dictionary with Authorization and Content-Type headers.
    """
    token = get_access_token()
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
