"""SQLite predicates preserving the analytics service's Python matching rules."""

from __future__ import annotations

import json
import sqlite3

from .input_reader import _canon_lower
from .models import AnalyticsFilter

_GEOGRAPHY_ALIASES = {
    "province": ("Tỉnh/TP", "Tinh/TP", "Tỉnh thành", "Tinh thanh"),
    "district": ("Quận/huyện", "Quan/huyen", "Quận huyện", "Quan huyen"),
}


def _fold(value: object) -> str:
    return str(value or "").strip().casefold()


def _label_fold(value: str) -> str:
    # Unlike ordinary fields, stored labels were never stripped by _matches_label.
    return value.casefold()


def _raw_geography(raw_json: str, field: str) -> str | None:
    """Match _raw_value's direct-first aliases and last canonical-key winner."""
    try:
        raw = json.loads(raw_json)
    except Exception:
        raw = {}
    aliases = _GEOGRAPHY_ALIASES[field]
    for alias in aliases:
        value = raw.get(alias)
        if value is not None and str(value).strip():
            return str(value).strip()
    normalized = {_canon_lower(key): value for key, value in raw.items()}
    for alias in aliases:
        value = str(normalized.get(_canon_lower(alias)) or "").strip()
        if value:
            return value
    return None


def register_issue_functions(conn: sqlite3.Connection) -> None:
    """SQLite lower/trim do not implement Unicode Python strip/casefold."""
    conn.create_function("analytics_fold", 1, _fold, deterministic=True)
    conn.create_function("analytics_label_fold", 1, _label_fold, deterministic=True)
    conn.create_function("analytics_geography", 2, _raw_geography, deterministic=True)


def issues_where(
    scope: AnalyticsFilter,
    *,
    source: str | None,
    unit_name: str | None,
    label: str | None,
    product: str | None,
    business_status: str | None,
) -> tuple[str, list[object]]:
    """Use bound values and EXISTS so multi-label rows never inflate totals."""
    conditions = ["r.is_active = 1"]
    params: list[object] = []
    if scope.date_from is not None or scope.date_to is not None:
        conditions.append("r.issue_date IS NOT NULL AND r.issue_date != ''")
        for operator, value in ((">=", scope.date_from), ("<=", scope.date_to)):
            if value is not None:
                conditions.append(f"r.issue_date {operator} ?")
                params.append(value)
    for field, value in (("province", scope.province), ("district", scope.district)):
        if value:
            conditions.append("analytics_fold(analytics_geography(r.raw_data_json, ?)) = ?")
            params.extend((field, _fold(value)))
    for field, value in (
        ("unit_name", scope.unit_name or None),
        ("source", source),
        ("unit_name", unit_name),
        ("product", product),
        ("business_status", business_status),
    ):
        if value is not None:
            conditions.append(f"analytics_fold(r.{field}) = ?")
            params.append(_fold(value))
    if label is not None:
        if _fold(label) == "__unlabeled__":
            conditions.append(
                "NOT EXISTS (SELECT 1 FROM feedback_labels l WHERE l.feedback_id = r.feedback_id)"
            )
        else:
            conditions.append(
                "EXISTS (SELECT 1 FROM feedback_labels l WHERE l.feedback_id = r.feedback_id "
                "AND analytics_label_fold(l.label) = ?)"
            )
            params.append(_fold(label))
    return " AND ".join(conditions), params
