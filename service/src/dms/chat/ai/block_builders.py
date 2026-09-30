"""Dựng ``data_block`` từ kết quả truy vấn bằng Python (spec ``chat-data-blocks``, b05 D3).

Mọi con số người dùng thấy đều đi qua đây: lấy thẳng từ kết quả hoặc do Python tính, rồi
định dạng bằng ``vi_format``. LLM không tham gia bước này.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from typing import Any

from .function_catalog import FUNCTION_CATALOG, KPI_LABELS_VI, KPI_RATE_KEYS, BlockSpec
from .vi_format import (
    NOT_ENOUGH_DATA,
    delta_from_change_percent,
    format_date,
    format_date_range,
    format_int,
    format_metric,
    format_percent,
    truncate_text,
)

logger = logging.getLogger("dms-chat-blocks")

DEFAULT_TABLE_MAX_ROWS = 20
DEFAULT_QUOTE_MAX_CHARS = 240
MAX_QUOTES = 5
# Tra cứu FTS (b08 D8): khối quote tối đa 10; >10 kết quả thì thêm KPI "Tìm thấy N phản hồi".
FTS_FUNCTION = "fts5_search"
FTS_FILE_FUNCTION = "fts5_file_lookup"
FTS_MAX_QUOTES = 10
SEMANTIC_FUNCTION = "semantic_view"
SQL_RANKING_MAX_ROWS = 20
SQL_FACT_ROWS = 10
UNKNOWN_LABEL = "Chưa xác định"
RATE_ALIAS_PREFIXES = ("ty_le", "ti_le", "tyle", "tile", "phan_tram")
FILE_LOOKUP_COLUMNS = (
    ("issue_code", "Mã vấn đề", "text"),
    ("source_file_name", "File nguồn", "text"),
    ("source_row_number", "Dòng", "text"),  # số dòng không có dấu chấm nghìn
    ("unit_name", "Đơn vị", "text"),
    ("issue_date", "Ngày", "date"),
)


@dataclass(frozen=True)
class BlockConfig:
    table_max_rows: int = DEFAULT_TABLE_MAX_ROWS
    quote_max_chars: int = DEFAULT_QUOTE_MAX_CHARS

    @classmethod
    def from_settings(cls, settings: Any) -> BlockConfig:
        return cls(
            table_max_rows=int(getattr(settings, "chat_table_max_rows", DEFAULT_TABLE_MAX_ROWS)),
            quote_max_chars=int(getattr(settings, "chat_quote_max_chars", DEFAULT_QUOTE_MAX_CHARS)),
        )


@dataclass(frozen=True)
class DataBlock:
    block_id: str
    kind: str
    title: str
    payload: dict[str, Any]
    subtitle: str = ""
    chart_hint: str = "none"
    # Phần của báo cáo: {"id", "title", "index"} (b10 D4); câu trả lời thường để None.
    section: dict[str, Any] | None = None
    fact_scope: dict[str, Any] = field(default_factory=dict)  # dữ liệu thô cho fact_sheet

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "block_id": self.block_id,
            "kind": self.kind,
            "title": self.title,
            "subtitle": self.subtitle,
            "payload": self.payload,
            "chart_hint": self.chart_hint,
        }
        if self.section is not None:
            data["section"] = dict(self.section)
        return data


def build_blocks(
    step_results: Any,
    *,
    config: BlockConfig | None = None,
    subtitle: str = "",
) -> list[DataBlock]:
    """Dựng khối cho mọi bước có trạng thái ``ok``; bước khác bị bỏ qua."""
    cfg = config or BlockConfig()
    blocks: list[DataBlock] = []
    for step in step_results:
        if not getattr(step.status, "ok", False) or step.result is None:
            continue
        payload_rows = step.result.data or []
        if not payload_rows:
            continue
        if step.function_name == SEMANTIC_FUNCTION:
            blocks.append(
                build_sql_block(
                    payload_rows,
                    step_index=step.index,
                    config=cfg,
                    subtitle=subtitle,
                    output_columns=getattr(step, "output_columns", ()),
                )
            )
            continue
        if step.function_name in (FTS_FUNCTION, FTS_FILE_FUNCTION):
            blocks.extend(
                build_lookup_blocks(
                    str(step.function_name),
                    payload_rows,
                    step_index=step.index,
                    config=cfg,
                    subtitle=subtitle,
                )
            )
            continue
        block = build_block(
            function_name=str(step.function_name or ""),
            row=payload_rows[0],
            step_index=step.index,
            config=cfg,
            subtitle=subtitle,
        )
        if block is not None:
            blocks.append(block)
    return blocks


def build_block(
    *,
    function_name: str,
    row: dict[str, Any],
    step_index: int = 1,
    config: BlockConfig | None = None,
    subtitle: str = "",
) -> DataBlock | None:
    cfg = config or BlockConfig()
    info = FUNCTION_CATALOG.get(function_name)
    spec = info.block_spec if info is not None else None
    if spec is not None and spec.title:
        title = spec.title
    else:
        # Không lộ tên hàm/tham số ra tiêu đề khối.
        title = "Kết quả"
    block_id = f"s{step_index}.{function_name or 'block'}"

    if spec is None:
        logger.info("block_spec_missing", extra={"function_name": function_name})
        return _generic_table(block_id, title, subtitle, row, cfg)

    builder = _BUILDERS.get(spec.kind, _generic_table_from_spec)
    return builder(block_id, title, subtitle, row, spec, cfg)


def build_sql_block(
    rows: list[dict[str, Any]],
    *,
    step_index: int = 1,
    config: BlockConfig | None = None,
    subtitle: str = "",
    output_columns: Any = (),
) -> DataBlock:
    """Kết quả Pattern 2 theo hình dạng (b09 D8): kpi / ranking / timeseries / table."""
    from .semantic_schema import column_title

    cfg = config or BlockConfig()
    block_id = f"s{step_index}.sql"
    columns = list(rows[0].keys())
    meanings = {
        str(c.get("alias")): str(c.get("meaning_vi") or "")
        for c in output_columns or ()
        if isinstance(c, dict) and c.get("alias")
    }

    def title_of(alias: str) -> str:
        return (
            column_title(alias)
            if alias in _known_aliases() or not meanings.get(alias)
            else meanings[alias]
        )

    def is_number(value: Any) -> bool:
        return isinstance(value, int | float) and not isinstance(value, bool)

    numeric = [c for c in columns if all(r.get(c) is None or is_number(r.get(c)) for r in rows)]
    sql_facts = {"columns": columns, "rows": rows[:SQL_FACT_ROWS]}
    title = "Kết quả truy vấn"

    if len(rows) == 1 and len(columns) == 1 and numeric:
        alias = columns[0]
        value = rows[0][alias]
        item = {
            "key": alias,
            "label": title_of(alias),
            "available": value is not None,
            "value": value,
            "display": format_sql_cell(value, numeric=True, rate=is_rate_alias(alias)),
            "denominator": None,
            "excluded_missing_issue_code": None,
        }
        return DataBlock(
            block_id=block_id,
            kind="kpi",
            title=title,
            subtitle=subtitle,
            payload={"items": [item]},
            fact_scope={"kpi": [item], "sql": sql_facts},
        )

    first = columns[0]
    if (
        len(columns) == 2
        and first not in numeric
        and columns[1] in numeric
        and len(rows) <= SQL_RANKING_MAX_ROWS
    ):
        if _looks_like_dates(rows, first):
            return _sql_timeseries(block_id, title, subtitle, rows, first, columns[1], sql_facts)
        value_col = columns[1]
        total = sum(r.get(value_col) or 0 for r in rows)
        items = [
            {
                "rank": i,
                "label": str(r.get(first) if r.get(first) is not None else UNKNOWN_LABEL),
                "value": r.get(value_col),
                "display": format_sql_cell(
                    r.get(value_col), numeric=True, rate=is_rate_alias(value_col)
                ),
                "percent": None,
                "percent_display": "",
            }
            for i, r in enumerate(rows, start=1)
        ]
        return DataBlock(
            block_id=block_id,
            kind="ranking",
            title=f"{title_of(value_col)} theo {title_of(first).lower()}",
            subtitle=subtitle,
            payload={
                "items": items,
                "total_rows": len(rows),
                "truncated": False,
                "total_issues": None,
                "total_display": "",
                "value_title": title_of(value_col),
                "label_title": title_of(first),
                "sum_hint": total,
            },
            chart_hint="bar",
            fact_scope={"items": items, "sql": sql_facts},
        )
    if (
        len(columns) >= 2
        and _looks_like_dates(rows, first)
        and any(c in numeric for c in columns[1:])
    ):
        value_col = next(c for c in columns[1:] if c in numeric)
        return _sql_timeseries(block_id, title, subtitle, rows, first, value_col, sql_facts)

    shown = rows[: cfg.table_max_rows]
    table_columns = [
        {
            "key": c,
            "header_vi": title_of(c),
            "format": "int" if c in numeric else "text",
            "align": "right" if c in numeric else "left",
        }
        for c in columns
    ]
    # Ô chữ rỗng là nhóm không có giá trị (vd. sản phẩm để trống) — "Chưa xác định" như khối
    # xếp hạng; "Chưa đủ dữ liệu" chỉ dành cho số không tính được.
    table_rows = [
        {c: format_sql_cell(r.get(c), numeric=c in numeric, rate=is_rate_alias(c)) for c in columns}
        for r in shown
    ]
    return DataBlock(
        block_id=block_id,
        kind="table",
        title=title,
        subtitle=subtitle,
        payload={
            "columns": table_columns,
            "rows": table_rows,
            "total_rows": len(rows),
            "truncated": len(rows) > len(shown),
        },
        fact_scope={"rows": shown, "sql": sql_facts},
    )


def _sql_timeseries(block_id, title, subtitle, rows, date_col, value_col, sql_facts) -> DataBlock:
    points = [
        {
            "date": str(r.get(date_col)),
            "display_date": format_date(r.get(date_col))
            if len(str(r.get(date_col))) == 10
            else str(r.get(date_col)),
            "value": r.get(value_col),
            "display": format_sql_cell(
                r.get(value_col), numeric=True, rate=is_rate_alias(value_col)
            ),
        }
        for r in rows
    ]
    return DataBlock(
        block_id=block_id,
        kind="timeseries",
        title=title,
        subtitle=subtitle,
        payload={"points": points, "granularity": "custom", "aggregate_allowed": False},
        chart_hint="line",
        fact_scope={"points": points, "aggregate_allowed": False, "sql": sql_facts},
    )


def _looks_like_dates(rows: list[dict[str, Any]], column: str) -> bool:
    import re as _re

    pattern = _re.compile(r"^\d{4}-\d{2}(-\d{2})?$")
    values = [r.get(column) for r in rows if r.get(column) is not None]
    return bool(values) and all(isinstance(v, str) and pattern.match(v) for v in values)


def is_rate_alias(alias: str) -> bool:
    """Prompt sinh SQL đặt tên cột tỉ lệ là ``ty_le``/``ti_le``… — giá trị đã nhân 100."""
    return str(alias).lower().startswith(RATE_ALIAS_PREFIXES)


def format_sql_cell(value: Any, *, numeric: bool, rate: bool = False) -> str:
    """Ô chữ rỗng là nhóm không có giá trị (vd. sản phẩm để trống) nên ghi "Chưa xác định" như
    khối xếp hạng; "Chưa đủ dữ liệu" chỉ dành cho con số không tính được. Cột tỉ lệ có "%"."""
    if value is None and not numeric:
        return UNKNOWN_LABEL
    if rate and isinstance(value, int | float) and not isinstance(value, bool):
        return format_percent(value)
    return _format_sql_value(value)


def _format_sql_value(value: Any) -> str:
    if value is None:
        return NOT_ENOUGH_DATA
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, int):
        return format_int(value)
    if isinstance(value, float):
        return format_int(int(value)) if value.is_integer() else format_percent(value).rstrip("%")
    return str(value)


def _known_aliases() -> dict[str, str]:
    from .semantic_schema import load_column_aliases

    return load_column_aliases()


def build_lookup_blocks(
    function_name: str,
    rows: list[dict[str, Any]],
    *,
    step_index: int = 1,
    config: BlockConfig | None = None,
    subtitle: str = "",
) -> list[DataBlock]:
    """Khối cho kết quả tra cứu FTS: mỗi dòng là một phản hồi (khác các hàm thống kê)."""
    cfg = config or BlockConfig()
    prefix = f"s{step_index}.{function_name}"
    if function_name == FTS_FILE_FUNCTION:
        spec = BlockSpec(kind="table", title="Vị trí trong file nguồn", columns=FILE_LOOKUP_COLUMNS)
        return [
            _build_table(
                f"{prefix}.table",
                spec.title,
                subtitle,
                {"rows": rows},
                replace(spec, items_path="rows"),
                cfg,
            )
        ]

    blocks: list[DataBlock] = []
    total = len(rows)
    if total > FTS_MAX_QUOTES:
        item = {
            "key": "found",
            "label": "Tìm thấy",
            "available": True,
            "value": total,
            "display": f"{format_int(total)} phản hồi",
            "denominator": None,
            "excluded_missing_issue_code": None,
        }
        blocks.append(
            DataBlock(
                block_id=f"{prefix}.count",
                kind="kpi",
                title="Kết quả tìm kiếm",
                subtitle=subtitle,
                payload={"items": [item]},
                fact_scope={"kpi": [item]},
            )
        )
    spec = BlockSpec(kind="quote", title="Phản hồi tìm được", items_path="rows")
    quote = _build_quote(
        f"{prefix}.quotes", spec.title, subtitle, {"rows": rows}, spec, cfg, limit=FTS_MAX_QUOTES
    )
    blocks.append(quote)
    return blocks


# ── Từng loại khối ──


def _build_kpi(
    block_id: str, title: str, subtitle: str, row: dict, spec: BlockSpec, cfg: BlockConfig
) -> DataBlock:
    items: list[dict[str, Any]] = []
    for key in spec.kpi_keys:
        metric = row.get(key)
        if not isinstance(metric, dict):
            continue
        item: dict[str, Any] = {
            "key": key,
            "label": KPI_LABELS_VI.get(key, key),
            "available": bool(metric.get("available")),
            "value": metric.get("value"),
            "display": format_metric(metric),
            "denominator": metric.get("denominator"),
            "excluded_missing_issue_code": metric.get("excluded_missing_issue_code"),
        }
        if metric.get("reason"):
            item["reason"] = metric["reason"]
        if "numerator" in metric:
            item["numerator"] = metric["numerator"]
        comparison = metric.get("comparison")
        if isinstance(comparison, dict):
            item["comparison"] = {
                "available": bool(comparison.get("available")),
                "value": comparison.get("value"),
                "display": format_int(comparison.get("value")),
                "change_percent": comparison.get("change_percent"),
                # Giao diện chỉ hiển thị chuỗi đã định dạng (b07 D9).
                "change_display": delta_from_change_percent(comparison.get("change_percent")),
                "direction": comparison.get("direction"),
            }
        items.append(item)
    return DataBlock(
        block_id=block_id,
        kind="kpi",
        title=title,
        subtitle=subtitle,
        payload={"items": items},
        chart_hint=spec.chart_hint,
        fact_scope={"kpi": items},
    )


def _build_ranking(
    block_id: str, title: str, subtitle: str, row: dict, spec: BlockSpec, cfg: BlockConfig
) -> DataBlock:
    raw_items = _as_list(row.get(spec.items_path))
    total_rows = len(raw_items)
    shown = raw_items[: cfg.table_max_rows]
    items = []
    for rank, item in enumerate(shown, start=1):
        value = item.get(spec.value_field)
        percent = item.get(spec.percent_field)
        items.append(
            {
                "rank": rank,
                "label": str(item.get(spec.label_field) or ""),
                "value": value,
                "display": format_int(value),
                "percent": percent,
                "percent_display": format_percent(percent) if percent is not None else "",
            }
        )
    total = row.get(spec.total_path)
    return DataBlock(
        block_id=block_id,
        kind="ranking",
        title=title,
        subtitle=subtitle,
        payload={
            "items": items,
            "total_rows": total_rows,
            "truncated": total_rows > len(shown),
            "total_issues": total,
            "total_display": format_int(total) if total is not None else "",
            "membership_count": row.get("membership_count"),
            "excluded_missing_issue_code": row.get("excluded_missing_issue_code"),
        },
        chart_hint=spec.chart_hint,
        fact_scope={"rank": items, "total": total},
    )


def _build_timeseries(
    block_id: str, title: str, subtitle: str, row: dict, spec: BlockSpec, cfg: BlockConfig
) -> DataBlock:
    points = []
    for item in _as_list(row.get(spec.items_path)):
        value = item.get(spec.value_field)
        points.append(
            {
                "date": item.get(spec.date_field),
                "display_date": format_date(item.get(spec.date_field)),
                "value": value,
                "display": format_int(value),
            }
        )
    return DataBlock(
        block_id=block_id,
        kind="timeseries",
        title=title,
        subtitle=subtitle,
        payload={
            "points": points,
            "granularity": spec.granularity,
            # daily_trend đếm distinct theo từng ngày nên không được cộng dồn.
            "aggregate_allowed": spec.aggregate_allowed,
        },
        chart_hint=spec.chart_hint,
        fact_scope={"points": points, "aggregate_allowed": spec.aggregate_allowed},
    )


def _build_quote(
    block_id: str,
    title: str,
    subtitle: str,
    row: dict,
    spec: BlockSpec,
    cfg: BlockConfig,
    limit: int = MAX_QUOTES,
) -> DataBlock:
    quotes = []
    for item in _as_list(row.get(spec.items_path))[:limit]:
        content = str(item.get("content") or item.get("summary") or "")
        quote: dict[str, Any] = {
            "issue_code": item.get("issue_code"),
            "content": truncate_text(content, cfg.quote_max_chars),
            "unit_name": item.get("unit_name") or item.get("department"),
            "issue_date": item.get("issue_date"),
            "display_date": format_date(item.get("issue_date")),
        }
        for optional in (
            "feedback_id",
            "source_file_name",
            "source_row_number",
            "sentiment",
            "status",
        ):
            if item.get(optional) is not None:
                quote[optional] = item[optional]
        quotes.append(quote)
    total = row.get("total")
    return DataBlock(
        block_id=block_id,
        kind="quote",
        title=title,
        subtitle=subtitle,
        payload={
            "quotes": quotes,
            "total": total,
            "total_display": format_int(total) if total is not None else "",
        },
        chart_hint=spec.chart_hint,
        fact_scope={"quotes": quotes},
    )


_NUMERIC_FORMATS = frozenset({"int", "pct", "metric"})


def _build_table(
    block_id: str, title: str, subtitle: str, row: dict, spec: BlockSpec, cfg: BlockConfig
) -> DataBlock:
    raw_items = _as_list(row.get(spec.items_path))
    total_rows = len(raw_items)
    shown = raw_items[: cfg.table_max_rows]
    columns = [
        {
            "key": key,
            "header_vi": header,
            "format": fmt,
            # Giao diện căn phải cả ô lẫn tiêu đề cột số, nhờ vậy cột thẳng hàng (b07).
            "align": "right" if fmt in _NUMERIC_FORMATS else "left",
        }
        for key, header, fmt in spec.columns
    ]
    rows = [
        {
            column["key"]: _format_cell(
                item.get(column["key"]), _resolve_format(column["format"], item)
            )
            for column in columns
        }
        for item in shown
    ]
    return DataBlock(
        block_id=block_id,
        kind="table",
        title=title,
        subtitle=subtitle,
        payload={
            "columns": columns,
            "rows": rows,
            "total_rows": total_rows,
            "truncated": total_rows > len(shown),
        },
        chart_hint=spec.chart_hint,
        fact_scope={"rows": shown},
    )


def _generic_table_from_spec(
    block_id: str, title: str, subtitle: str, row: dict, spec: BlockSpec, cfg: BlockConfig
) -> DataBlock:
    return _generic_table(block_id, title, subtitle, row, cfg)


def _generic_table(
    block_id: str, title: str, subtitle: str, row: dict, cfg: BlockConfig
) -> DataBlock:
    """Khối dự phòng cho hàm chưa khai báo ``block_spec``: các khoá cấp 1 dạng vô hướng."""
    columns = [
        {"key": "field", "header_vi": "Chỉ tiêu", "format": "text"},
        {"key": "value", "header_vi": "Giá trị", "format": "text"},
    ]
    rows = [
        {"field": key, "value": format_int(value) if isinstance(value, int) else str(value)}
        for key, value in row.items()
        if isinstance(value, int | float | str)
    ][: cfg.table_max_rows]
    return DataBlock(
        block_id=block_id,
        kind="table",
        title=title,
        subtitle=subtitle,
        payload={"columns": columns, "rows": rows, "total_rows": len(rows), "truncated": False},
        chart_hint="none",
    )


_BUILDERS = {
    "kpi": _build_kpi,
    "ranking": _build_ranking,
    "timeseries": _build_timeseries,
    "quote": _build_quote,
    "table": _build_table,
}


# ── Tiện ích ──


def _as_list(value: Any) -> list[dict[str, Any]]:
    """Danh sách bản ghi; ``metrics`` của ``get_comparison`` là dict nên được trải thành list.

    Khoá kỹ thuật (``total_issues``) được đổi sang nhãn tiếng Việt và giữ lại ở ``key`` để bước
    định dạng biết KPI nào là tỉ lệ.
    """
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        return [
            {"key": key, "label": KPI_LABELS_VI.get(key, key), **item}
            for key, item in value.items()
            if isinstance(item, dict)
        ]
    return []


def _resolve_format(fmt: str, item: dict[str, Any]) -> str:
    """``metric`` = đơn vị phụ thuộc KPI của dòng: tỉ lệ thì phần trăm, còn lại là số đếm."""
    if fmt != "metric":
        return fmt
    return "pct" if str(item.get("key") or "") in KPI_RATE_KEYS else "int"


def _format_cell(value: Any, fmt: str) -> str:
    if value is None:
        return NOT_ENOUGH_DATA
    if fmt == "int":
        return format_int(value)
    if fmt == "pct":
        return format_percent(value)
    if fmt == "date":
        return format_date(value)
    return str(value)


def subtitle_for_range(date_from: str | None, date_to: str | None, units: tuple[str, ...]) -> str:
    """Phụ đề khối: khoảng ngày và phạm vi đơn vị đang áp dụng."""
    parts = []
    range_text = format_date_range(date_from, date_to)
    if range_text:
        parts.append(range_text)
    if units:
        parts.append(", ".join(units))
    return " · ".join(parts)
