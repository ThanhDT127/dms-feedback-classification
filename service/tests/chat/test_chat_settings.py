from __future__ import annotations

import pytest
from pydantic import ValidationError

from dms.chat.ai.planner_config import PlannerConfig
from dms.chat.ai.understanding import UnderstandingConfig
from dms.settings import Settings

_REQUIRED = {
    "azure_tenant_id": "tenant",
    "azure_client_id": "client",
    "azure_client_secret": "secret",
    "sharepoint_drive_id": "drive",
    "sharepoint_root_folder_id": "root",
    "gemini_backend": "vertex",
    "gcp_project_id": "project",
    "jwt_secret_key": "test-secret-key-that-is-at-least-32-bytes-long",
}


def test_chat_understanding_settings_defaults(settings):
    assert settings.chat_timezone == "Asia/Ho_Chi_Minh"
    assert settings.chat_max_question_chars == 1000
    assert settings.chat_history_turns == 5
    assert settings.chat_fuzzy_match_threshold == pytest.approx(0.88)


def test_understanding_config_reads_settings(settings):
    config = UnderstandingConfig.from_settings(settings)
    assert config.timezone == settings.chat_timezone
    assert config.max_question_chars == settings.chat_max_question_chars
    assert config.history_turns == settings.chat_history_turns
    assert config.fuzzy_match_threshold == settings.chat_fuzzy_match_threshold


def test_chat_planner_settings_defaults(settings):
    config = PlannerConfig.from_settings(settings)
    assert settings.chat_enabled_patterns == "sql_template"
    assert config.enabled_patterns == frozenset({"sql_template"})
    assert config.milestone == "M1"
    assert config.max_repair == 1
    assert config.metadata_ttl_seconds == 300


def test_chat_patterns_and_milestone_are_normalized():
    settings = Settings(
        **{
            **_REQUIRED,
            "chat_enabled_patterns": " sql_template, fts5_search,sql_template",
            "chat_milestone": "m3",
        }
    )
    assert settings.chat_enabled_patterns == "sql_template,fts5_search"
    assert settings.chat_milestone == "M3"


@pytest.mark.parametrize(
    "override",
    [
        {"chat_enabled_patterns": "sql_template,raw_sql"},
        {"chat_enabled_patterns": " , "},
        {"chat_milestone": "M9"},
        {"chat_planner_max_repair": -1},
        {"chat_metadata_ttl_seconds": -5},
        {"chat_max_question_chars": 0},
        {"chat_history_turns": -1},
        {"chat_fuzzy_match_threshold": 0},
        {"chat_fuzzy_match_threshold": 1.5},
    ],
)
def test_invalid_chat_settings_rejected(override):
    with pytest.raises(ValidationError):
        Settings(**{**_REQUIRED, **override})


def test_chat_plan_guard_settings_defaults(settings):
    assert settings.chat_plan_min_confidence == pytest.approx(0.6)
    assert settings.chat_fuzzy_ambiguity_margin == pytest.approx(0.05)
    assert settings.chat_step_timeout_seconds == pytest.approx(20.0)
    assert settings.chat_turn_timeout_seconds == pytest.approx(60.0)


@pytest.mark.parametrize(
    "override",
    [
        {"chat_plan_min_confidence": -0.1},
        {"chat_plan_min_confidence": 1.5},
        {"chat_fuzzy_ambiguity_margin": -0.1},
        {"chat_fuzzy_ambiguity_margin": 1.0},
        {"chat_step_timeout_seconds": 0},
        {"chat_step_timeout_seconds": 30, "chat_turn_timeout_seconds": 10},
    ],
)
def test_invalid_plan_guard_settings_rejected(override):
    with pytest.raises(ValidationError):
        Settings(**{**_REQUIRED, **override})


def test_chat_llm_gateway_settings_defaults(settings):
    assert settings.chat_gemini_model == ""
    assert settings.chat_llm_max_retry == 1
    assert settings.chat_stream_first_chunk_timeout_seconds == pytest.approx(15.0)
    assert settings.chat_stream_idle_timeout_seconds == pytest.approx(10.0)
    assert settings.chat_stream_total_timeout_seconds == pytest.approx(60.0)
    assert settings.chat_gemini_thinking_budget is None


def test_chat_gemini_model_is_normalized_like_pipeline_model():
    settings = Settings(**{**_REQUIRED, "chat_gemini_model": " Gemini 2.5 Flash "})
    assert settings.chat_gemini_model == "gemini-2.5-flash"


@pytest.mark.parametrize(
    "override",
    [
        {"chat_llm_max_retry": -1},
        {"chat_stream_first_chunk_timeout_seconds": 0},
        {"chat_stream_idle_timeout_seconds": -1},
        {"chat_stream_total_timeout_seconds": 0},
        {
            "chat_stream_first_chunk_timeout_seconds": 30,
            "chat_stream_total_timeout_seconds": 10,
        },
        {"chat_gemini_thinking_budget": -5},
    ],
)
def test_invalid_chat_stream_settings_rejected(override):
    with pytest.raises(ValidationError):
        Settings(**{**_REQUIRED, **override})


def test_chat_response_shaper_settings_defaults(settings):
    assert settings.chat_commentary_enabled is True
    assert settings.chat_commentary_max_sentences == 4
    assert settings.chat_commentary_max_output_tokens == 400
    assert settings.chat_table_max_rows == 20
    assert settings.chat_quote_max_chars == 240


