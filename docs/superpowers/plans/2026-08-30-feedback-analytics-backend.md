# Feedback Analytics Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist every classified feedback row and its immutable per-job history in SQLite, then expose authenticated analytics APIs for the future dashboard.

**Architecture:** A shared Excel reader produces source-row-aware feedback records for both the pipeline and persistence layer. `FeedbackAnalyticsRepository` owns forward-only SQLite migrations, current-record projection, immutable versions and labels; `FeedbackAnalyticsService` calculates all dashboard KPI responses. Manual worker jobs and SharePoint Watcher jobs persist input before model calls and update results batch-by-batch through the same repository.

**Tech Stack:** Python 3.11, FastAPI, Pydantic v2, SQLite (WAL), pandas/openpyxl, pytest, ruff, mypy.

**Spec:** `docs/superpowers/specs/2026-08-30-feedback-analytics-backend-design.md`

## Global Constraints

- Reuse `Settings.classification_jobs_db_path` (`work/classification_jobs.db`); do not add a database service or a second SQLite file.
- Keep original input values in UTF-8 JSON. Missing business metadata remains SQL `NULL`; no default business value may be generated.
- `feedback_id` and source keys are technical identifiers only, never substitutes for `issue_code` KPI counts.
- Keep `classification_job_results` unchanged for existing progress/WebSocket contracts; analytics never reads that JSON table.
- Use forward-only, versioned migrations; do not physically remove a data column or historical data in this feature.
- Store current values in `feedback_records`, immutable snapshots in `feedback_record_versions`, and current labels in `feedback_labels`.
- All analytics routes require the existing authenticated-user dependency. No dashboard UI or history-view UI is included.
- Preserve existing upload, Watcher, metrics and job APIs; run their regression tests.

---

## File Structure

| File | Responsibility |
|---|---|
| `service/src/dms/analytics/models.py` | Typed input, result and filter value objects; string/date normalization. |
| `service/src/dms/analytics/input_reader.py` | Single Excel/header/metadata reader shared by Worker and `PipelineRunner`. |
| `service/src/dms/analytics/repository.py` | SQLite migrations, current projection, immutable versions, labels and low-level read queries. |
| `service/src/dms/analytics/service.py` | KPI computations and stable response payloads for the API. |
| `service/src/dms/analytics/__init__.py` | Public analytics package exports. |
| `service/src/dms/web/api/analytics_api.py` | Validates FastAPI query parameters and delegates to `FeedbackAnalyticsService`. |
| `service/src/dms/pipeline/runner.py` | Uses the shared reader and includes `source_row_number` in each callback result. |
| `service/src/dms/classification_worker.py` | Persists manual-upload input before running the model, batches results, and finalizes failed rows. |
| `service/src/dms/watcher.py` | Creates Watcher jobs before the pipeline and uses the same persistence lifecycle. |
| `service/src/dms/web/deps.py`, `service/src/dms/web/app.py` | Creates one repository per configured job database and registers `/api/analytics`. |
| `service/tests/test_analytics_input_reader.py` | Reader and metadata alias regression tests. |
| `service/tests/analytics_support.py` | Factories that create valid test jobs, input records, results and seeded analytics rows. |
| `service/tests/test_analytics_repository.py` | Migration, versioning, labels, idempotency and failure tests. |
| `service/tests/test_analytics_service.py` | KPI calculation, filters, unavailable values and details-table tests. |
| `service/tests/test_analytics_api.py` | Authenticated endpoint, validation and payload-contract tests. |
| Existing pipeline/worker/Watcher tests | Adapt fixtures and assert the new integration behavior without breaking old job behavior. |

## Public Interfaces

```python
# dms.analytics.models
@dataclass(frozen=True)
class FeedbackInputRecord:
    source_row_number: int
    raw_data: dict[str, str | None]
    content: str
    normalized_content: str
    issue_code: str | None
    issue_date: str | None  # ISO YYYY-MM-DD only when input is parseable
    source: str | None
    unit_name: str | None
    business_status: str | None

@dataclass(frozen=True)
class BatchClassificationResult:
    source_row_number: int
    text: str
    product: str | None
    product_line: str | None
    model: str | None
    bm25_score: float | None
    sentiment: str | None
    labels: list[str]
    brand: str | None

@dataclass(frozen=True)
class AnalyticsFilter:
    date_from: str | None = None
    date_to: str | None = None
    compare_from: str | None = None
    compare_to: str | None = None
```

`ParsedFeedbackWorkbook` contains `dataframe: pd.DataFrame`, `text_column: str`,
`source_row_numbers: list[int]`, and `records: list[FeedbackInputRecord]`.
The reader exports `read_feedback_workbook(input_path: Path) ->
ParsedFeedbackWorkbook` and `sha256_file(input_path: Path) -> str`.

`FeedbackAnalyticsRepository` exports:

