"""Typed exception hierarchy for the DMS service."""


class DMSError(Exception):
    """Base exception for all service-specific failures."""


class ConfigurationError(DMSError):
    """Raised when required configuration is missing or invalid."""


class AuthenticationError(DMSError):
    """Raised when Azure AD authentication fails."""


class SharePointError(DMSError):
    """Raised when a SharePoint or Graph API operation fails."""


class GeminiError(DMSError):
    """Raised when Gemini or Vertex AI operations fail."""


class PipelineError(DMSError):
    """Raised when pipeline processing fails."""


class ModelArtifactError(DMSError):
    """Raised when local baseline-model artifacts are missing or invalid."""
