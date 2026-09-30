"""Chạy tự động bộ câu hỏi ``docs/chatbot/bo-cau-hoi-kiem-thu-theo-moc.md`` rồi chấm điểm.

    uv run python scripts/run_chat_suite.py                      # mọi ca tự động được
    uv run python scripts/run_chat_suite.py --only 17,18,28b     # vài ca (tự kéo theo ca nguồn)
    uv run python scripts/run_chat_suite.py --group M4,M4-quyền --max-retries 8
    uv run python scripts/run_chat_suite.py --list

Chạy trong tiến trình, không cần docker: dựng ``build_chat_services`` như ứng dụng web, gọi
Gemini thật trên DB thật. Mặc định DB được chép sang thư mục của lần chạy để phiên thử không
ghi vào ``work/classification_jobs.db``.

Hạn mức Vertex: mọi lời gọi LLM đi qua một lớp bọc ghi lại lỗi. Có lỗi 429 /
RESOURCE_EXHAUSTED (hoặc 5xx) trong một ca thì ca đó bị bỏ, đồng hồ đếm ngược chờ theo
backoff luỹ thừa, rồi chạy lại **cả ca trong phiên mới** (lượt hỏng đã được lưu vào phiên
cũ nên không dùng lại). Hết số lần thử thì ca được đánh ``QUOTA`` (chưa kết luận), không
tính là trượt.

Kết quả nằm ở ``work/eval/chat_suite/<thời điểm>/``: ``report.md``, ``results.json``,
``events/<ca>.json`` và ``run.log``.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import random
import sqlite3
import sys
import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

SERVICE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVICE_DIR / "scripts"))
if str(SERVICE_DIR / "src") not in sys.path:
    sys.path.insert(0, str(SERVICE_DIR / "src"))

from chat_suite_cases import CASE_INDEX, CASES, PROFILES, USERS, Case  # noqa: E402
from chat_suite_checks import CheckResult, TurnResult  # noqa: E402

DEFAULT_DB = SERVICE_DIR / "work" / "classification_jobs.db"
DEFAULT_OUT = SERVICE_DIR / "work" / "eval" / "chat_suite"
DOC_SNAPSHOT = {"active_rows": 27220, "issues": 27012, "max_date": "2026-09-05"}

QUOTA_MARKERS = ("429", "resource_exhausted", "resource exhausted", "quota", "too many requests")
# Vertex chậm (``GeminiStreamTimeout``, "timed out") cũng là lỗi tạm thời của dịch vụ ngoài:
# nhận định bị bỏ vì hết giờ không nói gì về chatbot. Lần thử lại vẫn ghi vào báo cáo.
TRANSIENT_MARKERS = (
    "503", "502", "504", "unavailable", "overloaded", "500 internal", "timed out", "timeout",
)
# ``hang``: lượt không trả về trong hạn (lời gọi Vertex treo) — cũng chờ rồi chạy lại ca.
RETRYABLE = frozenset({"quota", "transient", "hang"})

VERDICT_ICONS = {
    "PASS": "✅",
    "FAIL": "❌",
    "QUOTA": "⏳",
    "ERROR": "💥",
    "INFO": "ℹ️",
    "MANUAL": "🖐",
    "SKIPPED": "⏭",
    "NOT_RUN": "·",
}


# ═══════════════════════════════════════════════════════════════════
# Theo dõi lỗi LLM
# ═══════════════════════════════════════════════════════════════════


def classify_failure(exc: BaseException) -> str:
    """``quota`` (429), ``transient`` (5xx) hoặc ``other``; đi hết chuỗi ``__cause__``."""
    texts: list[str] = []
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if getattr(current, "category", None) == "quota":  # GatewayError
            return "quota"
        if 429 in (getattr(current, "code", None), getattr(current, "status_code", None)):
            return "quota"
        texts.append(f"{type(current).__name__} {current}")
        current = current.__cause__ or current.__context__
    blob = " ".join(texts).lower()
    if any(marker in blob for marker in QUOTA_MARKERS):
        return "quota"
    if any(marker in blob for marker in TRANSIENT_MARKERS):
        return "transient"
    return "other"


@dataclass(frozen=True)
class LLMFailure:
    kind: str
    call_type: str
    error: str

    def to_dict(self) -> dict[str, str]:
        return {"kind": self.kind, "call_type": self.call_type, "error": self.error}


class FailureLog:
    """Lỗi LLM của lần thử hiện tại; lượt chạy ở thread của runner nên cần khoá."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: list[LLMFailure] = []

    def record(self, call_type: str, exc: BaseException) -> None:
        failure = LLMFailure(classify_failure(exc), call_type, f"{type(exc).__name__}: {exc}"[:300])
        with self._lock:
            self._items.append(failure)

    def record_kind(self, kind: str, call_type: str, error: str) -> None:
        with self._lock:
            self._items.append(LLMFailure(kind, call_type, error))

    def reset(self) -> None:
        with self._lock:
            self._items.clear()

    def snapshot(self) -> list[LLMFailure]:
        with self._lock:
            return list(self._items)

    def retryable(self) -> list[LLMFailure]:
        return [f for f in self.snapshot() if f.kind in RETRYABLE]