- `persist_input_snapshot(job_id, source_file_key, source_file_name, records, deactivate_absent) -> None`
- `apply_batch_results(job_id, results, minor_to_major) -> None`
- `mark_job_unfinished_failed(job_id) -> None`
- `fetch_current_records(row=None) -> list[dict]`, `fetch_versions(job_id=None, row=None) -> list[dict]`, and `fetch_current_labels(row) -> list[str]` for repository tests.

`FeedbackAnalyticsService` exports `overview`, `sources`, `units`, `groups`,
`products`, `issues`, and `data_quality`, each receiving `AnalyticsFilter`;
`issues` additionally receives page, page size, source, unit, label, product and business-status filters.

Every ratio response uses `{"available", "value", "denominator", "excluded_missing_issue_code", "reason"}`. A period comparison additionally returns `{"available", "value", "change_percent", "direction", "reason"}`; percentage change is unavailable when comparison value is zero.

All analytics tests use the following helpers created in `tests/analytics_support.py`; this prevents fixtures from bypassing the public persistence path:

The helper module exports `make_record`, `make_result`, `create_job`, and
`seed_classified_records` with the exact parameter names shown in Task 2.

`seed_classified_records()` creates one normal `classification_jobs` row per entry, persists a one-row source snapshot, and applies its labels/result through `apply_batch_results()`. Each entry accepts the exact keys of `make_record` plus `labels`, `product`, and `sentiment`.

### Task 1: Build the shared Excel input reader

**Files:**

- Create: `service/src/dms/analytics/__init__.py`
- Create: `service/src/dms/analytics/models.py`
- Create: `service/src/dms/analytics/input_reader.py`
- Create: `service/tests/test_analytics_input_reader.py`
- Modify: `service/src/dms/pipeline/runner.py:40-117`
- Test: `service/tests/test_pipeline.py:152-157`

**Consumes:** pandas, `unidecode`, and the existing `TEXT_ALIASES`/header-detection behavior.

**Produces:** `read_feedback_workbook()` and all input model types used by the repository, Worker and runner. The existing symbols `TEXT_ALIASES`, `_canon_lower`, and `detect_header_and_textcol` remain importable from `dms.pipeline.runner` as compatibility re-exports.

- [ ] **Step 1: Write failing reader tests**

```python
def test_reader_keeps_source_rows_aliases_and_null_metadata(tmp_path: Path):
    input_path = tmp_path / "feedback.xlsx"
    pd.DataFrame([
        ["exported", None, None, None],
        ["Mã vấn đề", "Ngày", "Nguồn", "Nội dung phản hồi"],
        ["MA-1", "15/08/2026", "CRM", "Đèn không sáng"],
        [None, None, None, "Cần catalogue"],
    ]).to_excel(input_path, index=False, header=False)

    parsed = read_feedback_workbook(input_path)

    assert parsed.source_row_numbers == [3, 4]
    assert parsed.records[0].issue_code == "MA-1"
    assert parsed.records[0].issue_date == "2026-08-15"
    assert parsed.records[1].source is None
    assert parsed.records[1].content == "Cần catalogue"


def test_reader_normalizes_duplicate_content_without_inventing_metadata(tmp_path: Path):
    input_path = tmp_path / "feedback.xlsx"
    pd.DataFrame({"Nội dung phản hồi": ["  Lỗi   ĐÈN "]}).to_excel(input_path, index=False)

    record = read_feedback_workbook(input_path).records[0]

    assert record.normalized_content == "lỗi đèn"
    assert record.issue_code is None
    assert record.source is None
```

- [ ] **Step 2: Run the focused reader test to verify it fails**

Run: `pytest tests/test_analytics_input_reader.py -v`

Expected: FAIL because `dms.analytics` and `read_feedback_workbook` do not exist.

- [ ] **Step 3: Implement models and reader with source-row preservation**

```python
METADATA_ALIASES = {
    "issue_code": ("Mã vấn đề", "Ma van de"),
    "issue_date": ("Ngày ghi nhận", "Ngày", "Date"),
    "source": ("Nguồn", "Source"),
    "unit_name": ("Tên đơn vị", "Đơn vị", "Unit"),
    "business_status": ("Trạng thái", "Status"),
}

def normalize_text(value: object) -> str | None:
    value = "" if pd.isna(value) else str(value).strip()
    return value or None

def normalize_duplicate_content(value: str) -> str:
    return re.sub(r"\s+", " ", value).casefold().strip()
```

Move the existing header detection helpers into `input_reader.py`. Track the one-based Excel row for each emitted DataFrame row: header-based files start at `header_row_index + 2`; fallback files start at row `1`. Parse dates with `pd.to_datetime(value, dayfirst=True, errors="coerce")`, write valid dates as ISO dates, and leave the typed date `None` for blank or unparseable values while retaining the exact raw value in `raw_data`.

Make `PipelineRunner` import and re-export the legacy helpers, then replace its local `pd.read_excel`/header-detection block with `read_feedback_workbook(input_path)`.

- [ ] **Step 4: Run reader and existing header tests**

