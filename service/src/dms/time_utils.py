"""Timezone-aware datetime helpers."""

from __future__ import annotations

from datetime import UTC, date, datetime, time


def utc_now() -> datetime:
    """Return the current timezone-aware UTC datetime."""
    return datetime.now(UTC)


def utc_now_iso(*, timespec: str = "seconds") -> str:
    """Return the current UTC datetime as ISO-8601 text with timezone suffix."""
    return utc_now().isoformat(timespec=timespec)


def utc_from_timestamp(timestamp: float) -> datetime:
    """Convert a POSIX timestamp to a timezone-aware UTC datetime."""
    return datetime.fromtimestamp(timestamp, UTC)


def parse_utc_datetime(value: str) -> datetime:
    """Parse legacy naive or aware ISO text and normalize it to UTC."""
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def utc_day_bounds_iso(
    *,
    date_from: str | None = None,
    date_to: str | None = None,
    fallback_start: datetime | None = None,
    fallback_end: datetime | None = None,
) -> tuple[str, str]:
    """Return UTC-aware ISO bounds for optional YYYY-MM-DD inputs."""
    start_dt = (
        datetime.combine(date.fromisoformat(date_from), time.min, UTC)
        if date_from
        else fallback_start
    )
    end_dt = (
        datetime.combine(date.fromisoformat(date_to), time.max, UTC)
        if date_to
        else fallback_end
    )
    if start_dt is None or end_dt is None:
        raise ValueError("Both start and end bounds are required")
    if start_dt.tzinfo is None:
        start_dt = start_dt.replace(tzinfo=UTC)
    if end_dt.tzinfo is None:
        end_dt = end_dt.replace(tzinfo=UTC)
    return (
        start_dt.astimezone(UTC).isoformat(timespec="seconds"),
        end_dt.astimezone(UTC).isoformat(timespec="seconds"),
    )
