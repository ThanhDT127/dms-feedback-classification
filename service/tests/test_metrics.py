from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

from dms.metrics import MetricsCollector


def test_metrics_concurrent_updates_do_not_lose_counts(tmp_path):
    metrics = MetricsCollector(tmp_path / "metrics.json")

    def worker(worker_id: int):
        for idx in range(20):
            metrics.record_success(
                f"ok-{worker_id}-{idx}.xlsx",
                rows=2,
                duration=0.5,
                label_dist={"A": 1},
            )
            metrics.record_retry_failure(
                f"fail-{worker_id}-{idx}.xlsx",
                "RuntimeError",
                "boom",
                is_final=True,
            )
            metrics.record_gemini_call(
                retries=1,
                prompt_tokens=3,
                completion_tokens=4,
                cost_usd=0.01,
            )

    with ThreadPoolExecutor(max_workers=5) as pool:
        list(pool.map(worker, range(5)))

    assert metrics.files_processed == 100
    assert metrics.files_failed == 100
    assert metrics.total_rows == 200
    assert metrics.label_distribution["A"] == 100
    assert metrics.gemini_calls == 100
    assert metrics.gemini_retries == 100
    assert metrics.total_prompt_tokens == 300
    assert metrics.total_completion_tokens == 400


def test_record_retry_failure_intermediate_only_increments_retries(tmp_path):
    """Task 9.1: intermediate retry only increments total_retries, not files_failed."""
    metrics = MetricsCollector(tmp_path / "metrics.json")

    # Intermediate retries (is_final=False)
    metrics.record_retry_failure("file.xlsx", "RuntimeError", "boom", is_final=False)
    metrics.record_retry_failure("file.xlsx", "RuntimeError", "boom", is_final=False)

    assert metrics.total_retries == 2
    assert metrics.daily_retries == 2
    assert metrics.files_failed == 0
    assert metrics.daily_failed == 0


def test_record_retry_failure_final_increments_files_failed(tmp_path):
    """Task 9.1: final retry increments both total_retries and files_failed."""
    metrics = MetricsCollector(tmp_path / "metrics.json")

    # 2 intermediate + 1 final
    metrics.record_retry_failure("file.xlsx", "RuntimeError", "boom", is_final=False)
    metrics.record_retry_failure("file.xlsx", "RuntimeError", "boom", is_final=False)
    metrics.record_retry_failure("file.xlsx", "RuntimeError", "boom", is_final=True)

    assert metrics.total_retries == 3
    assert metrics.files_failed == 1
    assert metrics.daily_failed == 1


def test_record_success_was_previously_failed_decrements(tmp_path):
    """Task 9.2: record_success(was_previously_failed=True) decrements files_failed."""
    metrics = MetricsCollector(tmp_path / "metrics.json")

    # Simulate a file that failed then got auto-reset and succeeded
    metrics.record_retry_failure("file.xlsx", "RuntimeError", "boom", is_final=True)
    assert metrics.files_failed == 1

    metrics.record_success("file.xlsx", rows=10, duration=1.0, was_previously_failed=True)
    assert metrics.files_failed == 0
    assert metrics.files_processed == 1


def test_record_success_was_previously_failed_floor_at_zero(tmp_path):
    """Task 9.2: files_failed never goes below 0."""
    metrics = MetricsCollector(tmp_path / "metrics.json")
    assert metrics.files_failed == 0

    # Decrement on success when nothing failed yet — should stay at 0
    metrics.record_success("file.xlsx", rows=5, duration=0.5, was_previously_failed=True)
    assert metrics.files_failed == 0


def test_reconstruct_from_seen_rebuilds_labels(tmp_path):
    """Task 9.3: _reconstruct_from_seen() rebuilds label_distribution from seen_files."""
    seen_path = tmp_path / "seen_files.json"
    seen_data = {
        "file1": {
            "status": "done",
            "total_rows": 10,
            "duration_seconds": 1.5,
            "processed_at": "2026-07-16T10:00:00Z",
            "name": "file1.xlsx",
            "label_distribution": {"ProductQuality": 3, "Price": 2},
        },
        "file2": {
            "status": "done",
            "total_rows": 5,
            "duration_seconds": 0.8,
            "processed_at": "2026-07-16T11:00:00Z",
            "name": "file2.xlsx",
            "label_distribution": {"ProductQuality": 1, "Service": 4},
        },
        "file3": {
            "status": "failed",
            "failures": 3,
            "name": "file3.xlsx",
            "last_attempt": "2026-07-16T12:00:00Z",
        },
    }
    seen_path.write_text(json.dumps(seen_data), encoding="utf-8")

    metrics = MetricsCollector(tmp_path / "metrics.json")

    # Label distribution should be reconstructed from seen_files
    assert dict(metrics.label_distribution) == {
        "ProductQuality": 4,
        "Price": 2,
        "Service": 4,
    }
    # File counts should be reconstructed
    assert metrics.files_processed >= 2
    assert metrics.files_failed >= 1