Run: `pytest tests/test_analytics_input_reader.py tests/test_pipeline.py::test_detect_header_and_text_column -v`

Expected: PASS; source rows map to physical Excel rows and legacy imports still work.

- [ ] **Step 5: Commit the reader foundation**

```bash
git add service/src/dms/analytics service/src/dms/pipeline/runner.py service/tests/test_analytics_input_reader.py service/tests/test_pipeline.py
git commit -m "feat: add shared feedback input reader"
```

### Task 2: Add analytics schema, current projection and immutable versions

**Files:**

- Create: `service/src/dms/analytics/repository.py`
- Create: `service/tests/analytics_support.py`
- Create: `service/tests/test_analytics_repository.py`
- Modify: `service/src/dms/analytics/__init__.py`

**Consumes:** `FeedbackInputRecord`, `BatchClassificationResult`, `utc_now_iso()` and the same SQLite path currently used by `ClassificationJobStore`.

**Produces:** `FeedbackAnalyticsRepository` with migration, persistence and failure-finalization methods. It never changes `classification_jobs` or `classification_job_results` contracts.

- [ ] **Step 1: Write failing repository tests for the schema and idempotent persistence**

```python
def test_persist_input_creates_current_row_and_immutable_version(tmp_path: Path):
    db_path = tmp_path / "classification_jobs.db"
    jobs = ClassificationJobStore(db_path)
    create_job(jobs, "job-1")
    repo = FeedbackAnalyticsRepository(db_path)
    repo.persist_input_snapshot(
        job_id="job-1", source_file_key="sha256:abc", source_file_name="a.xlsx",
        records=[make_record(source_row_number=2, issue_code=None, content="Đèn lỗi")], deactivate_absent=False,
    )

    current = repo.fetch_current_records()
    versions = repo.fetch_versions(job_id="job-1")

    assert current[0]["issue_code"] is None
    assert json.loads(current[0]["raw_data_json"])["Nội dung phản hồi"] == "Đèn lỗi"
    assert versions[0]["classification_state"] == "pending"


def test_retry_preserves_completed_version_and_soft_deactivates_removed_watcher_rows(tmp_path: Path):
    db_path = tmp_path / "classification_jobs.db"
    jobs = ClassificationJobStore(db_path)
    create_job(jobs, "job-1")
    repo = FeedbackAnalyticsRepository(db_path)
    repo.persist_input_snapshot(job_id="job-1", source_file_key="sp-1", source_file_name="a.xlsx",
                                records=[make_record(source_row_number=2), make_record(source_row_number=3)], deactivate_absent=True)
    repo.apply_batch_results(job_id="job-1", results=[make_result(source_row_number=2, labels=["Báo lỗi"])],
                             minor_to_major={"Báo lỗi": "Sản phẩm"})
    repo.persist_input_snapshot(job_id="job-1", source_file_key="sp-1", source_file_name="a.xlsx",
                                records=[make_record(source_row_number=2)], deactivate_absent=True)

    assert repo.fetch_versions(job_id="job-1", row=2)[0]["classification_state"] == "completed"
    assert repo.fetch_current_records(row=3)[0]["is_active"] == 0
```

- [ ] **Step 2: Run repository tests to verify they fail**

Run: `pytest tests/test_analytics_repository.py -v`

Expected: FAIL with `ModuleNotFoundError` for `dms.analytics.repository`.

- [ ] **Step 3: Implement forward-only migration and persistence transactions**

Create an `analytics_schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)` table. Apply migration `1` in one transaction to create:

```sql
CREATE TABLE feedback_records (
    feedback_id INTEGER PRIMARY KEY,
    source_file_key TEXT NOT NULL,
    source_file_name TEXT NOT NULL,
    source_row_number INTEGER NOT NULL,
    last_job_id TEXT NOT NULL REFERENCES classification_jobs(job_id),
    raw_data_json TEXT NOT NULL,
    content TEXT NOT NULL,
    normalized_content TEXT NOT NULL,
    issue_code TEXT,
    issue_date TEXT,
    source TEXT,
    unit_name TEXT,
    business_status TEXT,
    product TEXT,
    product_line TEXT,
    model TEXT,
    sentiment TEXT,
    brand TEXT,
    bm25_score REAL,
    classification_state TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    classified_at TEXT,
    UNIQUE(source_file_key, source_row_number)
);
```

Create `feedback_record_versions` with its own integer key, `feedback_id` and `job_id` foreign keys, source identity, input columns, classification columns, `labels_json TEXT NOT NULL DEFAULT '[]'`, state/timestamps, and `UNIQUE(feedback_id, job_id)`. Create `feedback_labels(feedback_id, label, major_group, created_at, UNIQUE(feedback_id, label))`. Add indexes for active/date, active/issue-code, source, unit, version/job and labels/major-group.