class WatchedStream:
    def __init__(self, inner: Any, call_type: str, failures: FailureLog) -> None:
        self._inner = inner
        self._call_type = call_type
        self._failures = failures

    def __iter__(self) -> WatchedStream:
        return self

    def __next__(self) -> str:
        try:
            return next(self._inner)
        except StopIteration:
            raise
        except Exception as exc:
            self._failures.record(self._call_type, exc)
            raise

    def close(self) -> None:
        self._inner.close()

    def __enter__(self) -> WatchedStream:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


class WatchedLLM:
    """Bọc ``GeminiChatGateway``: không đổi hành vi, chỉ ghi lại lỗi để runner quyết định chờ."""

    def __init__(self, inner: Any, failures: FailureLog) -> None:
        self._inner = inner
        self._failures = failures
        self.calls = 0

    @property
    def ledger(self) -> Any:
        return getattr(self._inner, "ledger", None)

    @ledger.setter
    def ledger(self, value: Any) -> None:  # build_chat_services gắn sổ usage vào LLM
        self._inner.ledger = value

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def generate_json(self, prompt: str, *, call_type: str, **kwargs: Any) -> Any:
        self.calls += 1
        try:
            return self._inner.generate_json(prompt, call_type=call_type, **kwargs)
        except Exception as exc:
            self._failures.record(call_type, exc)
            raise

    def stream(self, prompt: str, *, call_type: str, **kwargs: Any) -> WatchedStream:
        self.calls += 1
        try:
            inner = self._inner.stream(prompt, call_type=call_type, **kwargs)
        except Exception as exc:
            self._failures.record(call_type, exc)
            raise
        return WatchedStream(inner, call_type, self._failures)


# ═══════════════════════════════════════════════════════════════════
# Log
# ═══════════════════════════════════════════════════════════════════

_STANDARD_ATTRS = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {
    "message",
    "asctime",
}


class LogTap(logging.Handler):
    """Ghi mọi log ra ``run.log`` và giữ trong RAM để gắn vào lượt đang chạy."""

    def __init__(self, path: Path) -> None:
        super().__init__(logging.INFO)
        self._lock_lines = threading.Lock()
        self.lines: list[str] = []
        self._file = path.open("a", encoding="utf-8")

    def emit(self, record: logging.LogRecord) -> None:
        try:
            extras = {k: v for k, v in record.__dict__.items() if k not in _STANDARD_ATTRS}
            stamp = datetime.fromtimestamp(record.created).strftime("%H:%M:%S")
            line = f"{stamp} {record.levelname} {record.name}: {record.getMessage()}"
            if extras:
                line += " " + json.dumps(extras, ensure_ascii=False, default=str)
            if record.exc_info:
                line += "\n" + "".join(traceback.format_exception(*record.exc_info))
            with self._lock_lines:
                self.lines.append(line)
                self._file.write(line + "\n")
                self._file.flush()
        except Exception:
            self.handleError(record)

    def mark(self) -> int:
        with self._lock_lines:
            return len(self.lines)

    def since(self, mark: int) -> list[str]:
        with self._lock_lines:
            return self.lines[mark:]

    def close(self) -> None:
        self._file.close()
        super().close()


