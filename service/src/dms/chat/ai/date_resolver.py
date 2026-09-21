"""Giải mốc thời gian tiếng Việt thành khoảng ngày (design b01 D8).

Python thuần, không gọi LLM (nguyên tắc Tool-First). Neo theo ngày hiện tại ở múi giờ
``CHAT_TIMEZONE``; tuần bắt đầu thứ Hai; kỳ đang diễn ra bị giới hạn ở hôm nay.
"""

from __future__ import annotations

import calendar
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .text_match import normalize_match_text
from .types import DateRange, QueryIssue

DEFAULT_TIMEZONE = "Asia/Ho_Chi_Minh"
VAGUE_DAYS = 30
# Việt Nam không có giờ mùa hè nên offset cố định là dự phòng an toàn khi thiếu tzdata.
_VN_FALLBACK_TZ = timezone(timedelta(hours=7), name="UTC+07:00")

_ROMAN = {"i": 1, "ii": 2, "iii": 3, "iv": 4}
_UNIT_MONTHS = {"month": 1, "quarter": 3, "half": 6, "year": 12}
_SHIFT_WORDS = {
    "thang truoc": "month",
    "thang roi": "month",
    "nam truoc": "year",
    "nam ngoai": "year",
    "nam roi": "year",
    "quy truoc": "quarter",
    "tuan truoc": "week",
    "hom qua": "day",
}
_VAGUE_LABELS = {
    "thoi gian gan day": "thời gian gần đây",
    "thoi gian qua": "thời gian qua",
    "gan day": "gần đây",
    "dao nay": "dạo này",
    "moi day": "mới đây",
}


def resolve_timezone(name: str) -> tzinfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return _VN_FALLBACK_TZ


@dataclass(frozen=True)
class DateResolution:
    date_range: DateRange | None = None
    compare_range: DateRange | None = None
    assumptions: tuple[str, ...] = ()
    issues: tuple[QueryIssue, ...] = ()


@dataclass(frozen=True)
class _Period:
    start: date
    end: date
    label: str = ""
    assumption: str | None = None


class _InvalidDate(Exception):
    """Cụm trông giống ngày nhưng không tồn tại; không đoán."""


# ── Tiện ích ngày ──


def _fmt(d: date) -> str:
    return d.strftime("%d/%m/%Y")


def _make_date(year: int, month: int, day: int) -> date:
    try:
        return date(year, month, day)
    except ValueError as exc:
        raise _InvalidDate from exc


def _month_end(year: int, month: int) -> date:
    return date(year, month, calendar.monthrange(year, month)[1])


def _is_month_end(d: date) -> bool:
    return d.day == calendar.monthrange(d.year, d.month)[1]


def _add_months(d: date, months: int, *, keep_month_end: bool = False) -> date:
    year, month_index = divmod(d.year * 12 + d.month - 1 + months, 12)
    month = month_index + 1
    last = calendar.monthrange(year, month)[1]
    return date(year, month, last if keep_month_end else min(d.day, last))


def _cap(start: date, end: date, today: date) -> date:
    """Kỳ đang diễn ra kết thúc ở hôm nay."""
    return min(end, today) if start <= today else end


def _year(text: str | None) -> int | None:
    if not text:
        return None
    value = int(text)
    return 2000 + value if value < 100 else value


def _span(
    start: date, end: date, today: date, *, label: str = "", assumption: str | None = None
) -> _Period:
    return _Period(start=start, end=_cap(start, end, today), label=label, assumption=assumption)


# ── Handler cho từng mẫu ──

Handler = Callable[[re.Match[str], date], "_Period | None"]