Use `PRAGMA journal_mode=WAL`, `busy_timeout=30000`, `foreign_keys=ON`, and an `RLock`, matching the job store. `persist_input_snapshot()` must upsert the current projection, create or refresh only unfinished same-job versions, preserve completed versions on retry, and set absent records inactive only for the matching Watcher source key. `apply_batch_results()` must replace a current row's labels, update its result fields only when `last_job_id` equals the callback job, and finalize the matching version in the same transaction. `mark_job_unfinished_failed()` changes only pending rows/versions.

Create the test helpers with real persistence calls:

```python
def make_record(**values: object) -> FeedbackInputRecord:
    content = str(values.get("content", "Đèn lỗi"))
    return FeedbackInputRecord(
        source_row_number=int(values.get("source_row_number", 2)),
        raw_data={"Nội dung phản hồi": content},
        content=content,
        normalized_content=normalize_duplicate_content(content),
        issue_code=values.get("issue_code", "A"),
        issue_date=values.get("issue_date", "2026-08-01"),
        source=values.get("source", "CRM"),
        unit_name=values.get("unit_name", "North"),
        business_status=values.get("business_status"),
    )

def make_result(**values: object) -> BatchClassificationResult:
    return BatchClassificationResult(
        source_row_number=int(values.get("source_row_number", 2)), text="Đèn lỗi",
        product=values.get("product"), product_line=None, model=None, bm25_score=None,
        sentiment=values.get("sentiment"), labels=list(values.get("labels", [])), brand=None,
    )

def create_job(store: ClassificationJobStore, job_id: str) -> None:
    store.create_job(job_id=job_id, owner_username="tester", owner_role="user",
                     filename=f"{job_id}.xlsx", mode="single", input_path="input.xlsx",
                     output_path="output.xlsx")

def seed_classified_records(repo: FeedbackAnalyticsRepository, *, db_path: Path,
                            entries: list[dict[str, object]]) -> None:
    jobs = ClassificationJobStore(db_path)
    groups = {"Báo lỗi": "Sản phẩm", "Báo CL tốt": "Sản phẩm",
              "Y/c cải tiến": "Sản phẩm", "Đề xuất SPM": "Sản phẩm",
              "Bảo hành": "Dịch vụ", "Website": "Website"}
    for index, entry in enumerate(entries):
        job_id = f"seed-{index}"
        create_job(jobs, job_id)
        record = make_record(source_row_number=2, **{k: v for k, v in entry.items()
                                                       if k not in {"labels", "product", "sentiment"}})
        repo.persist_input_snapshot(job_id=job_id, source_file_key=job_id,
                                    source_file_name=f"{job_id}.xlsx", records=[record],
                                    deactivate_absent=False)
        repo.apply_batch_results(job_id=job_id,
                                 results=[make_result(source_row_number=2,
                                                      labels=list(entry.get("labels", [])),
                                                      product=entry.get("product"),
                                                      sentiment=entry.get("sentiment"))],
                                 minor_to_major=groups)
```

- [ ] **Step 4: Extend tests for label snapshot, rollback and version history**

```python
def test_batch_replaces_current_labels_but_keeps_each_version_label_snapshot(tmp_path: Path):
    db_path = tmp_path / "classification_jobs.db"
    jobs = ClassificationJobStore(db_path)
    repo = FeedbackAnalyticsRepository(db_path)
    create_job(jobs, "job-1")
    repo.persist_input_snapshot(job_id="job-1", source_file_key="sha256:a", source_file_name="a.xlsx",
                                records=[make_record(source_row_number=2)], deactivate_absent=False)
    repo.apply_batch_results(job_id="job-1", results=[make_result(source_row_number=2, labels=["Báo lỗi", "Website"])],
                             minor_to_major={"Báo lỗi": "Sản phẩm", "Website": "Website"})
    create_job(jobs, "job-2")
    repo.persist_input_snapshot(job_id="job-2", source_file_key="sha256:a", source_file_name="a.xlsx",
                                records=[make_record(source_row_number=2)], deactivate_absent=False)
    repo.apply_batch_results(job_id="job-2", results=[make_result(source_row_number=2, labels=["Bảo hành"])],
                             minor_to_major={"Bảo hành": "Dịch vụ"})

    assert repo.fetch_current_labels(row=2) == ["Bảo hành"]
    assert json.loads(repo.fetch_versions(job_id="job-1")[0]["labels_json"])[0]["label"] == "Báo lỗi"
```

- [ ] **Step 5: Run repository test suite**

Run: `pytest tests/test_analytics_repository.py -v`

Expected: PASS; verify migration works on a database that already contains `classification_jobs`.

- [ ] **Step 6: Commit persistence layer**

```bash
git add service/src/dms/analytics service/tests/test_analytics_repository.py
git commit -m "feat: persist feedback analytics records"
```

### Task 3: Persist manual upload jobs and source-row batch results

**Files:**

- Modify: `service/src/dms/pipeline/runner.py:175-499`
- Modify: `service/src/dms/classification_worker.py:37-190`
- Modify: `service/src/dms/web/deps.py:40-65, 184-216`
- Modify: `service/tests/test_pipeline.py`
- Modify: `service/tests/test_classification_worker.py`