@pytest.mark.parametrize(
    "override",
    [
        {"chat_commentary_max_sentences": 0},
        {"chat_commentary_max_output_tokens": 0},
        {"chat_table_max_rows": 0},
        {"chat_quote_max_chars": 10},
    ],
)
def test_invalid_response_shaper_settings_rejected(override):
    with pytest.raises(ValidationError):
        Settings(**{**_REQUIRED, **override})


def test_chat_websocket_settings_defaults():
    # Không đọc service/.env: máy dev có thể đang bật CHAT_ENABLED.
    settings = Settings(_env_file=None, **_REQUIRED)
    assert settings.chat_enabled is False
    assert settings.chat_max_concurrent_turns == 4
    assert settings.chat_max_active_turns_per_user == 1
    assert settings.chat_rate_limit_per_minute == 10
    assert settings.chat_answer_buffer_ttl_seconds == 600
    assert settings.chat_ws_idle_timeout_seconds == 300
    assert settings.chat_message_metadata_max_bytes == 262144


@pytest.mark.parametrize(
    "override",
    [
        {"chat_max_concurrent_turns": 0},
        {"chat_max_active_turns_per_user": 0},
        {"chat_rate_limit_per_minute": 0},
        {"chat_answer_buffer_ttl_seconds": 0},
        {"chat_ws_idle_timeout_seconds": 0},
        {"chat_message_metadata_max_bytes": 100},
    ],
)
def test_invalid_chat_websocket_settings_rejected(override):
    with pytest.raises(ValidationError):
        Settings(**{**_REQUIRED, **override})


def test_chat_fts_settings_defaults(settings):
    assert settings.chat_fts_max_terms == 6
    assert settings.chat_fts_default_limit == 20
    assert settings.chat_fts_max_relax == 2
    assert settings.chat_similar_top_terms == 8


@pytest.mark.parametrize(
    "override",
    [
        {"chat_fts_max_terms": 0},
        {"chat_fts_default_limit": 51},
        {"chat_fts_max_relax": -1},
        {"chat_similar_top_terms": 0},
    ],
)
def test_invalid_chat_fts_settings_rejected(override):
    with pytest.raises(ValidationError):
        Settings(**{**_REQUIRED, **override})


def test_chat_sql_settings_defaults(settings):
    assert settings.chat_sql_max_rows == 200
    assert settings.chat_sql_max_repair == 2
    assert settings.chat_sql_examples_top_k == 4
    assert settings.chat_sql_max_joins == 2
    assert settings.chat_sql_max_subquery_depth == 3


@pytest.mark.parametrize(
    "override",
    [
        {"chat_sql_max_rows": 0},
        {"chat_sql_max_repair": 6},
        {"chat_sql_examples_top_k": -1},
        {"chat_sql_max_joins": 9},
        {"chat_sql_max_subquery_depth": 0},
    ],
)
def test_invalid_chat_sql_settings_rejected(override):
    with pytest.raises(ValidationError):
        Settings(**{**_REQUIRED, **override})


def test_chat_report_settings_defaults(settings):
    assert settings.chat_report_max_steps == 8
    assert settings.chat_report_turn_timeout_seconds == pytest.approx(90.0)
    assert settings.chat_report_commentary_max_sentences == 8
    assert settings.chat_export_ttl_hours == 24
    assert settings.chat_export_max_rows == 5000
    assert settings.chat_export_max_bytes == 10485760
    assert settings.chat_export_max_active_per_user == 20


@pytest.mark.parametrize(
    "override",
    [
        {"chat_report_max_steps": 0},
        {"chat_report_max_steps": 21},
        {"chat_report_turn_timeout_seconds": 1.0},
        {"chat_report_commentary_max_sentences": 0},
        {"chat_export_ttl_hours": 0},
        {"chat_export_max_rows": 0},
        {"chat_export_max_bytes": 1024},
        {"chat_export_max_active_per_user": 0},
    ],
)
def test_invalid_chat_report_settings_rejected(override):
    with pytest.raises(ValidationError):
        Settings(**{**_REQUIRED, **override})


def test_chat_memory_and_budget_settings_defaults(settings):
    assert settings.chat_memory_summary_max_chars == 800
    assert settings.chat_memory_compact_batch == 3
    assert settings.chat_history_char_budget == 3000
    assert settings.chat_user_daily_token_budget == 200000
    assert settings.chat_budget_exempt_admin is True
    assert settings.chat_budget_warning_ratio == pytest.approx(0.8)
    assert settings.chat_session_ttl_days == 7
    assert settings.chat_session_cleanup_interval_seconds == 3600


@pytest.mark.parametrize(
    "override",
    [
        {"chat_memory_summary_max_chars": 50},
        {"chat_memory_compact_batch": -1},
        {"chat_history_char_budget": 100},
        {"chat_user_daily_token_budget": -1},
        {"chat_budget_warning_ratio": 0},
        {"chat_budget_warning_ratio": 1.5},
        {"chat_session_ttl_days": 0},
        {"chat_session_cleanup_interval_seconds": 30},
    ],
)
def test_invalid_chat_memory_settings_rejected(override):
    with pytest.raises(ValidationError):
        Settings(**{**_REQUIRED, **override})


def test_chat_redis_url_defaults_to_empty(settings):
    assert settings.chat_redis_url == ""


def test_chat_redis_url_is_trimmed():
    settings = Settings(**{**_REQUIRED, "chat_redis_url": "  redis://redis:6379/0  "})
    assert settings.chat_redis_url == "redis://redis:6379/0"


@pytest.mark.parametrize("override", [{"chat_redis_url": "localhost:6379"}, {"chat_redis_url": "redis"}])
def test_invalid_chat_redis_url_rejected(override):
    with pytest.raises(ValidationError):
        Settings(**{**_REQUIRED, **override})
