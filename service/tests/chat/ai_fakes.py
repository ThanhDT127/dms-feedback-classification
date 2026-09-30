"""Fake dùng chung cho test của Dev B (không gọi mạng, không cần DB)."""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path

from dms.chat.ai.types import HistoryTurn, LLMResult
from dms.chat.contract import QueryPlan, QueryResult, UserScope

DEFAULT_USAGE = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}

SAMPLE_METADATA: dict[str, list[str]] = {
    "units": [
        "Truyền thống Vùng 1",
        "Truyền thống Vùng 2",
        "Truyền thống Vùng 3",
        "Nha Trang",
        "Biên Hòa",
        "Hồ Chí Minh",
    ],
    "provinces": ["Hà Nội", "Hồ Chí Minh", "Khánh Hòa", "Đồng Nai", "Hà Nam", "Nam Định"],
    "districts": ["Ba Đình", "Hoàn Kiếm", "Biên Hòa"],
    "products": ["Đèn LED Bulb", "Đèn LED Tube", "Bóng đèn huỳnh quang", "Phích nước"],
    "statuses": ["Đã xử lý", "Chờ xử lý"],
}


class FakeTextStream:
    """Luồng văn bản giả, đủ dùng cho ``LLMClient.stream`` trong test."""

    def __init__(
        self,
        chunks: Sequence[str | BaseException],
        *,
        usage: Mapping[str, int] | None = None,
    ) -> None:
        self._chunks = list(chunks)
        self.usage = dict(usage or {})
        self.finish_reason: str | None = "STOP"
        self.closed = False
        self.close_calls = 0

    def __iter__(self) -> FakeTextStream:
        return self

    def __next__(self) -> str:
        if self.closed or not self._chunks:
            raise StopIteration
        item = self._chunks.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    def close(self) -> None:
        self.close_calls += 1
        self.closed = True

    def __enter__(self) -> FakeTextStream:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


class ScriptedLLM:
    """Trả lần lượt các phản hồi định sẵn; phần tử là Exception thì ném ra."""

    def __init__(
        self,
        responses: Sequence[str | BaseException] | None = None,
        *,
        default: str | None = None,
        stream_chunks: Sequence[Sequence[str | BaseException]] | None = None,
    ) -> None:
        self._responses: list[str | BaseException] = list(responses or [])
        self._default = default
        self._stream_chunks: list[Sequence[str | BaseException]] = list(stream_chunks or [])
        self.calls: list[tuple[str, str]] = []  # (call_type, prompt)
        self.system_instructions: list[str | None] = []
        self.stream_kwargs: list[dict[str, object]] = []
        self.streams: list[FakeTextStream] = []

    def generate_json(
        self, prompt: str, *, call_type: str, system_instruction: str | None = None
    ) -> LLMResult:
        self.calls.append((call_type, prompt))
        self.system_instructions.append(system_instruction)
        if self._responses:
            item = self._responses.pop(0)
        elif self._default is not None:
            item = self._default
        else:
            raise AssertionError("ScriptedLLM: no scripted response left")
        if isinstance(item, BaseException):
            raise item
        return LLMResult(text=item, usage=dict(DEFAULT_USAGE), latency_ms=1, model="fake-model")

    def stream(
        self,
        prompt: str,
        *,
        call_type: str,
        system_instruction: str | None = None,
        temperature: float | None = None,
        max_output_tokens: int | None = None,
    ) -> FakeTextStream:
        """Stream giả: cắt phản hồi định sẵn thành các đoạn (b04 task 6.4, b05 task 1.4).

        Phần tử là Exception trong ``stream_chunks`` sẽ được ném ra đúng lúc tới lượt nó.
        """
        self.calls.append((call_type, prompt))
        self.stream_kwargs.append(
            {
                "system_instruction": system_instruction,
                "temperature": temperature,
                "max_output_tokens": max_output_tokens,
            }
        )
        if self._stream_chunks:
            chunks = list(self._stream_chunks.pop(0))
        elif self._responses:
            item = self._responses.pop(0)
            if isinstance(item, BaseException):
                raise item
            chunks = [item]
        elif self._default is not None:
            chunks = [self._default]
        else:
            raise AssertionError("ScriptedLLM: no scripted stream left")
        stream = FakeTextStream(chunks, usage=dict(DEFAULT_USAGE))
        self.streams.append(stream)
        return stream

    def calls_for(self, call_type: str) -> list[str]:
        return [prompt for kind, prompt in self.calls if kind == call_type]


class InMemoryHistory:
    def __init__(self) -> None:
        self._turns: dict[str, list[HistoryTurn]] = {}

    def add(self, session_id: str, question: str, answer_summary: str) -> None:
        self._turns.setdefault(session_id, []).append(HistoryTurn(question, answer_summary))

    def last_turns(self, session_id: str, n: int) -> list[HistoryTurn]:
        turns = self._turns.get(session_id, [])
        return turns[-n:] if n > 0 else []


