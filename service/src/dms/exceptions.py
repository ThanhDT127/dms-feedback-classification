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


class GatewayError(GeminiError):
    """Safe public failure; never accepts provider messages or response bodies."""

    def __init__(self, category: str, *, retryable: bool = False,
                 outcome_unknown: bool = False) -> None:
        allowed = {"identity", "configuration", "auth", "request", "quota", "redirect",
                   "unreachable", "outcome_unknown", "response", "accounting", "direct"}
        self.category = category if category in allowed else "outcome_unknown"
        self.retryable = retryable
        self.outcome_unknown = outcome_unknown or self.category == "outcome_unknown"
        self.retry_after: float | None = None
        self.usage: dict | None = None
        self.response_id: str | None = None
        self.model_actual: str | None = None
        super().__init__(f"LLM gateway failure: {self.category}")


class PipelineError(DMSError):
    """Raised when pipeline processing fails."""


class PipelineCancelled(PipelineError):
    """Raised when a cooperative cancellation request stops pipeline work."""


class ModelArtifactError(DMSError):
    """Raised when local baseline-model artifacts are missing or invalid."""


class ConfigAssetSyncError(DMSError):
    """Raised when SharePoint-backed config asset synchronization fails."""
