"""Dựng các thành phần chat cho ứng dụng web (design b06 D3, D8; ``web/deps.py`` gọi vào đây).

Mọi phụ thuộc đều truyền vào được để test thay bằng bản giả.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ...settings import Settings
from ..ai.answer_events import DoneStatus
from ..ai.budget.budget_policy import BudgetConfig, BudgetPolicy
from ..ai.budget.usage_ledger import ChatUsageLedger, InMemoryChatUsageLedger
from ..ai.export.exporter import ChatExporter
from ..ai.fts_query_builder import CachedTermStats, FtsQueryBuilder, TermStats
from ..ai.memory.session_summarizer import SessionSummarizer, SummarizerConfig
from ..ai.memory.store import InMemorySessionMemoryStore, SessionMemoryStore
from ..ai.orchestrator import ChatOrchestrator, OrchestratorConfig
from ..ai.planner_config import PlannerConfig
from ..ai.query_normalizer import CachedMetadataProvider
from ..ai.query_planner import QueryPlanner
from ..ai.response_shaper import AnswerComposer, ComposerConfig
from ..ai.schema_retriever import SchemaRetriever
from ..ai.sql_generator import SqlGeneratorConfig
from ..ai.types import LLMClient, MetadataProvider, QueryExecutor
from ..ai.understanding import UnderstandingConfig
from ..db.chat_store import ChatStore
from ..guardrails.plan_guard import PlanGuard, PlanGuardConfig
from ..guardrails.sql_guard import SqlGuard, SqlGuardConfig
from .answer_buffer import AnswerBuffer, InMemoryAnswerBuffer
from .audit import log_turn_audit
from .history_adapter import HistoryAdapter
from .rate_limiter import AskRateLimiter
from .redis_answer_buffer import RedisAnswerBuffer, build_redis_client
from .session_service import ChatSessionService, SessionServiceConfig
from .turn_runner import ChatTurnRunner, RunnerConfig, TurnRecord

logger = logging.getLogger("dms-chat")

WORKER_COUNT_ENV_VARS = ("GUNICORN_WORKERS", "WEB_CONCURRENCY", "UVICORN_WORKERS")


class ThreadLocalConnections:
    """Một kết nối SQLite cho mỗi thread; ``ChatStore`` dùng ``with conn`` nên không tự đóng."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._local = threading.local()
        self._all: list[sqlite3.Connection] = []
        self._lock = threading.Lock()

    def __call__(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(self.path), timeout=10, check_same_thread=False)
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA busy_timeout = 10000")
            self._local.conn = conn
            with self._lock:
                self._all.append(conn)
        return conn

    def close_all(self) -> None:
        with self._lock:
            for conn in self._all:
                try:
                    conn.close()
                except sqlite3.Error:
                    pass
            self._all.clear()
        self._local = threading.local()


@dataclass
class ChatServices:
    settings: Settings
    buffer: AnswerBuffer
    runner: ChatTurnRunner
    sessions: ChatSessionService
    history: HistoryAdapter
    rate_limiter: AskRateLimiter
    connections: ThreadLocalConnections | None = None
    # Trí nhớ phiên và hạn mức token (b11)
    ledger: ChatUsageLedger | None = None
    budget: BudgetPolicy | None = None
    memory: SessionMemoryStore | None = None
    summarizer: SessionSummarizer | None = None

    def shutdown(self) -> None:
        self.runner.shutdown()
        if self.connections is not None:
            self.connections.close_all()


def configured_worker_count() -> int:
    for name in WORKER_COUNT_ENV_VARS:
        raw = os.environ.get(name, "").strip()
        if raw.isdigit() and int(raw) > 0:
            return int(raw)
    return 1


def warn_if_buffer_not_shared(buffer: AnswerBuffer, workers: int | None = None) -> bool:
    count = configured_worker_count() if workers is None else workers
    if count > 1 and isinstance(buffer, InMemoryAnswerBuffer):
        logger.warning(
            "answer_buffer_not_shared",
            extra={"workers": count, "buffer": type(buffer).__name__},
        )
        return True
    return False