def test_reconstruct_from_seen_empty_preserves_existing(tmp_path):
    """Task 9.3: empty seen_files preserves existing label data."""
    # Create metrics.json with existing labels
    metrics_data = {
        "files_processed": 5,
        "files_failed": 1,
        "total_rows_processed": 100,
        "total_processing_seconds": 10.0,
        "label_distribution": {"ProductQuality": 10, "Price": 5},
        "total_retries": 2,
    }
    (tmp_path / "metrics.json").write_text(json.dumps(metrics_data), encoding="utf-8")

    # Empty seen_files
    (tmp_path / "seen_files.json").write_text("{}", encoding="utf-8")

    metrics = MetricsCollector(tmp_path / "metrics.json")

    # Existing labels should be preserved since seen_files is empty
    assert metrics.label_distribution["ProductQuality"] == 10
    assert metrics.label_distribution["Price"] == 5


def test_get_pending_retry_count(tmp_path):
    """Test get_pending_retry_count reads from seen_files.json."""
    seen_data = {
        "f1": {"status": "done"},
        "f2": {"status": "retry"},
        "f3": {"status": "failed"},
        "f4": {"status": "retry"},
    }
    (tmp_path / "seen_files.json").write_text(json.dumps(seen_data), encoding="utf-8")

    metrics = MetricsCollector(tmp_path / "metrics.json")
    assert metrics.get_pending_retry_count() == 2


def test_get_pending_retry_count_no_file(tmp_path):
    """Test get_pending_retry_count returns 0 when no seen_files.json."""
    metrics = MetricsCollector(tmp_path / "metrics.json")
    assert metrics.get_pending_retry_count() == 0


def test_total_retries_persisted_in_flush(tmp_path):
    """Verify total_retries is serialized and loaded back."""
    metrics = MetricsCollector(tmp_path / "metrics.json")
    metrics.record_retry_failure("f.xlsx", "Error", "msg", is_final=False)
    metrics.record_retry_failure("f.xlsx", "Error", "msg", is_final=False)
    metrics.flush()

    # Load back
    data = json.loads((tmp_path / "metrics.json").read_text(encoding="utf-8"))
    assert data["total_retries"] == 2

    # Verify reconstruction from file
    metrics2 = MetricsCollector(tmp_path / "metrics.json")
    assert metrics2.total_retries == 2


def test_reconstruct_from_seen_overwrites_stale_labels(tmp_path):
    """Edge case: metrics.json has same label count but wrong content."""
    # metrics.json has 2 labels (stale/wrong data)
    metrics_data = {
        "files_processed": 2,
        "files_failed": 0,
        "total_rows_processed": 15,
        "total_processing_seconds": 2.0,
        "label_distribution": {"OldLabel": 5, "StaleLabel": 3},
        "total_retries": 0,
    }
    (tmp_path / "metrics.json").write_text(json.dumps(metrics_data), encoding="utf-8")

    # seen_files.json also has 2 labels but CORRECT data
    seen_data = {
        "file1": {
            "status": "done",
            "total_rows": 10,
            "duration_seconds": 1.0,
            "processed_at": "2026-07-16T10:00:00Z",
            "name": "file1.xlsx",
            "label_distribution": {"ProductQuality": 3, "Service": 2},
        },
        "file2": {
            "status": "done",
            "total_rows": 5,
            "duration_seconds": 1.0,
            "processed_at": "2026-07-16T11:00:00Z",
            "name": "file2.xlsx",
            "label_distribution": {"ProductQuality": 1, "Service": 4},
        },
    }
    (tmp_path / "seen_files.json").write_text(json.dumps(seen_data), encoding="utf-8")

    metrics = MetricsCollector(tmp_path / "metrics.json")

    # Should have overwritten with seen_files data, not kept stale labels
    assert "OldLabel" not in metrics.label_distribution
    assert "StaleLabel" not in metrics.label_distribution
    assert metrics.label_distribution["ProductQuality"] == 4
    assert metrics.label_distribution["Service"] == 6
