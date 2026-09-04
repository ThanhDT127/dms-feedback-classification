from __future__ import annotations

from pathlib import Path

import pytest
from analytics_support import create_job, make_record, make_result, seed_classified_records

from dms.analytics import AnalyticsFilter, FeedbackAnalyticsRepository, FeedbackInputRecord
from dms.analytics.service import FeedbackAnalyticsService
from dms.classification_jobs import ClassificationJobStore


@pytest.fixture
def repo(tmp_path: Path) -> FeedbackAnalyticsRepository:
    return FeedbackAnalyticsRepository(tmp_path / "classification_jobs.db")


def test_overview_uses_distinct_issue_codes_and_reports_missing_code_exclusions(repo):
    seed_classified_records(
        repo,
        db_path=repo.db_path,
        entries=[
            {
                "issue_code": "A",
                "issue_date": "2026-08-01",
                "business_status": "Đã xử lý",
                "labels": ["Báo lỗi"],
                "sentiment": "Tiêu cực",
            },
            {
                "issue_code": "A",
                "issue_date": "2026-08-01",
                "business_status": "Đã xử lý",
                "labels": ["Website"],
                "sentiment": "Tiêu cực",
            },
            {
                "issue_code": None,
                "issue_date": None,
                "business_status": None,
                "labels": [],
                "sentiment": None,
            },
        ],
    )

    body = FeedbackAnalyticsService(repo).overview(AnalyticsFilter())

    assert body["total_issues"]["value"] == 1
    assert body["processed_issues"]["value"] == 1
    assert body["label_coverage"]["available"] is True
    assert body["label_coverage"]["value"] == 100.0
    assert body["multi_label_rate"]["value"] == 100.0
    assert body["total_issues"]["excluded_missing_issue_code"] == 1
    assert body["model_accuracy"] == {
        "available": False,
        "value": None,
        "denominator": 0,
        "excluded_missing_issue_code": 1,
        "reason": "No human-verified ground-truth labels are stored.",
    }


def test_sources_units_products_and_duplicates_have_documented_buckets(repo):
    seed_classified_records(
        repo,
        db_path=repo.db_path,
        entries=[
            {
                "issue_code": "A",
                "source": None,
                "unit_name": "North",
                "product": None,
                "labels": ["Báo lỗi"],
                "sentiment": "Tiêu cực",
                "content": "  Đèn  lỗi ",
            },
            {
                "issue_code": "B",
                "source": "CRM",
                "unit_name": "North",
                "product": "LED",
                "labels": ["Báo CL tốt"],
                "sentiment": "Tích cực",
                "content": "Đèn lỗi",
            },
        ],
    )
    service = FeedbackAnalyticsService(repo)

    sources = service.sources(AnalyticsFilter())
    units = service.units(AnalyticsFilter())
    products = service.products(AnalyticsFilter())
    overview = service.overview(AnalyticsFilter())

    assert sources["membership_count"] == 2
    assert any(item["label"] == "Chưa xác định" for item in sources["items"])
    assert units["items"] == [{"label": "North", "issue_count": 2, "percentage": 100.0}]
    assert any(item["quality_labels"]["Báo lỗi"] == 1 for item in products["items"])
    assert overview["duplicate_record_rate"]["value"] == 100.0
    assert overview["duplicate_issue_rate"]["value"] == 100.0


def test_groups_count_distinct_issue_memberships_and_sentiments(repo):
    seed_classified_records(
        repo,
        db_path=repo.db_path,
        entries=[
            {
                "issue_code": "A",
                "labels": ["Báo lỗi", "Website"],
                "sentiment": "Tiêu cực",
            },
            {
                "issue_code": "A",
                "labels": ["Báo CL tốt"],
                "sentiment": "Tích cực",
            },
            {
                "issue_code": "B",
                "labels": ["Bảo hành"],
                "sentiment": None,
            },
        ],
    )

    body = FeedbackAnalyticsService(repo).groups(AnalyticsFilter())

    assert body["total_issues"] == 2
    assert body["membership_count"] == 3
    product_group = next(item for item in body["items"] if item["label"] == "Sản phẩm")
    assert product_group["issue_count"] == 1
    assert product_group["sentiment_counts"] == {"Tiêu cực": 1, "Tích cực": 1}
    assert any(item["label"] == "Website" for item in body["items"])