def build_answer_buffer(settings: Settings) -> AnswerBuffer:
    """Có ``CHAT_REDIS_URL`` thì dùng buffer chung cho mọi worker; không thì giữ trong RAM.

    Redis hỏng lúc khởi động không được làm sập web: ghi log và lùi về bản in-memory, lúc đó
    ``warn_if_buffer_not_shared`` sẽ cảnh báo tiếp nếu đang chạy nhiều worker.
    """
    stale_after = 2 * float(settings.chat_turn_timeout_seconds)
    url = str(settings.chat_redis_url or "").strip()
    if not url:
        return InMemoryAnswerBuffer(stale_after_seconds=stale_after)
    try:
        client = build_redis_client(url)
        client.ping()
    except Exception as exc:
        logger.error(
            "chat_answer_buffer_redis_unavailable",
            extra={"error_type": type(exc).__name__, "error": str(exc)[:200]},
        )
        return InMemoryAnswerBuffer(stale_after_seconds=stale_after)
    logger.info("chat_answer_buffer_redis", extra={"ttl_stale_after": stale_after})
    return RedisAnswerBuffer(client, stale_after_seconds=stale_after)


def build_orchestrator(
    settings: Settings,
    *,
    llm: LLMClient,
    executor: QueryExecutor,
    metadata: MetadataProvider,
    term_stats: TermStats | None = None,
) -> ChatOrchestrator:
    cached = CachedMetadataProvider(metadata, ttl_seconds=float(settings.chat_metadata_ttl_seconds))
    planner_config = PlannerConfig.from_settings(settings)
    guard_config = PlanGuardConfig.from_settings(settings)
    return ChatOrchestrator(
        llm=llm,
        planner=QueryPlanner(
            llm, SchemaRetriever(cached, config=planner_config), config=planner_config
        ),
        plan_guard=PlanGuard(
            cached,
            config=guard_config,
            fts_builder=FtsQueryBuilder(
                stats=term_stats,
                max_terms=guard_config.fts_max_terms,
                default_limit=guard_config.fts_default_limit,
            ),
        ),
        executor=executor,
        metadata=cached,
        config=OrchestratorConfig.from_settings(settings),
        understanding=UnderstandingConfig.from_settings(settings),
        sql_generator_config=SqlGeneratorConfig.from_settings(settings),
        sql_guard=SqlGuard(SqlGuardConfig.from_settings(settings)),
    )


