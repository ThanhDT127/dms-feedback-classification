"""Service settings and path helpers."""

from __future__ import annotations

import os
import threading
from pathlib import Path

from pydantic import Field, ValidationError, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .exceptions import ConfigurationError

if os.environ.get("SERVICE_DIR"):
    SERVICE_DIR = Path(os.environ["SERVICE_DIR"])
elif Path("/app").is_dir() and (Path("/app/src").exists() or Path("/app/static").exists()):
    SERVICE_DIR = Path("/app")
else:
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
    gemini_model_pricing: str = Field(
        '{"gemini-2.5-flash": {"input": 0.30, "output": 2.50}, "gemini-2.0-flash": {"input": 0.10, "output": 0.40}, "gemini-2.5-flash-lite": {"input": 0.025, "output": 0.30}, "gemini-3.5-flash": {"input": 1.50, "output": 9.00}, "gemini-3.1-flash-lite": {"input": 0.025, "output": 0.10}}',
        alias="GEMINI_MODEL_PRICING",
    )

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
    rate_gap_sec: float = Field(4.0, alias="RATE_LIMIT_GAP")
    bm25_min_score: float = 5.0
    http_timeout_seconds: float = 30.0
    gemini_timeout_seconds: float = Field(120.0, alias="GEMINI_TIMEOUT_SECONDS")
    cors_allowed_origins: str = Field(
        "http://localhost:8501,http://localhost:3000",
        alias="CORS_ALLOWED_ORIGINS",
    )
    enable_sharepoint_config_sync: bool = Field(True, alias="ENABLE_SHAREPOINT_CONFIG_SYNC")
    upload_input_to_sharepoint: bool = Field(True, alias="UPLOAD_INPUT_TO_SHAREPOINT")
    enable_runtime_cleanup: bool = Field(False, alias="ENABLE_RUNTIME_CLEANUP")
    cleanup_output_ttl_days: int = Field(7, alias="CLEANUP_OUTPUT_TTL_DAYS")
    cleanup_log_ttl_days: int = Field(7, alias="CLEANUP_LOG_TTL_DAYS")
    cleanup_staging_ttl_hours: int = Field(24, alias="CLEANUP_STAGING_TTL_HOURS")
    classification_worker_concurrency: int = Field(1, alias="CLASSIFICATION_WORKER_CONCURRENCY")
    classification_per_user_running_limit: int = Field(
        1, alias="CLASSIFICATION_PER_USER_RUNNING_LIMIT"
    )
    classification_per_user_queued_limit: int = Field(
        3, alias="CLASSIFICATION_PER_USER_QUEUED_LIMIT"
    )
    classification_retry_count: int = Field(2, alias="CLASSIFICATION_RETRY_COUNT")
    classification_stale_running_timeout_seconds: int = Field(
        900, alias="CLASSIFICATION_STALE_RUNNING_TIMEOUT_SECONDS"
    )
    classification_worker_poll_interval_seconds: float = Field(
        1.0, alias="CLASSIFICATION_WORKER_POLL_INTERVAL_SECONDS"
    )
    classification_worker_heartbeat_seconds: float = Field(
        15.0, alias="CLASSIFICATION_WORKER_HEARTBEAT_SECONDS"
    )

    # Auth
    jwt_secret_key: str = Field(alias="JWT_SECRET_KEY")
    default_admin_password: str = Field(default="", alias="DEFAULT_ADMIN_PASSWORD")
    environment: str = Field(default="development", alias="ENVIRONMENT")

    data_dir: Path = Field(default_factory=lambda: SERVICE_DIR / "data", alias="DATA_DIR")
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
            raise ValueError("Missing required settings: " + ", ".join(sorted(missing)))

        backend = self.gemini_backend.lower().strip()
        if backend not in {"vertex", "apikey"}:
            raise ValueError(
                f"Unsupported GEMINI_BACKEND: {self.gemini_backend!r}. Use 'vertex' or 'apikey'."
            )
        self.gemini_backend = backend

        # Normalize model name: "Gemini 2.5 Flash Lite" → "gemini-2.5-flash-lite"
        self.gemini_model = self.gemini_model.strip().lower().replace(" ", "-")

        if backend == "vertex" and not self.gcp_project_id:
            raise ValueError("GCP_PROJECT_ID is required when GEMINI_BACKEND=vertex")
        if backend == "apikey" and not self.gemini_api_key:
            raise ValueError("GEMINI_API_KEY is required when GEMINI_BACKEND=apikey")

        self.environment = (self.environment or "development").strip().lower()

        # JWT secret is now required (no default) and must be strong
        if len(self.jwt_secret_key) < 32:
            raise ValueError(
                "JWT_SECRET_KEY must be at least 32 characters long. "
                'Generate one with: python -c "import secrets; print(secrets.token_urlsafe(48))"'
            )

        positive_int_fields = (
            "classification_worker_concurrency",
            "classification_per_user_running_limit",
            "classification_per_user_queued_limit",
            "classification_stale_running_timeout_seconds",
        )
        for field_name in positive_int_fields:
            if int(getattr(self, field_name)) < 1:
                raise ValueError(f"{field_name} must be >= 1")
        if int(self.classification_retry_count) < 0:
            raise ValueError("classification_retry_count must be >= 0")
        if float(self.classification_worker_poll_interval_seconds) <= 0:
            raise ValueError("classification_worker_poll_interval_seconds must be > 0")
        if float(self.classification_worker_heartbeat_seconds) <= 0:
            raise ValueError("classification_worker_heartbeat_seconds must be > 0")

        return self

    @property
    def keyword_dir(self) -> Path:
        return self.keyword_dir_override or self._default_asset_dir("Keyword")

    @property
    def model_dir(self) -> Path:
        return self.model_dir_override or self._default_asset_dir("Model")

    def _default_asset_dir(self, name: str) -> Path:
        candidate = self.data_dir / name
        legacy = SERVICE_DIR / name
        if self._uses_service_data() and self._is_empty_dir(candidate) and legacy.exists():
            return legacy
        return candidate

    def _uses_service_data(self) -> bool:
        return self.data_dir.resolve() == (SERVICE_DIR / "data").resolve()

    @staticmethod
    def _is_empty_dir(path: Path) -> bool:
        if not path.exists():
            return True
        if not path.is_dir():
            return False
        return not any(path.iterdir())

    @property
    def kw_map_path(self) -> Path:
        return self.keyword_dir / "kw_map.json"

    @property
    def df_products_path(self) -> Path:
        filename = "Phân Chia Nhóm Sản Phẩm V2.xlsx"
        candidate = self.keyword_dir / filename
        legacy = SERVICE_DIR / "Keyword" / filename
        if (
            self.keyword_dir_override is None
            and self._uses_service_data()
            and not candidate.exists()
            and legacy.exists()
        ):
            return legacy
        return candidate

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
    def label_history_db_path(self) -> Path:
        return self.work_dir / "label_history.db"

    @property
    def classification_jobs_db_path(self) -> Path:
        return self.work_dir / "classification_jobs.db"

    @property
    def label_config_path(self) -> Path:
        return self.work_dir / "labels.json"

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


