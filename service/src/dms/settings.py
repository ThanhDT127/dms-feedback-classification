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

    # Chatbot (Dev B — hiểu câu hỏi)
    chat_timezone: str = Field("Asia/Ho_Chi_Minh", alias="CHAT_TIMEZONE")
    chat_max_question_chars: int = Field(1000, alias="CHAT_MAX_QUESTION_CHARS")
    chat_history_turns: int = Field(5, alias="CHAT_HISTORY_TURNS")
    chat_fuzzy_match_threshold: float = Field(0.88, alias="CHAT_FUZZY_MATCH_THRESHOLD")
    # Chatbot (Dev B — Query Planner)
    chat_enabled_patterns: str = Field("sql_template", alias="CHAT_ENABLED_PATTERNS")
    chat_planner_max_repair: int = Field(1, alias="CHAT_PLANNER_MAX_REPAIR")
    chat_metadata_ttl_seconds: int = Field(300, alias="CHAT_METADATA_TTL_SECONDS")
    chat_milestone: str = Field("M1", alias="CHAT_MILESTONE")
    # Chatbot (Dev B — Plan Guard & Orchestrator)
    chat_plan_min_confidence: float = Field(0.6, alias="CHAT_PLAN_MIN_CONFIDENCE")
    chat_fuzzy_ambiguity_margin: float = Field(0.05, alias="CHAT_FUZZY_AMBIGUITY_MARGIN")
    chat_step_timeout_seconds: float = Field(20.0, alias="CHAT_STEP_TIMEOUT_SECONDS")
    chat_turn_timeout_seconds: float = Field(60.0, alias="CHAT_TURN_TIMEOUT_SECONDS")
    # Chatbot (Dev B — LLM gateway & stream)
    chat_gemini_model: str = Field("", alias="CHAT_GEMINI_MODEL")
    chat_llm_max_retry: int = Field(1, alias="CHAT_LLM_MAX_RETRY")
    chat_stream_first_chunk_timeout_seconds: float = Field(
        15.0, alias="CHAT_STREAM_FIRST_CHUNK_TIMEOUT_SECONDS"
    )
    chat_stream_idle_timeout_seconds: float = Field(10.0, alias="CHAT_STREAM_IDLE_TIMEOUT_SECONDS")
    chat_stream_total_timeout_seconds: float = Field(
        60.0, alias="CHAT_STREAM_TOTAL_TIMEOUT_SECONDS"
    )
    chat_gemini_thinking_budget: int | None = Field(None, alias="CHAT_GEMINI_THINKING_BUDGET")
    # Chatbot (Dev B — Response Shaper chế độ 3)
    chat_commentary_enabled: bool = Field(True, alias="CHAT_COMMENTARY_ENABLED")
    chat_commentary_max_sentences: int = Field(4, alias="CHAT_COMMENTARY_MAX_SENTENCES")
    chat_commentary_max_output_tokens: int = Field(400, alias="CHAT_COMMENTARY_MAX_OUTPUT_TOKENS")
    chat_table_max_rows: int = Field(20, alias="CHAT_TABLE_MAX_ROWS")
    chat_quote_max_chars: int = Field(240, alias="CHAT_QUOTE_MAX_CHARS")
    # Chat FTS (b08)
    chat_fts_max_terms: int = Field(6, alias="CHAT_FTS_MAX_TERMS")
    chat_fts_default_limit: int = Field(20, alias="CHAT_FTS_DEFAULT_LIMIT")
    chat_fts_max_relax: int = Field(2, alias="CHAT_FTS_MAX_RELAX")
    chat_similar_top_terms: int = Field(8, alias="CHAT_SIMILAR_TOP_TERMS")
    # Chat text2sql (b09)
    chat_sql_max_rows: int = Field(200, alias="CHAT_SQL_MAX_ROWS")
    chat_sql_max_repair: int = Field(2, alias="CHAT_SQL_MAX_REPAIR")
    chat_sql_examples_top_k: int = Field(4, alias="CHAT_SQL_EXAMPLES_TOP_K")
    chat_sql_max_joins: int = Field(2, alias="CHAT_SQL_MAX_JOINS")
    chat_sql_max_subquery_depth: int = Field(3, alias="CHAT_SQL_MAX_SUBQUERY_DEPTH")
    # Chat WebSocket (b06)
    chat_enabled: bool = Field(False, alias="CHAT_ENABLED")
    chat_max_concurrent_turns: int = Field(4, alias="CHAT_MAX_CONCURRENT_TURNS")
    chat_max_active_turns_per_user: int = Field(1, alias="CHAT_MAX_ACTIVE_TURNS_PER_USER")
    chat_rate_limit_per_minute: int = Field(10, alias="CHAT_RATE_LIMIT_PER_MINUTE")
    chat_answer_buffer_ttl_seconds: int = Field(600, alias="CHAT_ANSWER_BUFFER_TTL_SECONDS")
    chat_ws_idle_timeout_seconds: float = Field(300.0, alias="CHAT_WS_IDLE_TIMEOUT_SECONDS")
    chat_message_metadata_max_bytes: int = Field(262144, alias="CHAT_MESSAGE_METADATA_MAX_BYTES")
    # Buffer câu trả lời dùng chung giữa các worker; rỗng = giữ trong RAM của một tiến trình.
    chat_redis_url: str = Field("", alias="CHAT_REDIS_URL")
    # Chat báo cáo và xuất Excel (b10)
    chat_report_max_steps: int = Field(8, alias="CHAT_REPORT_MAX_STEPS")
    chat_report_turn_timeout_seconds: float = Field(
        90.0, alias="CHAT_REPORT_TURN_TIMEOUT_SECONDS"
    )
    chat_report_commentary_max_sentences: int = Field(
        8, alias="CHAT_REPORT_COMMENTARY_MAX_SENTENCES"
    )
    chat_export_ttl_hours: int = Field(24, alias="CHAT_EXPORT_TTL_HOURS")
    chat_export_max_rows: int = Field(5000, alias="CHAT_EXPORT_MAX_ROWS")
    chat_export_max_bytes: int = Field(10485760, alias="CHAT_EXPORT_MAX_BYTES")
    chat_export_max_active_per_user: int = Field(20, alias="CHAT_EXPORT_MAX_ACTIVE_PER_USER")
    # Chat trí nhớ hội thoại và hạn mức token (b11)
    chat_memory_summary_max_chars: int = Field(800, alias="CHAT_MEMORY_SUMMARY_MAX_CHARS")
    chat_memory_compact_batch: int = Field(3, alias="CHAT_MEMORY_COMPACT_BATCH")
    chat_history_char_budget: int = Field(3000, alias="CHAT_HISTORY_CHAR_BUDGET")
    chat_user_daily_token_budget: int = Field(200000, alias="CHAT_USER_DAILY_TOKEN_BUDGET")
    chat_budget_exempt_admin: bool = Field(True, alias="CHAT_BUDGET_EXEMPT_ADMIN")
    chat_budget_warning_ratio: float = Field(0.8, alias="CHAT_BUDGET_WARNING_RATIO")
    chat_session_ttl_days: int = Field(7, alias="CHAT_SESSION_TTL_DAYS")
    chat_session_cleanup_interval_seconds: int = Field(
        3600, alias="CHAT_SESSION_CLEANUP_INTERVAL_SECONDS"
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
        if int(self.chat_max_question_chars) < 1:
            raise ValueError("chat_max_question_chars must be >= 1")
        if int(self.chat_history_turns) < 0:
            raise ValueError("chat_history_turns must be >= 0")
        if not 0 < float(self.chat_fuzzy_match_threshold) <= 1:
            raise ValueError("chat_fuzzy_match_threshold must be in (0, 1]")
        patterns = [p.strip() for p in self.chat_enabled_patterns.split(",") if p.strip()]
        allowed_patterns = {"sql_template", "semantic_view", "fts5_search", "json_extract"}
        if not patterns or not set(patterns) <= allowed_patterns:
            raise ValueError(
                "CHAT_ENABLED_PATTERNS must be a comma-separated subset of "
                + ", ".join(sorted(allowed_patterns))
            )
        self.chat_enabled_patterns = ",".join(dict.fromkeys(patterns))
        self.chat_milestone = self.chat_milestone.strip().upper()
        if self.chat_milestone not in {"M1", "M2", "M3", "M4", "M5"}:
            raise ValueError("CHAT_MILESTONE must be one of M1..M5")
        if int(self.chat_planner_max_repair) < 0:
            raise ValueError("chat_planner_max_repair must be >= 0")
        if int(self.chat_metadata_ttl_seconds) < 0:
            raise ValueError("chat_metadata_ttl_seconds must be >= 0")
        if not 0 <= float(self.chat_plan_min_confidence) <= 1:
            raise ValueError("chat_plan_min_confidence must be in [0, 1]")
        if not 0 <= float(self.chat_fuzzy_ambiguity_margin) < 1:
            raise ValueError("chat_fuzzy_ambiguity_margin must be in [0, 1)")
        if float(self.chat_step_timeout_seconds) <= 0:
            raise ValueError("chat_step_timeout_seconds must be > 0")
        if float(self.chat_turn_timeout_seconds) < float(self.chat_step_timeout_seconds):
            raise ValueError("chat_turn_timeout_seconds must be >= chat_step_timeout_seconds")
        # Model riêng cho chat: chuẩn hoá giống gemini_model, để rỗng thì dùng chung GEMINI_MODEL.
        self.chat_gemini_model = self.chat_gemini_model.strip().lower().replace(" ", "-")
        if int(self.chat_llm_max_retry) < 0:
            raise ValueError("chat_llm_max_retry must be >= 0")
        for stream_field in (
            "chat_stream_first_chunk_timeout_seconds",
            "chat_stream_idle_timeout_seconds",
            "chat_stream_total_timeout_seconds",
        ):
            if float(getattr(self, stream_field)) <= 0:
                raise ValueError(f"{stream_field} must be > 0")
        if float(self.chat_stream_total_timeout_seconds) < float(
            self.chat_stream_first_chunk_timeout_seconds
        ):
            raise ValueError(
                "chat_stream_total_timeout_seconds must be >= "
                "chat_stream_first_chunk_timeout_seconds"
            )
        if (
            self.chat_gemini_thinking_budget is not None
            and int(self.chat_gemini_thinking_budget) < 0
        ):
            raise ValueError("chat_gemini_thinking_budget must be >= 0")
        if int(self.chat_commentary_max_sentences) < 1:
            raise ValueError("chat_commentary_max_sentences must be >= 1")
        if int(self.chat_commentary_max_output_tokens) < 1:
            raise ValueError("chat_commentary_max_output_tokens must be >= 1")
        if int(self.chat_table_max_rows) < 1:
            raise ValueError("chat_table_max_rows must be >= 1")
        if int(self.chat_quote_max_chars) < 20:
            raise ValueError("chat_quote_max_chars must be >= 20")
        for name in (
            "chat_max_concurrent_turns",
            "chat_max_active_turns_per_user",
            "chat_rate_limit_per_minute",
            "chat_answer_buffer_ttl_seconds",
        ):
            if int(getattr(self, name)) < 1:
                raise ValueError(f"{name} must be >= 1")
        if not 1 <= int(self.chat_fts_max_terms) <= 20:
            raise ValueError("chat_fts_max_terms must be in [1, 20]")
        if not 1 <= int(self.chat_fts_default_limit) <= 50:
            raise ValueError("chat_fts_default_limit must be in [1, 50]")
        if not 0 <= int(self.chat_fts_max_relax) <= 5:
            raise ValueError("chat_fts_max_relax must be in [0, 5]")
        if int(self.chat_similar_top_terms) < 1:
            raise ValueError("chat_similar_top_terms must be >= 1")
        if not 1 <= int(self.chat_sql_max_rows) <= 1000:
            raise ValueError("chat_sql_max_rows must be in [1, 1000]")
        if not 0 <= int(self.chat_sql_max_repair) <= 5:
            raise ValueError("chat_sql_max_repair must be in [0, 5]")
        if not 0 <= int(self.chat_sql_examples_top_k) <= 10:
            raise ValueError("chat_sql_examples_top_k must be in [0, 10]")
        if not 0 <= int(self.chat_sql_max_joins) <= 5:
            raise ValueError("chat_sql_max_joins must be in [0, 5]")
        if not 1 <= int(self.chat_sql_max_subquery_depth) <= 5:
            raise ValueError("chat_sql_max_subquery_depth must be in [1, 5]")
        if float(self.chat_ws_idle_timeout_seconds) <= 0:
            raise ValueError("chat_ws_idle_timeout_seconds must be > 0")
        if int(self.chat_message_metadata_max_bytes) < 4096:
            raise ValueError("chat_message_metadata_max_bytes must be >= 4096")
        self.chat_redis_url = self.chat_redis_url.strip()
        if self.chat_redis_url and "://" not in self.chat_redis_url:
            raise ValueError("chat_redis_url must be a URL, e.g. redis://redis:6379/0")
        if not 1 <= int(self.chat_report_max_steps) <= 20:
            raise ValueError("chat_report_max_steps must be in [1, 20]")
        if float(self.chat_report_turn_timeout_seconds) < float(self.chat_step_timeout_seconds):
            raise ValueError(
                "chat_report_turn_timeout_seconds must be >= chat_step_timeout_seconds"
            )
        if int(self.chat_report_commentary_max_sentences) < 1:
            raise ValueError("chat_report_commentary_max_sentences must be >= 1")
        if int(self.chat_export_ttl_hours) < 1:
            raise ValueError("chat_export_ttl_hours must be >= 1")
        if not 1 <= int(self.chat_export_max_rows) <= 100000:
            raise ValueError("chat_export_max_rows must be in [1, 100000]")
        if int(self.chat_export_max_bytes) < 65536:
            raise ValueError("chat_export_max_bytes must be >= 65536")
        if int(self.chat_export_max_active_per_user) < 1:
            raise ValueError("chat_export_max_active_per_user must be >= 1")
        if int(self.chat_memory_summary_max_chars) < 100:
            raise ValueError("chat_memory_summary_max_chars must be >= 100")
        if int(self.chat_memory_compact_batch) < 0:
            raise ValueError("chat_memory_compact_batch must be >= 0")
        if int(self.chat_history_char_budget) < 500:
            raise ValueError("chat_history_char_budget must be >= 500")
        if int(self.chat_user_daily_token_budget) < 0:
            raise ValueError("chat_user_daily_token_budget must be >= 0")
        if not 0 < float(self.chat_budget_warning_ratio) <= 1:
            raise ValueError("chat_budget_warning_ratio must be in (0, 1]")
        if int(self.chat_session_ttl_days) < 1:
            raise ValueError("chat_session_ttl_days must be >= 1")
        if int(self.chat_session_cleanup_interval_seconds) < 60:
            raise ValueError("chat_session_cleanup_interval_seconds must be >= 60")

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