# ═══════════════════════════════════════════════════════════════════
# Môi trường chatbot
# ═══════════════════════════════════════════════════════════════════


def copy_database(source: Path, work_dir: Path) -> Path:
    """Chép DB bằng backup API (an toàn cả khi web đang ghi WAL)."""
    work_dir.mkdir(parents=True, exist_ok=True)
    target = work_dir / "classification_jobs.db"
    src = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    dst = sqlite3.connect(target)
    try:
        with dst:
            src.backup(dst)
    finally:
        src.close()
        dst.close()
    return target


def db_snapshot(db: Path) -> dict[str, Any]:
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        rows, issues, max_date = conn.execute(
            "SELECT COUNT(*), COUNT(DISTINCT issue_code), MAX(issue_date) "
            "FROM feedback_records WHERE is_active = 1"
        ).fetchone()
    finally:
        conn.close()
    return {"active_rows": rows, "issues": issues, "max_date": str(max_date)[:10]}


def build_settings(profile: str, work_dir: Path, log_dir: Path) -> Any:
    """Đặt biến môi trường rồi dựng ``Settings`` (env thắng ``.env``); ai gọi ``get_settings``
    trong lúc chạy cũng thấy đúng thư mục làm việc tạm."""
    from dms.settings import Settings

    env = {
        "WORK_DIR": str(work_dir),
        "LOG_DIR": str(log_dir),
        "CHAT_ENABLED": "true",
        "CHAT_REDIS_URL": "",  # buffer trong RAM; host ``redis`` chỉ có trong docker
        "CHAT_MILESTONE": PROFILES[profile]["chat_milestone"],
        "CHAT_ENABLED_PATTERNS": PROFILES[profile]["chat_enabled_patterns"],
    }
    credentials = os.environ.get("GCP_SERVICE_ACCOUNT_JSON", "")
    local_key = SERVICE_DIR / "testvertex.json"
    if (not credentials or not Path(credentials).exists()) and local_key.exists():
        env["GCP_SERVICE_ACCOUNT_JSON"] = str(local_key)
    os.environ.update(env)
    return Settings()


class SuiteEnv:
    def __init__(self, work_dir: Path, log_dir: Path) -> None:
        self.work_dir = work_dir
        self.log_dir = log_dir
        self.failures = FailureLog()
        self._services: dict[str, Any] = {}
        self.llm_calls = 0
        self._llms: list[WatchedLLM] = []

    def services(self, profile: str) -> Any:
        if profile not in self._services:
            from dms.chat.ai.llm_gateway import GeminiChatGateway
            from dms.chat.ws.services import build_chat_services

            settings = build_settings(profile, self.work_dir, self.log_dir)
            llm = WatchedLLM(GeminiChatGateway(settings), self.failures)
            self._llms.append(llm)
            self._services[profile] = build_chat_services(settings, llm=llm)
        return self._services[profile]

    @property
    def total_llm_calls(self) -> int:
        return sum(llm.calls for llm in self._llms)

    def shutdown(self) -> None:
        for services in self._services.values():
            try:
                services.shutdown()
            except Exception:
                logging.getLogger("chat-suite").exception("shutdown_failed")


def wait_idle(runner: Any, baseline: int = 0, timeout: float = 180.0) -> None:
    """Chờ tác vụ nền (tóm tắt phiên) của lượt vừa rồi xong, như người dùng đọc xong mới hỏi
    tiếp. ``baseline``: số việc đã có trong pool từ trước (vd. một lượt cũ đang treo)."""
    deadline = time.monotonic() + timeout
    while runner.inflight > baseline and time.monotonic() < deadline:
        time.sleep(0.2)