**Consumes:** the shared reader, `FeedbackAnalyticsRepository`, current label mapping from `get_label_config_snapshot()`, and existing job-store progress APIs.

**Produces:** each callback result contains its physical source row; worker jobs persist raw rows before calling Gemini, persist each batch atomically, and mark unfinished snapshots failed/cancelled without breaking existing job results.

- [ ] **Step 1: Write failing runner and worker integration tests**

```python
def test_pipeline_progress_result_contains_physical_source_row(settings, tmp_path: Path):
    callbacks: list[list[dict]] = []
    # Add this callback to the existing test_pipeline_runner_processes_file_with_prelim_handoff
    # after its configured `runner` and `input_path` have been created.
    def capture(done=None, total=None, new_results=None, **kwargs):
        if new_results:
            callbacks.append(new_results)
    runner.run_pipeline(input_path, tmp_path / "out.xlsx", tmp_path / "ckpt.json",
                        progress_callback=capture)
    assert callbacks[0][0]["source_row_number"] == 2


def test_worker_persists_input_then_batch_results(tmp_path: Path):
    settings = _settings(tmp_path)
    db_path = tmp_path / "jobs.db"
    store = ClassificationJobStore(db_path)
    input_path = tmp_path / "complete.xlsx"
    pd.DataFrame({"Nội dung phản hồi": ["Đèn lỗi"]}).to_excel(input_path, index=False)
    store.create_job(job_id="complete", owner_username="alice", owner_role="user",
                     filename="complete.xlsx", mode="single", input_path=input_path,
                     output_path=tmp_path / "complete_out.xlsx")
    repo = FeedbackAnalyticsRepository(db_path)
    manager = ClassificationWorkerManager(settings=settings, job_store=store,
        runner_factory=lambda: SuccessfulRunner(), sharepoint_factory=lambda: None,
        analytics_repository=repo)
    manager.start()
    try:
        _wait_for_status(store, "complete", JOB_STATUS_COMPLETED)
    finally:
        manager.stop()
    assert repo.fetch_current_records()[0]["classification_state"] == "completed"
```

- [ ] **Step 2: Run focused integration tests to verify they fail**

Run: `pytest tests/test_pipeline.py::test_pipeline_progress_result_contains_physical_source_row tests/test_classification_worker.py::test_worker_persists_input_then_batch_results -v`

Expected: FAIL because callback payloads and Worker constructor do not yet expose analytics persistence.

- [ ] **Step 3: Add callback source rows and repository injection**

In `PipelineRunner`, use `ParsedFeedbackWorkbook.dataframe`/`source_row_numbers` and add `"source_row_number": source_row_numbers[i + idx_in_batch]` to every `new_results_batch` item.

Add `get_feedback_analytics_repository()` to `web/deps.py`, keyed separately in `_cache` but constructed with `settings.classification_jobs_db_path`. Add an optional `analytics_repository` argument to `ClassificationWorkerManager`; `build_default_worker_manager()` injects the dependency.

Before `runner.run_pipeline()`, Worker loads the workbook, derives `source_file_key = "sha256:" + sha256_file(input_path)`, and calls:

```python
analytics_repository.persist_input_snapshot(
    job_id=job_id,
    source_file_key=source_file_key,
    source_file_name=job["filename"],
    records=parsed.records,
    deactivate_absent=False,
)
```

Convert callback dictionaries to `BatchClassificationResult` and call `apply_batch_results(job_id=job_id, results=batch_results, minor_to_major=get_label_config_snapshot()["minor_to_major"])` before `append_results()`. On `PipelineCancelled`, unrecoverable exceptions and retryable exceptions before requeue, call `mark_job_unfinished_failed(job_id)`. A persistence exception prevents the pipeline run and flows through the existing job error path, so no output upload occurs.

- [ ] **Step 4: Make fakes use valid workbooks and add retry/cancel assertions**

Replace the fake binary upload input in affected tests with a one-row `.xlsx` file. Assert a retry of the same job retains the completed first-batch version and that a cancelled job marks only remaining pending records failed. Keep assertions for `classification_job_results` unchanged.

- [ ] **Step 5: Run worker and pipeline regressions**

Run: `pytest tests/test_pipeline.py tests/test_classification_worker.py tests/test_classification_jobs.py -v`

Expected: PASS; all existing queue/retry/progress behavior remains intact.

- [ ] **Step 6: Commit manual-job integration**

```bash
git add service/src/dms/pipeline/runner.py service/src/dms/classification_worker.py service/src/dms/web/deps.py service/tests/test_pipeline.py service/tests/test_classification_worker.py
git commit -m "feat: persist uploaded feedback classifications"
```

### Task 4: Move Watcher job creation before processing and persist its rows

**Files:**

- Modify: `service/src/dms/watcher.py:252-413`
- Modify: `service/src/dms/__main__.py:85-101`
- Modify: `service/tests/test_watcher_job_tracking.py`

