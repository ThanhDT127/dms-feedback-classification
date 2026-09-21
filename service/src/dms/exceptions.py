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


class GeminiStreamError(GeminiError):
    """Raised when a streaming Gemini call fails.

    ``partial`` is True when text has already been streamed to the caller, so the caller must
    not retry: part of the answer may already be on screen (design b04 D5).
    """

    def __init__(
        self, message: str = "", *, partial: bool = False, code: str | None = None
    ) -> None:
        super().__init__(message)
        self.partial = partial
        self.code = code


class GeminiStreamTimeout(GeminiStreamError):
    """Raised when a stream exceeds one of its time limits.

    ``stage`` is one of ``first_chunk``, ``idle`` or ``total``.
    """

    def __init__(
        self, message: str = "", *, stage: str = "first_chunk", partial: bool = False
    ) -> None:
        super().__init__(message, partial=partial, code="TIMEOUT")
        self.stage = stage


class PipelineError(DMSError):
    """Raised when pipeline processing fails."""


class PipelineCancelled(PipelineError):
    """Raised when a cooperative cancellation request stops pipeline work."""


class ModelArtifactError(DMSError):
    """Raised when local baseline-model artifacts are missing or invalid."""


class ConfigAssetSyncError(DMSError):
    """Raised when SharePoint-backed config asset synchronization fails."""