def test_issues_filters_and_paginates_current_records(repo):
    seed_classified_records(
        repo,
        db_path=repo.db_path,
        entries=[
            {
                "issue_code": "A",
                "issue_date": "2026-08-01",
                "source": "CRM",
                "unit_name": "North",
                "business_status": "Đã xử lý",
                "labels": ["Báo lỗi"],
                "product": "LED",
                "sentiment": "Tiêu cực",
            },
            {
                "issue_code": "B",
                "issue_date": "2026-08-02",
                "source": "Email",
                "unit_name": "South",
                "business_status": "Mới",
                "labels": ["Website"],
                "product": "Portal",
                "sentiment": "Trung lập",
            },
        ],
    )
    service = FeedbackAnalyticsService(repo)

    body = service.issues(
        AnalyticsFilter(),
        page=1,
        page_size=1,
        source="CRM",
        unit_name="North",
        label="Báo lỗi",
        product="LED",
        business_status="Đã xử lý",
    )

    assert body["total"] == 1
    assert body["page"] == 1
    assert body["page_size"] == 1
    assert body["total_pages"] == 1
    assert body["items"][0]["issue_code"] == "A"
    assert body["items"][0]["labels"] == ["Báo lỗi"]
    assert body["items"][0]["raw_data"] == {"Nội dung phản hồi": "Đèn lỗi"}


def test_data_quality_distinguishes_missing_and_invalid_input_dates(repo):
    jobs = ClassificationJobStore(repo.db_path)
    create_job(jobs, "valid")
    create_job(jobs, "invalid")
    create_job(jobs, "missing")

    valid = make_record(source_row_number=2, issue_code="A", issue_date="2026-08-01")
    invalid_base = make_record(source_row_number=2, issue_code="B", issue_date=None)
    invalid = FeedbackInputRecord(
        source_row_number=invalid_base.source_row_number,
        raw_data={"Nội dung phản hồi": "Đèn lỗi", "Ngày": "not-a-date"},
        content=invalid_base.content,
        normalized_content=invalid_base.normalized_content,
        issue_code=invalid_base.issue_code,
        issue_date=None,
        source=None,
        unit_name=invalid_base.unit_name,
        business_status=None,
    )
    missing = make_record(
        source_row_number=2,
        issue_code=None,
        issue_date=None,
        source=None,
        unit_name=None,
        business_status=None,
    )
    for job_id, record in (("valid", valid), ("invalid", invalid), ("missing", missing)):
        repo.persist_input_snapshot(
            job_id=job_id,
            source_file_key=job_id,
            source_file_name=f"{job_id}.xlsx",
            records=[record],
            deactivate_absent=False,
        )
        repo.apply_batch_results(
            job_id=job_id,
            results=[make_result(source_row_number=2)],
            minor_to_major={},
        )

    body = FeedbackAnalyticsService(repo).data_quality(AnalyticsFilter())

    assert body["total_records"] == 3
    assert body["fields"]["issue_code"] == {"present": 2, "missing": 1}
    assert body["fields"]["source"] == {"present": 1, "missing": 2}
    assert body["fields"]["issue_date"] == {
        "present": 1,
        "missing": 1,
        "invalid": 1,
    }


def test_comparison_and_paginated_detail_table(repo):
    seed_classified_records(
        repo,
        db_path=repo.db_path,
        entries=[
            {"issue_code": "A", "issue_date": "2026-08-01", "labels": []},
            {"issue_code": "B", "issue_date": "2026-08-08", "labels": []},
        ],
    )
    service = FeedbackAnalyticsService(repo)

    overview = service.overview(
        AnalyticsFilter(
            date_from="2026-08-08",
            date_to="2026-08-08",
            compare_from="2026-08-01",
            compare_to="2026-08-01",
        )
    )
    details = service.issues(
        AnalyticsFilter(),
        page=2,
        page_size=1,
        source=None,
        unit_name=None,
        label=None,
        product=None,
        business_status=None,
    )

    assert overview["total_issues"]["comparison"] == {
        "available": True,
        "value": 1,
        "change_percent": 0.0,
        "direction": "unchanged",
        "reason": None,
    }
    assert details["total"] == 2
    assert details["total_pages"] == 2
    assert len(details["items"]) == 1
    assert details["items"][0]["issue_code"] == "A"