**Consumes:** `ClassificationJobStore`, `FeedbackAnalyticsRepository`, `read_feedback_workbook()`, existing Watcher retry/metrics/notification behavior.

**Produces:** exactly one Watcher job per processing attempt created before the model call, with row-level persistence and current-file soft deactivation keyed by SharePoint file ID.

- [ ] **Step 1: Write failing Watcher lifecycle tests**

```python
def test_watcher_creates_running_job_and_persists_rows_before_pipeline(watcher, job_store, analytics_repo):
    file_info = {"id": "test-file-id-001", "name": "feedback.xlsx", "lastModifiedDateTime": ""}
    watcher.sharepoint_client.download_file.side_effect = (
        lambda _file_id, target: pd.DataFrame({"Nội dung phản hồi": ["Đèn lỗi"]}).to_excel(target, index=False)
    )
    def assert_persisted(*args, **kwargs):
        assert job_store.list_jobs(owner_username="system_watcher", include_results=False)[0]["status"] == "running"
        assert analytics_repo.fetch_current_records()[0]["source_file_key"] == "test-file-id-001"
        kwargs["progress_callback"](done=1, total=1, new_results=[
            {"source_row_number": 2, "text": "Đèn lỗi", "product": "", "product_line": "",
             "model": "", "bm25_score": 0, "sentiment": "Tiêu cực", "labels": ["Báo lỗi"], "brand": ""}
        ])
        return {"total_rows": 1, "processed_rows": 1, "duration_seconds": 0.1, "label_distribution": {}}
    watcher.pipeline_runner.run_pipeline.side_effect = assert_persisted

    assert watcher._process_file(file_info, {}) is True


def test_watcher_failure_marks_pending_versions_failed(watcher, job_store, analytics_repo):
    file_info = {"id": "test-file-id-002", "name": "feedback.xlsx", "lastModifiedDateTime": ""}
    watcher.sharepoint_client.download_file.side_effect = (
        lambda _file_id, target: pd.DataFrame({"Nội dung phản hồi": ["Đèn lỗi"]}).to_excel(target, index=False)
    )
    watcher.pipeline_runner.run_pipeline.side_effect = RuntimeError("provider unavailable")
    assert watcher._process_file(file_info, {}) is False
    assert analytics_repo.fetch_versions()[0]["classification_state"] == "failed"
```

- [ ] **Step 2: Run Watcher tests to verify they fail**

Run: `pytest tests/test_watcher_job_tracking.py -v`

Expected: FAIL because current Watcher creates its job only after pipeline success.

- [ ] **Step 3: Refactor `_process_file()` around the shared lifecycle**

Add an optional `analytics_repository: FeedbackAnalyticsRepository | None` constructor argument to `Watcher`. In `dms.__main__.py`, construct it with `settings.classification_jobs_db_path` alongside `ClassificationJobStore` and pass it to `Watcher`; update test fixtures to pass the same database-backed repository. After downloading the file, create the job once with UUID and call `mark_running()`. Because each Watcher retry has a new job/version, remove only the prior local derived checkpoint and output for that Watcher file before calling the pipeline; this prevents a new version being incorrectly resumed from another job. Read/persist the workbook with `source_file_key=file_info["id"]`, filename and `deactivate_absent=True`; then invoke `run_pipeline()` with `job_id=watcher_job_id` and a progress callback that updates the job, appends existing result payloads, and applies analytics batch results.

On success, upload output/checkpoint, call `complete_job()` on the same ID, then retain the existing `seen_files`, metrics, notification and cleanup behavior. On failure, call `mark_job_unfinished_failed(watcher_job_id)` before `fail_job()` on that same ID. Do not create a second success/failure job or append synthetic label-distribution-only analytics data.

- [ ] **Step 4: Run Watcher regressions**

Run: `pytest tests/test_watcher_job_tracking.py tests/test_watcher.py -v`

Expected: PASS; Watcher outcomes are recorded once and existing retry-state semantics continue to pass.

- [ ] **Step 5: Commit Watcher integration**

```bash
git add service/src/dms/watcher.py service/tests/test_watcher_job_tracking.py service/tests/test_watcher.py
git commit -m "feat: persist watcher feedback classifications"
```

### Task 5: Implement dashboard analytics service and KPI rules

**Files:**

- Create: `service/src/dms/analytics/service.py`
- Create: `service/tests/test_analytics_service.py`
- Modify: `service/src/dms/analytics/repository.py`
- Modify: `service/src/dms/analytics/__init__.py`

**Consumes:** active current records, current labels and normalized fields from the repository.

**Produces:** deterministic, JSON-serializable service responses for overview, distributions, groups, products, details and data quality. All data comes from SQLite, never `metrics.json` or Excel files.

- [ ] **Step 1: Seed current records and write failing KPI tests**