def ask(
    services: Any,
    user: dict[str, Any],
    question: str,
    session_id: str | None,
    tap: LogTap,
    *,
    timeout: float,
) -> TurnResult:
    """Làm đúng các bước của ``ChatConnection._handle_ask`` (trừ rate limit và hạn mức)."""
    from dms.chat.ai.types import HistoryWindow, TurnRequest
    from dms.chat.contract import UserScope

    settings = services.settings
    username = str(user["username"])
    mark = tap.mark()
    session = services.sessions.open_for_ask(username, session_id, question)
    if session_id:
        history = services.history.window(
            session.session_id,
            max_turns=int(settings.chat_history_turns),
            budget_chars=int(settings.chat_history_char_budget),
        )
        previous_quotes = services.history.last_quotes(session.session_id)
        previous_plans = services.history.last_plans(session.session_id)
    else:
        history, previous_quotes, previous_plans = HistoryWindow(), [], []
    turn = TurnRequest(
        question=question,
        scope=UserScope.from_user_dict(user),
        session_id=session.session_id,
        history=history,
        previous_slots=services.sessions.previous_slots(session),
        request_id=uuid.uuid4().hex,
        previous_quotes=tuple(previous_quotes),
        previous_plans=tuple(previous_plans),
        display_name=str(user.get("display_name") or username),
    )
    baseline = services.runner.inflight
    future = services.runner.submit(
        answer_id=uuid.uuid4().hex,
        username=username,
        session_id=session.session_id,
        client_msg_id=uuid.uuid4().hex[:16],
        turn=turn,
    )
    record = future.result(timeout=timeout)
    wait_idle(services.runner, baseline)
    return TurnResult(
        question=question,
        events=list(record.events),
        outcome=record.outcome,
        runner_error=record.error,
        total_ms=record.total_ms,
        session_id=session.session_id,
        history_summary_chars=len(getattr(history, "summary", "") or ""),
        logs=tap.since(mark),
    )


# ═══════════════════════════════════════════════════════════════════
# Chạy và chấm từng ca
# ═══════════════════════════════════════════════════════════════════


@dataclass
class CaseRun:
    case: Case
    verdict: str = "NOT_RUN"
    attempts: int = 0
    waited_s: float = 0.0
    duration_s: float = 0.0
    turns: list[TurnResult] = field(default_factory=list)
    checks: list[list[CheckResult]] = field(default_factory=list)
    failures: list[LLMFailure] = field(default_factory=list)
    error: str | None = None
    retry_log: list[str] = field(default_factory=list)

    @property
    def failed_checks(self) -> list[tuple[int, CheckResult]]:
        return [(i, c) for i, turn in enumerate(self.checks, 1) for c in turn if c.ok is False]


def run_check(check: Any, result: TurnResult) -> CheckResult:
    try:
        return check(result)
    except Exception as exc:  # phép kiểm lỗi = trượt, nhưng không làm dừng cả bộ
        return CheckResult(getattr(check, "__qualname__", "check"), False, f"lỗi phép kiểm: {exc!r}")


def grade(checks: list[list[CheckResult]]) -> str:
    hard = [c for turn in checks for c in turn if c.ok is not None]
    if not hard:
        return "INFO"
    return "PASS" if all(c.ok for c in hard) else "FAIL"


def backoff_seconds(attempt: int, base: float, cap: float) -> float:
    wait = min(cap, base * (2 ** (attempt - 1)))
    return wait + random.uniform(0, wait * 0.1)


def countdown(seconds: float, label: str) -> None:
    end = time.monotonic() + seconds
    tty = sys.stderr.isatty()
    last = 0.0
    while (left := end - time.monotonic()) > 0:
        if tty:
            sys.stderr.write(f"\r   ⏳ {label}: còn {int(left) + 1:>4}s ")
            sys.stderr.flush()
        elif time.monotonic() - last >= 30 or last == 0.0:
            print(f"   ⏳ {label}: còn {int(left) + 1}s", flush=True)
            last = time.monotonic()
        time.sleep(min(1.0, left))
    if tty:
        sys.stderr.write("\r" + " " * 110 + "\r")
        sys.stderr.flush()


