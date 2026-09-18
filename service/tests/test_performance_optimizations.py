"""Tests for performance optimizations across all tabs (OpenSpec change optimize-all-tabs-performance)."""

from __future__ import annotations

import time
from pathlib import Path

import pandas as pd
import pytest
from conftest import apply_auth_overrides
from fastapi.testclient import TestClient

from dms.classification_jobs import ClassificationJobStore
from dms.sharepoint_cache import SharePointListCache
from dms.web.api.metrics_api import tail_log_file
from dms.web.api.pipeline_api import _products_cache, _products_lock, invalidate_products_cache
from dms.web.app import create_app


@pytest.fixture
def app_client(settings, monkeypatch):
    """Test client with admin auth and initialized settings."""
    settings.ensure_runtime_dirs()
    from dms.settings import get_settings_provider

    get_settings_provider().set_for_tests(settings)
    monkeypatch.setattr("dms.web.deps.get_settings", lambda: settings)
    monkeypatch.setattr("dms.web.api.metrics_api._work_dir", lambda: settings.work_dir)
    monkeypatch.setattr("dms.web.api.metrics_api._log_dir", lambda: settings.log_dir)
    app = create_app()
    apply_auth_overrides(app)
    return TestClient(app)


# ---------------------------------------------------------------------------
# 1. Reverse Log Tail Seeking Tests
# ---------------------------------------------------------------------------


def test_tail_log_file_empty(tmp_path: Path):
    """An empty file should return an empty list."""
    empty_log = tmp_path / "empty.log"
    empty_log.touch()
    assert tail_log_file(empty_log, max_lines=200) == []


def test_tail_log_file_small(tmp_path: Path):
    """A small file with fewer lines than max_lines should return all lines."""
    small_log = tmp_path / "small.log"
    lines = [f"2026-09-14 10:00:{i:02d} [INFO] log line {i}" for i in range(25)]
    small_log.write_text("\n".join(lines) + "\n", encoding="utf-8")

    result = tail_log_file(small_log, max_lines=50)
    assert len(result) == 25
    assert result == lines


def test_tail_log_file_exceeds_max_lines(tmp_path: Path):
    """Should return only the last N lines when file has more than max_lines."""
    log_file = tmp_path / "app.log"
    lines = [f"Line {i:04d}" for i in range(500)]
    log_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    result = tail_log_file(log_file, max_lines=50)
    assert len(result) == 50
    assert result == lines[-50:]


def test_tail_log_file_large_file_performance(tmp_path: Path):
    """Should tail a multi-megabyte log file in under 50ms using reverse seek."""
    large_log = tmp_path / "large.log"
    chunk = (
        "2026-09-14 12:00:00 [INFO] Worker batch classification event payload data here\n" * 100
    ).encode("utf-8")
    with open(large_log, "wb") as f:
        for _ in range(250):  # ~2.5 MB
            f.write(chunk)
        f.write(b"2026-09-14 12:01:00 [INFO] FINAL_LINE_FOR_VERIFICATION\n")

    t0 = time.perf_counter()
    result = tail_log_file(large_log, max_lines=10)
    duration = time.perf_counter() - t0

    assert duration < 0.05, f"Tail took too long: {duration:.4f}s"
    assert len(result) == 10
    assert "FINAL_LINE_FOR_VERIFICATION" in result[-1]


# ---------------------------------------------------------------------------
# 2. SharePoint In-Memory List Cache Tests
# ---------------------------------------------------------------------------


def test_sharepoint_list_cache_hit_and_miss():
    """Cache should return None on miss, and cached data on hit."""
    cache = SharePointListCache(default_ttl=5.0)
    assert cache.get("input") is None

    test_items = [{"name": "file1.xlsx", "size": 1024}]
    cache.set("input", test_items)

    cached = cache.get("input")
    assert cached == test_items
    assert cached is not test_items


def test_sharepoint_list_cache_expiration():
    """Cache should expire after TTL."""
    cache = SharePointListCache(default_ttl=0.1)
    cache.set("output", [{"name": "out.xlsx"}])
    assert cache.get("output") is not None

    time.sleep(0.15)
    assert cache.get("output") is None


def test_sharepoint_list_cache_invalidation():
    """Invalidate should clear specific folder or all folders."""
    cache = SharePointListCache(default_ttl=60.0)
    cache.set("input", [{"name": "in.xlsx"}])
    cache.set("output", [{"name": "out.xlsx"}])

    cache.invalidate("input")
    assert cache.get("input") is None
    assert cache.get("output") is not None

    cache.invalidate()
    assert cache.get("output") is None


# ---------------------------------------------------------------------------
# 3. Label Distribution SQL Aggregation Tests
# ---------------------------------------------------------------------------