```python
@pytest.fixture
def repo(tmp_path: Path) -> FeedbackAnalyticsRepository:
    return FeedbackAnalyticsRepository(tmp_path / "classification_jobs.db")

def test_overview_uses_distinct_issue_codes_and_marks_missing_code_ratios_unavailable(repo):
    seed_classified_records(repo, db_path=repo.db_path, entries=[
        {"issue_code": "A", "issue_date": "2026-08-01", "business_status": "Đã xử lý", "labels": ["Báo lỗi"], "sentiment": "Tiêu cực"},
        {"issue_code": "A", "issue_date": "2026-08-01", "business_status": "Đã xử lý", "labels": ["Website"], "sentiment": "Tiêu cực"},
        {"issue_code": None, "issue_date": None, "business_status": None, "labels": [], "sentiment": None},
    ])

    body = FeedbackAnalyticsService(repo).overview(AnalyticsFilter())

    assert body["total_issues"]["value"] == 1
    assert body["processed_issues"]["value"] == 1
    assert body["label_coverage"]["available"] is True
    assert body["total_issues"]["excluded_missing_issue_code"] == 1


def test_sources_units_groups_products_and_duplicates_have_documented_buckets(repo):
    seed_classified_records(repo, db_path=repo.db_path, entries=[
        {"issue_code": "A", "source": None, "unit_name": "North", "product": None, "labels": ["Báo lỗi"], "sentiment": "Tiêu cực", "content": "  Đèn  lỗi "},
        {"issue_code": "B", "source": "CRM", "unit_name": "North", "product": "LED", "labels": ["Báo CL tốt"], "sentiment": "Tích cực", "content": "Đèn lỗi"},
    ])
    service = FeedbackAnalyticsService(repo)

    assert any(item["label"] == "Chưa xác định" for item in service.sources(AnalyticsFilter())["items"])
    assert any(item["quality_labels"]["Báo lỗi"] == 1 for item in service.products(AnalyticsFilter())["items"])
    assert service.overview(AnalyticsFilter())["duplicate_record_rate"]["value"] == 100.0
```

- [ ] **Step 2: Run service tests to verify they fail**

Run: `pytest tests/test_analytics_service.py -v`

Expected: FAIL because `FeedbackAnalyticsService` does not exist.

- [ ] **Step 3: Implement filters and standard metric builders**

Apply `is_active = 1` to every query. With no current date range, include every active record; with `from`/`to`, include only valid `issue_date` in the inclusive ISO range. Validate date order in the API layer, not in SQL.

Build distinct non-empty `issue_code` sets for total, processed, label/sentiment/product coverage and multi-label metrics. A code is processed when any filtered row's trim/casefold-normalized status equals `đã xử lý`. Grouped source/unit queries count distinct issue codes per bucket; return top-level `membership_count` and state that bucket shares can exceed 100% when a code belongs to multiple buckets. Convert `NULL` source/unit/product to `Chưa xác định` only in the response.

For duplicate content, group non-empty `normalized_content`; return duplicate row count, active-record denominator/rate, and a separate issue-code denominator/rate when issue codes exist. Return model accuracy as unavailable with reason `"No human-verified ground-truth labels are stored."`.

- [ ] **Step 4: Implement all service payloads with stable fields**

`overview()` returns total/processed, label/sentiment/product coverage, multi-label, duplicate metrics, accuracy and an optional period comparison. `sources()` and `units()` return `{items, membership_count}`. `groups()` returns each `major_group` with issue membership and `sentiment_counts`. `products()` returns product buckets with major-group membership and the four exact quality labels `Báo lỗi`, `Báo CL tốt`, `Y/c cải tiến`, `Đề xuất SPM`. `issues()` returns a paginated current-record table with issue/business metadata, classification values, labels and raw input JSON. `data_quality()` reports present/missing counts for each metadata field plus invalid/missing date counts.

- [ ] **Step 5: Add comparison, pagination and unavailable-value tests**

```python
def test_comparison_and_paginated_detail_table(repo):
    seed_classified_records(repo, db_path=repo.db_path, entries=[
        {"issue_code": "A", "issue_date": "2026-08-01", "labels": []},
        {"issue_code": "B", "issue_date": "2026-08-08", "labels": []},
    ])
    service = FeedbackAnalyticsService(repo)

    overview = service.overview(AnalyticsFilter(date_from="2026-08-08", date_to="2026-08-08",
                                                 compare_from="2026-08-01", compare_to="2026-08-01"))
    details = service.issues(AnalyticsFilter(), page=1, page_size=1,
                             source=None, unit_name=None, label=None, product=None,
                             business_status=None)

    assert overview["total_issues"]["comparison"]["change_percent"] == 0.0
    assert details["total"] == 2
    assert len(details["items"]) == 1
```

- [ ] **Step 6: Run analytics service tests**

Run: `pytest tests/test_analytics_repository.py tests/test_analytics_service.py -v`

Expected: PASS, including empty data, missing issue codes, membership overlap, comparison and detail pagination.

- [ ] **Step 7: Commit KPI service**

```bash
git add service/src/dms/analytics service/tests/test_analytics_service.py service/tests/test_analytics_repository.py
git commit -m "feat: add feedback analytics KPI service"
```