def test_comparison_is_unavailable_when_baseline_has_zero_issues(repo):
    seed_classified_records(
        repo,
        db_path=repo.db_path,
        entries=[{"issue_code": "A", "issue_date": "2026-08-08", "labels": []}],
    )

    metric = FeedbackAnalyticsService(repo).overview(
        AnalyticsFilter(
            date_from="2026-08-08",
            date_to="2026-08-08",
            compare_from="2026-08-01",
            compare_to="2026-08-01",
        )
    )["total_issues"]["comparison"]

    assert metric["available"] is False
    assert metric["value"] == 0
    assert metric["change_percent"] is None
    assert metric["direction"] is None
    assert metric["reason"] == "Percentage change is unavailable when comparison value is zero."


def test_empty_state_has_stable_unavailable_metrics(repo):
    service = FeedbackAnalyticsService(repo)

    overview = service.overview(AnalyticsFilter())

    assert overview["total_issues"]["value"] == 0
    assert overview["total_issues"]["available"] is False
    assert overview["total_issues"]["denominator"] == 0
    assert overview["total_issues"]["reason"] == "No issue codes are available for this metric."
    assert overview["processed_issues"]["value"] == 0
    assert overview["processed_issues"]["available"] is False
    assert overview["processed_issues"]["denominator"] == 0
    assert overview["processed_issues"]["reason"] == (
        "No issue codes are available for this metric."
    )
    assert overview["label_coverage"]["available"] is False
    assert overview["duplicate_record_rate"]["available"] is False
    assert service.sources(AnalyticsFilter())["items"] == []
    assert service.groups(AnalyticsFilter())["items"] == []
    assert service.products(AnalyticsFilter())["items"] == []
    assert service.data_quality(AnalyticsFilter())["total_records"] == 0


def test_daily_trend_counts_distinct_issue_codes_in_date_order(repo):
    seed_classified_records(
        repo,
        db_path=repo.db_path,
        entries=[
            {"issue_code": "A", "issue_date": "2026-08-02", "labels": []},
            {"issue_code": "A", "issue_date": "2026-08-02", "labels": []},
            {"issue_code": "B", "issue_date": "2026-08-01", "labels": []},
            {"issue_code": "C", "issue_date": None, "labels": []},
        ],
    )

    body = FeedbackAnalyticsService(repo).daily_trend(AnalyticsFilter())

    assert body == {
        "items": [
            {"date": "2026-08-01", "issue_count": 1},
            {"date": "2026-08-02", "issue_count": 1},
        ],
        "total_issues": 3,
        "excluded_missing_date": 1,
    }


def test_issue_types_count_distinct_codes_from_raw_business_field(repo):
    seed_classified_records(
        repo,
        db_path=repo.db_path,
        entries=[
            {"issue_code": "A", "raw_data": {"Loại vấn đề": "Yêu cầu khách hàng"}},
            {"issue_code": "A", "raw_data": {"Loại vấn đề": "Yêu cầu khách hàng"}},
            {"issue_code": "B", "raw_data": {"Loại vấn đề": "Vấn đề khác"}},
            {"issue_code": "C", "raw_data": {}},
        ],
    )

    body = FeedbackAnalyticsService(repo).issue_types(AnalyticsFilter())

    assert body["items"] == [
        {"label": "Chưa xác định", "issue_count": 1, "percentage": 33.33},
        {"label": "Vấn đề khác", "issue_count": 1, "percentage": 33.33},
        {"label": "Yêu cầu khách hàng", "issue_count": 1, "percentage": 33.33},
    ]
    assert body["total_issues"] == 3


def test_duplicate_details_groups_content_and_paginates(repo):
    seed_classified_records(
        repo,
        db_path=repo.db_path,
        entries=[
            {"issue_code": "A", "content": " Đèn  lỗi ", "unit_name": "North"},
            {"issue_code": "B", "content": "đèn lỗi", "unit_name": "South"},
            {"issue_code": "C", "content": "Đèn lỗi", "unit_name": "North"},
            {"issue_code": "D", "content": "Nội dung riêng", "unit_name": "North"},
        ],
    )

    body = FeedbackAnalyticsService(repo).duplicate_details(AnalyticsFilter(), page=1, page_size=10)

    assert body["total"] == 1
    assert body["total_pages"] == 1
    assert body["items"] == [
        {
            "content": "Đèn lỗi",
            "record_count": 3,
            "duplicate_rows": 2,
            "issue_count": 3,
            "issue_codes": ["A", "B", "C"],
            "units": ["North", "South"],
        }
    ]