def _range_days(m: re.Match[str], today: date) -> _Period:
    year_end = _year(m.group("y2"))
    year_start = _year(m.group("y1")) or year_end
    explicit = year_start is not None
    base = year_start if year_start is not None else today.year
    start = _make_date(base, int(m.group("m1")), int(m.group("d1")))
    end = _make_date(
        year_end if year_end is not None else base, int(m.group("m2")), int(m.group("d2"))
    )
    if year_end is None and end < start:
        end = _make_date(end.year + 1, end.month, end.day)
    assumption = None
    if not explicit and start > today:
        start = _make_date(start.year - 1, start.month, start.day)
        end = _make_date(end.year - 1, end.month, end.day)
        assumption = (
            f"Hiểu khoảng ngày là {_fmt(start)}–{_fmt(end)} vì khoảng này của năm "
            f"{today.year} chưa tới."
        )
    if start > end:
        raise _InvalidDate
    return _span(start, end, today, assumption=assumption)


def _range_months(m: re.Match[str], today: date) -> _Period:
    month_start, month_end = int(m.group("m1")), int(m.group("m2"))
    if not (1 <= month_start <= 12 and 1 <= month_end <= 12):
        raise _InvalidDate
    year_end = _year(m.group("y2") or m.group("y3"))
    year_start = _year(m.group("y1")) or year_end
    explicit = year_start is not None
    ys = year_start if year_start is not None else today.year
    ye = year_end if year_end is not None else ys
    start = date(ys, month_start, 1)
    end = _month_end(ye, month_end)
    if year_end is None and end < start:
        end = _month_end(ye + 1, month_end)
    assumption = None
    if not explicit and start > today:
        start = date(start.year - 1, start.month, 1)
        end = _month_end(end.year - 1, end.month)
        assumption = (
            f"Hiểu khoảng tháng là {_fmt(start)}–{_fmt(end)} vì khoảng này của năm "
            f"{today.year} chưa tới."
        )
    if start > end:
        raise _InvalidDate
    return _span(start, end, today, assumption=assumption)


def _full_date(m: re.Match[str], today: date) -> _Period:
    d = _make_date(int(m.group("y")), int(m.group("m")), int(m.group("d")))
    return _Period(start=d, end=d, label=_fmt(d))


def _day_month(m: re.Match[str], today: date) -> _Period | None:
    day, month = int(m.group("d")), int(m.group("m"))
    year_text = m.groupdict().get("y")
    if not year_text and not (1 <= month <= 12 and 1 <= day <= 31):
        return None  # "15/30" không phải ngày
    if year_text:
        d = _make_date(int(year_text), month, day)
        return _Period(start=d, end=d, label=_fmt(d))
    d = _make_date(today.year, month, day)
    assumption = None
    if d > today:
        d = _make_date(today.year - 1, month, day)
        assumption = (
            f"Hiểu ngày {day:02d}/{month:02d} là {_fmt(d)} vì ngày này năm {today.year} chưa tới."
        )
    return _Period(start=d, end=d, label=_fmt(d), assumption=assumption)


def _month(m: re.Match[str], today: date) -> _Period | None:
    month = int(m.group("m"))
    if not 1 <= month <= 12:
        return None
    year_text = m.groupdict().get("y")
    assumption = None
    if year_text:
        year = int(year_text)
    else:
        year = today.year
        if month > today.month:
            year -= 1
            assumption = (
                f"Hiểu “tháng {month}” là tháng {month}/{year} vì tháng {month}/{today.year} "
                "chưa tới."
            )
    start = date(year, month, 1)
    return _span(
        start, _month_end(year, month), today, label=f"tháng {month}/{year}", assumption=assumption
    )


def _quarter(m: re.Match[str], today: date) -> _Period:
    raw = m.group("q")
    quarter = _ROMAN[raw] if raw in _ROMAN else int(raw)
    first_month = 3 * (quarter - 1) + 1
    year_text = m.groupdict().get("y")
    assumption = None
    if year_text:
        year = int(year_text)
    else:
        year = today.year
        if date(year, first_month, 1) > today:
            year -= 1
            assumption = (
                f"Hiểu “quý {quarter}” là quý {quarter}/{year} vì quý {quarter}/{today.year} "
                "chưa tới."
            )
    start = date(year, first_month, 1)
    end = _month_end(year, first_month + 2)
    return _span(start, end, today, label=f"quý {quarter}/{year}", assumption=assumption)


