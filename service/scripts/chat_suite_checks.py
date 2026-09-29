"""Kết quả một lượt và các phép kiểm cho bộ câu hỏi chatbot (``run_chat_suite.py``).

Mỗi phép kiểm nhận ``TurnResult`` và trả ``CheckResult``: ``ok=True/False`` là đạt/trượt,
``ok=None`` chỉ ghi lại giá trị quan sát được để người đọc báo cáo tự đánh giá.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from typing import Any


@dataclass
class TurnResult:
    question: str
    events: list[dict[str, Any]]
    outcome: Any | None = None  # ``TurnOutcome`` của lượt, None khi lượt hỏng trước orchestrator
    runner_error: str | None = None
    total_ms: int = 0
    session_id: str = ""
    history_summary_chars: int = 0
    logs: list[str] = field(default_factory=list)  # log ghi trong lúc chạy lượt này

    # ── Truy cập event ──

    def of_type(self, event_type: str) -> list[dict[str, Any]]:
        return [e.get("data") or {} for e in self.events if e.get("type") == event_type]

    @property
    def done(self) -> dict[str, Any]:
        items = self.of_type("done")
        return items[-1] if items else {}

    @property
    def status(self) -> str:
        return str(self.done.get("status") or "missing")

    @property
    def blocks(self) -> list[dict[str, Any]]:
        return self.of_type("data_block")

    def blocks_of(self, kind: str) -> list[dict[str, Any]]:
        return [b for b in self.blocks if b.get("kind") == kind]

    @property
    def texts(self) -> list[str]:
        """Mọi câu chữ người dùng đọc được ngoài khối số liệu."""
        out: list[str] = []
        for kind in ("clarify", "refusal", "commentary", "error"):
            out.extend(str(d.get("text") or "") for d in self.of_type(kind))
        for data in self.of_type("clarify"):
            out.extend(str(o) for o in data.get("options") or [])
        return [t for t in out if t]

    @property
    def commentary(self) -> list[str]:
        return [str(d.get("text") or "") for d in self.of_type("commentary")]

    # ── Truy cập outcome ──

    @property
    def intents(self) -> set[str]:
        """Intent của Planner và loại báo cáo (xuất Excel một báo cáo mang cả hai)."""
        outcome = self.outcome
        values = (getattr(outcome, "intent", None), getattr(outcome, "report_type", None))
        return {str(getattr(v, "value", v)) for v in values if v is not None}

    @property
    def intent(self) -> str | None:
        outcome = self.outcome
        if outcome is None:
            return None
        value = getattr(outcome, "report_type", None) or getattr(outcome, "intent", None)
        return getattr(value, "value", value)

    @property
    def reasons(self) -> set[str]:
        """Mã lý do từ outcome, Input Guard, notice và các event refusal/error."""
        found: set[str] = set()
        outcome = self.outcome
        if outcome is not None:
            for value in (
                getattr(outcome, "reason", None),
                getattr(getattr(outcome, "guard", None), "reason_code", None),
            ):
                if value is not None:
                    found.add(str(getattr(value, "value", value)))
            for notice in getattr(outcome, "notices", ()) or ():
                found.add(str(getattr(notice.kind, "value", notice.kind)))
            plan = getattr(outcome, "validated_plan", None)
            if plan is not None and getattr(plan, "reason", None) is not None:
                found.add(str(getattr(plan.reason, "value", plan.reason)))
        for data in self.of_type("refusal") + self.of_type("error"):
            code = data.get("reason") or data.get("code")
            if code:
                found.add(str(code))
        return found

    @property
    def functions(self) -> list[str]:
        outcome = self.outcome
        if outcome is None:
            return []
        return [str(s.function_name) for s in outcome.step_results if s.function_name]

    @property
    def patterns(self) -> list[str]:
        plan = getattr(self.outcome, "validated_plan", None)
        steps = getattr(plan, "steps", ()) or ()
        return [str(getattr(s.pattern, "value", s.pattern)) for s in steps]

    @property
    def sql(self) -> list[str]:
        outcome = self.outcome
        if outcome is None:
            return []
        return [s.sql for s in outcome.step_results if getattr(s, "sql", None)]

    def brief(self) -> dict[str, Any]:
        """Tóm tắt để in ra báo cáo."""
        done = self.done
        return {
            "question": self.question,
            "status": self.status,
            "intent": self.intent,
            "reasons": sorted(self.reasons),
            "functions": self.functions,
            "patterns": self.patterns,
            "commentary_status": done.get("commentary_status"),
            "dropped_sentences": done.get("dropped_sentences"),
            "total_ms": self.total_ms,
            "blocks": [
                {"kind": b.get("kind"), "title": b.get("title"), "subtitle": b.get("subtitle")}
                for b in self.blocks
            ],
            "texts": self.texts[:6],
            "sql": self.sql,
            "runner_error": self.runner_error,
        }


@dataclass(frozen=True)
class CheckResult:
    label: str
    ok: bool | None
    detail: str = ""


Check = Callable[[TurnResult], CheckResult]


@dataclass
class TurnSpec:
    question: str
    checks: list[Check] = field(default_factory=list)


# ═══════════════════════════════════════════════════════════════════
# Tiện ích
# ═══════════════════════════════════════════════════════════════════


def norm(text: Any) -> str:
    """So khớp nhãn: bỏ dấu cách, thường hoá, NFC (``Tốt/ ko tốt`` == ``Tốt/ko tốt``)."""
    value = unicodedata.normalize("NFC", str(text or "")).lower()
    return re.sub(r"\s+", "", value)


def _fmt(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def _walk(value: Any) -> Iterator[Any]:
    if isinstance(value, dict):
        for item in value.values():
            yield from _walk(item)
    elif isinstance(value, list | tuple):
        for item in value:
            yield from _walk(item)
    else:
        yield value


def _numbers_in(result: TurnResult) -> set[float]:
    """Mọi con số trong khối dữ liệu: giá trị gốc và chuỗi hiển thị ``1.234``/``12,5%``."""
    found: set[float] = set()
    for block in result.blocks:
        for value in _walk(block.get("payload")):
            if isinstance(value, bool):
                continue
            if isinstance(value, int | float):
                found.add(float(value))
            elif isinstance(value, str):
                for token in re.findall(r"\d[\d.]*(?:,\d+)?", value):
                    try:
                        found.add(float(token.replace(".", "").replace(",", ".")))
                    except ValueError:
                        pass
    return found


def _kpi_items(result: TurnResult) -> list[dict[str, Any]]:
    return [item for b in result.blocks_of("kpi") for item in b.get("payload", {}).get("items", [])]


def _parse_int(text: Any) -> int | None:
    if isinstance(text, int):
        return text
    cleaned = str(text or "").replace(".", "")
    return int(cleaned) if cleaned.isdigit() else None


def _ranking_items(result: TurnResult) -> list[dict[str, Any]]:
    """Khối xếp hạng; không có thì lấy bảng 2 cột nhãn–số (text2sql trả >20 dòng ra ``table``)."""
    blocks = result.blocks_of("ranking")
    if blocks:
        return list(blocks[0].get("payload", {}).get("items", []))
    for b in result.blocks_of("table"):
        payload = b.get("payload", {})
        cols = [c.get("key") for c in payload.get("columns", [])]
        rows = payload.get("rows", [])
        if len(cols) != 2 or not rows or _parse_int(rows[0].get(cols[1])) is None:
            continue
        return [
            {"rank": i, "label": row.get(cols[0]), "value": _parse_int(row.get(cols[1])),
             "display": row.get(cols[1])}
            for i, row in enumerate(rows, 1)
        ]
    return []


def _all_visible_text(result: TurnResult) -> str:
    parts = list(result.texts)
    for block in result.blocks:
        parts.append(str(block.get("title") or ""))
        parts.append(str(block.get("subtitle") or ""))
        parts.extend(str(v) for v in _walk(block.get("payload")) if isinstance(v, str))
    return "\n".join(parts)


# ═══════════════════════════════════════════════════════════════════
# Phép kiểm
# ═══════════════════════════════════════════════════════════════════


def status(*allowed: str) -> Check:
    def check(r: TurnResult) -> CheckResult:
        return CheckResult(f"done.status ∈ {{{', '.join(allowed)}}}", r.status in allowed, r.status)

    return check


def not_status(*disallowed: str) -> Check:
    def check(r: TurnResult) -> CheckResult:
        return CheckResult(
            f"done.status ∉ {{{', '.join(disallowed)}}}", r.status not in disallowed, r.status
        )

    return check


def intent(*allowed: str) -> Check:
    def check(r: TurnResult) -> CheckResult:
        got = r.intents
        return CheckResult(
            f"intent ∈ {{{', '.join(allowed)}}}", bool(got & set(allowed)), ", ".join(sorted(got)) or "None"
        )

    return check


def reason(*any_of: str) -> Check:
    def check(r: TurnResult) -> CheckResult:
        reasons = r.reasons
        return CheckResult(
            f"lý do có một trong {{{', '.join(any_of)}}}",
            bool(reasons & set(any_of)),
            ", ".join(sorted(reasons)) or "(không có)",
        )

    return check


def no_reason(*codes: str) -> Check:
    def check(r: TurnResult) -> CheckResult:
        hit = r.reasons & set(codes)
        return CheckResult(
            f"không có lý do {{{', '.join(codes)}}}", not hit, ", ".join(sorted(hit)) or "không"
        )

    return check


def kpi(key: str, expected: float, *, tol: float = 0.0) -> Check:
    def check(r: TurnResult) -> CheckResult:
        for item in _kpi_items(r):
            if item.get("key") == key:
                value = item.get("value")
                ok = isinstance(value, int | float) and abs(float(value) - expected) <= tol
                return CheckResult(
                    f"KPI {key} = {_fmt(expected)}", ok, f"{item.get('display')} ({_fmt(value)})"
                )
        return CheckResult(f"KPI {key} = {_fmt(expected)}", False, "không có KPI này")

    return check


def kpi_not(key: str, forbidden: float) -> Check:
    """KPI có mặt thì không được bằng ``forbidden`` (vd. ``processed_issues`` = 0 là hồi quy)."""

    def check(r: TurnResult) -> CheckResult:
        for item in _kpi_items(r):
            if item.get("key") == key:
                value = item.get("value")
                return CheckResult(
                    f"KPI {key} ≠ {_fmt(forbidden)}", value != forbidden, str(item.get("display"))
                )
        return CheckResult(f"KPI {key} ≠ {_fmt(forbidden)}", None, "không có KPI này")

    return check


def numbers_present(*expected: float, any_of: bool = False) -> Check:
    def check(r: TurnResult) -> CheckResult:
        found = _numbers_in(r)
        missing = [n for n in expected if float(n) not in found]
        present = [n for n in expected if float(n) in found]
        ok = bool(present) if any_of else not missing
        word = "một trong" if any_of else "đủ"
        label = f"khối dữ liệu có {word} số {', '.join(_fmt(n) for n in expected)}"
        detail = f"thiếu {', '.join(_fmt(n) for n in missing)}" if missing else "có đủ"
        return CheckResult(label, ok, detail)

    return check


def ranking_top(
    labels: Iterable[str], values: Iterable[float] | None = None, *, loose: bool = False
) -> Check:
    """``loose``: nhãn thật chỉ cần chứa nhãn kỳ vọng (``Thành phố Hà Nội`` ~ ``Hà Nội``)."""
    labels = list(labels)
    values = list(values) if values is not None else None

    def same(got: str, want: str) -> bool:
        return norm(want) in norm(got) if loose else norm(got) == norm(want)

    def check(r: TurnResult) -> CheckResult:
        items = _ranking_items(r)
        got = [(str(i.get("label")), i.get("value")) for i in items[: len(labels)]]
        ok = len(got) == len(labels) and all(
            same(g[0], label) for g, label in zip(got, labels, strict=False)
        )
        if ok and values is not None:
            ok = all(g[1] == v for g, v in zip(got, values, strict=False))
        want = " · ".join(
            f"{label} {_fmt(v)}" if values else label
            for label, v in zip(labels, values or [None] * len(labels), strict=False)
        )
        have = " · ".join(f"{g[0]} {_fmt(g[1])}" for g in got) or "không có khối xếp hạng"
        return CheckResult(f"xếp hạng đầu: {want}", ok, have)

    return check


def ranking_has(label: str, value: float | None = None) -> Check:
    def check(r: TurnResult) -> CheckResult:
        for item in _ranking_items(r):
            if norm(item.get("label")) == norm(label):
                ok = value is None or item.get("value") == value
                return CheckResult(
                    f"xếp hạng có {label}" + (f" = {_fmt(value)}" if value is not None else ""),
                    ok,
                    f"hạng {item.get('rank')}: {item.get('display')}",
                )
        return CheckResult(f"xếp hạng có {label}", False, "không thấy")

    return check


def block(kind: str, *, min_count: int = 1) -> Check:
    def check(r: TurnResult) -> CheckResult:
        count = len(r.blocks_of(kind))
        kinds = ", ".join(str(b.get("kind")) for b in r.blocks) or "không có khối"
        return CheckResult(f"có ≥{min_count} khối {kind}", count >= min_count, kinds)

    return check


def timeseries_points(min_points: int = 1) -> Check:
    def check(r: TurnResult) -> CheckResult:
        blocks = r.blocks_of("timeseries")
        points = max((len(b.get("payload", {}).get("points", [])) for b in blocks), default=0)
        return CheckResult(f"biểu đồ theo ngày ≥{min_points} điểm", points >= min_points, str(points))

    return check


def found_between(low: int, high: int) -> Check:
    """Số phản hồi tìm được: KPI ``found`` (khi >10) hoặc số trích dẫn."""

    def check(r: TurnResult) -> CheckResult:
        count = None
        for item in _kpi_items(r):
            if item.get("key") == "found":
                count = item.get("value")
        if count is None:
            quotes = r.blocks_of("quote")
            count = sum(len(b.get("payload", {}).get("quotes", [])) for b in quotes)
        ok = isinstance(count, int) and low <= count <= high
        return CheckResult(f"tìm thấy {low}–{high} phản hồi", ok, str(count))

    return check


def quotes(min_count: int = 1) -> Check:
    def check(r: TurnResult) -> CheckResult:
        items = [q for b in r.blocks_of("quote") for q in b.get("payload", {}).get("quotes", [])]
        cited = [q for q in items if q.get("issue_code") or q.get("feedback_id")]
        return CheckResult(
            f"≥{min_count} trích dẫn có mã", len(cited) >= min_count, f"{len(cited)}/{len(items)}"
        )

    return check


def table_columns(*headers: str) -> Check:
    def check(r: TurnResult) -> CheckResult:
        seen = []
        for b in r.blocks_of("table"):
            cols = [str(c.get("header_vi")) for c in b.get("payload", {}).get("columns", [])]
            seen.append(cols)
            if all(h in cols for h in headers):
                return CheckResult(f"bảng có cột {', '.join(headers)}", True, " | ".join(cols))
        return CheckResult(f"bảng có cột {', '.join(headers)}", False, str(seen) or "không có bảng")

    return check


def file_location_rows() -> Check:
    """Bảng *Vị trí trong file nguồn* có ít nhất một dòng đủ File nguồn + Dòng."""

    def check(r: TurnResult) -> CheckResult:
        for b in r.blocks_of("table"):
            if b.get("title") != "Vị trí trong file nguồn":
                continue
            rows = b.get("payload", {}).get("rows", [])
            good = [
                row
                for row in rows
                if str(row.get("source_file_name") or "").lower().endswith((".xlsx", ".xls"))
                and str(row.get("source_row_number") or "").isdigit()
            ]
            sample = (
                f"{good[0]['source_file_name']} dòng {good[0]['source_row_number']}" if good else ""
            )
            return CheckResult(
                "bảng vị trí file có File nguồn + Dòng", bool(good), f"{len(good)} dòng · {sample}"
            )
        return CheckResult("bảng vị trí file có File nguồn + Dòng", False, "không có bảng vị trí")

    return check


def export_link() -> Check:
    def check(r: TurnResult) -> CheckResult:
        for b in r.blocks_of("export"):
            payload = b.get("payload", {})
            ok = bool(payload.get("export_id")) and not payload.get("error")
            detail = payload.get("error") or f"{payload.get('filename')} · {payload.get('rows_exported')} dòng"
            return CheckResult("có link tải Excel", ok, str(detail))
        return CheckResult("có link tải Excel", False, "không có khối export")

    return check


def text_contains(*needles: str, any_of: bool = False) -> Check:
    def check(r: TurnResult) -> CheckResult:
        haystack = norm(" ".join(r.texts))
        hits = [n for n in needles if norm(n) in haystack]
        ok = bool(hits) if any_of else len(hits) == len(needles)
        preview = " / ".join(r.texts)[:220] or "(không có câu chữ)"
        word = "một trong" if any_of else ""
        return CheckResult(f"câu trả lời nhắc {word} {', '.join(repr(n) for n in needles)}", ok, preview)

    return check


def no_technical_keys() -> Check:
    """Câu chữ người dùng đọc không chứa tên khoá kỹ thuật dạng ``issue_code``."""
    regex = re.compile(r"\b[a-z]+(?:_[a-z0-9]+)+\b")

    def check(r: TurnResult) -> CheckResult:
        leaked = sorted({m for t in r.texts for m in regex.findall(t)})
        return CheckResult("không lộ tên khoá kỹ thuật", not leaked, ", ".join(leaked) or "sạch")

    return check


def commentary_not_matching(pattern: str, label: str) -> Check:
    regex = re.compile(pattern, re.IGNORECASE)

    def check(r: TurnResult) -> CheckResult:
        bad = [t for t in r.commentary if regex.search(t)]
        return CheckResult(label, not bad, bad[0] if bad else "không")

    return check


def has_commentary(min_sentences: int = 1) -> Check:
    def check(r: TurnResult) -> CheckResult:
        count = len(r.commentary)
        return CheckResult(
            f"có ≥{min_sentences} câu nhận định",
            count >= min_sentences,
            f"{count} câu · {r.done.get('commentary_status')}",
        )

    return check


def dropped_at_most(limit: int) -> Check:
    def check(r: TurnResult) -> CheckResult:
        dropped = r.done.get("dropped_sentences")
        ok = isinstance(dropped, int) and dropped <= limit
        return CheckResult(f"dropped_sentences ≤ {limit}", ok, str(dropped))

    return check


def commentary_status(*allowed: str) -> Check:
    def check(r: TurnResult) -> CheckResult:
        value = str(r.done.get("commentary_status"))
        return CheckResult(f"commentary_status ∈ {{{', '.join(allowed)}}}", value in allowed, value)

    return check


def pattern_used(name: str) -> Check:
    def check(r: TurnResult) -> CheckResult:
        used = r.patterns + r.functions
        return CheckResult(f"dùng {name}", name in used, ", ".join(used) or "không có bước")

    return check


def subtitle_contains(text: str) -> Check:
    def check(r: TurnResult) -> CheckResult:
        subs = [str(b.get("subtitle") or "") for b in r.blocks]
        ok = any(text in s for s in subs)
        return CheckResult(f"phụ đề khối có '{text}'", ok, " | ".join(dict.fromkeys(subs)) or "không có khối")

    return check


def nowhere(text: str) -> Check:
    """``text`` không xuất hiện ở bất cứ đâu người dùng nhìn thấy (khối, phụ đề, câu chữ)."""

    def check(r: TurnResult) -> CheckResult:
        visible = norm(_all_visible_text(r))
        return CheckResult(f"không lộ '{text}'", norm(text) not in visible, "có lộ" if norm(text) in visible else "sạch")

    return check


def no_content_column() -> Check:
    """Không khối nào trả nội dung phản hồi (cột ``content``) qua đường text2sql."""

    def check(r: TurnResult) -> CheckResult:
        leaked = []
        for b in r.blocks:
            if b.get("kind") == "quote":
                continue  # trích dẫn FTS là đường hợp lệ khác, không phải text2sql
            for col in b.get("payload", {}).get("columns", []) or []:
                key, header = str(col.get("key") or ""), norm(col.get("header_vi"))
                if "content" in key or "nộidung" in header:
                    leaked.append(key or header)
        sql_hits = [s for s in r.sql if re.search(r"\bcontent\b", s, re.IGNORECASE)]
        ok = not leaked and not sql_hits
        return CheckResult("không lộ cột content", ok, f"cột {leaked} · sql {len(sql_hits)}" if not ok else "sạch")

    return check


def comparison_table_localized() -> Check:
    """Ca 49 (phần dữ liệu): Chỉ tiêu tiếng Việt, tỉ lệ có %, cột số căn phải."""

    def check(r: TurnResult) -> CheckResult:
        for b in r.blocks_of("table"):
            payload = b.get("payload", {})
            cols = payload.get("columns", [])
            if not any(c.get("header_vi") == "Chỉ tiêu" for c in cols):
                continue
            problems = []
            for row in payload.get("rows", []):
                label = str(row.get("label") or "")
                if re.fullmatch(r"[a-z_]+", label):
                    problems.append(f"khoá kỹ thuật '{label}'")
                if label.startswith("Tỉ lệ"):
                    for key in ("current", "previous"):
                        cell = str(row.get(key) or "")
                        if cell and cell != "Chưa đủ dữ liệu" and "%" not in cell:
                            problems.append(f"{label}/{key}='{cell}' thiếu %")
            for col in cols:
                if col.get("key") in ("current", "previous", "change_percent") and col.get("align") != "right":
                    problems.append(f"cột {col.get('header_vi')} không căn phải")
            return CheckResult(
                "bảng so sánh: nhãn Việt, tỉ lệ có %, cột số căn phải",
                not problems,
                "; ".join(problems[:4]) or "đạt",
            )
        # Planner chọn get_overview kèm kỳ so sánh thì ra KPI, không có bảng để kiểm.
        return CheckResult(
            "bảng so sánh: nhãn Việt, tỉ lệ có %, cột số căn phải",
            None,
            "không ra bảng Chỉ tiêu (planner trả KPI) — không kiểm được",
        )

    return check


def report_range(date_from: str, date_to: str) -> Check:
    def check(r: TurnResult) -> CheckResult:
        rng = getattr(r.outcome, "report_range", None)
        got = f"{rng.date_from.isoformat()} → {rng.date_to.isoformat()}" if rng else "không có"
        return CheckResult(f"khoảng báo cáo {date_from} → {date_to}", got == f"{date_from} → {date_to}", got)

    return check


def report_sections(min_count: int) -> Check:
    def check(r: TurnResult) -> CheckResult:
        sections = getattr(r.outcome, "sections", ()) or ()
        ids = [s.section_id for s in sections]
        return CheckResult(f"báo cáo ≥{min_count} phần", len(ids) >= min_count, ", ".join(ids) or "0")

    return check


def _plan_values(r: TurnResult, key: str) -> list[str]:
    plan = getattr(r.outcome, "validated_plan", None)
    values = []
    for step in getattr(plan, "steps", ()) or ():
        for source in (getattr(step, "params", {}) or {}, getattr(step, "fts_filters", {}) or {}):
            if source.get(key) not in (None, ""):
                values.append(str(source[key]))
    return values


def plan_param(key: str, contains: str) -> Check:
    """Một bước của plan lọc ``key`` chứa ``contains`` (so khớp bỏ dấu cách, không phân biệt hoa)."""

    def check(r: TurnResult) -> CheckResult:
        values = _plan_values(r, key)
        ok = any(norm(contains) in norm(v) for v in values)
        return CheckResult(f"plan lọc {key} ~ '{contains}'", ok, ", ".join(values) or "không lọc")

    return check


def plan_param_absent(key: str, contains: str) -> Check:
    def check(r: TurnResult) -> CheckResult:
        values = _plan_values(r, key)
        hit = [v for v in values if norm(contains) in norm(v)]
        return CheckResult(f"plan không lọc {key} ~ '{contains}'", not hit, ", ".join(values) or "không lọc")

    return check


def logs_not_contain(needle: str) -> Check:
    def check(r: TurnResult) -> CheckResult:
        hits = [line for line in r.logs if needle.lower() in line.lower()]
        return CheckResult(f"log không có '{needle}'", not hits, hits[0][:200] if hits else "sạch")

    return check


def no_runner_error() -> Check:
    def check(r: TurnResult) -> CheckResult:
        return CheckResult("runner không ném lỗi", r.runner_error is None, r.runner_error or "không")

    return check


def info(label: str, getter: Callable[[TurnResult], Any]) -> Check:
    """Chỉ ghi lại giá trị để đọc, không tính đạt/trượt."""

    def check(r: TurnResult) -> CheckResult:
        try:
            value = getter(r)
        except Exception as exc:  # phép kiểm thông tin không được làm hỏng cả ca
            value = f"<{type(exc).__name__}: {exc}>"
        return CheckResult(label, None, str(value))

    return check


def top_ranking(n: int = 5) -> Check:
    return info(
        f"top {n} xếp hạng",
        lambda r: " · ".join(f"{i.get('label')} {i.get('display')}" for i in _ranking_items(r)[:n])
        or "không có",
    )


def kpi_summary() -> Check:
    return info(
        "KPI",
        lambda r: " · ".join(f"{i.get('label')} {i.get('display')}" for i in _kpi_items(r)[:5])
        or "không có",
    )


def custom(label: str, predicate: Callable[[TurnResult], tuple[bool, str]]) -> Check:
    def check(r: TurnResult) -> CheckResult:
        ok, detail = predicate(r)
        return CheckResult(label, ok, detail)

    return check