def run_case(case: Case, env: SuiteEnv, tap: LogTap, args: argparse.Namespace) -> CaseRun:
    run = CaseRun(case=case)
    started = time.monotonic()
    services = env.services(case.profile)
    user = USERS[case.user]
    settings = services.settings
    timeout = (
        max(float(settings.chat_turn_timeout_seconds), float(settings.chat_report_turn_timeout_seconds))
        + 60.0
    )
    max_attempts = args.max_retries + 1
    for attempt in range(1, max_attempts + 1):
        run.attempts = attempt
        env.failures.reset()
        turns: list[TurnResult] = []
        session_id: str | None = None
        try:
            for spec in case.turns:
                result = ask(services, user, spec.question, session_id, tap, timeout=timeout)
                turns.append(result)
                session_id = result.session_id
                if env.failures.retryable():
                    break  # lượt này đã hỏng vì hạn mức; không phí thêm lời gọi cho lượt sau
                if args.turn_pause:
                    time.sleep(args.turn_pause)
        except TimeoutError:
            # Lượt treo quá hạn (orchestrator không tự cắt được): ghi lại rồi thử lại như 5xx.
            env.failures.record_kind("hang", "turn", f"lượt không trả về sau {timeout:.0f}s")
        except Exception as exc:
            run.error = f"{type(exc).__name__}: {exc}"
            run.turns = turns
            run.verdict = "ERROR"
            run.failures = env.failures.snapshot()
            logging.getLogger("chat-suite").exception("case_crashed", extra={"case": case.id})
            break
        retryable = env.failures.retryable()
        run.failures = env.failures.snapshot()
        run.turns = turns
        if not retryable:
            run.checks = [
                [run_check(check, result) for check in spec.checks]
                for spec, result in zip(case.turns, turns, strict=False)
            ]
            run.verdict = grade(run.checks)
            break
        kinds = sorted({f.kind for f in retryable})
        calls = sorted({f.call_type for f in retryable})
        note = f"lần {attempt}: {'/'.join(kinds)} ở {', '.join(calls)} — {retryable[0].error[:120]}"
        run.retry_log.append(note)
        if attempt == max_attempts:
            run.verdict = "QUOTA"
            break
        wait = backoff_seconds(attempt, args.retry_base, args.retry_cap)
        run.waited_s += wait
        countdown(wait, f"[{case.id}] {'/'.join(kinds)} ({', '.join(calls)}), thử lại {attempt + 1}/{max_attempts}")
    run.duration_s = time.monotonic() - started
    return run


def grade_reuse(case: Case, runs: dict[str, CaseRun]) -> CaseRun:
    run = CaseRun(case=case)
    sources = [runs.get(source_id) for source_id in case.reuse]
    if any(s is None or s.verdict in ("NOT_RUN", "SKIPPED") for s in sources):
        run.verdict = "SKIPPED"
        run.error = "ca nguồn chưa chạy"
        return run
    if any(s.verdict in ("QUOTA", "ERROR") for s in sources):  # type: ignore[union-attr]
        run.verdict = "QUOTA" if any(s.verdict == "QUOTA" for s in sources) else "ERROR"  # type: ignore[union-attr]
        run.error = "ca nguồn chưa kết luận: " + ", ".join(
            f"{s.case.id}={s.verdict}" for s in sources if s  # type: ignore[union-attr]
        )
        return run
    for source in sources:
        last = source.turns[-1]  # type: ignore[union-attr]
        run.turns.append(last)
        run.checks.append([run_check(check, last) for check in case.reuse_checks])
    run.attempts = 0
    run.verdict = grade(run.checks)
    return run


# ═══════════════════════════════════════════════════════════════════
# Báo cáo
# ═══════════════════════════════════════════════════════════════════


def _turn_dict(result: TurnResult, checks: list[CheckResult]) -> dict[str, Any]:
    data = result.brief()
    data["checks"] = [{"label": c.label, "ok": c.ok, "detail": c.detail} for c in checks]
    return data


def case_dict(run: CaseRun) -> dict[str, Any]:
    case = run.case
    turns = [
        _turn_dict(result, run.checks[i] if i < len(run.checks) else [])
        for i, result in enumerate(run.turns)
    ]
    return {
        "id": case.id,
        "group": case.group,
        "title": case.title,
        "profile": case.profile,
        "user": case.user,
        "verdict": run.verdict,
        "attempts": run.attempts,
        "waited_s": round(run.waited_s, 1),
        "duration_s": round(run.duration_s, 1),
        "manual": case.manual,
        "reuse": list(case.reuse),
        "note": case.note,
        "error": run.error,
        "retry_log": run.retry_log,
        "llm_failures": [f.to_dict() for f in run.failures],
        "turns": turns,
    }


