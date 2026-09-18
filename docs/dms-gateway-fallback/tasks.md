# DMS Gateway + fallback — Tasks

**Trạng thái: HOÀN THÀNH.** Đã hoàn thành triển khai Gateway transport, fallback, identity propagation, settings, usage tracker audit và test suite offline (173 tests passed).

Tài liệu liên quan: [proposal](proposal.md), [design](design.md), [spec](specs/dms-gateway-fallback/spec.md), [hướng dẫn sử dụng](HUONG-DAN-SU-DUNG.md).

## 0. Khóa baseline và phạm vi

- [ ] Kiểm tra `git status --short --branch`, HEAD và thay đổi người dùng; giữ nguyên `docs/gateway-fallback-drop-mail-alerts/` và `.hermes/`.
- [ ] Chốt contract GW-01..GW-12, đặc biệt strict pre-send fallback, probe fail-closed, log-only notification và thay đổi job error trong Gateway mode.
- [ ] Kiểm kê lại mọi call LLM trong `service/src` và `service/scripts`; phân loại text/JSON/admin/background/CLI. Nếu xuất hiện OCR/embedding thì ghi bổ sung scope trước khi làm.
- [ ] Xác định interpreter/venv và manifests `service/pyproject.toml`, `service/requirements.txt`; không dùng mù `Makefile` vì đang hard-code `d:\Works\.venv`.
- [ ] Chuẩn bị test isolation: chặn dotenv ở Pydantic lẫn python-dotenv trước import; không đọc `.env`/service-account thật; chặn egress trừ loopback fake server/asyncio wakeup. Không cài thêm production dependency.
- [ ] Chạy baseline đầy đủ trong môi trường cô lập; lưu command/output và failure có sẵn. Không giả định baseline của CRM là baseline DMS.

## 1. Settings và bảo mật API — GW-01, GW-11

**Sửa dự kiến:** `service/src/dms/settings.py`, `service/src/dms/web/api/settings_api.py`.
**Test hiện có để mở rộng:** `service/tests/test_settings.py`, `test_settings_api.py`, `test_settings_provider.py`.

- [ ] Test backend mặc định/legacy không đổi; Gateway-only không yêu cầu Google key.
- [ ] Test endpoint URL đầy đủ, không redirect/userinfo/query/fragment; HTTP phải opt-in, alias giữ literal; key invalid báo lỗi không lộ giá trị.
- [ ] Test threshold/interval runtime validation, fallback mặc định tắt, thiếu direct config thì Gateway-only có cảnh báo.
- [ ] Test mask `gateway_api_key` và `GATEWAY_API_KEY`, kể cả `get_settings_partial` fallback; validation exception không chứa key.
- [ ] Triển khai các field/validator theo design; không ghi `.env` thật, không thêm field editor frontend.
- [ ] Gateway save chỉ validate local, không gọi model hoặc nói đã xác thực model; explicit test mới được generation.

## 2. Scope actor/job và typed errors — GW-03, GW-08

**Cập nhật phạm vi:** không tạo module context; dùng keyword arguments tường minh theo yêu cầu thu gọn của người dùng.
**Sửa:** `exceptions.py`, `classification_worker.py`, `watcher.py`, `web/api/settings_api.py`, các CLI được liệt kê trong design.
**Test:** mở rộng `test_classification_worker.py`, `test_watcher_job_tracking.py`, `test_settings_api.py`, `test_classify_persistent_jobs.py`; thêm `service/tests/test_gateway.py` cho scope/transport contract.

- [ ] Test missing identity chặn trước network và trước direct fallback.
- [ ] Test authenticated upload → persisted owner → worker scope sau khi HTTP kết thúc; spoof header/body không đổi actor.
- Đã loại khỏi phạm vi theo yêu cầu: test watcher service actor, CLI actor thiếu/đủ, admin username.
- Đã loại khỏi phạm vi theo yêu cầu: test hai actor interleave, executor propagation, finally reset sau lỗi/cancel.
- Đã loại khỏi thiết kế: ContextVar/token reset. Thay bằng tham số từng call; giữ exception category/retryable đã lọc và không dùng mutable singleton headers.

## 3. Gateway transport — GW-02, GW-04

**Sửa:** `service/src/dms/gemini_client.py`.
**Test:** `service/tests/test_gateway.py` mới, `service/tests/test_gemini.py` hiện có.

- [ ] Viết wire tests bằng HTTP server loopback: đúng URL/model/Authorization/X-User/body user, temperature, JSON mode và usage response.
- [ ] Test request body thực, không chỉ SDK kwargs; text và JSON có test riêng.
- [ ] Test redirect, malformed JSON, auth/model errors, 429 bounded wait, pre-send failures, read timeout/reset/5xx unknown-outcome.
- [ ] Triển khai bằng requests với timeout và không retry adapter POST; giữ Graph/SharePoint factory nguyên vẹn.
- [ ] Retry tại một ranh giới, đúng `max_retry` attempt tổng; không sleep sau lượt cuối.
- [ ] Kiểm legacy text/JSON vẫn chạy như trước; không đổi prompt/model hay API response để ép test pass.