def _half_year(m: re.Match[str], today: date) -> _Period:
    first = m.group("h") == "dau"
    first_month = 1 if first else 7
    year_text = m.group("y")
    assumption = None
    name = "6 tháng đầu năm" if first else "6 tháng cuối năm"
    if year_text:
        year = int(year_text)
    else:
        year = today.year
        if date(year, first_month, 1) > today:
            year -= 1
            assumption = f"Hiểu “{name}” là {name} {year} vì kỳ này của năm {today.year} chưa tới."
    start = date(year, first_month, 1)
    end = _month_end(year, first_month + 5)
    return _span(start, end, today, label=f"{name} {year}", assumption=assumption)


def _last_n(m: re.Match[str], today: date) -> _Period | None:
    n = int(m.group("n"))
    if n <= 0:
        return None
    unit = m.group("u")
    if unit == "ngay":
        start = today - timedelta(days=n - 1)
        label = f"{n} ngày gần nhất"
    elif unit == "tuan":
        start = today - timedelta(days=7 * n - 1)
        label = f"{n} tuần gần nhất"
    elif unit == "thang":
        start = _add_months(today, -n) + timedelta(days=1)
        label = f"{n} tháng gần nhất"
    else:
        start = _add_months(today, -12 * n) + timedelta(days=1)
        label = f"{n} năm gần nhất"
    return _Period(start=start, end=today, label=label)


def _week_relative(m: re.Match[str], today: date) -> _Period:
    word = m.group("w")
    monday = today - timedelta(days=today.weekday())
    if word == "nay":
        return _Period(start=monday, end=today, label="tuần này")
    if word == "qua":
        return _Period(start=today - timedelta(days=6), end=today, label="7 ngày qua")
    return _Period(
        start=monday - timedelta(days=7), end=monday - timedelta(days=1), label="tuần trước"
    )


def _day_relative(m: re.Match[str], today: date) -> _Period:
    offset = {"nay": 0, "qua": 1, "kia": 2}[m.group("w")]
    d = today - timedelta(days=offset)
    return _Period(start=d, end=d, label=_fmt(d))


def _month_relative(m: re.Match[str], today: date) -> _Period:
    if m.group("w") == "nay":
        return _Period(start=today.replace(day=1), end=today, label="tháng này")
    start = _add_months(today.replace(day=1), -1)
    return _Period(start=start, end=_month_end(start.year, start.month), label="tháng trước")


