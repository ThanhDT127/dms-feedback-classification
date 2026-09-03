"""Deterministic KPI calculations over the current feedback projection."""

from __future__ import annotations

import json
import math
from collections import defaultdict
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
        if analytics_filter.date_from is None and analytics_filter.date_to is None:
            return rows
        return [
            row
            for row in rows
            if row.get("issue_date")
            and (
                analytics_filter.date_from is None
                or row["issue_date"] >= analytics_filter.date_from
            )
            and (analytics_filter.date_to is None or row["issue_date"] <= analytics_filter.date_to)
        ]

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
    def _distribution_sort_key(item: dict[str, Any]) -> tuple[int, str]:
        return -int(item["issue_count"]), str(item["label"])

    def _comparison(self, analytics_filter: AnalyticsFilter) -> dict[str, Any] | None:
        if analytics_filter.compare_from is None or analytics_filter.compare_to is None:
            return None
        comparison_rows = self._rows(
            AnalyticsFilter(
                date_from=analytics_filter.compare_from,
                date_to=analytics_filter.compare_to,
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
