"""Timezone-aware datetime helpers."""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, time
from pathlib import Path


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
        datetime.combine(date.fromisoformat(date_to), time.max, UTC) if date_to else fallback_end
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


def extract_date_from_filename(
    filename: str | Path | None,
    reference_date: str | datetime | date | None = None,
) -> str | None:
    """Extract a validated business reporting date from a supported filename."""
    if not filename:
        return None
    stem = Path(filename).stem

    def valid_date(year: int, month: int, day: int) -> date | None:
        try:
            return date(year, month, day)
        except ValueError:
            return None

    # DMS/DMST names are a strict contract. Once a leading marker is present,
    # malformed ranges must not fall through to a more permissive date pattern.
    if re.match(r"(?i)^DMST", stem):
        match = re.fullmatch(
            r"(?i)DMST(\d{2})(\d{2})[-_](\d{1,2})(?:[-_](\d{1,2}))?",
            stem,
        )
        if not match:
            return None
        month = int(match.group(1))
        year = 2000 + int(match.group(2))
        start_day = int(match.group(3))
        end_day = int(match.group(4) or match.group(3))
        start = valid_date(year, month, start_day)
        end = valid_date(year, month, end_day)
        return end.isoformat() if start and end and start <= end else None

    if re.match(r"(?i)^DMS(?=[-_])", stem):
        full_day = re.fullmatch(r"(?i)DMS[-_](\d{2})(\d{2})(20\d{2})", stem)
        if full_day:
            parsed = valid_date(
                int(full_day.group(3)), int(full_day.group(2)), int(full_day.group(1))
            )
            return parsed.isoformat() if parsed else None

        full_range = re.fullmatch(
            r"(?i)DMS[-_](\d{2})(\d{2})[-_](\d{2})(\d{2})(20\d{2})",
            stem,
        )
        if full_range:
            year = int(full_range.group(5))
            start = valid_date(year, int(full_range.group(2)), int(full_range.group(1)))
            end = valid_date(year, int(full_range.group(4)), int(full_range.group(3)))
            return end.isoformat() if start and end and start <= end else None

        separated = re.fullmatch(r"(?i)DMS[-_](\d{2})[-_.](\d{2})[-_.](20\d{2})", stem)
        if separated:
            parsed = valid_date(
                int(separated.group(3)), int(separated.group(2)), int(separated.group(1))
            )
            return parsed.isoformat() if parsed else None

        yearless_range = re.fullmatch(r"(?i)DMS[-_](\d{2})(\d{2})[-_](\d{2})(\d{2})", stem)
        if yearless_range and reference_date:
            year_match = re.search(r"(20\d{2})", str(reference_date).strip())
            if year_match:
                year = int(year_match.group(1))
                start = valid_date(year, int(yearless_range.group(2)), int(yearless_range.group(1)))
                end = valid_date(year, int(yearless_range.group(4)), int(yearless_range.group(3)))
                return end.isoformat() if start and end and start <= end else None
        return None

    # Non-DMS filenames may contain an explicit full date.
    patterns = (
        (r"(?:^|[^\d])(\d{2})[-_.](\d{2})[-_.](20\d{2})(?:[^\d]|$)", (3, 2, 1)),
        (r"(?:^|[^\d])(20\d{2})[-_.](\d{2})[-_.](\d{2})(?:[^\d]|$)", (1, 2, 3)),
        (r"(?:^|[^\d])(20\d{2})(\d{2})(\d{2})(?:[^\d]|$)", (1, 2, 3)),
    )
    for pattern, order in patterns:
        for match in re.finditer(pattern, stem):
            parsed = valid_date(*(int(match.group(index)) for index in order))
            if parsed:
                return parsed.isoformat()
    return None


def resolve_file_reporting_date(
    filename: str | None,
    source_modified_at: str | None,
    completed_at: str | None,
    owner_username: str | None = None,
) -> str:
    """Resolve the business reporting date for a classification job.

    Priority:
    1. Reporting date embedded in filename (e.g. DMS-13102025, DMST0826-28-31)
    2. For watcher jobs: SharePoint modification date (source_modified_at), fallback completed_at
    3. For web uploads: Job completion audit timestamp (completed_at)
    """
    ref = source_modified_at or completed_at or ""
    if filename:
        parsed = extract_date_from_filename(filename, reference_date=ref)
        if parsed:
            return parsed

    if owner_username == "system_watcher":
        if source_modified_at and source_modified_at.strip():
            return source_modified_at.strip()
        return completed_at or ""

    return completed_at or ""