def test_unit_issue_type_matrix_counts_distinct_issue_codes(repo):
    seed_classified_records(
        repo,
        db_path=repo.db_path,
        entries=[
            {"issue_code": "A", "unit_name": "North", "raw_data": {"Loại vấn đề": "Báo lỗi"}},
            {"issue_code": "A", "unit_name": "North", "raw_data": {"Loại vấn đề": "Báo lỗi"}},
            {"issue_code": "B", "unit_name": "North", "raw_data": {"Loại vấn đề": "Cải tiến"}},
            {"issue_code": "C", "unit_name": "South", "raw_data": {"Loại vấn đề": "Báo lỗi"}},
        ],
    )

    body = FeedbackAnalyticsService(repo).unit_issue_type_matrix(AnalyticsFilter())

    assert body["units"] == ["North", "South"]
    assert body["issue_types"] == ["Báo lỗi", "Cải tiến"]
    assert body["rows"] == [
        {"unit": "North", "total": 2, "counts": {"Báo lỗi": 1, "Cải tiến": 1}},
        {"unit": "South", "total": 1, "counts": {"Báo lỗi": 1, "Cải tiến": 0}},
    ]


def test_geography_distribution_and_global_filter_use_raw_fields(repo):
    seed_classified_records(
        repo,
        db_path=repo.db_path,
        entries=[
            {"issue_code": "A", "raw_data": {"Tỉnh/TP": "Hà Nội", "Quận/huyện": "Hoàng Mai"}},
            {"issue_code": "A", "raw_data": {"Tỉnh/TP": "Hà Nội", "Quận/huyện": "Hoàng Mai"}},
            {"issue_code": "B", "raw_data": {"Tỉnh/TP": "Hà Nội", "Quận/huyện": "Hà Đông"}},
            {"issue_code": "C", "raw_data": {"Tỉnh/TP": "Quảng Ninh", "Quận/huyện": "Hạ Long"}},
        ],
    )
    service = FeedbackAnalyticsService(repo)

    body = service.geography(AnalyticsFilter())
    filtered = service.overview(AnalyticsFilter(province="Hà Nội", district="Hoàng Mai"))

    assert body["provinces"] == [
        {"label": "Hà Nội", "issue_count": 2, "percentage": 66.67},
        {"label": "Quảng Ninh", "issue_count": 1, "percentage": 33.33},
    ]
    assert {item["label"] for item in body["districts"]} == {"Hoàng Mai", "Hà Đông", "Hạ Long"}
    assert filtered["total_issues"]["value"] == 1


def test_status_backlog_reports_distribution_and_age_buckets(repo):
    seed_classified_records(
        repo,
        db_path=repo.db_path,
        entries=[
            {"issue_code": "A", "issue_date": "2026-08-31", "business_status": "Đã xử lý"},
            {"issue_code": "A", "issue_date": "2026-08-30", "business_status": "Chờ xử lý"},
            {"issue_code": "B", "issue_date": "2026-08-31", "business_status": "Chờ xử lý"},
            {"issue_code": "C", "issue_date": "2026-08-25", "business_status": "Chờ xử lý"},
            {"issue_code": "D", "issue_date": "2026-07-01", "business_status": None},
        ],
    )

    body = FeedbackAnalyticsService(repo).status_backlog(AnalyticsFilter())

    assert body["statuses"] == [
        {"label": "Chờ xử lý", "issue_count": 2, "percentage": 50.0},
        {"label": "Chưa xác định", "issue_count": 1, "percentage": 25.0},
        {"label": "Đã xử lý", "issue_count": 1, "percentage": 25.0},
    ]
    assert body["processed_count"] == 1
    assert body["backlog_count"] == 3
    assert body["backlog_rate"] == 75.0
    assert body["age_as_of"] == "2026-08-31"
    assert body["age_buckets"] == [
        {"label": "0–2 ngày", "issue_count": 1},
        {"label": "3–7 ngày", "issue_count": 1},
        {"label": "8–30 ngày", "issue_count": 0},
        {"label": "31+ ngày", "issue_count": 1},
        {"label": "Thiếu ngày", "issue_count": 0},
    ]
