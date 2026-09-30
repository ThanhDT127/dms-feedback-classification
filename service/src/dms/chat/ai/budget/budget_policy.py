"""Hạn mức token theo user theo ngày (spec ``chat-token-budget``, design b11 D9).

Giới hạn **mềm**: chỉ kiểm trước khi nhận câu hỏi, không cắt câu trả lời đang chạy.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from typing import Any

from ..date_resolver import DEFAULT_TIMEZONE, resolve_timezone
from .usage_ledger import ChatUsageLedger


@dataclass(frozen=True)
class BudgetConfig:
    daily_tokens: int = 200000  # 0 = không giới hạn
    exempt_admin: bool = True
    warning_ratio: float = 0.8
    timezone: str = DEFAULT_TIMEZONE

    @classmethod
    def from_settings(cls, settings: Any) -> BudgetConfig:
        return cls(
            daily_tokens=int(settings.chat_user_daily_token_budget),
            exempt_admin=bool(settings.chat_budget_exempt_admin),
            warning_ratio=float(settings.chat_budget_warning_ratio),
            timezone=str(settings.chat_timezone),
        )

    @property
    def enabled(self) -> bool:
        return self.daily_tokens > 0


@dataclass(frozen=True)
class BudgetStatus:
    used: int
    limit: int
    reset_at: str  # ISO, 00:00 ngày mai theo giờ Việt Nam
    exempt: bool = False

    @property
    def unlimited(self) -> bool:
        return self.exempt or self.limit <= 0

    @property
    def exceeded(self) -> bool:
        return not self.unlimited and self.used >= self.limit

    @property
    def used_ratio(self) -> float:
        if self.unlimited:
            return 0.0
        return round(self.used / self.limit, 4)


class BudgetPolicy:
    def __init__(
        self,
        ledger: ChatUsageLedger,
        *,
        config: BudgetConfig | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.ledger = ledger
        self.config = config or BudgetConfig()
        self._clock = clock or (lambda: datetime.now(UTC))
        self._tz = resolve_timezone(self.config.timezone)

    def day_window(self, now: datetime | None = None) -> tuple[datetime, datetime]:
        """``[00:00 hôm nay, 00:00 ngày mai)`` theo ``CHAT_TIMEZONE``, trả về giờ UTC."""
        moment = (now or self._clock()).astimezone(self._tz)
        start_local = datetime.combine(moment.date(), time.min, tzinfo=self._tz)
        end_local = start_local + timedelta(days=1)
        return start_local.astimezone(UTC), end_local.astimezone(UTC)

    def status(self, username: str, *, is_admin: bool = False) -> BudgetStatus:
        now = self._clock()
        start, end = self.day_window(now)
        reset_at = end.astimezone(self._tz).isoformat()
        exempt = is_admin and self.config.exempt_admin
        if not self.config.enabled or exempt:
            return BudgetStatus(
                used=0, limit=self.config.daily_tokens, reset_at=reset_at, exempt=True
            )
        used = self.ledger.total_tokens(username, start, now + timedelta(microseconds=1))
        return BudgetStatus(used=used, limit=self.config.daily_tokens, reset_at=reset_at)

    def warning(self, username: str, *, is_admin: bool = False) -> dict[str, Any] | None:
        """``budget_warning`` cho ``done`` khi đã dùng từ ngưỡng cảnh báo trở lên."""
        status = self.status(username, is_admin=is_admin)
        if status.unlimited or status.used_ratio < self.config.warning_ratio:
            return None
        return {"used_ratio": status.used_ratio, "reset_at": status.reset_at}


__all__ = ["BudgetConfig", "BudgetPolicy", "BudgetStatus"]