def build_chat_services(
    settings: Settings,
    *,
    llm: LLMClient | None = None,
    executor: QueryExecutor | None = None,
    metadata: MetadataProvider | None = None,
    store: ChatStore | None = None,
    buffer: AnswerBuffer | None = None,
    usage_tracker: Any | None = None,
    term_stats: TermStats | None = None,
    ledger: ChatUsageLedger | None = None,
    memory_store: SessionMemoryStore | None = None,
) -> ChatServices:
    connections: ThreadLocalConnections | None = None
    # Dev A chưa có store thật (b11 task 1.3): dùng bản in-memory, mất khi khởi động lại.
    ledger = ledger if ledger is not None else InMemoryChatUsageLedger()
    memory_store = memory_store if memory_store is not None else InMemorySessionMemoryStore()
    if store is None:
        connections = ThreadLocalConnections(settings.classification_jobs_db_path)
        _apply_migrations(connections)
        store = ChatStore(connections)
    if llm is None:
        from ..ai.llm_gateway import GeminiChatGateway

        llm = GeminiChatGateway(settings, usage_tracker=usage_tracker, ledger=ledger)
    elif getattr(llm, "ledger", False) is None:
        llm.ledger = ledger  # type: ignore[attr-defined]
    if executor is None or metadata is None:
        from ...analytics import FeedbackAnalyticsRepository, FeedbackAnalyticsService

        repository = FeedbackAnalyticsRepository(settings.classification_jobs_db_path)
        if executor is None:
            from ..db.query_executor import SecureQueryExecutor

            executor = SecureQueryExecutor(repository)
        analytics = FeedbackAnalyticsService(repository)
        if metadata is None:
            from ..ai.metadata_adapter import AnalyticsMetadataAdapter

            metadata = AnalyticsMetadataAdapter(analytics)
        if term_stats is None:
            from ..ai.metadata_adapter import load_document_frequencies

            term_stats = CachedTermStats(
                lambda: load_document_frequencies(analytics),
                ttl_seconds=float(settings.chat_metadata_ttl_seconds),
            )

    buffer = buffer or build_answer_buffer(settings)
    warn_if_buffer_not_shared(buffer)
    sessions = ChatSessionService(store, config=SessionServiceConfig.from_settings(settings))

    budget_config = BudgetConfig.from_settings(settings)
    budget = BudgetPolicy(ledger, config=budget_config) if budget_config.enabled else None
    warn_if_ledger_not_persistent(ledger, budget_config)
    history = HistoryAdapter(store, memory=memory_store)
    summarizer = SessionSummarizer(
        memory_store, llm=llm, config=SummarizerConfig.from_settings(settings)
    )
    window_turns = int(settings.chat_history_turns)

    def summarize_session(record: TurnRecord) -> None:
        """Tác vụ nền sau ``done`` (b11 D2): gộp các lượt đã ra khỏi cửa sổ."""
        if not summarizer.config.enabled or record.done_status is DoneStatus.CANCELLED:
            return
        pending = history.pending_turns(record.session_id, window_turns=window_turns)
        if not summarizer.should_run(pending):
            return
        # Tóm tắt nền tính vào hạn mức nhưng không bị chặn: hết hạn mức thì dùng bản trích xuất.
        over_budget = budget is not None and budget.status(
            record.username, is_admin=record.turn.scope.is_admin
        ).exceeded
        summarizer.summarize(
            record.session_id,
            pending,
            request_id=record.turn.request_id or record.answer_id,
            use_llm=not over_budget,
        )

    def budget_warning(outcome) -> dict[str, Any] | None:
        if budget is None:
            return None
        return budget.warning(outcome.username, is_admin=outcome.is_admin)

    def persist_user_message(record: TurnRecord) -> None:
        sessions.persist_user_message(
            record.session_id,
            record.turn.question,
            client_msg_id=record.client_msg_id,
            answer_id=record.answer_id,
        )

    runner = ChatTurnRunner(
        buffer=buffer,
        orchestrator=build_orchestrator(
            settings, llm=llm, executor=executor, metadata=metadata, term_stats=term_stats
        ),
        composer=AnswerComposer(
            llm=llm,
            config=ComposerConfig.from_settings(settings),
            exporter=ChatExporter.from_settings(settings),
            budget_warning=budget_warning,
        ),
        config=RunnerConfig.from_settings(settings),
        starters=[persist_user_message],
        finalizers=[sessions.persist_turn, log_turn_audit],
        background=[summarize_session],
    )
    return ChatServices(
        settings=settings,
        buffer=buffer,
        runner=runner,
        sessions=sessions,
        history=history,
        rate_limiter=AskRateLimiter(int(settings.chat_rate_limit_per_minute)),
        connections=connections,
        ledger=ledger,
        budget=budget,
        memory=memory_store,
        summarizer=summarizer,
    )


def warn_if_ledger_not_persistent(ledger: ChatUsageLedger, config: BudgetConfig) -> bool:
    """Hạn mức đang bật mà sổ chỉ nằm trong RAM thì khởi động lại là quên hết (b11 D8)."""
    if config.enabled and isinstance(ledger, InMemoryChatUsageLedger):
        logger.warning(
            "chat_usage_ledger_not_persistent",
            extra={"daily_token_budget": config.daily_tokens, "ledger": type(ledger).__name__},
        )
        return True
    return False


def _apply_migrations(connections: ThreadLocalConnections) -> None:
    from ..db.migrations import apply_chat_migrations

    try:
        apply_chat_migrations(connections())
    except sqlite3.Error as exc:
        # View/FTS phụ thuộc bảng phản hồi; DB mới chưa có dữ liệu vẫn phải chạy được chat_*.
        logger.warning("chat_migrations_incomplete", extra={"error": str(exc)[:300]})