class StaticMetadataProvider:
    def __init__(self, values: Mapping[str, Sequence[str]] | None = None) -> None:
        self.values = {k: list(v) for k, v in (values or SAMPLE_METADATA).items()}
        self.calls = 0

    def valid_values(self) -> Mapping[str, Sequence[str]]:
        self.calls += 1
        return self.values


class FixedClock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


# ── Executor giả (b03) ──

# Câu thông báo quyền của ScopePolicy (nhánh anthanh). Ghim ở đây để ResultInterpreter
# được test đúng với chữ của executor thật; Dev A đổi chữ thì test đỏ (design b03 D8).
NO_UNIT_MESSAGE = "Tài khoản '{username}' chưa được phân công đơn vị nào để tra cứu dữ liệu."
FORBIDDEN_UNIT_MESSAGE = (
    "Bạn không có quyền xem dữ liệu của đơn vị '{unit}'. Các đơn vị được phép: {allowed}"
)

SAMPLE_ROWS: list[dict[str, object]] = [
    {"unit_name": "Truyền thống Vùng 1", "label": "Chất lượng", "issue_count": 12},
    {"unit_name": "Truyền thống Vùng 2", "label": "Giao hàng", "issue_count": 7},
    {"unit_name": "Truyền thống Vùng 3", "label": "Bảo hành", "issue_count": 4},
    {"unit_name": "Nha Trang", "label": "Chất lượng", "issue_count": 9},
    {"unit_name": "Biên Hòa", "label": "Giao hàng", "issue_count": 5},
]


class ScopeRespectingFakeExecutor:
    """Executor giả lọc đúng theo nhiều đơn vị — điều executor v1.0 chưa làm được (review R03)."""

    def __init__(self, rows: Sequence[Mapping[str, object]] | None = None) -> None:
        self.rows = [dict(row) for row in (rows if rows is not None else SAMPLE_ROWS)]
        self.calls: list[tuple[QueryPlan, UserScope]] = []

    def execute(self, plan: QueryPlan, scope: UserScope) -> QueryResult:
        self.calls.append((plan, scope))
        allowed = list(scope.unit_ids)
        if not scope.is_admin and not allowed:
            return QueryResult.error(NO_UNIT_MESSAGE.format(username=scope.username))

        requested = plan.params.get("unit_name")
        if requested and not scope.is_admin and requested not in allowed:
            return QueryResult.error(
                FORBIDDEN_UNIT_MESSAGE.format(unit=requested, allowed=", ".join(allowed))
            )

        rows = self.rows
        if not scope.is_admin:
            rows = [row for row in rows if row.get("unit_name") in allowed]
        if requested:
            rows = [row for row in rows if row.get("unit_name") == requested]

        if not rows:
            return QueryResult.no_data()
        return QueryResult.ok(
            [dict(row) for row in rows],
            total_rows=len(rows),
            scope_applied=scope.scope_description,
            pattern_used=plan.pattern.value,
        )


class SlowFakeExecutor:
    """Ngủ trước khi trả kết quả, để test timeout theo bước và theo lượt."""

    def __init__(
        self,
        delay_seconds: float,
        inner: ScopeRespectingFakeExecutor | None = None,
        *,
        slow_steps: Sequence[int] | None = None,
    ) -> None:
        self.delay_seconds = delay_seconds
        self.inner = inner or ScopeRespectingFakeExecutor()
        self.slow_steps = set(slow_steps) if slow_steps is not None else None
        self.calls: list[tuple[QueryPlan, UserScope]] = []

    def execute(self, plan: QueryPlan, scope: UserScope) -> QueryResult:
        self.calls.append((plan, scope))
        if self.slow_steps is None or len(self.calls) in self.slow_steps:
            time.sleep(self.delay_seconds)
        return self.inner.execute(plan, scope)


# ── FTS (b08 task 1.4) ──

FTS_RECORDS_PATH = Path(__file__).resolve().parent / "fixtures" / "fts_records.json"
CITATION_COLUMNS = ("source_file_name", "source_row_number")


def load_fts_records() -> list[dict]:
    return json.loads(FTS_RECORDS_PATH.read_text(encoding="utf-8"))