def _md_escape(text: Any) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def write_outputs(
    out_dir: Path,
    runs: list[CaseRun],
    meta: dict[str, Any],
) -> None:
    events_dir = out_dir / "events"
    events_dir.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}
    for run in runs:
        counts[run.verdict] = counts.get(run.verdict, 0) + 1
        if run.turns and not run.case.reuse:
            (events_dir / f"{run.case.id}.json").write_text(
                json.dumps(
                    [{"question": t.question, "events": t.events, "sql": t.sql} for t in run.turns],
                    ensure_ascii=False,
                    indent=2,
                    default=str,
                ),
                encoding="utf-8",
            )
    payload = {**meta, "summary": counts, "cases": [case_dict(run) for run in runs]}
    (out_dir / "results.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    (out_dir / "report.md").write_text(render_markdown(runs, meta, counts), encoding="utf-8")


def render_markdown(runs: list[CaseRun], meta: dict[str, Any], counts: dict[str, int]) -> str:
    lines = [
        "# Kết quả chạy bộ câu hỏi chatbot",
        "",
        f"Bắt đầu {meta['started']} · kết thúc {meta.get('finished') or '(đang chạy)'} · "
        f"{meta.get('llm_calls', 0)} lời gọi LLM · chờ hạn mức tổng {meta.get('waited_s', 0):.0f}s",
        "",
        f"Nguồn câu hỏi: `docs/chatbot/bo-cau-hoi-kiem-thu-theo-moc.md` · DB: `{meta['db_source']}`"
        + (" (bản chép)" if meta.get("db_copied") else " (ghi thẳng)"),
        "",
    ]
    snap = meta.get("db_snapshot") or {}
    if snap:
        drift = {k: v for k, v in snap.items() if DOC_SNAPSHOT.get(k) != v}
        lines.append(
            f"Dữ liệu: {snap.get('active_rows')} dòng · {snap.get('issues')} vấn đề · "
            f"đến {snap.get('max_date')}"
            + (f" — **khác ảnh chụp trong tài liệu** {DOC_SNAPSHOT}, số kỳ vọng có thể lệch" if drift else " — khớp ảnh chụp trong tài liệu")
        )
        lines.append("")
    order = ["PASS", "FAIL", "QUOTA", "ERROR", "INFO", "MANUAL", "SKIPPED", "NOT_RUN"]
    lines.append(" · ".join(f"{VERDICT_ICONS[v]} {v} {counts[v]}" for v in order if counts.get(v)))
    lines += ["", "| # | Nhóm | Ca | Kết quả | Lần chạy | Ghi chú |", "|---|---|---|---|---|---|"]
    for run in runs:
        case = run.case
        if run.verdict == "FAIL":
            note = "; ".join(f"{c.label} → {c.detail}" for _, c in run.failed_checks[:3])
        elif run.verdict in ("QUOTA", "ERROR", "SKIPPED"):
            note = run.error or (run.retry_log[-1] if run.retry_log else "")
        elif run.verdict == "MANUAL":
            note = case.manual or ""
        else:
            infos = [f"{c.label}: {c.detail}" for turn in run.checks for c in turn if c.ok is None]
            note = "; ".join(infos[:2])
        attempts = f"{run.attempts}" + (f" (chờ {run.waited_s:.0f}s)" if run.waited_s else "")
        lines.append(
            f"| {case.id} | {case.group} | {_md_escape(case.title)} | "
            f"{VERDICT_ICONS[run.verdict]} {run.verdict} | {attempts if run.attempts else '—'} | "
            f"{_md_escape(note)[:300]} |"
        )

    detail_runs = [r for r in runs if r.verdict in ("FAIL", "ERROR", "QUOTA", "INFO")]
    if detail_runs:
        lines += ["", "## Chi tiết các ca cần xem", ""]
    for run in detail_runs:
        case = run.case
        lines.append(f"### {VERDICT_ICONS[run.verdict]} {case.id} — {case.title}")
        if case.note:
            lines.append(f"_{case.note}_")
        if run.error:
            lines.append(f"- Lỗi: `{_md_escape(run.error)}`")
        for note in run.retry_log:
            lines.append(f"- Hạn mức {note}")
        for i, result in enumerate(run.turns):
            checks = run.checks[i] if i < len(run.checks) else []
            lines.append(
                f"- Lượt {i + 1}: **{_md_escape(result.question)}** → `{result.status}` · "
                f"intent `{result.intent}` · lý do `{', '.join(sorted(result.reasons)) or '—'}` · "
                f"bước `{', '.join(result.functions) or '—'}` · {result.total_ms} ms"
            )
            for check in checks:
                mark = {True: "✔", False: "✘", None: "·"}[check.ok]
                lines.append(f"  - {mark} {check.label} — {_md_escape(check.detail)[:240]}")
            if checks and any(c.ok is False for c in checks) and result.texts:
                lines.append(f"  - Câu chữ: {_md_escape(' / '.join(result.texts))[:300]}")
            for sql in result.sql[:1]:
                lines.append(f"  - SQL: `{_md_escape(sql)[:400]}`")
        lines.append("")

    rerun = [r.case.id for r in runs if r.verdict in ("QUOTA", "ERROR", "NOT_RUN")]
    if rerun:
        lines += [
            "## Chạy lại các ca chưa kết luận",
            "",
            "```bash",
            f"uv run python scripts/run_chat_suite.py --only {','.join(rerun)}",
            "```",
            "",
        ]
    return "\n".join(lines) + "\n"


def print_case_line(index: int, total: int, run: CaseRun) -> None:
    case = run.case
    extra = f"{run.attempts} lần" if run.attempts else ""
    if run.waited_s:
        extra += f", chờ {run.waited_s:.0f}s"
    if run.duration_s:
        extra += f", {run.duration_s:.1f}s"
    position = f"[{index:>2}/{total}]" if total else "[kiểm lại]"
    print(
        f"{position} {VERDICT_ICONS[run.verdict]} {run.verdict:<7} {case.id:<6} "
        f"{case.title}" + (f" ({extra.strip(', ')})" if extra else ""),
        flush=True,
    )
    for turn_no, check in run.failed_checks[:4]:
        print(f"         ✘ lượt {turn_no}: {check.label} → {check.detail[:160]}", flush=True)
    if run.verdict in ("ERROR", "QUOTA", "SKIPPED") and (run.error or run.retry_log):
        print(f"         {run.error or run.retry_log[-1]}", flush=True)


# ═══════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════


def select_cases(args: argparse.Namespace) -> list[Case]:
    selected = list(CASES)
    if args.group:
        groups = {g.strip() for g in args.group.split(",") if g.strip()}
        selected = [c for c in selected if c.group in groups]
    if args.only:
        wanted = {i.strip() for i in args.only.split(",") if i.strip()}
        unknown = wanted - set(CASE_INDEX)
        if unknown:
            raise SystemExit(f"Không có ca: {', '.join(sorted(unknown))}")
        # Ca kiểm lại (28b, 32b, 49) cần ca nguồn chạy trước.
        for case_id in list(wanted):
            wanted.update(CASE_INDEX[case_id].reuse)
        selected = [c for c in CASES if c.id in wanted]
    return selected


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Chạy tự động bộ câu hỏi kiểm thử chatbot")
    parser.add_argument("--only", help="danh sách mã ca, vd. 1,17,28b")
    parser.add_argument("--group", help="lọc theo nhóm: M1,M1-âm,M3,M4,M4-quyền,M5,M5-nhớ,UI")
    parser.add_argument("--list", action="store_true", help="in danh sách ca rồi thoát")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help="DB nguồn")
    parser.add_argument(
        "--in-place", action="store_true", help="chạy thẳng trên DB nguồn thay vì bản chép"
    )
    parser.add_argument("--out", type=Path, default=None, help="thư mục kết quả")
    parser.add_argument("--max-retries", type=int, default=6, help="số lần thử lại khi hết hạn mức")
    parser.add_argument("--retry-base", type=float, default=30.0, help="giây chờ lần đầu (x2 mỗi lần)")
    parser.add_argument("--retry-cap", type=float, default=300.0, help="giây chờ tối đa mỗi lần")
    parser.add_argument("--pace", type=float, default=2.0, help="giây nghỉ giữa hai ca")
    parser.add_argument(
        "--cooldown-pace", type=float, default=10.0, help="giây nghỉ giữa hai ca ngay sau khi bị 429"
    )
    parser.add_argument("--turn-pause", type=float, default=1.0, help="giây nghỉ giữa hai lượt")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    cases = select_cases(args)
    if args.list:
        for case in CASES:
            kind = "tay" if case.manual else ("kiểm lại " + ",".join(case.reuse) if case.reuse else f"{case.llm_turns} lượt")
            first = case.turns[-1].question if case.turns else ""
            print(f"{case.id:<6} {case.group:<9} {case.profile} {case.user:<5} [{kind}] {case.title} — {first}")
        return 0

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = args.out or DEFAULT_OUT / stamp
    out_dir.mkdir(parents=True, exist_ok=True)
    tap = LogTap(out_dir / "run.log")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(tap)

    source_db = args.db.resolve()
    if not source_db.exists():
        print(f"Không thấy DB {source_db}", file=sys.stderr)
        return 2
    if args.in_place:
        work_dir = source_db.parent
    else:
        print(f"Chép DB sang {out_dir / 'work'} …", flush=True)
        copy_database(source_db, out_dir / "work")
        work_dir = out_dir / "work"
    snapshot = db_snapshot(work_dir / "classification_jobs.db")
    meta: dict[str, Any] = {
        "started": datetime.now().isoformat(timespec="seconds"),
        "finished": None,
        "db_source": str(source_db),
        "db_copied": not args.in_place,
        "db_snapshot": snapshot,
        "args": {k: str(v) for k, v in vars(args).items()},
    }
    print(
        f"DB: {snapshot['active_rows']} dòng · {snapshot['issues']} vấn đề · đến {snapshot['max_date']}"
        f" → kết quả ở {out_dir}",
        flush=True,
    )

    env = SuiteEnv(work_dir, out_dir / "logs")
    (out_dir / "logs").mkdir(exist_ok=True)
    runs: dict[str, CaseRun] = {c.id: CaseRun(case=c) for c in cases}
    ordered = [runs[c.id] for c in cases]
    live = [c for c in cases if not c.manual and not c.reuse]
    # M5 trước rồi mới M1: mỗi mốc dựng services một lần.
    live.sort(key=lambda c: (list(PROFILES).index(c.profile), CASES.index(c)))
    total = len(live)
    cooldown = 0
    interrupted = False

    def flush_outputs() -> None:
        meta["llm_calls"] = env.total_llm_calls
        meta["waited_s"] = sum(r.waited_s for r in runs.values())
        write_outputs(out_dir, ordered, meta)

    try:
        for index, case in enumerate(live, 1):
            run = run_case(case, env, tap, args)
            runs[case.id] = run
            ordered = [runs[c.id] for c in cases]
            print_case_line(index, total, run)
            flush_outputs()
            if run.retry_log:
                cooldown = 5  # vừa chạm hạn mức: giãn nhịp cho vài ca tiếp theo
            if index < total:
                time.sleep(args.cooldown_pace if cooldown else args.pace)
                cooldown = max(0, cooldown - 1)
    except KeyboardInterrupt:
        interrupted = True
        print("\nDừng giữa chừng — ghi báo cáo phần đã chạy.", flush=True)

    for case in cases:
        if case.manual:
            runs[case.id] = CaseRun(case=case, verdict="MANUAL")
        elif case.reuse:
            runs[case.id] = grade_reuse(case, runs)
    ordered = [runs[c.id] for c in cases]
    for run in ordered:
        if run.case.reuse and run.verdict != "SKIPPED":
            print_case_line(0, 0, run)
    meta["finished"] = datetime.now().isoformat(timespec="seconds")
    flush_outputs()
    env.shutdown()
    root.removeHandler(tap)
    tap.close()

    counts: dict[str, int] = {}
    for run in ordered:
        counts[run.verdict] = counts.get(run.verdict, 0) + 1
    print("\n" + " · ".join(f"{VERDICT_ICONS[k]} {k} {v}" for k, v in sorted(counts.items())))
    print(f"Báo cáo: {out_dir / 'report.md'}")
    if interrupted:
        return 130
    if counts.get("FAIL") or counts.get("ERROR"):
        return 1
    return 2 if counts.get("QUOTA") else 0


if __name__ == "__main__":
    raise SystemExit(main())