def _quarter_relative(m: re.Match[str], today: date) -> _Period:
    current_first = date(today.year, 3 * ((today.month - 1) // 3) + 1, 1)
    if m.group("w") == "nay":
        return _Period(start=current_first, end=today, label="quý này")
    start = _add_months(current_first, -3)
    end = _month_end(start.year, start.month + 2)
    return _Period(start=start, end=end, label="quý trước")


def _year_relative(m: re.Match[str], today: date) -> _Period:
    if m.group("w") == "nay":
        return _Period(start=date(today.year, 1, 1), end=today, label=f"năm {today.year}")
    year = today.year - 1
    return _Period(start=date(year, 1, 1), end=date(year, 12, 31), label=f"năm {year}")


def _year_absolute(m: re.Match[str], today: date) -> _Period:
    year = int(m.group("y"))
    return _span(date(year, 1, 1), date(year, 12, 31), today, label=f"năm {year}")


def _vague(m: re.Match[str], today: date) -> _Period:
    start = today - timedelta(days=VAGUE_DAYS - 1)
    phrase = _VAGUE_LABELS.get(m.group(0), m.group(0))
    return _Period(
        start=start,
        end=today,
        label=f"{VAGUE_DAYS} ngày gần nhất",
        assumption=(
            f"Hiểu “{phrase}” là {VAGUE_DAYS} ngày gần nhất ({_fmt(start)}–{_fmt(today)})."
        ),
    )


# ── Bảng mẫu, xếp theo độ cụ thể giảm dần ──

_D = r"(?P<{d}>\d{{1,2}})[/-](?P<{m}>\d{{1,2}})(?:[/-](?P<{y}>\d{{2,4}}))?"
_Q_TAIL = r"(?:(?:[/ -]| nam )(?P<y>\d{4})\b)?"

_SAME_PERIOD = re.compile(
    r"\bcung ky(?: (?P<u>thang truoc|thang roi|nam truoc|nam ngoai|nam roi|quy truoc|tuan truoc|hom qua))?\b"
)

_RULES: tuple[tuple[re.Pattern[str], Handler], ...] = (
    (
        re.compile(
            r"\btu (?:ngay )?"
            + _D.format(d="d1", m="m1", y="y1")
            + r" ?(?:den|toi|->|-|~) ?(?:ngay )?"
            + _D.format(d="d2", m="m2", y="y2")
            + r"\b"
        ),
        _range_days,
    ),
    (
        re.compile(
            r"\btu thang (?P<m1>\d{1,2})(?:[/-](?P<y1>\d{4}))? (?:den|toi) thang "
            r"(?P<m2>\d{1,2})(?:[/-](?P<y2>\d{4}))?(?: nam (?P<y3>\d{4}))?\b"
        ),
        _range_months,
    ),
    (re.compile(r"\b(?P<d>\d{1,2})[/-](?P<m>\d{1,2})[/-](?P<y>\d{4})\b"), _full_date),
    (re.compile(r"\bngay (?P<d>\d{1,2}) thang (?P<m>\d{1,2})(?: nam (?P<y>\d{4}))?\b"), _day_month),
    (re.compile(r"\b(?:thang|th|t) ?(?P<m>\d{1,2})[/-](?P<y>\d{4})\b"), _month),
    (re.compile(r"\bthang (?P<m>\d{1,2}) nam (?P<y>\d{4})\b"), _month),
    (re.compile(r"\b(?:6 thang|nua) (?P<h>dau|cuoi) nam(?: (?P<y>\d{4}))?\b"), _half_year),
    (re.compile(r"\bquy (?P<q>[1-4]|iv|iii|ii|i)\b" + _Q_TAIL), _quarter),
    (re.compile(r"\bq(?P<q>[1-4])\b" + _Q_TAIL), _quarter),
    (
        re.compile(
            r"\b(?P<n>\d{1,3}) (?P<u>ngay|tuan|thang|nam) "
            r"(?:gan nhat|gan day|vua qua|vua roi|tro lai day|qua|truoc)\b"
        ),
        _last_n,
    ),
    (re.compile(r"\btuan (?P<w>nay|vua qua|vua roi|truoc|roi|qua)\b"), _week_relative),
    (re.compile(r"\bhom (?P<w>nay|qua|kia)\b"), _day_relative),
    (re.compile(r"\bthang (?P<w>nay|vua qua|vua roi|truoc|roi|qua)\b"), _month_relative),
    (re.compile(r"\bquy (?P<w>nay|vua qua|vua roi|truoc|roi)\b"), _quarter_relative),
    (re.compile(r"\bnam (?P<w>nay|ngoai|vua qua|vua roi|truoc|roi|qua)\b"), _year_relative),
    (re.compile(r"\bnam (?P<y>\d{4})\b"), _year_absolute),
    (re.compile(r"(?<!\d )\bthang (?P<m>\d{1,2})\b(?![/-]\d)"), _month),
    (re.compile(r"\b(?:th|t)(?P<m>\d{1,2})\b(?![/-]\d)"), _month),
    (re.compile(r"(?<!vung )(?<![\d/-])\b(?P<d>\d{1,2})/(?P<m>\d{1,2})\b(?![/-]\d)"), _day_month),
    (
        re.compile(r"\b(?:thoi gian gan day|thoi gian qua|gan day|dao nay|moi day)\b"),
        _vague,
    ),
)


def _overlaps(span: tuple[int, int], occupied: list[tuple[int, int]]) -> bool:
    return any(span[0] < end and start < span[1] for start, end in occupied)


def _shift(period: _Period, unit: str) -> _Period:
    if unit == "day":
        delta = timedelta(days=1)
    elif unit == "week":
        delta = timedelta(days=7)
    else:
        months = _UNIT_MONTHS[unit]
        return _Period(
            start=_add_months(period.start, -months),
            end=_add_months(period.end, -months, keep_month_end=_is_month_end(period.end)),
        )
    return _Period(start=period.start - delta, end=period.end - delta)


def _to_range(period: _Period) -> DateRange:
    label = period.label or f"{_fmt(period.start)}–{_fmt(period.end)}"
    return DateRange(date_from=period.start, date_to=period.end, label=label)


# ── Khoảng mặc định của báo cáo (b10 D2) ──


def yesterday(today: date) -> DateRange:
    """Khoảng mặc định của "báo cáo ngày": hôm qua."""
    day = today - timedelta(days=1)
    return DateRange(date_from=day, date_to=day, label=_fmt(day))


def last_full_week(today: date) -> DateRange:
    """Khoảng mặc định của "báo cáo tuần": tuần trước, thứ Hai tới Chủ nhật."""
    monday_this_week = today - timedelta(days=today.weekday())
    start = monday_this_week - timedelta(days=7)
    end = start + timedelta(days=6)
    return DateRange(date_from=start, date_to=end, label=f"{_fmt(start)}–{_fmt(end)}")


def previous_period(current: DateRange) -> DateRange:
    """Kỳ so sánh: cùng độ dài, liền ngay trước kỳ đang xem."""
    length = (current.date_to - current.date_from).days + 1
    end = current.date_from - timedelta(days=1)
    start = end - timedelta(days=length - 1)
    return DateRange(date_from=start, date_to=end, label=f"{_fmt(start)}–{_fmt(end)}")


class DateResolver:
    def __init__(
        self,
        clock: Callable[[], datetime] | None = None,
        tz_name: str = DEFAULT_TIMEZONE,
    ) -> None:
        self._clock = clock or (lambda: datetime.now(UTC))
        self.tz = resolve_timezone(tz_name)

    def today(self) -> date:
        now = self._clock()
        if now.tzinfo is None:
            now = now.replace(tzinfo=UTC)
        return now.astimezone(self.tz).date()

    def resolve(self, text: str) -> DateResolution:
        today = self.today()
        normalized = normalize_match_text(text)
        occupied: list[tuple[int, int]] = []
        found: list[tuple[int, _Period]] = []
        invalid = False

        shift_unit: str | None = None
        shift_explicit = False
        same_period = _SAME_PERIOD.search(normalized)
        if same_period:
            occupied.append(same_period.span())
            shift_explicit = bool(same_period.group("u"))
            shift_unit = _SHIFT_WORDS.get(same_period.group("u") or "", "year")

        for pattern, handler in _RULES:
            for match in pattern.finditer(normalized):
                if _overlaps(match.span(), occupied):
                    continue
                occupied.append(match.span())
                try:
                    period = handler(match, today)
                except _InvalidDate:
                    invalid = True
                    continue
                if period is not None:
                    found.append((match.start(), period))

        if invalid:
            return DateResolution(issues=(QueryIssue.INVALID_DATE,))
        if not found:
            return DateResolution()

        periods = [period for _, period in sorted(found, key=lambda item: item[0])]
        primary = periods[0]
        compare: _Period | None = None
        assumptions = [p.assumption for p in periods[:2] if p.assumption]
        if shift_unit is not None:
            compare = _shift(primary, shift_unit)
            if not shift_explicit:
                assumptions.append("Hiểu “cùng kỳ” là cùng kỳ năm trước.")
        elif len(periods) >= 2:
            compare = periods[1]
            if len(periods) > 2:
                assumptions.append("Chỉ dùng hai mốc thời gian đầu tiên trong câu hỏi.")

        return DateResolution(
            date_range=_to_range(primary),
            compare_range=_to_range(compare) if compare else None,
            assumptions=tuple(assumptions),
        )