### Task 6: Expose authenticated `/api/analytics` endpoints

**Files:**

- Create: `service/src/dms/web/api/analytics_api.py`
- Create: `service/tests/test_analytics_api.py`
- Modify: `service/src/dms/web/app.py:17-25, 171-178`
- Modify: `service/src/dms/web/deps.py`

**Consumes:** `get_current_user`, `get_feedback_analytics_repository`, `FeedbackAnalyticsService`, and `AnalyticsFilter`.

**Produces:** seven read-only FastAPI endpoints under `/api/analytics` with ISO-date validation and no dependency on existing operational metrics routes.

- [ ] **Step 1: Write failing API contract tests**

```python
@pytest.fixture
def api_client(settings, monkeypatch):
    repository = FeedbackAnalyticsRepository(settings.classification_jobs_db_path)
    monkeypatch.setattr("dms.web.deps.get_feedback_analytics_repository", lambda: repository)
    app = create_app()
    apply_auth_overrides(app)
    return TestClient(app)

def test_analytics_overview_requires_auth_and_returns_metric_contract(api_client):
    response = api_client.get("/api/analytics/overview?from=2026-08-01&to=2026-08-31")

    assert response.status_code == 200
    assert set(response.json()["total_issues"]) >= {
        "available", "value", "denominator", "excluded_missing_issue_code", "reason"
    }


def test_analytics_rejects_invalid_or_partial_comparison_dates(api_client):
    assert api_client.get("/api/analytics/overview?from=2026-99-01").status_code == 422
    assert api_client.get("/api/analytics/overview?compare_from=2026-08-01").status_code == 422
```

- [ ] **Step 2: Run API tests to verify they fail**

Run: `pytest tests/test_analytics_api.py -v`

Expected: FAIL with 404 because the analytics router is not registered.

- [ ] **Step 3: Implement router, query validation and dependencies**

Define routes:

```text
GET /api/analytics/overview
GET /api/analytics/sources
GET /api/analytics/units
GET /api/analytics/groups
GET /api/analytics/products
GET /api/analytics/issues?page=1&page_size=50
GET /api/analytics/data-quality
```

Use aliases `from`, `to`, `compare_from`, `compare_to`; require both ends of every supplied range, parse with `date.fromisoformat`, and reject an end before its start using `HTTPException(status_code=422, detail="Invalid date range")`. `issues` also accepts optional `source`, `unit`, `label`, `product`, `status`, and enforces `page >= 1`, `1 <= page_size <= 200`. Require `Depends(get_current_user)` for every route. Register `analytics_router` in `create_app()` without changing existing router prefixes.

- [ ] **Step 4: Add empty-state, auth and detail-filter tests**

```python
def test_analytics_empty_state_and_detail_filters(api_client):
    assert api_client.get("/api/analytics/data-quality").json()["total_records"] == 0
    response = api_client.get("/api/analytics/issues?source=CRM&page=1&page_size=25")
    assert response.status_code == 200
    assert response.json()["page_size"] == 25
```

Use `apply_auth_overrides()` to verify the successful authenticated requests and a separate unoverridden app to assert `401`.

- [ ] **Step 5: Run endpoint and operational-metrics regressions**

Run: `pytest tests/test_analytics_api.py tests/test_metrics_api.py tests/test_web_authz.py -v`

Expected: PASS; `/api/metrics` behavior is unchanged and analytics data comes only from the repository fixture.

- [ ] **Step 6: Commit API layer**

```bash
git add service/src/dms/web/api/analytics_api.py service/src/dms/web/app.py service/src/dms/web/deps.py service/tests/test_analytics_api.py
git commit -m "feat: expose feedback analytics API"
```

### Task 7: Verify the backend branch and document handoff

**Files:**

- Verify: `service/src/dms/analytics/`, `service/src/dms/web/api/analytics_api.py`, and affected pipeline integration files

**Consumes:** all completed tasks and test suites.

**Produces:** a verified backend-only branch ready for code review and later dashboard branch consumption.

- [ ] **Step 1: Run focused complete analytics suite**

Run: `pytest tests/test_analytics_input_reader.py tests/test_analytics_repository.py tests/test_analytics_service.py tests/test_analytics_api.py tests/test_pipeline.py tests/test_classification_worker.py tests/test_watcher_job_tracking.py -v`

Expected: PASS.

- [ ] **Step 2: Run full test, lint and type-check commands**

Run:

```bash
pytest -q
ruff check src tests
mypy src/dms
```

Expected: all tests pass and no new ruff/mypy diagnostics are introduced. If a pre-existing diagnostic remains, capture its exact file and line separately from this feature's results.

- [ ] **Step 3: Inspect migration and API surfaces manually**

Run:

```bash
git diff main...HEAD --check
git status --short
git log --oneline main..HEAD
```

Expected: no whitespace errors, no accidental artifacts, and commits limited to the analytics backend.
