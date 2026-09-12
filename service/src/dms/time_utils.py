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
    filename: str | Path,
    reference_date: str | datetime | date | None = None,
) -> str | None:
    """Extract business reporting date (YYYY-MM-DD) from a DMS filename.

    Supports:
    - Batch periods: DMSTMMYY-DD-DD or DMSTMMYY-DD (e.g. DMST0826-28-31 -> 2026-08-31)
    - Full standard dates: DMS-DDMMYYYY or DMS_DDMMYYYY (e.g. DMS-13102025 -> 2025-10-13)
    - Date ranges: DMS-DDMM-DDMMYYYY (e.g. DMS-1510-17102025 -> 2025-10-17)
    - Separated dates: DD-MM-YYYY or DD_MM_YYYY or DD.MM.YYYY
    - ISO dates: YYYY-MM-DD or YYYY_MM_DD
    - Compact ISO: YYYYMMDD
    - Year-less date ranges with reference_date: DMS-1510-1710.xlsx
    """
    if not filename:
        return None
    stem = Path(filename).stem

    # 1. Batch periods: DMSTMMYY-DD-DD or DMSTMMYY-DD
    # e.g. DMST0826-28-31 -> month=8, year=2026, day=31
    m_dmst = re.search(
        r"(?i)DMST(\d{2})(\d{2})[-_](?:(\d{1,2})[-_])?(\d{1,2})(?:[^\d]|$)",
        stem,
    )
    if m_dmst:
        month = int(m_dmst.group(1))
        year = 2000 + int(m_dmst.group(2))
        day = int(m_dmst.group(4))
        try:
            return date(year, month, day).isoformat()
        except ValueError:
            pass

    # 2. DMS-DDMM-DDMMYYYY or DMS-DDMMYYYY
    # e.g. DMS-13102025 -> day=13, month=10, year=2025
    # e.g. DMS-1510-17102025 -> day=17, month=10, year=2025
    m_dms_full = re.search(
        r"(?i)DMS[-_](?:(?:\d{2})(?:\d{2})[-_])?(\d{2})(\d{2})(20\d{2})(?:[^\d]|$)",
        stem,
    )
    if m_dms_full:
        day = int(m_dms_full.group(1))
        month = int(m_dms_full.group(2))
        year = int(m_dms_full.group(3))
        try:
            return date(year, month, day).isoformat()
        except ValueError:
            pass

    # 3. Separated dates: DD-MM-YYYY or DD_MM_YYYY
    m_separated = re.search(
        r"(?i)(?:^|[^\d])(\d{2})[-_.](\d{2})[-_.](20\d{2})(?:[^\d]|$)",
        stem,
    )
    if m_separated:
        day = int(m_separated.group(1))
        month = int(m_separated.group(2))
        year = int(m_separated.group(3))
        try:
            return date(year, month, day).isoformat()
        except ValueError:
            pass

    # 4. ISO format: YYYY-MM-DD or YYYY_MM_DD
    m_iso = re.search(
        r"(?i)(?:^|[^\d])(20\d{2})[-_.](\d{2})[-_.](\d{2})(?:[^\d]|$)",
        stem,
    )
    if m_iso:
        year = int(m_iso.group(1))
        month = int(m_iso.group(2))
        day = int(m_iso.group(3))
        try:
            return date(year, month, day).isoformat()
        except ValueError:
            pass

    # 5. Compact ISO: YYYYMMDD
    m_compact = re.search(
        r"(?i)(?:^|[^\d])(20\d{2})(\d{2})(\d{2})(?:[^\d]|$)",
        stem,
    )
    if m_compact:
        year = int(m_compact.group(1))
        month = int(m_compact.group(2))
        day = int(m_compact.group(3))
        try:
            return date(year, month, day).isoformat()
        except ValueError:
            pass

    # 6. Year-less range with reference_date: e.g. DMS-1510-1710.xlsx
    if reference_date:
        ref_str = str(reference_date).strip()
        ref_year_match = re.search(r"(20\d{2})", ref_str)
        if ref_year_match:
            ref_year = int(ref_year_match.group(1))
            m_yearless = re.search(
                r"(?i)DMS[-_](?:(?:\d{2})(?:\d{2})[-_])?(\d{2})(\d{2})(?:[^\d]|$)",
                stem,
            )
            if m_yearless:
                day = int(m_yearless.group(1))
                month = int(m_yearless.group(2))
                try:
                    return date(ref_year, month, day).isoformat()
                except ValueError:
                    pass

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