def test_sql_label_distribution_fast(tmp_path: Path):
    """Direct SQL GROUP BY should aggregate labels without scanning job payloads."""
    db_path = tmp_path / "test_jobs.db"
    store = ClassificationJobStore(db_path)

    with store._lock, store._conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS feedback_labels (
                feedback_id INTEGER,
                label TEXT NOT NULL,
                major_group TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        data = (
            [(i, "Cháy bóng", "Lỗi kỹ thuật", "2026-09-14T00:00:00") for i in range(150)]
            + [(i + 200, "Vỡ hỏng", "Vận chuyển", "2026-09-14T00:00:00") for i in range(80)]
            + [(i + 400, "Đèn nhấp nháy", "Chất lượng", "2026-09-14T00:00:00") for i in range(40)]
        )
        conn.executemany("INSERT INTO feedback_labels VALUES (?, ?, ?, ?)", data)
        conn.commit()

    dist = store.get_label_distribution()
    assert dist["Cháy bóng"] == 150
    assert dist["Vỡ hỏng"] == 80
    assert dist["Đèn nhấp nháy"] == 40


def test_metrics_endpoint_response(app_client, settings):
    """GET /api/metrics should return aggregated label distribution smoothly."""
    res = app_client.get("/api/metrics")
    assert res.status_code == 200
    data = res.json()
    assert "label_distribution" in data
    assert "total_files" in data


# ---------------------------------------------------------------------------
# 4. Classification Jobs Pagination & Lightweight Payload Tests
# ---------------------------------------------------------------------------


def test_list_jobs_pagination_and_lightweight(tmp_path: Path):
    """list_jobs should support limit, offset, and exclude result payloads by default."""
    db_path = tmp_path / "jobs.db"
    store = ClassificationJobStore(db_path)

    for i in range(15):
        job_id = f"job-{i:02d}"
        store.create_job(
            job_id=job_id,
            owner_username="admin",
            owner_role="admin",
            filename=f"file_{i}.xlsx",
            mode="single",
            input_path=f"/path/{i}",
            output_path="",
        )
        store.append_results(job_id, [{"row": r, "label": "test"} for r in range(20)])

    jobs_light = store.list_jobs(include_results=False, limit=5)
    assert len(jobs_light) == 5
    for job in jobs_light:
        assert job["results"] == []

    page1 = store.list_jobs(include_results=False, limit=5, offset=0)
    page2 = store.list_jobs(include_results=False, limit=5, offset=5)
    assert len(page1) == 5
    assert len(page2) == 5
    assert [j["job_id"] for j in page1] != [j["job_id"] for j in page2]


def test_api_classify_jobs_default_lightweight(app_client, settings, monkeypatch):
    """GET /api/classify/jobs should return lightweight jobs without heavy results payloads."""
    db_path = settings.work_dir / "classification_jobs.db"
    store = ClassificationJobStore(db_path)
    monkeypatch.setattr("dms.web.deps.get_classification_job_store", lambda: store)

    job_id = "test-job-light"
    store.create_job(
        job_id=job_id,
        owner_username="admin",
        owner_role="admin",
        filename="test.xlsx",
        mode="single",
        input_path="/path/test.xlsx",
        output_path="",
    )
    store.append_results(job_id, [{"row": 1, "large_payload": "x" * 5000}])

    res = app_client.get("/api/classify/jobs")
    assert res.status_code == 200
    jobs = res.json()
    assert len(jobs) >= 1
    target = next((j for j in jobs if j["job_id"] == job_id), None)
    assert target is not None
    assert target["results"] == []


# ---------------------------------------------------------------------------
# 5. Pipeline In-Memory Product Catalog Caching Tests
# ---------------------------------------------------------------------------


def test_pipeline_products_cache(app_client, settings, monkeypatch, tmp_path: Path):
    """GET /api/pipeline/products should cache results and re-read on mtime change."""
    invalidate_products_cache()

    custom_excel = tmp_path / "test_products.xlsx"
    monkeypatch.setattr(type(settings), "df_products_path", property(lambda self: custom_excel))

    df = pd.DataFrame(
        {
            "Sản phẩm": ["Đèn LED Bulb", "Đèn Downlight"],
            "Dòng SP": ["LED Tiết Kiệm", "LED Smart"],
            "Model": ["LED A60", "DL 90/7W"],
        }
    )
    with pd.ExcelWriter(custom_excel, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="LED", index=False)

    res1 = app_client.get("/api/pipeline/products")
    assert res1.status_code == 200
    data1 = res1.json()
    assert data1["total_products"] == 2

    with _products_lock:
        assert _products_cache["summary"] is not None
        assert _products_cache["mtime"] > 0

    res2 = app_client.get("/api/pipeline/products")
    assert res2.status_code == 200
    assert res2.json() == data1

    time.sleep(0.05)
    df2 = pd.DataFrame(
        {
            "Sản phẩm": ["Đèn LED Bulb", "Đèn Downlight", "Đèn Pha"],
            "Dòng SP": ["LED Tiết Kiệm", "LED Smart", "Công nghiệp"],
            "Model": ["LED A60", "DL 90/7W", "CP06"],
        }
    )
    with pd.ExcelWriter(custom_excel, engine="openpyxl") as writer:
        df2.to_excel(writer, sheet_name="LED", index=False)

    res3 = app_client.get("/api/pipeline/products")
    assert res3.status_code == 200
    data3 = res3.json()
    assert data3["total_products"] == 3
