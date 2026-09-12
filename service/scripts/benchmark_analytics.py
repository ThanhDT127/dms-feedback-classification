"""Reproducible SYNTHETIC analytics benchmark; never reads production data.

Run from the repository root:
    uv run --directory service --python 3.11 --extra dev python scripts/benchmark_analytics.py

Generated databases, baseline modules and results stay under .hermes/. Timings
are instrumented service calls, not HTTP/production latency. Cold means a fresh
repository's projection cache, NOT flushed OS/SQLite page caches. Warm means a
second call on that same repository. The old nine calls share one repository,
so its existing projection TTL cache is preserved (a conservative comparison).
"""

from __future__ import annotations

import argparse
import gc
import hashlib
import importlib.util
import json
import platform
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import time
import types
from collections import Counter
from dataclasses import asdict
from datetime import date, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
ROWS = 27_012
PANELS = {
    "overview": "overview",
    "dailyTrend": "daily_trend",
    "issueTypes": "issue_types",
    "geography": "geography",
    "sources": "sources",
    "units": "units",
    "groups": "groups",
    "products": "products",
    "status": "status_backlog",
}
COUNTERS = (
    "sql_rows_materialized",
    "full_record_rows_materialized",
    "full_projection_fetch_calls",
    "sql_select_statements",
)


class SqlCounter:
    """Count actual SQLite row materializations, including scalar/label rows.

    raw_data_json in the returned columns identifies full-record rows. SELECT
    counts use SQLite tracing; rows visited inside SQL/UDFs are NOT counted as
    materialized. Wrapping each connection's row factory preserves SQLite Row.
    """

    def __init__(self) -> None:
        self.counts: Counter[str] = Counter()

    def attach(self, repository: Any) -> None:
        original_connection = repository._conn
        original_fetch = repository.fetch_analytics_rows

        def connection():
            conn = original_connection()
            original_factory = conn.row_factory

            def row_factory(cursor, row):
                self.counts["sql_rows_materialized"] += 1
                if any(column[0] == "raw_data_json" for column in cursor.description):
                    self.counts["full_record_rows_materialized"] += 1
                return original_factory(cursor, row) if original_factory else row

            def trace(statement):
                if statement.lstrip().upper().startswith(("SELECT", "WITH")):
                    self.counts["sql_select_statements"] += 1

            conn.row_factory = row_factory
            conn.set_trace_callback(trace)
            return conn

        def fetch():
            self.counts["full_projection_fetch_calls"] += 1
            return original_fetch()

        repository._conn = connection
        repository.fetch_analytics_rows = fetch

    def reset(self) -> None:
        self.counts.clear()


def self_test() -> None:
    """Test real SQLite materialization, cursor iteration, and cached reads."""

    class Repository:
        cached = None

        def _conn(self):
            connection = sqlite3.connect(":memory:")
            connection.row_factory = sqlite3.Row
            return connection

        def fetch_analytics_rows(self):
            if self.cached is None:
                with self._conn() as connection:
                    self.cached = connection.execute("SELECT 'payload' AS raw_data_json").fetchall()
            return self.cached

    repository = Repository()
    counter = SqlCounter()
    counter.attach(repository)
    assert repository.fetch_analytics_rows()[0]["raw_data_json"] == "payload"
    assert counter.counts["sql_rows_materialized"] == 1
    assert counter.counts["full_record_rows_materialized"] == 1
    assert counter.counts["full_projection_fetch_calls"] == 1
    counter.reset()
    repository.fetch_analytics_rows()
    assert counter.counts["sql_rows_materialized"] == 0
    assert counter.counts["full_projection_fetch_calls"] == 1
    with repository._conn() as connection:
        assert [row[0] for row in connection.execute("SELECT 1 UNION ALL SELECT 2")] == [1, 2]
    assert counter.counts["sql_rows_materialized"] == 2
    assert counter.counts["sql_select_statements"] == 1
    print("SQL instrumentation self-test passed", flush=True)


def git(*arguments: str) -> str:
    return subprocess.check_output(["git", "-C", str(ROOT), *arguments]).decode("utf-8")