## 4. Fallback và probe — GW-05, GW-06, GW-07

**Sửa:** `service/src/dms/gemini_client.py`.
**Mới đề xuất:** `service/tests/test_gateway_fallback.py`.

- [ ] Fake clock/provider tests: default off, threshold, success reset, quota xen giữa, threshold bằng max_retry cứu request hiện tại.
- [ ] Test mỗi helper có tối đa một direct attempt; helper mới sau direct lỗi còn budget; direct client init lỗi không bị nuốt.
- [ ] Test direct dùng GCP path/model hiện có, không hard-code root sa-key hoặc CRM project.
- [ ] Test probe success dùng ngay kết quả; probe unreachable cho direct; probe 401/403/429/policy/unknown không direct và đóng circuit.
- [ ] Test đồng thời vượt ngưỡng chỉ một transition, một client publish; probe single-flight; không giữ khóa trong I/O.
- [ ] Test incident thứ hai, counter riêng, late response không reset incident mới; state độc lập giữa client/process.
- [ ] Triển khai state tối thiểu theo design, monotonic clock và generation guard; không dependency circuit breaker mới.

## 5. Không che lỗi Gateway ở pipeline — GW-08

**Sửa:** `pipeline/rag_product.py`, `pipeline/issue_classifier.py`, `pipeline/runner.py`, `classification_worker.py`, `watcher.py`.
**Test:** `test_pipeline.py`, `test_classification_worker.py`, `test_watcher.py`, `test_watcher_job_tracking.py`.

- [ ] Test lỗi auth/outage không biến thành RAG NONE hoặc nhãn trung lập/job complete trong Gateway mode.
- [ ] Test typed error đi qua wrapper PipelineError mà không mất category; cancel vẫn ưu tiên đúng.
- [ ] Test lỗi quyền/config/unknown/persistence không kích hoạt per-row fan-out hoặc tự requeue/file retry.
- [ ] Test transport pre-send/rate-limit được retry hữu hạn theo ngân sách job hiện hữu; phân biệt helper budget và job budget.
- [ ] Test JSON repair thực sự vẫn giữ semantics, helper repair có operation/attempt mới.
- [ ] Chỉ sửa catch liên quan Gateway typed errors; không refactor thuật toán BM25, nhãn, Excel hoặc legacy provider behavior.

## 6. Usage/audit additive — GW-09

**Sửa:** `service/src/dms/usage_tracker.py`, `gemini_client.py`, `pipeline/runner.py`, `web/deps.py`; metrics wiring nếu cần.
**Test:** `service/tests/test_usage_tracker.py`, `test_pipeline.py`, `test_metrics.py`, `test_gateway.py`.

- [ ] Viết migration test bằng DB legacy có rows; migrate hai lần không mất dữ liệu, không đổi tổng legacy.
- [ ] Chốt columns/event schema như design; uniqueness `(attempt_id,event_kind)` và query FINISHED/legacy, không đếm STARTED thành call thành công.
- [ ] Test retry/repair/admin/probe/direct ghi đúng actor/job/route/model và token thực; `_last_usage` không làm rò usage giữa user.
- [ ] Test cùng event ghi lại không tăng tổng; failed/unknown không được tính như success; provider usage trên response lỗi vẫn giữ nếu có.
- [ ] Test audit fail trước request chặn network; fail sau response không generation lại; crash orphan STARTED được báo unknown.
- [ ] Test thiếu usage/pricing là unknown, không fabricated zero; không giả response ID Gateway cho direct.
- [ ] Bỏ double-count ở runner/metrics chỉ trong Gateway mode; chưa chạy ETL hoặc sửa external ledger.

## 7. Logging, reload và notification boundary — GW-10, GW-11

**Sửa có điều kiện:** `logging_config.py`, `__main__.py`, `web/app.py`, `classification_worker.py`, `web/deps.py`, `watcher.py`.
**Test:** `test_app_lifespan.py`, `test_watcher.py`, `test_settings_provider.py`, `test_gateway_fallback.py`; notification regression qua test hiện có liên quan.

- [ ] Test event log đúng incident/duration/attempt, không chứa key/JWT/prompt/raw body; counter riêng sự cố.
- [ ] Test web, standalone worker và watcher có file log/audit; nhiều process không rotate chung path; failure không bị báo healthy console-only.
- [ ] Test transition/recovery không gọi notification; pipeline success/error vẫn đi Teams/Graph như trước, toàn bộ network được mock.
- [ ] Test key/endpoint/model/direct config đổi thì client mới dùng snapshot mới, inflight không đổi; watcher reload không bỏ sót field Gateway.
- [ ] Không thêm SMTP/webhook, không chỉnh credential/mount/deployment trong bước này.

## 8. Offline regression và fault matrix — GW-12

