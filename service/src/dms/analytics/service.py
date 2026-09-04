"""Deterministic KPI calculations over the current feedback projection."""

from __future__ import annotations

import json
import math
from collections import defaultdict
from datetime import date
from typing import Any

from .input_reader import METADATA_ALIASES, _canon_lower
from .models import AnalyticsFilter
from .repository import FeedbackAnalyticsRepository

_UNKNOWN = "Chưa xác định"
_QUALITY_LABELS = ("Báo lỗi", "Báo CL tốt", "Y/c cải tiến", "Đề xuất SPM")


class FeedbackAnalyticsService:
    """Build stable JSON-ready analytics payloads from active current records."""

    def __init__(self, repository: FeedbackAnalyticsRepository) -> None:
        self.repository = repository

    @staticmethod
    def _issue_code(row: dict[str, Any]) -> str | None:
        value = str(row.get("issue_code") or "").strip()
        return value or None

    def _rows(self, analytics_filter: AnalyticsFilter) -> list[dict[str, Any]]:
        rows = [row for row in self.repository.fetch_analytics_rows() if row["is_active"] == 1]
        if analytics_filter.date_from is not None or analytics_filter.date_to is not None:
            rows = [
                row
                for row in rows
                if row.get("issue_date")
                and (
                    analytics_filter.date_from is None
                    or row["issue_date"] >= analytics_filter.date_from
                )
                and (
                    analytics_filter.date_to is None
                    or row["issue_date"] <= analytics_filter.date_to
                )
            ]
        if analytics_filter.province:
            rows = [
                row
                for row in rows
                if self._matches(
                    self._raw_value(row, "Tỉnh/TP", "Tinh/TP", "Tỉnh thành", "Tinh thanh"),
                    analytics_filter.province,
                )
            ]
        if analytics_filter.district:
            rows = [
                row
                for row in rows
                if self._matches(
                    self._raw_value(row, "Quận/huyện", "Quan/huyen", "Quận huyện", "Quan huyen"),
                    analytics_filter.district,
                )
            ]
        return rows

    def _issue_codes(self, rows: list[dict[str, Any]]) -> set[str]:
        return {code for row in rows if (code := self._issue_code(row)) is not None}

    def _excluded_missing_issue_code(self, rows: list[dict[str, Any]]) -> int:
        return sum(1 for row in rows if self._issue_code(row) is None)

    @staticmethod
    def _metric(
        *,
        value: int | float | None,
        denominator: int,
        excluded_missing_issue_code: int,
        available: bool = True,
        reason: str | None = None,
    ) -> dict[str, Any]:
        return {
            "available": available,
            "value": value,
            "denominator": denominator,
            "excluded_missing_issue_code": excluded_missing_issue_code,
            "reason": reason,
        }

    @classmethod
    def _rate_metric(
        cls,
        numerator: int,
        denominator: int,
        excluded_missing_issue_code: int,
        *,
        unavailable_reason: str = "No issue codes are available for this metric.",
    ) -> dict[str, Any]:
        if denominator == 0:
            return cls._metric(
                value=None,
                denominator=0,
                excluded_missing_issue_code=excluded_missing_issue_code,
                available=False,
                reason=unavailable_reason,
            )
        return cls._metric(
            value=round(numerator * 100 / denominator, 2),
            denominator=denominator,
            excluded_missing_issue_code=excluded_missing_issue_code,
        )

    @classmethod
    def _issue_count_metric(
        cls,
        value: int,
        denominator: int,
        excluded_missing_issue_code: int,
    ) -> dict[str, Any]:
        return cls._metric(
            value=value,
            denominator=denominator,
            excluded_missing_issue_code=excluded_missing_issue_code,
            available=denominator > 0,
            reason=None if denominator > 0 else "No issue codes are available for this metric.",
        )

    @staticmethod
    def _label_names(row: dict[str, Any]) -> set[str]:
        return {str(item["label"]) for item in row["labels"]}

    @staticmethod
    def _raw_value(row: dict[str, Any], *aliases: str) -> str | None:
        raw_data = json.loads(row["raw_data_json"])
        normalized = {_canon_lower(key): value for key, value in raw_data.items()}
        for alias in aliases:
            value = str(normalized.get(_canon_lower(alias)) or "").strip()
            if value:
                return value
        return None

    @staticmethod
    def _distribution_sort_key(item: dict[str, Any]) -> tuple[int, str]:
        return -int(item["issue_count"]), str(item["label"])

    def _comparison(self, analytics_filter: AnalyticsFilter) -> dict[str, Any] | None:
        if analytics_filter.compare_from is None or analytics_filter.compare_to is None:
            return None
        comparison_rows = self._rows(
            AnalyticsFilter(
                date_from=analytics_filter.compare_from,
                date_to=analytics_filter.compare_to,
                province=analytics_filter.province,
                district=analytics_filter.district,
            )
        )
        comparison_value = len(self._issue_codes(comparison_rows))
        current_value = len(self._issue_codes(self._rows(analytics_filter)))
        if comparison_value == 0:
            return {
                "available": False,
                "value": 0,
                "change_percent": None,
                "direction": None,
                "reason": "Percentage change is unavailable when comparison value is zero.",
            }
        change_percent = round(
            (current_value - comparison_value) * 100 / comparison_value,
            2,
        )
        return {
            "available": True,
            "value": comparison_value,
            "change_percent": change_percent,
            "direction": "up"
            if change_percent > 0
            else "down"
            if change_percent < 0
            else "unchanged",
            "reason": None,
        }

    def overview(self, analytics_filter: AnalyticsFilter) -> dict[str, Any]:
        rows = self._rows(analytics_filter)
        issue_codes = self._issue_codes(rows)
        excluded = self._excluded_missing_issue_code(rows)

        processed_codes = {
            code
            for row in rows
            if (code := self._issue_code(row)) is not None
            and str(row.get("business_status") or "").strip().casefold() == "đã xử lý".casefold()
        }
        labeled_codes = {
            code
            for row in rows
            if (code := self._issue_code(row)) is not None and self._label_names(row)
        }
        sentiment_codes = {
            code
            for row in rows
            if (code := self._issue_code(row)) is not None
            and str(row.get("sentiment") or "").strip()
        }
        product_codes = {
            code
            for row in rows
            if (code := self._issue_code(row)) is not None and str(row.get("product") or "").strip()
        }
        labels_by_code: dict[str, set[str]] = defaultdict(set)
        for row in rows:
            code = self._issue_code(row)
            if code is not None:
                labels_by_code[code].update(self._label_names(row))
        multi_label_codes = {code for code, labels in labels_by_code.items() if len(labels) >= 2}

        duplicate_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            normalized = str(row.get("normalized_content") or "").strip()
            if normalized:
                duplicate_groups[normalized].append(row)
        duplicate_rows = [
            row for group in duplicate_groups.values() if len(group) >= 2 for row in group
        ]
        duplicate_codes = {
            code for row in duplicate_rows if (code := self._issue_code(row)) is not None
        }
        duplicate_record_rate = self._rate_metric(
            len(duplicate_rows),
            len(rows),
            excluded,
            unavailable_reason="No active feedback records are available.",
        )
        duplicate_record_rate["duplicate_rows"] = len(duplicate_rows)
        duplicate_issue_rate = self._rate_metric(len(duplicate_codes), len(issue_codes), excluded)
        duplicate_issue_rate["duplicate_issue_codes"] = len(duplicate_codes)

        total_issues = self._issue_count_metric(
            value=len(issue_codes),
            denominator=len(issue_codes),
            excluded_missing_issue_code=excluded,
        )
        comparison = self._comparison(analytics_filter)
        if comparison is not None:
            total_issues["comparison"] = comparison

        return {
            "total_issues": total_issues,
            "processed_issues": self._issue_count_metric(
                value=len(processed_codes),
                denominator=len(issue_codes),
                excluded_missing_issue_code=excluded,
            ),
            "label_coverage": self._rate_metric(len(labeled_codes), len(issue_codes), excluded),
            "sentiment_coverage": self._rate_metric(
                len(sentiment_codes), len(issue_codes), excluded
            ),
            "product_coverage": self._rate_metric(len(product_codes), len(issue_codes), excluded),
            "multi_label_rate": self._rate_metric(
                len(multi_label_codes), len(issue_codes), excluded
            ),
            "duplicate_record_rate": duplicate_record_rate,
            "duplicate_issue_rate": duplicate_issue_rate,
            "model_accuracy": self._metric(
                value=None,
                denominator=0,
                excluded_missing_issue_code=excluded,
                available=False,
                reason="No human-verified ground-truth labels are stored.",
            ),
        }

    def _membership_distribution(self, rows: list[dict[str, Any]], field: str) -> dict[str, Any]:
        issue_codes = self._issue_codes(rows)
        memberships: dict[str, set[str]] = defaultdict(set)
        for row in rows:
            code = self._issue_code(row)
            if code is None:
                continue
            label = str(row.get(field) or "").strip() or _UNKNOWN
            memberships[label].add(code)
        items = [
            {
                "label": label,
                "issue_count": len(codes),
                "percentage": round(len(codes) * 100 / len(issue_codes), 2) if issue_codes else 0.0,
            }
            for label, codes in memberships.items()
        ]
        items.sort(key=self._distribution_sort_key)
        return {
            "items": items,
            "membership_count": sum(item["issue_count"] for item in items),
            "total_issues": len(issue_codes),
            "excluded_missing_issue_code": self._excluded_missing_issue_code(rows),
        }

    def sources(self, analytics_filter: AnalyticsFilter) -> dict[str, Any]:
        return self._membership_distribution(self._rows(analytics_filter), "source")

    def daily_trend(self, analytics_filter: AnalyticsFilter) -> dict[str, Any]:
        rows = self._rows(analytics_filter)
        issue_codes = self._issue_codes(rows)
        codes_by_date: dict[str, set[str]] = defaultdict(set)
        excluded_missing_date = 0
        for row in rows:
            code = self._issue_code(row)
            if code is None:
                continue
            issue_date = str(row.get("issue_date") or "").strip()
            if not issue_date:
                excluded_missing_date += 1
                continue
            codes_by_date[issue_date].add(code)
        return {
            "items": [
                {"date": issue_date, "issue_count": len(codes_by_date[issue_date])}
                for issue_date in sorted(codes_by_date)
            ],
            "total_issues": len(issue_codes),
            "excluded_missing_date": excluded_missing_date,
        }

    def issue_types(self, analytics_filter: AnalyticsFilter) -> dict[str, Any]:
        rows = self._rows(analytics_filter)
        issue_codes = self._issue_codes(rows)
        memberships: dict[str, set[str]] = defaultdict(set)
        for row in rows:
            code = self._issue_code(row)
            if code is None:
                continue
            issue_type = self._raw_value(row, "Loại vấn đề", "Loai van de") or _UNKNOWN
            memberships[issue_type].add(code)
        items = [
            {
                "label": label,
                "issue_count": len(codes),
                "percentage": round(len(codes) * 100 / len(issue_codes), 2) if issue_codes else 0.0,
            }
            for label, codes in memberships.items()
        ]
        items.sort(key=self._distribution_sort_key)
        return {
            "items": items,
            "total_issues": len(issue_codes),
            "excluded_missing_issue_code": self._excluded_missing_issue_code(rows),
        }

    def duplicate_details(
        self,
        analytics_filter: AnalyticsFilter,
        *,
        page: int,
        page_size: int,
    ) -> dict[str, Any]:
        groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in self._rows(analytics_filter):
            normalized = str(row.get("normalized_content") or "").strip()
            if normalized:
                groups[normalized].append(row)
        items = []
        for rows in groups.values():
            if len(rows) < 2:
                continue
            issue_codes = sorted(
                {code for row in rows if (code := self._issue_code(row)) is not None}
            )
            units = sorted(
                {
                    str(row.get("unit_name") or "").strip()
                    for row in rows
                    if str(row.get("unit_name") or "").strip()
                }
            )
            contents = sorted({" ".join(str(row["content"]).split()) for row in rows})
            items.append(
                {
                    "content": contents[0],
                    "record_count": len(rows),
                    "duplicate_rows": len(rows) - 1,
                    "issue_count": len(issue_codes),
                    "issue_codes": issue_codes,
                    "units": units,
                }
            )
        items.sort(key=lambda item: (-int(str(item["record_count"])), str(item["content"])))
        total = len(items)
        start = (page - 1) * page_size
        return {
            "items": items[start : start + page_size],
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": math.ceil(total / page_size) if total else 0,
        }

    def status_backlog(self, analytics_filter: AnalyticsFilter) -> dict[str, Any]:
        rows = self._rows(analytics_filter)
        issue_codes = self._issue_codes(rows)
        current_statuses: dict[str, tuple[date | None, str]] = {}
        processed_codes: set[str] = set()
        dates_by_code: dict[str, list[date]] = defaultdict(list)
        for row in rows:
            code = self._issue_code(row)
            if code is None:
                continue
            status = str(row.get("business_status") or "").strip() or _UNKNOWN
            issue_date_text = str(row.get("issue_date") or "").strip()
            issue_date = date.fromisoformat(issue_date_text) if issue_date_text else None
            if status.casefold() == "đã xử lý".casefold():
                processed_codes.add(code)
                current_statuses[code] = (issue_date, status)
            elif code not in processed_codes:
                existing = current_statuses.get(code)
                if (
                    existing is None
                    or existing[0] is None
                    or (issue_date is not None and issue_date >= existing[0])
                ):
                    current_statuses[code] = (issue_date, status)
            if issue_date is not None:
                dates_by_code[code].append(issue_date)
        status_codes: dict[str, set[str]] = defaultdict(set)
        for code, (_, status) in current_statuses.items():
            status_codes[status].add(code)
        statuses = [
            {
                "label": status,
                "issue_count": len(codes),
                "percentage": round(len(codes) * 100 / len(issue_codes), 2) if issue_codes else 0.0,
            }
            for status, codes in status_codes.items()
        ]
        statuses.sort(key=self._distribution_sort_key)
        backlog_codes = issue_codes - processed_codes
        all_dates = [value for values in dates_by_code.values() for value in values]
        age_as_of = max(all_dates) if all_dates else None
        buckets = {
            "0–2 ngày": 0,
            "3–7 ngày": 0,
            "8–30 ngày": 0,
            "31+ ngày": 0,
            "Thiếu ngày": 0,
        }
        for code in backlog_codes:
            if not age_as_of or not dates_by_code.get(code):
                buckets["Thiếu ngày"] += 1
                continue
            age = (age_as_of - max(dates_by_code[code])).days
            if age <= 2:
                buckets["0–2 ngày"] += 1
            elif age <= 7:
                buckets["3–7 ngày"] += 1
            elif age <= 30:
                buckets["8–30 ngày"] += 1
            else:
                buckets["31+ ngày"] += 1
        return {
            "statuses": statuses,
            "processed_count": len(processed_codes),
            "backlog_count": len(backlog_codes),
            "backlog_rate": round(len(backlog_codes) * 100 / len(issue_codes), 2)
            if issue_codes
            else 0.0,
            "age_as_of": age_as_of.isoformat() if age_as_of else None,
            "age_buckets": [
                {"label": label, "issue_count": count} for label, count in buckets.items()
            ],
            "total_issues": len(issue_codes),
        }

    def geography(self, analytics_filter: AnalyticsFilter) -> dict[str, Any]:
        rows = self._rows(analytics_filter)
        issue_codes = self._issue_codes(rows)

        def distribution(*aliases: str) -> list[dict[str, Any]]:
            memberships: dict[str, set[str]] = defaultdict(set)
            for row in rows:
                code = self._issue_code(row)
                if code is None:
                    continue
                label = self._raw_value(row, *aliases) or _UNKNOWN
                memberships[label].add(code)
            items = [
                {
                    "label": label,
                    "issue_count": len(codes),
                    "percentage": round(len(codes) * 100 / len(issue_codes), 2)
                    if issue_codes
                    else 0.0,
                }
                for label, codes in memberships.items()
            ]
            items.sort(key=self._distribution_sort_key)
            return items

        return {
            "provinces": distribution("Tỉnh/TP", "Tinh/TP", "Tỉnh thành", "Tinh thanh"),
            "districts": distribution("Quận/huyện", "Quan/huyen", "Quận huyện", "Quan huyen"),
            "total_issues": len(issue_codes),
        }

    def unit_issue_type_matrix(self, analytics_filter: AnalyticsFilter) -> dict[str, Any]:
        cells: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
        unit_codes: dict[str, set[str]] = defaultdict(set)
        issue_type_codes: dict[str, set[str]] = defaultdict(set)
        for row in self._rows(analytics_filter):
            code = self._issue_code(row)
            if code is None:
                continue
            unit = str(row.get("unit_name") or "").strip() or _UNKNOWN
            issue_type = self._raw_value(row, "Loại vấn đề", "Loai van de") or _UNKNOWN
            cells[unit][issue_type].add(code)
            unit_codes[unit].add(code)
            issue_type_codes[issue_type].add(code)
        units = sorted(unit_codes, key=lambda unit: (-len(unit_codes[unit]), unit))
        issue_types = sorted(
            issue_type_codes,
            key=lambda issue_type: (-len(issue_type_codes[issue_type]), issue_type),
        )
        return {
            "units": units,
            "issue_types": issue_types,
            "rows": [
                {
                    "unit": unit,
                    "total": len(unit_codes[unit]),
                    "counts": {
                        issue_type: len(cells[unit][issue_type]) for issue_type in issue_types
                    },
                }
                for unit in units
            ],
        }

    def units(self, analytics_filter: AnalyticsFilter) -> dict[str, Any]:
        return self._membership_distribution(self._rows(analytics_filter), "unit_name")

    def products(self, analytics_filter: AnalyticsFilter) -> dict[str, Any]:
        rows = self._rows(analytics_filter)
        issue_codes = self._issue_codes(rows)
        memberships: dict[str, set[str]] = defaultdict(set)
        groups: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
        labels: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
        for row in rows:
            code = self._issue_code(row)
            if code is None:
                continue
            product = str(row.get("product") or "").strip() or _UNKNOWN
            memberships[product].add(code)
            for item in row["labels"]:
                label = str(item["label"])
                labels[product][label].add(code)
                group = str(item.get("major_group") or "").strip() or _UNKNOWN
                groups[product][group].add(code)

        items = []
        for product, codes in memberships.items():
            label_counts = {
                label: len(label_codes) for label, label_codes in labels[product].items()
            }
            items.append(
                {
                    "label": product,
                    "issue_count": len(codes),
                    "percentage": round(len(codes) * 100 / len(issue_codes), 2)
                    if issue_codes
                    else 0.0,
                    "major_groups": {
                        group: len(group_codes) for group, group_codes in groups[product].items()
                    },
                    "quality_labels": {
                        label: label_counts.get(label, 0) for label in _QUALITY_LABELS
                    },
                    "labels": label_counts,
                }
            )
        items.sort(key=self._distribution_sort_key)
        return {
            "items": items,
            "membership_count": sum(item["issue_count"] for item in items),
            "total_issues": len(issue_codes),
            "excluded_missing_issue_code": self._excluded_missing_issue_code(rows),
        }

    def groups(self, analytics_filter: AnalyticsFilter) -> dict[str, Any]:
        rows = self._rows(analytics_filter)
        issue_codes = self._issue_codes(rows)
        memberships: dict[str, set[str]] = defaultdict(set)
        sentiments: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
        for row in rows:
            code = self._issue_code(row)
            if code is None:
                continue
            sentiment = str(row.get("sentiment") or "").strip()
            for item in row["labels"]:
                group = str(item.get("major_group") or "").strip() or _UNKNOWN
                memberships[group].add(code)
                if sentiment:
                    sentiments[group][sentiment].add(code)

        items = [
            {
                "label": group,
                "issue_count": len(codes),
                "percentage": round(len(codes) * 100 / len(issue_codes), 2) if issue_codes else 0.0,
                "sentiment_counts": {
                    sentiment: len(codes) for sentiment, codes in sentiments[group].items()
                },
            }
            for group, codes in memberships.items()
        ]
        items.sort(key=self._distribution_sort_key)
        return {
            "items": items,
            "membership_count": sum(item["issue_count"] for item in items),
            "total_issues": len(issue_codes),
            "excluded_missing_issue_code": self._excluded_missing_issue_code(rows),
        }

    @staticmethod
    def _matches(value: Any, expected: str | None) -> bool:
        if expected is None:
            return True
        return str(value or "").strip().casefold() == expected.strip().casefold()

    def issues(
        self,
        analytics_filter: AnalyticsFilter,
        *,
        page: int,
        page_size: int,
        source: str | None,
        unit_name: str | None,
        label: str | None,
        product: str | None,
        business_status: str | None,
    ) -> dict[str, Any]:
        rows = self._rows(analytics_filter)
        filtered = [
            row
            for row in rows
            if self._matches(row.get("source"), source)
            and self._matches(row.get("unit_name"), unit_name)
            and self._matches(row.get("product"), product)
            and self._matches(row.get("business_status"), business_status)
            and (
                label is None
                or label.strip().casefold() in {name.casefold() for name in self._label_names(row)}
            )
        ]
        filtered.sort(
            key=lambda row: (str(row.get("issue_date") or ""), int(row["feedback_id"])),
            reverse=True,
        )
        total = len(filtered)
        start = (page - 1) * page_size
        page_rows = filtered[start : start + page_size]
        items = [
            {
                "feedback_id": row["feedback_id"],
                "source_file_key": row["source_file_key"],
                "source_file_name": row["source_file_name"],
                "source_row_number": row["source_row_number"],
                "job_id": row["last_job_id"],
                "issue_code": row["issue_code"],
                "issue_date": row["issue_date"],
                "source": row["source"],
                "unit_name": row["unit_name"],
                "business_status": row["business_status"],
                "content": row["content"],
                "product": row["product"],
                "product_line": row["product_line"],
                "model": row["model"],
                "sentiment": row["sentiment"],
                "brand": row["brand"],
                "bm25_score": row["bm25_score"],
                "classification_state": row["classification_state"],
                "labels": [item["label"] for item in row["labels"]],
                "raw_data": json.loads(row["raw_data_json"]),
            }
            for row in page_rows
        ]
        return {
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": math.ceil(total / page_size) if total else 0,
        }

    def data_quality(self, analytics_filter: AnalyticsFilter) -> dict[str, Any]:
        rows = self._rows(analytics_filter)
        fields: dict[str, dict[str, int]] = {}
        for field in ("issue_code", "source", "unit_name", "business_status"):
            present = sum(1 for row in rows if str(row.get(field) or "").strip())
            fields[field] = {"present": present, "missing": len(rows) - present}

        date_aliases = {_canon_lower(alias) for alias in METADATA_ALIASES["issue_date"]}
        valid_dates = 0
        invalid_dates = 0
        missing_dates = 0
        for row in rows:
            if row.get("issue_date"):
                valid_dates += 1
                continue
            raw_data = json.loads(row["raw_data_json"])
            raw_date_values = [
                value
                for key, value in raw_data.items()
                if _canon_lower(key) in date_aliases and str(value or "").strip()
            ]
            if raw_date_values:
                invalid_dates += 1
            else:
                missing_dates += 1
        fields["issue_date"] = {
            "present": valid_dates,
            "missing": missing_dates,
            "invalid": invalid_dates,
        }
        return {
            "total_records": len(rows),
            "fields": fields,
            "invalid_issue_dates": invalid_dates,
            "missing_issue_dates": missing_dates,
        }
