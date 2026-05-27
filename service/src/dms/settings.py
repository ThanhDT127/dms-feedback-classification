"""Service settings and path helpers."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, ValidationError, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .exceptions import ConfigurationError

SERVICE_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """Validated runtime settings loaded from environment variables and .env."""

    model_config = SettingsConfigDict(
        env_file=str(SERVICE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    azure_tenant_id: str = Field("", alias="AZURE_TENANT_ID")
    azure_client_id: str = Field("", alias="AZURE_CLIENT_ID")
    azure_client_secret: str = Field("", alias="AZURE_CLIENT_SECRET")

    gemini_backend: str = Field("vertex", alias="GEMINI_BACKEND")
    gemini_api_key: str = Field("", alias="GEMINI_API_KEY")
    gemini_model: str = Field("gemini-2.5-flash-lite", alias="GEMINI_MODEL")

    gcp_project_id: str = Field("", alias="GCP_PROJECT_ID")
    gcp_location: str = Field("global", alias="GCP_LOCATION")
    gcp_service_account_json: str = Field("", alias="GCP_SERVICE_ACCOUNT_JSON")

    sharepoint_drive_id: str = Field("", alias="SHAREPOINT_DRIVE_ID")
    sharepoint_root_folder_id: str = Field("", alias="SHAREPOINT_ROOT_FOLDER_ID")

    poll_interval_seconds: int = Field(300, alias="POLL_INTERVAL_SECONDS")
    teams_webhook_url: str = Field("", alias="TEAMS_WEBHOOK_URL")
    notification_sender_email: str = Field("", alias="NOTIFICATION_SENDER_EMAIL")
    notification_recipients_raw: str = Field("", alias="NOTIFICATION_RECIPIENTS")
    notification_email: str = Field("", alias="NOTIFICATION_EMAIL")
    notify_on_success: bool = Field(True, alias="NOTIFY_ON_SUCCESS")
    notify_on_error: bool = Field(True, alias="NOTIFY_ON_ERROR")

    llm_batch_size: int = Field(20, alias="LLM_BATCH_SIZE")
    ckpt_every: int = Field(50, alias="CKPT_EVERY")
    base_wait: float = 4.0
    max_retry: int = 3
    rate_gap_sec: float = 4.0
    bm25_min_score: float = 5.0
    http_timeout_seconds: float = 30.0
    enable_sharepoint_config_sync: bool = Field(True, alias="ENABLE_SHAREPOINT_CONFIG_SYNC")
    enable_runtime_cleanup: bool = Field(True, alias="ENABLE_RUNTIME_CLEANUP")
    cleanup_output_ttl_days: int = Field(7, alias="CLEANUP_OUTPUT_TTL_DAYS")
    cleanup_log_ttl_days: int = Field(7, alias="CLEANUP_LOG_TTL_DAYS")
    cleanup_staging_ttl_hours: int = Field(24, alias="CLEANUP_STAGING_TTL_HOURS")

    data_dir: Path = Field(default_factory=lambda: SERVICE_DIR, alias="DATA_DIR")
    keyword_dir_override: Path | None = Field(default=None, alias="KEYWORD_DIR")
    model_dir_override: Path | None = Field(default=None, alias="MODEL_DIR")
    work_dir: Path = Field(default_factory=lambda: SERVICE_DIR / "work", alias="WORK_DIR")
    log_dir: Path = Field(default_factory=lambda: SERVICE_DIR / "logs", alias="LOG_DIR")

    graph_base: str = "https://graph.microsoft.com/v1.0"
    graph_scopes: list[str] = ["https://graph.microsoft.com/.default"]
    sp_input_folder: str = "Input"
    sp_output_folder: str = "Output"
    sp_checkpoint_folder: str = "Check_Point"
    sp_keyword_folder: str = Field("Keyword", alias="SHAREPOINT_KEYWORD_FOLDER")
    sp_model_folder: str = Field("Model", alias="SHAREPOINT_MODEL_FOLDER")

    @model_validator(mode="after")
    def validate_required_fields(self) -> Settings:
        missing = []
        for field_name in (
            "azure_tenant_id",
            "azure_client_id",
            "azure_client_secret",
            "sharepoint_drive_id",
            "sharepoint_root_folder_id",
        ):
            if not getattr(self, field_name):
                missing.append(field_name)
        if missing:
            raise ValueError(
                "Missing required settings: " + ", ".join(sorted(missing))
            )

        backend = self.gemini_backend.lower().strip()
        if backend not in {"vertex", "apikey"}:
            raise ValueError(
                f"Unsupported GEMINI_BACKEND: {self.gemini_backend!r}. Use 'vertex' or 'apikey'."
            )
        self.gemini_backend = backend

        if backend == "vertex" and not self.gcp_project_id:
            raise ValueError("GCP_PROJECT_ID is required when GEMINI_BACKEND=vertex")
        if backend == "apikey" and not self.gemini_api_key:
            raise ValueError("GEMINI_API_KEY is required when GEMINI_BACKEND=apikey")

        return self

    @property
    def keyword_dir(self) -> Path:
        return self.keyword_dir_override or (self.data_dir / "Keyword")

    @property
    def model_dir(self) -> Path:
        return self.model_dir_override or (self.data_dir / "Model")

    @property
    def kw_map_path(self) -> Path:
        return self.keyword_dir / "kw_map.json"

    @property
    def df_products_path(self) -> Path:
        return self.keyword_dir / "Phân Chia Nhóm Sản Phẩm V2.xlsx"

    @property
    def seen_files_path(self) -> Path:
        return self.work_dir / "seen_files.json"

    @property
    def health_file(self) -> Path:
        return self.work_dir / "health.json"

    @property
    def metrics_path(self) -> Path:
        return self.work_dir / "metrics.json"

    @property
    def config_assets_state_path(self) -> Path:
        return self.work_dir / "config_assets_state.json"

    @property
    def config_assets_cache_dir(self) -> Path:
        return self.work_dir / "config_assets"

    @property
    def active_keyword_dir(self) -> Path:
        return self.config_assets_cache_dir / "active" / "Keyword"

    @property
    def active_model_dir(self) -> Path:
        return self.config_assets_cache_dir / "active" / "Model"

    @property
    def tfidf_word_path(self) -> Path:
        return self.model_dir / "tfidf_word.pkl"

    @property
    def tfidf_char_path(self) -> Path:
        return self.model_dir / "tfidf_char.pkl"

    @property
    def ovr_logreg_path(self) -> Path:
        return self.model_dir / "ovr_logreg.pkl"

    @property
    def best_thresholds_path(self) -> Path:
        return self.model_dir / "best_thresholds.json"

    @property
    def label_cols_path(self) -> Path:
        return self.model_dir / "label_cols.json"

    @property
    def keyword_minors_path(self) -> Path:
        return self.model_dir / "keyword_minors.json"

    @property
    def required_model_artifact_paths(self) -> dict[str, Path]:
        return {
            "tfidf_word.pkl": self.tfidf_word_path,
            "tfidf_char.pkl": self.tfidf_char_path,
            "ovr_logreg.pkl": self.ovr_logreg_path,
            "best_thresholds.json": self.best_thresholds_path,
            "label_cols.json": self.label_cols_path,
        }

    @property
    def notification_recipients(self) -> list[str]:
        raw = self.notification_recipients_raw.strip()
        if raw:
            return [addr.strip() for addr in raw.split(",") if addr.strip()]
        if self.notification_email:
            return [self.notification_email]
        return []

    def ensure_runtime_dirs(self) -> None:
        """Create runtime directories used by the service."""
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.config_assets_cache_dir.mkdir(parents=True, exist_ok=True)
        (self.work_dir / "input").mkdir(parents=True, exist_ok=True)
        (self.work_dir / "output").mkdir(parents=True, exist_ok=True)
        (self.work_dir / "checkpoint").mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance."""
    try:
        return Settings()  # type: ignore[call-arg]
    except ValidationError as exc:
        raise ConfigurationError(str(exc)) from exc


def update_env_file(updates: dict[str, str]) -> None:
    """Update or append key-value pairs in the .env file while preserving comments and order."""
    env_path = SERVICE_DIR / ".env"
    if not env_path.exists():
        env_path.write_text("", encoding="utf-8")

    lines = env_path.read_text(encoding="utf-8").splitlines()
    updated_keys = set()
    new_lines = []

    for line in lines:
        line_stripped = line.strip()
        if not line_stripped or line_stripped.startswith("#"):
            new_lines.append(line)
            continue

        if "=" in line:
            key, sep, val = line.partition("=")
            key_stripped = key.strip()
            if key_stripped in updates:
                new_lines.append(f"{key_stripped}={updates[key_stripped]}")
                updated_keys.add(key_stripped)
            else:
                new_lines.append(line)
        else:
            new_lines.append(line)

    # Append any keys that weren't found in the existing .env file
    for key, val in updates.items():
        if key not in updated_keys:
            new_lines.append(f"{key}={val}")

    env_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