class SqliteFtsFakeExecutor:
    """Executor FTS thật trên SQLite: dựng 2 bảng FTS bằng migration + ``sync_fts_index`` của
    Dev A, dùng lại hàm làm sạch v1.0, nhưng áp bộ lọc ``fts_filters`` và phạm vi đơn vị,
    ``ORDER BY rank`` và trả ``no_data`` khi không khớp (khác mock M01).
    """

    def __init__(
        self,
        records: Sequence[dict] | None = None,
        *,
        with_citation_columns: bool = True,
        code_lookup: bool = False,
    ) -> None:
        from dms.chat.db.migrations import apply_chat_migrations, sync_fts_index

        self.records = list(records if records is not None else load_fts_records())
        self.with_citation_columns = with_citation_columns
        self.code_lookup = code_lookup
        self.calls: list[QueryPlan] = []
        self.scopes: list[UserScope] = []
        self.syntax_errors: list[str] = []
        self.conn = sqlite3.connect(":memory:", check_same_thread=False)
        self.conn.execute(
            """CREATE TABLE feedback_records (
                feedback_id INTEGER PRIMARY KEY, issue_code TEXT, content TEXT, product TEXT,
                unit_name TEXT, source TEXT, issue_date TEXT, sentiment TEXT,
                business_status TEXT, raw_data_json TEXT, source_file_name TEXT,
                source_row_number INTEGER, is_active INTEGER NOT NULL DEFAULT 1)"""
        )
        self.conn.execute("CREATE TABLE feedback_labels (feedback_id INTEGER, label TEXT)")
        for r in self.records:
            self.conn.execute(
                "INSERT INTO feedback_records VALUES (?,?,?,?,?,?,?,?,?,?,?,?,1)",
                (
                    r["feedback_id"],
                    r["issue_code"],
                    r["content"],
                    r.get("product"),
                    r["unit_name"],
                    r.get("source"),
                    r.get("issue_date"),
                    r.get("sentiment"),
                    r.get("business_status"),
                    "{}",
                    r.get("source_file_name"),
                    r.get("source_row_number"),
                ),
            )
            if r.get("label"):
                self.conn.execute(
                    "INSERT INTO feedback_labels VALUES (?, ?)", (r["feedback_id"], r["label"])
                )
        self.conn.commit()
        apply_chat_migrations(self.conn)
        sync_fts_index(self.conn)

    def document_frequencies(self):
        from dms.chat.ai.fts_query_builder import DocumentFrequencies

        return DocumentFrequencies.from_texts(r["content"] for r in self.records)

    def execute(self, plan: QueryPlan, scope: UserScope) -> QueryResult:
        from dms.chat.contract import QueryPattern

        self.calls.append(plan)
        self.scopes.append(scope)
        if plan.pattern == QueryPattern.FTS5_SEARCH:
            return self._fts(plan, scope)
        if (
            self.code_lookup
            and plan.function_name == "get_issues"
            and plan.params.get("issue_code")
        ):
            return self._by_code(str(plan.params["issue_code"]), scope)
        return QueryResult.error("SqliteFtsFakeExecutor chỉ hỗ trợ fts5_search")

    def _scope_sql(self, scope: UserScope, params: list) -> str:
        if scope.is_admin:
            return ""
        if not scope.unit_ids:
            return " AND 1 = 0"
        params.extend(scope.unit_ids)
        return f" AND r.unit_name IN ({','.join('?' * len(scope.unit_ids))})"

    def _row(self, row: sqlite3.Row) -> dict:
        data = dict(row)
        if not self.with_citation_columns:
            for column in CITATION_COLUMNS:
                data.pop(column, None)
        return data

    def _fts(self, plan: QueryPlan, scope: UserScope) -> QueryResult:
        from unidecode import unidecode

        from dms.chat.db.query_executor import _sanitize_fts_query

        query = _sanitize_fts_query(str(plan.fts_query or ""))
        if not query:
            return QueryResult.no_data()
        filters = dict(plan.fts_filters or {})
        limit = min(max(1, int(plan.fts_limit)), 100)
        self.conn.row_factory = sqlite3.Row
        for table, text in (
            ("feedback_fts_raw", query),
            ("feedback_fts_nodau", unidecode(query).lower()),
        ):
            params: list = [text]
            sql = (
                "SELECT r.feedback_id, r.issue_code, r.content, r.product, r.unit_name, r.source, "
                "r.issue_date, r.sentiment, r.business_status, r.source_file_name, r.source_row_number "
                f"FROM {table} f JOIN feedback_records r ON r.feedback_id = f.feedback_id "
                f"WHERE f.{table} MATCH ? AND r.is_active = 1"
            )
            for key, column in (("date_from", "r.issue_date >="), ("date_to", "r.issue_date <=")):
                if filters.get(key):
                    sql += f" AND {column} ?"
                    params.append(str(filters[key]))
            for key in ("unit_name", "sentiment", "source"):
                if filters.get(key):
                    sql += f" AND r.{key} = ?"
                    params.append(str(filters[key]))
            if filters.get("label"):
                sql += " AND EXISTS (SELECT 1 FROM feedback_labels l WHERE l.feedback_id = r.feedback_id AND l.label = ?)"
                params.append(str(filters["label"]))
            sql += self._scope_sql(scope, params)
            sql += f" ORDER BY f.rank LIMIT {limit}"
            try:
                rows = self.conn.execute(sql, params).fetchall()
            except sqlite3.OperationalError as exc:
                self.syntax_errors.append(f"{text!r}: {exc}")
                return QueryResult.error(f"Lỗi thực thi: {exc}")
            if rows:
                return QueryResult.ok([self._row(r) for r in rows])
        return QueryResult.no_data()

    def _by_code(self, code: str, scope: UserScope) -> QueryResult:
        self.conn.row_factory = sqlite3.Row
        params: list = [code]
        sql = "SELECT r.* FROM feedback_records r WHERE r.issue_code = ?" + self._scope_sql(
            scope, params
        )
        rows = self.conn.execute(sql, params).fetchall()
        return QueryResult.ok([self._row(r) for r in rows]) if rows else QueryResult.no_data()