- [ ] Chạy focused tests rồi full Python suite dưới guard không secret/egress, lưu kết quả thật; không tick chỉ vì syntax compile.
- [ ] Chạy frontend suite theo manifest/script thực tế phát hiện lúc triển khai; xác minh settings/job-status contracts không bị phá.
- [ ] Chạy linter/format ở check mode; không auto-format toàn repo để tránh drive-by changes.
- [ ] Dựng test harness cô lập nếu đã được duyệt phạm vi infrastructure; không dùng production Gateway hoặc dữ liệu người dùng thật.
- [ ] Với text helper thật: baseline → một proxy down → LB down fallback off → LB down fallback on → recovery → outage lần hai.
- [ ] Lặp lại đầy đủ cùng matrix với JSON helper thật; không lấy text PASS thay cho JSON.
- [ ] Fault tests tuần tự trên cùng stack, restore trong finally, read-back state/health rồi kiểm recovery request; không dừng dịch vụ ngoài scope.
- [ ] Kiểm job, wire identity, route, usage/audit tương ứng; không lấy HTTP 200 hay job complete làm bằng chứng duy nhất.
- [ ] Lập báo cáo PASS/FAIL/BLOCKED/NOT RUN có command, commit, artifact paths; đối chiếu totals bằng code.

### Lệnh kiểm tra đề xuất sau khi đã thiết lập isolation

Chạy từ gốc repo bằng interpreter venv đã kiểm tra; `python` dưới đây không phải chỉ thị dùng Python global:

```text
python -m pytest service/tests/test_gateway.py service/tests/test_gateway_fallback.py service/tests/test_gemini.py service/tests/test_usage_tracker.py service/tests/test_settings.py service/tests/test_settings_api.py service/tests/test_classification_worker.py service/tests/test_watcher_job_tracking.py service/tests/test_pipeline.py
python -m pytest service/tests/
python -m ruff check service/src/ service/tests/
python -m ruff format --check service/src/ service/tests/
git diff --check
```

Hai test file Gateway là file mới đề xuất, hiện chưa tồn tại. Các lệnh trên chỉ được chạy khi dotenv/egress guard hoạt động; không khẳng định pass trước khi thực thi. Không sửa `Makefile` chỉ để hoàn thành task này.

## 9. Gate trước live — không thuộc quyền thực thi hiện tại

- [ ] Xác nhận chính xác inference endpoint, TLS/mạng riêng, Virtual Key DMS, catalog mapping, alias/model thật và actor/department mapping.
- [ ] Xác nhận project/model direct, credential source được phép đọc, quyết định bật fallback và nghĩa vụ log-only.
- [ ] Được duyệt riêng giới hạn request/token/chi phí và dataset giả; không copy volume mock load sang paid tests.
- [ ] Được duyệt môi trường fault test cô lập; không stop proxy/LB production.
- [ ] Chạy provider-real bằng real DMS helpers và test authenticated worker; output có route/identity/usage xác thực.
- [ ] Đối soát Gateway response IDs với SpendLogs actor/token/model; direct chỉ có local accounting trừ khi ingestion nguồn đó được triển khai riêng.
- [ ] Ghi ETL/dashboard/cloud billing là NOT VERIFIED nếu chưa kiểm, không gộp với app usage hoặc mock PASS.

## 10. Bàn giao và rollout riêng

- [ ] Báo cáo thay đổi theo component: cũ → mới → lý do; legacy preservation, test scope và lỗi còn lại.
- [ ] Hướng dẫn env bằng placeholders không chứa secret; không tự sửa env thật hoặc template chưa kiểm tra có secret.
- [ ] Kiểm Compose/mount/data read-only trước proposal deploy; Gateway-only không cần direct key, không thêm Gateway container vào DMS.
- [ ] Hướng dẫn rollback backend/config, giữ migration additive và mọi volumes; không drop usage/DB.
- [ ] Chỉ commit/push/deploy khi có yêu cầu riêng; trước push phải secret scan và sau push đọc lại remote.

## Ma trận truy vết

| Requirement | Nhóm việc | Bằng chứng cần |
|---|---|---|
| GW-01 | 1 | Settings/legacy/fallback validation |
| GW-02 | 3 | Wire text + JSON |
| GW-03 | 2, 8 | Authenticated job, concurrent identity |
| GW-04 | 3, 4, 5 | Typed errors, bounded attempts, no replay |
| GW-05 | 4 | Threshold/default off/current request |
| GW-06 | 4, 8 | Probe failure policy + recovery |
| GW-07 | 4 | Race, single-flight, second incident |
| GW-08 | 5 | Job-level fail-closed/cancel/repair |
| GW-09 | 6, 9 | Additive migration, no double count, reconciliation |
| GW-10 | 7 | Safe durable log, notification unchanged |
| GW-11 | 1, 7 | Secrets, save/test behavior, reload |
| GW-12 | 0, 8, 9 | Isolation, fault artifacts, honest evidence tiers |
