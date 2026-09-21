"""Sổ usage theo user và hạn mức token (spec ``chat-token-budget``, b11 task 4.1–4.8)."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest

from dms.chat.ai.budget.budget_policy import BudgetConfig, BudgetPolicy
from dms.chat.ai.budget.request_context import ChatRequestContext, bind, current, use_context
from dms.chat.ai.budget.usage_ledger import InMemoryChatUsageLedger
from dms.chat.ai.llm_gateway import GeminiChatGateway
from dms.chat.ws.services import warn_if_ledger_not_persistent
from dms.gemini_client import GeminiResponse

from .test_llm_gateway import STREAM_USAGE, FakeGemini, FakeGeminiStream, FakeTracker

USAGE = {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}
CONTEXT = ChatRequestContext(username="tv1_sales", request_id="req-1", session_id="s1")


def gateway(settings, ledger, tracker=None):
    gemini = FakeGemini(
        GeminiResponse(text="{}", usage=USAGE),
        stream=FakeGeminiStream(["Câu một. "], usage=STREAM_USAGE),
    )
    return GeminiChatGateway(settings, usage_tracker=tracker, gemini=gemini, ledger=ledger)


def run_turn(client) -> None:
    """Một lượt: một lần lập kế hoạch và một lần viết nhận định."""
    client.generate_json("plan", call_type="chat_plan")
    stream = client.stream("synth", call_type="chat_synthesis")
    list(stream)


# ── Context đi qua thread pool (4.1, 4.2) ──


def test_context_is_lost_in_a_plain_thread_pool():
    with use_context(CONTEXT), ThreadPoolExecutor(max_workers=1) as pool:
        assert pool.submit(current).result() is None


def test_bind_carries_context_into_the_pool():
    with ThreadPoolExecutor(max_workers=1) as pool:
        assert pool.submit(bind(CONTEXT, current)).result() == CONTEXT
    assert current() is None


def test_turn_calls_from_runner_thread_are_recorded_for_the_user(settings):
    ledger = InMemoryChatUsageLedger()
    tracker = FakeTracker()
    client = gateway(settings, ledger, tracker)

    with ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(bind(CONTEXT, run_turn), client).result()

    assert [(e.username, e.request_id, e.call_type) for e in ledger.entries] == [
        ("tv1_sales", "req-1", "chat_plan"),
        ("tv1_sales", "req-1", "chat_synthesis"),
    ]
    assert ledger.entries[0].total_tokens == 120
    assert ledger.entries[1].total_tokens == STREAM_USAGE["total_tokens"]
    assert len(tracker.records) == 2  # UsageTracker vẫn được ghi như trước


def test_calls_outside_a_chat_turn_skip_the_ledger(settings):
    ledger = InMemoryChatUsageLedger()
    tracker = FakeTracker()
    run_turn(gateway(settings, ledger, tracker))
    assert ledger.entries == []
    assert len(tracker.records) == 2


def test_ledger_failure_only_logs(settings, caplog):
    class Broken(InMemoryChatUsageLedger):
        def record(self, **kwargs):
            raise RuntimeError("db locked")

    client = gateway(settings, Broken())
    with use_context(CONTEXT), caplog.at_level(logging.WARNING, logger="dms-chat-llm"):
        result = client.generate_json("plan", call_type="chat_plan")
    assert result.text == "{}"
    assert any(r.message == "chat_usage_ledger_failed" for r in caplog.records)


# ── Hạn mức theo ngày giờ Việt Nam (4.3, 4.8) ──


def at_vn(day: int, hour: int, minute: int = 0) -> datetime:
    """Giờ Việt Nam (UTC+7) → UTC."""
    return datetime(2026, 9, day, hour, minute, tzinfo=UTC) - timedelta(hours=7)


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def spend(ledger, username: str, tokens: int, at: datetime) -> None:
    ledger.record(
        username=username,
        request_id=f"r-{at.isoformat()}",
        session_id="s1",
        call_type="chat_plan",
        model="m",
        prompt_tokens=tokens,
        completion_tokens=0,
        total_tokens=tokens,
        cost_usd=0.0,
        success=True,
        at=at,
    )


def policy(ledger, now: datetime, **config) -> BudgetPolicy:
    return BudgetPolicy(
        ledger,
        config=BudgetConfig(**{"daily_tokens": 1000, "warning_ratio": 0.8, **config}),
        clock=Clock(now),
    )


def test_budget_exceeded_with_reset_at_next_midnight_vietnam_time():
    ledger = InMemoryChatUsageLedger()
    spend(ledger, "an", 1200, at_vn(17, 10))
    status = policy(ledger, at_vn(17, 15)).status("an")

    assert status.exceeded
    assert status.used == 1200
    assert status.reset_at == "2026-09-18T00:00:00+07:00"


def test_new_day_in_vietnam_resets_the_budget():
    ledger = InMemoryChatUsageLedger()
    spend(ledger, "an", 1200, at_vn(17, 23, 50))
    assert policy(ledger, at_vn(17, 23, 55)).status("an").exceeded
    assert not policy(ledger, at_vn(18, 0, 5)).status("an").exceeded


def test_day_boundary_uses_vietnam_time_not_utc():
    ledger = InMemoryChatUsageLedger()
    # 06:30 sáng giờ VN = 23:30 UTC hôm trước: vẫn là "hôm nay" theo giờ VN.
    spend(ledger, "an", 900, at_vn(17, 6, 30))
    assert policy(ledger, at_vn(17, 20)).status("an").used == 900


def test_admin_is_exempt_when_configured():
    ledger = InMemoryChatUsageLedger()
    spend(ledger, "admin", 5000, at_vn(17, 9))
    assert not policy(ledger, at_vn(17, 10)).status("admin", is_admin=True).exceeded
    assert policy(ledger, at_vn(17, 10), exempt_admin=False).status("admin", is_admin=True).exceeded


def test_zero_budget_means_unlimited():
    ledger = InMemoryChatUsageLedger()
    spend(ledger, "an", 10**9, at_vn(17, 9))
    status = policy(ledger, at_vn(17, 10), daily_tokens=0).status("an")
    assert status.unlimited and not status.exceeded


def test_other_users_do_not_count():
    ledger = InMemoryChatUsageLedger()
    spend(ledger, "binh", 5000, at_vn(17, 9))
    assert policy(ledger, at_vn(17, 10)).status("an").used == 0


@pytest.mark.parametrize(("used", "warned"), [(799, False), (800, True), (850, True)])
def test_warning_from_the_ratio(used, warned):
    ledger = InMemoryChatUsageLedger()
    spend(ledger, "an", used, at_vn(17, 9))
    warning = policy(ledger, at_vn(17, 10)).warning("an")
    if warned:
        assert warning == {"used_ratio": used / 1000, "reset_at": "2026-09-18T00:00:00+07:00"}
    else:
        assert warning is None


# ── Cảnh báo cấu hình (4.7) ──


def test_in_memory_ledger_with_budget_logs_once(caplog):
    with caplog.at_level(logging.WARNING, logger="dms-chat"):
        assert warn_if_ledger_not_persistent(
            InMemoryChatUsageLedger(), BudgetConfig(daily_tokens=200000)
        )
    assert [r.message for r in caplog.records].count("chat_usage_ledger_not_persistent") == 1


def test_no_warning_when_budget_disabled(caplog):
    with caplog.at_level(logging.WARNING, logger="dms-chat"):
        assert not warn_if_ledger_not_persistent(
            InMemoryChatUsageLedger(), BudgetConfig(daily_tokens=0)
        )
    assert not caplog.records