def load_baseline(folder: Path, ref: str):
    """Isolate both old modules without checkout/stash or patching application imports."""
    import dms.analytics

    name = "dms.analytics._benchmark_baseline"
    package = types.ModuleType(name)
    package.__path__ = [str(folder), *dms.analytics.__path__]
    sys.modules[name] = package
    loaded = {}
    for module_name in ("repository", "service"):
        source = git("show", f"{ref}:service/src/dms/analytics/{module_name}.py")
        filename = folder / f"{module_name}.py"
        filename.write_text(source, encoding="utf-8")
        spec = importlib.util.spec_from_file_location(f"{name}.{module_name}", filename)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        loaded[module_name] = module
    assert (
        loaded["service"].FeedbackAnalyticsRepository
        is loaded["repository"].FeedbackAnalyticsRepository
    )
    return loaded["repository"].FeedbackAnalyticsRepository, loaded[
        "service"
    ].FeedbackAnalyticsService


def seed_fixture(path: Path, repository_class: Any) -> dict:
    """One explicitly synthetic job, bulk transaction, real SQLite JSON strings.

    Direct SQL is intentional: the normal per-result version lookup is not
    indexed by source row and can make this large one-job seed quadratic.
    Current records, immutable versions and two label memberships per row are
    seeded together. No classification/model-quality claim is made.
    """
    from dms.classification_jobs import ClassificationJobStore

    assert not path.exists(), "Refusing to seed an existing database"
    jobs = ClassificationJobStore(path)
    jobs.create_job(
        job_id="synthetic-27012",
        owner_username="synthetic-benchmark",
        owner_role="user",
        filename="SYNTHETIC-not-production.xlsx",
        mode="single",
        input_path="SYNTHETIC-input.xlsx",
        output_path="SYNTHETIC-output.xlsx",
    )
    repository_class(path)
    now = "2026-09-01T00:00:00+00:00"
    columns = (
        "feedback_id, source_file_key, source_file_name, source_row_number, last_job_id, "
        "raw_data_json, content, normalized_content, issue_code, issue_date, source, "
        "unit_name, business_status, product, product_line, model, sentiment, brand, "
        "bm25_score, classification_state, is_active, created_at, updated_at, classified_at"
    )
    records = []
    memberships = []
    labels = ("Báo lỗi", "Báo CL tốt", "Y/c cải tiến", "Đề xuất SPM", "Bảo hành", "Website")
    statuses = ("Chưa xử lý", "Đang xử lý", "Đã xử lý", "Quá hạn")
    sentiments = ("Tiêu cực", "Trung lập", "Tích cực", None)
    narrative = (
        "Synthetic customer report: the lighting product intermittently flickers after "
        "installation. The service desk recorded inspection details, purchase channel, "
        "replacement request and follow-up notes. This is generated benchmark data only. "
    ) * 5
    for index in range(ROWS):
        content = f"Synthetic feedback group {index % 4502:05d}: lighting and service request"
        issue_date = (date(2026, 6, 1) + timedelta(days=index % 92)).isoformat()
        unit = f"Unit {index % 24:02d}"
        selected_labels = (labels[index % len(labels)], labels[(index + 1) % len(labels)])
        raw = json.dumps(
            {
                "Nội dung phản hồi": content,
                "Tỉnh/TP": f"Province {index % 8:02d}",
                "Quận/huyện": f"District {(index // 24) % 12:02d}",
                "Loại vấn đề": selected_labels[0],
                "Đơn vị": unit,
                "Mô tả chi tiết": narrative,
                "Synthetic provenance": "not production",
            },
            ensure_ascii=False,
        )
        records.append(
            (
                index + 1,
                "synthetic-27012-v1",
                "SYNTHETIC-not-production.xlsx",
                index + 2,
                "synthetic-27012",
                raw,
                content,
                content.casefold(),
                None if index % 101 == 0 else f"SYN-{index % 9004:05d}",
                None if index % 97 == 0 else issue_date,
                ("DMS", "CRM", "Email")[index % 3],
                unit,
                statuses[(index // 3) % 4],
                f"Product {index % 18:02d}",
                f"Line {index % 6}",
                None,
                sentiments[index % 4],
                "SYNTHETIC",
                None,
                "completed",
                1,
                now,
                now,
                now,
            )
        )
        memberships.extend(
            (index + 1, label, "Sản phẩm" if label in labels[:4] else "Dịch vụ", now)
            for label in selected_labels
        )
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.executemany(
            f"INSERT INTO feedback_records ({columns}) VALUES ({','.join('?' for _ in records[0])})",
            records,
        )
        connection.executemany(
            "INSERT INTO feedback_labels (feedback_id,label,major_group,created_at) VALUES (?,?,?,?)",
            memberships,
        )
        version_columns = columns.replace("last_job_id", "job_id").replace("is_active, ", "")
        source_columns = columns.replace("is_active, ", "")
        connection.execute(
            f"INSERT INTO feedback_record_versions ({version_columns}, labels_json) "
            f"SELECT {source_columns}, (SELECT json_group_array(json_object("
            "'label', label, 'major_group', major_group)) FROM feedback_labels l "
            "WHERE l.feedback_id = r.feedback_id) FROM feedback_records r"
        )
        connection.execute(
            "UPDATE classification_jobs SET status='completed', total_rows=?, rows_done=?, percent=100 "
            "WHERE job_id='synthetic-27012'",
            (ROWS, ROWS),
        )
        summary = dict(
            zip(
                (
                    "records",
                    "distinct_issue_codes",
                    "raw_json_min_bytes",
                    "raw_json_max_bytes",
                    "raw_json_mean_bytes",
                ),
                connection.execute(
                    "SELECT COUNT(*),COUNT(DISTINCT issue_code),MIN(LENGTH(CAST(raw_data_json AS BLOB))),"
                    "MAX(LENGTH(CAST(raw_data_json AS BLOB))),AVG(LENGTH(CAST(raw_data_json AS BLOB))) "
                    "FROM feedback_records"
                ).fetchone(),
                strict=True,
            )
        )
        for table in ("classification_jobs", "feedback_record_versions", "feedback_labels"):
            summary[table] = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        assert summary["records"] == ROWS
        assert summary["classification_jobs"] == 1
        assert summary["feedback_record_versions"] == ROWS
        assert summary["feedback_labels"] == ROWS * 2
        assert 1024 <= summary["raw_json_min_bytes"] <= summary["raw_json_max_bytes"] <= 2048
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    summary["fixture_sha256"] = hashlib.sha256(
        json.dumps(records, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return summary


def payload_digest(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()


def call_panel(service: Any, panel: str, scope: Any, *, current: bool):
    if panel == "issues":
        return service.issues(
            scope,
            page=1,
            page_size=25,
            source=None,
            unit_name=None,
            label=None,
            product=None,
            business_status=None,
        )
    if panel == "duplicates":
        return service.duplicate_details(scope, page=1, page_size=25)
    if panel == "priority":
        return service.priority_issues(scope, limit=10)
    if panel == "matrix":
        return service.unit_issue_type_matrix(scope)
    if current:
        return service.dashboard(scope)
    return {key: getattr(service, method)(scope) for key, method in PANELS.items()}


def main() -> None:
    from dms.analytics.models import AnalyticsFilter
    from dms.analytics.repository import FeedbackAnalyticsRepository
    from dms.analytics.service import FeedbackAnalyticsService

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--baseline",
        default="HEAD",
        help="Git source ref; default HEAD must still be baseline 402ea28",
    )
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument(
        "--scopes",
        nargs="+",
        default=["all", "date", "unit", "district", "combined"],
        choices=["all", "date", "unit", "district", "combined"],
    )
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    self_test()
    if args.self_test:
        return
    if args.repetitions < 1:
        parser.error("--repetitions must be positive")
    baseline_commit = git("rev-parse", args.baseline).strip()
    if not baseline_commit.startswith("402ea28"):
        parser.error("Expected baseline 402ea28; explicitly use --baseline 402ea28 if HEAD moved")
    if not hasattr(FeedbackAnalyticsService, "dashboard"):
        parser.error("Working tree grouped dashboard implementation is not present")
    scratch = ROOT / ".hermes"
    scratch.mkdir(exist_ok=True)
    folder = Path(tempfile.mkdtemp(prefix="analytics-benchmark-", dir=scratch))
    old_repository, old_service = load_baseline(folder, args.baseline)
    database = folder / "SYNTHETIC-27012.sqlite3"
    started = time.perf_counter()
    fixture = seed_fixture(database, FeedbackAnalyticsRepository)
    fixture["seed_elapsed_seconds"] = time.perf_counter() - started
    scopes = {
        "all": AnalyticsFilter(),
        "date": AnalyticsFilter(date_from="2026-08-01", date_to="2026-08-31"),
        "unit": AnalyticsFilter(unit_name="Unit 03"),
        "district": AnalyticsFilter(district="District 04"),
        "combined": AnalyticsFilter(
            date_from="2026-08-01",
            date_to="2026-08-31",
            unit_name="Unit 03",
            district="District 04",
        ),
    }
    source_hashes = {
        name: hashlib.sha256((ROOT / "service/src/dms/analytics" / name).read_bytes()).hexdigest()
        for name in ("repository.py", "service.py", "sql_queries.py")
        if (ROOT / "service/src/dms/analytics" / name).exists()
    }
    report = {
        "synthetic_not_production": True,
        "baseline_commit": baseline_commit,
        "working_tree_source_sha256": source_hashes,
        "python": sys.version,
        "platform": platform.platform(),
        "sqlite": sqlite3.sqlite_version,
        "fixture": fixture,
        "database": str(database),
        "repetitions": args.repetitions,
        "methodology": {
            "cold": "fresh repository projection cache; OS/SQLite page cache NOT flushed",
            "warm": "second same-panel call on same repository immediately after cold",
            "timer": "perf_counter; includes row instrumentation; excludes repository init, GC, parity/hash checks",
            "summary9": "old nine sequential summary calls share one repository and its original TTL cache",
            "sql_rows_materialized": "SQLite rows returned to Python, including labels/scalars; not SQL rows scanned",
            "full_record_rows_materialized": "returned SQLite rows containing raw_data_json column",
            "full_projection_fetch_calls": "fetch_analytics_rows invocations including in-memory cache hits",
            "scope": "service layer only; no HTTP serialization, shared API cache, browser or production traffic",
        },
        "results": [],
        "exact_payload_parity": True,
    }
    output = folder / "results.json"
    print(f"SYNTHETIC fixture: {json.dumps(fixture)}\nResults: {output}", flush=True)
    for scope_name in dict.fromkeys(args.scopes):
        scope = scopes[scope_name]
        for panel in ("issues", "duplicates", "priority", "matrix", "summary9_vs_dashboard"):
            expected = None
            rows = []
            for variant, repo_class, service_class in (
                ("baseline", old_repository, old_service),
                ("current", FeedbackAnalyticsRepository, FeedbackAnalyticsService),
            ):
                samples = {"cold": [], "warm": []}
                for repetition in range(args.repetitions):
                    repo = repo_class(database)
                    service = service_class(repo)
                    counter = SqlCounter()
                    counter.attach(repo)
                    for temperature in ("cold", "warm"):
                        gc.collect()
                        counter.reset()
                        start = time.perf_counter()
                        payload = call_panel(service, panel, scope, current=variant == "current")
                        elapsed = time.perf_counter() - start
                        counts = {key: counter.counts[key] for key in COUNTERS}
                        if expected is None:
                            expected = payload
                        parity = payload == expected
                        if not parity:
                            report["exact_payload_parity"] = False
                            (
                                folder / f"parity-{scope_name}-{panel}-{variant}-{temperature}.json"
                            ).write_text(
                                json.dumps(
                                    {"expected": expected, "actual": payload},
                                    ensure_ascii=False,
                                    indent=2,
                                ),
                                encoding="utf-8",
                            )
                        samples[temperature].append(
                            {
                                "repetition": repetition + 1,
                                "elapsed_ms": elapsed * 1000,
                                **counts,
                                "exact_payload_parity": parity,
                            }
                        )
                    del service, repo
                for temperature, observations in samples.items():
                    row = {
                        "scope": scope_name,
                        "filter": asdict(scope),
                        "panel": panel,
                        "variant": variant,
                        "temperature": temperature,
                        "elapsed_ms_median": statistics.median(
                            item["elapsed_ms"] for item in observations
                        ),
                        "counter_medians": {
                            key: statistics.median(item[key] for item in observations)
                            for key in COUNTERS
                        },
                        "payload_sha256": payload_digest(payload),
                        "samples": observations,
                        "exact_payload_parity": all(
                            item["exact_payload_parity"] for item in observations
                        ),
                    }
                    rows.append(row)
                    report["results"].append(row)
                output.write_text(
                    json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
                )
            print(
                json.dumps(
                    {
                        "scope": scope_name,
                        "panel": panel,
                        "measurements": [
                            {
                                key: row[key]
                                for key in (
                                    "variant",
                                    "temperature",
                                    "elapsed_ms_median",
                                    "counter_medians",
                                    "exact_payload_parity",
                                )
                            }
                            for row in rows
                        ],
                    }
                ),
                flush=True,
            )
    final_hashes = {
        name: hashlib.sha256((ROOT / "service/src/dms/analytics" / name).read_bytes()).hexdigest()
        for name in source_hashes
    }
    report["working_tree_changed_during_run"] = source_hashes != final_hashes
    report["working_tree_final_source_sha256"] = final_hashes
    expected_count = len(set(args.scopes)) * 5 * 2 * 2
    assert len(report["results"]) == expected_count
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"Verified {expected_count} measurement groups. Exact payload parity: "
        f"{report['exact_payload_parity']}. Source changed during run: "
        f"{report['working_tree_changed_during_run']}. Report: {output}",
        flush=True,
    )
    if not report["exact_payload_parity"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