class SettingsProvider:
    """Thread-safe authoritative settings cache."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._settings: Settings | None = None

    def get(self) -> Settings:
        with self._lock:
            if self._settings is not None:
                return self._settings
            try:
                self._settings = Settings()  # type: ignore[call-arg]
            except ValidationError as exc:
                raise ConfigurationError(str(exc)) from exc
            return self._settings

    def reload(self) -> Settings:
        with self._lock:
            self._settings = None
            return self.get()

    def invalidate(self) -> None:
        with self._lock:
            self._settings = None

    def set_for_tests(self, settings: Settings) -> None:
        with self._lock:
            self._settings = settings


_SETTINGS_PROVIDER = SettingsProvider()


def get_settings_provider() -> SettingsProvider:
    return _SETTINGS_PROVIDER


def get_settings() -> Settings:
    """Return a cached Settings instance."""
    return _SETTINGS_PROVIDER.get()


def invalidate_settings_cache() -> None:
    _SETTINGS_PROVIDER.invalidate()


get_settings.cache_clear = invalidate_settings_cache  # type: ignore[attr-defined]


def update_env_file(updates: dict[str, str]) -> None:
    """Update or append key-value pairs in the .env file while preserving comments and order."""
    from .utils import atomic_write_text

    env_path = SERVICE_DIR / ".env"
    if not env_path.exists():
        atomic_write_text(env_path, "")

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
                new_lines.append(f"{key_stripped}={_format_env_value(updates[key_stripped])}")
                updated_keys.add(key_stripped)
            else:
                new_lines.append(line)
        else:
            new_lines.append(line)

    # Append any keys that weren't found in the existing .env file
    for key, val in updates.items():
        if key not in updated_keys:
            new_lines.append(f"{key}={_format_env_value(val)}")

    atomic_write_text(env_path, "\n".join(new_lines) + "\n")


def _format_env_value(value: str) -> str:
    text = "" if value is None else str(value)
    needs_quotes = (
        text == ""
        or text != text.strip()
        or any(char in text for char in ("#", "=", '"', "'", "\n", "\r"))
    )
    if not needs_quotes:
        return text
    escaped = (
        text.replace("\\", "\\\\").replace("\r", "\\r").replace("\n", "\\n").replace('"', '\\"')
    )
    return f'"{escaped}"'
