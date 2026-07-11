from __future__ import annotations

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
            metrics.record_failure(
                f"fail-{worker_id}-{idx}.xlsx",
                "RuntimeError",
                "boom",
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
