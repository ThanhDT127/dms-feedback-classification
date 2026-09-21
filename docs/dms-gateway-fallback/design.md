# DMS Gateway + fallback — Design

**Trạng thái:** DRAFT; tất cả lớp/field mới dưới đây là thiết kế đề xuất, không phải API đã tồn tại. Chỉ viết tài liệu. Mốc code và phạm vi xem [proposal](proposal.md); hợp đồng nghiệm thu xem [spec](specs/dms-gateway-fallback/spec.md).

## 1. Kiến trúc tối thiểu

Giữ `GeminiClient` làm điểm vào duy nhất cho LLM. Thêm transport Gateway và state fallback nội bộ tại đó; chỉ tách một module nếu độ lớn thực tế cần, không tạo provider registry/interface một implementation. Dùng `requests` hiện có, Google SDK hiện có, tham số tường minh cho từng call, `threading.Lock`, `time.monotonic` và SQLite.

```text
Web upload → JWT/RBAC → lưu job.owner_username → worker claim job
                                                    │
Watcher → actor system_watcher + job ID ─────────────┤
CLI → actor vận hành được cấu hình rõ ───────────────┤
Admin test → admin đã xác thực ──────────────────────┤
                                                    ▼
                                    phạm vi actor/job/operation riêng
                                                    ▼
                      PipelineRunner → RAG text / IssueClassifier JSON
                                                    ▼
                                               GeminiClient
                                          ┌─────────┴────────┐
                              backend legacy            backend gateway
                              giữ hiện trạng             │
                                                kiểm identity/config
                                                        │
                                      Gateway attempt + audit cục bộ
                                          │                      │
                                    thành công             lỗi được phân loại
                                          │                      │
                                  response + usage        đủ điều kiện fallback?
                                                           │            │
                                                          có          không
                                                           │            │
                                               một direct Vertex     lỗi typed
                                               attempt / helper call   nổi lên job
                                                           │
                                               response + usage local
```

Admin test gọi client trực tiếp trong scope, không phải chạy pipeline. Routing state nằm trong client và dùng chung giữa các luồng của client đó; không dùng chung giữa process/web/watcher/container. Không thêm hạ tầng để đồng bộ circuit breaker toàn deployment.

## 2. Entry points và điểm cần sửa

| Code hiện hữu | Hành vi / thay đổi tối thiểu được đề xuất |
|---|---|
| `service/src/dms/gemini_client.py` | Thêm nhánh Gateway cho `generate`/`generate_json`, typed classification, fallback/probe, transport timeout, audit tại ranh giới request; giữ legacy |
| `service/src/dms/settings.py` | Thêm settings Gateway/fallback có validate; giữ secret khỏi repr/lỗi trả về; snapshot cấu hình mỗi call |
| `service/src/dms/exceptions.py` | Thêm lỗi Gateway có category/retryable đã lọc; đi xuyên pipeline không mất phân loại |
| `service/src/dms/pipeline/rag_product.py` | Không nuốt lỗi Gateway thành `NONE`; không dùng `_last_usage` chia sẻ để ghi usage Gateway |
| `service/src/dms/pipeline/issue_classifier.py` | Không nuốt lỗi Gateway thành chuỗi rỗng hoặc retry per-row sau lỗi quyền/hạ tầng; giữ logic repair khi phản hồi model thực sự nhận được nhưng chưa đủ dòng |
| `service/src/dms/pipeline/runner.py` | Propagate lỗi Gateway qua các catch và `PipelineError`; đặt operation `rag_extract`/`classify_batch`; không ghi lại usage đã ghi ở client; `_current_job_id` không là nguồn actor/job cho Gateway |
| `service/src/dms/classification_worker.py` | Scope từ job đã claim; phân loại lỗi để không requeue lỗi quyền/cấu hình hoặc unknown-outcome; reset scope trong `finally` |
| `service/src/dms/watcher.py` | Scope actor `system_watcher`; policy retry Gateway tương ứng; invalidate client khi cấu hình Gateway/fallback thay đổi |
| `service/src/dms/web/deps.py` | Nối tracker/client theo dependency hiện có; tránh vòng lặp dependency; một snapshot settings/client mới cho việc reload |
| `service/src/dms/web/api/settings_api.py` | Mask key trong cả dict field và raw env fallback; admin test có identity và route; Gateway save chỉ validate local, không tự generation; không báo đã xác thực nếu chưa gọi |
| `service/src/dms/usage_tracker.py` | Migration additive trong DB hiện có; attempt audit cùng nguồn usage cho Gateway mode |
| `service/src/dms/logging_config.py` | Trường audit có whitelist, lỗi lọc; đảm bảo logger ở web/worker/watcher, không chỉ watcher |
| `service/src/dms/__main__.py`, `service/src/dms/web/app.py` | Kiểm wiring lifecycle logging/tracker; chỉ sửa nếu entrypoint chưa thiết lập phần bắt buộc |
| `service/scripts/run_pipeline.py`, `service/scripts/run_and_compare.py`, `service/scripts/test_sharepoint.py` | Gateway CLI phải có actor cấu hình và operation; thiếu actor thì chặn trước network, không tự gán admin |
| `service/src/dms/notification.py` | Không sửa transport; không nối sự kiện fallback vào mail/Teams |
| `service/src/dms/http_client.py` | Không thay retry của Graph/SharePoint; KHÔNG dùng nguyên factory retry POST của nó cho inference |

Theo thu hẹp phạm vi của người dùng: không tạo `llm_context.py`, không dùng ContextVar. Truyền `actor`, `job_id`, `operation` bằng keyword argument riêng từ caller tới helper. Không nhét danh tính vào mutable singleton headers. Bỏ các test riêng watcher/CLI/admin identity và actor interleave/executor/reset; không tuyên bố các phạm vi bị bỏ đã được kiểm chứng. Giữ typed error classification để không bypass quyền/quota.

### Những việc không được bỏ qua

- `web/api/settings_api.py` hiện có cả test explicit và generation tự động khi lưu settings legacy; không thêm generation ẩn vào nhánh Gateway.
- `web/deps.py:get_settings_partial` có nhánh đọc raw env khi Settings lỗi: key mới phải được mask ở cả chữ hoa và tên field, không chỉ đường model_dump thành công.
- Watcher hiện so sánh backend/model/API key để reset lazy clients; phải bao gồm endpoint/key Gateway và cấu hình direct khi triển khai.
- RAG, classifier và runner có nhiều catch tổng quát. Chỉ sửa client mà không sửa các catch này sẽ không đạt fail-closed ở cấp nghiệp vụ.
- DMS có worker thread và nhiều web process; không lấy giả định ba worker của CRM làm topology DMS.

## 3. Cấu hình đề xuất

Các tên mới chưa tồn tại trong DMS. Không ghi giá trị thực vào `.env` khi triển khai code hoặc viết test.

| Biến | Default đề xuất | Hợp đồng |
|---|---|---|
| `GEMINI_BACKEND` | Giữ `vertex` hiện tại | Thêm `gateway`, không tự bật |
| `GATEWAY_CHAT_COMPLETIONS_URL` | Rỗng | URL đầy đủ đến inference; không tự thêm/đổi path; validate scheme, host, không userinfo/query/fragment |
| `GATEWAY_API_KEY` | Rỗng, secret | Bắt buộc trong Gateway mode; không tự strip/repair token; từ chối whitespace/template delimiter; không xét prefix tùy đoán |
| `GATEWAY_MODEL` | Rỗng | Alias đã được xác nhận, giữ nguyên token và chữ hoa/thường; không áp normalizer `GEMINI_MODEL` |
| `GATEWAY_ALLOW_INSECURE_HTTP` | `false` | HTTP chỉ khi bật rõ cho loopback test hoặc mạng riêng được phê duyệt; không tự suy ra domain công ty là an toàn |
| `GATEWAY_SYSTEM_USER` | Rỗng | Actor CLI được mapping phê duyệt; không thay người dùng web bằng actor này |
| `FALLBACK_ENABLED` | `false` | Chỉ có hiệu lực ở Gateway mode |
| `FALLBACK_FAIL_THRESHOLD` | `3` | Số nguyên dương, không lớn hơn `max_retry` khi bật fallback |
| `FALLBACK_RETRY_AFTER_S` | `60` | Số dương; dùng monotonic, probe theo traffic, không tạo timer thread |
| `GCP_PROJECT_ID`, `GCP_LOCATION`, `GCP_SERVICE_ACCOUNT_JSON`, `GEMINI_MODEL` | Tái sử dụng | Cấu hình direct Vertex; model/project mapping phải được duyệt, không đoán từ Gateway alias |
| `max_retry`, `base_wait`, `GEMINI_TIMEOUT_SECONDS` | Tái sử dụng | `max_retry` là số attempt tổng, không phải số retry cộng thêm; validate hữu hạn/dương phù hợp |

Gateway mode không yêu cầu direct Google credential khi fallback tắt. Các yêu cầu Azure/SharePoint và JWT khác vẫn giữ. Nếu fallback bật nhưng thiếu cấu hình/file direct: ghi cảnh báo cấu hình đã lọc, tắt fallback hiệu lực, tiếp tục Gateway-only; không âm thầm đi AI Studio. File tồn tại không chứng minh quyền Google; lỗi dựng direct client phải nổi lên và được audit. Cấu hình ngưỡng không hợp lệ phải bị từ chối, không được sửa ngầm.

URL cung cấp `https://apigateway.rangdong.com.vn:50888/` chỉ là origin tham khảo, không phải giá trị sẵn dùng của `GATEWAY_CHAT_COMPLETIONS_URL`. HTTPS hoặc mạng riêng được duyệt là gate trước live use.

## 4. Wire contract

- POST duy nhất tới URL cấu hình; disable redirect để không chuyển key sang host khác.
- `Authorization: Bearer <virtual-key>`; `X-User` và body `user` lấy từ scope server-side; không lấy từ header/body do browser tự gửi.
- `model=GATEWAY_MODEL`, `messages` chứa prompt hiện tại, temperature giữ theo caller; JSON mode dùng cơ chế structured output đã kiểm chứng với model đó. Không tự gọi lại bỏ JSON mode khi lỗi quyền/model/schema.
- Một cấu hình timeout ở transport cho connect/read; không dùng thread wrapper như bằng chứng hard timeout. Wrapper `_call_with_timeout` hiện dùng executor context manager có thể chờ thread khi thoát. Không mở rộng sửa legacy nếu không cần, nhưng không tái dùng điểm yếu này cho Gateway.
- Không có SDK/HTTPAdapter retry ngầm. Chỉ retry tại một ranh giới client có ngân sách rõ; không sleep sau attempt cuối.
- Không stream trong phạm vi này. Parser kiểm shape/text/usage; malformed response không được kích hoạt direct fallback.
- Kết quả Gateway và direct giữ `.text`, `.usage`; thiếu usage phải là unknown, không tạo token giả. Thinking/cached usage giữ khi provider có; không tự gán tổng token thành output billable.

## 5. Failure policy và state

### Phân loại

| Quan sát | Retry Gateway trong helper | Direct fallback | Job retry |
|---|---|---|---|
| DNS failure / connection refused / connect timeout chắc chắn trước gửi | Có, trong ngân sách | Có nếu bật và đủ ngưỡng | Có, theo budget job hiện có sau khi helper hết lượt |
| 429, budget/quota rejection | Bounded backoff/Retry-After hợp lệ | Không | Bounded, không đổi tuyến để né quota |
| 400/401/403/404/422, policy/model/config/missing identity | Không | Không | Không |
| Read timeout/reset sau gửi hoặc không biết đã gửi chưa | Không tự phát lại | Không | Không tự requeue; báo outcome unknown để vận hành xử lý |
| HTTP 5xx không có bảo đảm pre-send từ ingress | Không tự phát lại | Không | Không tự requeue vì chưa biết upstream đã xử lý |
| TLS/certificate/proxy config hoặc exception không nhận diện | Không | Không | Không cho đến khi phân loại được an toàn |
| Response parse/SQLite/log write lỗi sau inference | Không generation lại | Không | Không tự replay công việc đã có outcome không chắc; báo lỗi và kiểm chứng thủ công |

Không phân loại theo một substring bất kỳ trong exception. Khi transport không đủ bằng chứng pre-send, chọn unknown-outcome. Không mặc định coi 502/503/504 là chưa gọi model. Mở rộng policy cho mã ingress cụ thể là thay đổi cần bằng chứng riêng.

### Trạng thái đơn giản

`GATEWAY → DIRECT → PROBE → GATEWAY/DIRECT`. PROBE là một cờ in-flight và thời hạn, không phải một scheduler mới.

- Một Gateway success đặt bộ đếm unreachable về 0. Rate-limit/request error không tăng và không reset bộ đếm; nhưng request đó phải fail closed.
- Khi chạm ngưỡng, chuyển trạng thái đúng một lần, tạo incident ID, reset số direct attempt/success của sự cố, thử direct cho chính helper đang chạy.
- Mỗi invocation `generate`/`generate_json` có tối đa một direct attempt. Budget này tạo mới mỗi invocation, không khóa cả tiến trình sau một lần direct lỗi.
- Trong DIRECT chưa tới hạn: request hợp lệ đi direct. Mỗi request vẫn validate identity/config; auth không được bỏ qua chỉ vì circuit đã mở.
- Đến hạn: một worker được probe bằng chính request của nó; các request đồng thời có thể tiếp tục direct theo trạng thái snapshot. Không giữ khóa trong network I/O.
- Probe success: trả ngay kết quả đó, đóng incident, reset counters. Không thêm direct attempt.
- Probe gặp unreachable đủ bằng chứng: tiếp tục DIRECT, cập nhật mốc probe, request được một direct attempt.
- Probe gặp lỗi quota/quyền/model/config/unknown-outcome: KHÔNG direct cho request đó; đóng circuit DIRECT về GATEWAY và reset unreachable, ghi lý do. Các request mới phải qua Gateway; không hứa thu hồi được direct request đã gửi trước khi phát hiện lỗi.
- Guard chuyển trạng thái với generation/incident token: response cũ không được reset/đóng nhầm incident mới. Direct client dựng lười và publish một instance sau khi thành công; nếu dựng thất bại không cache một object lỗi.
- Client/process mới bắt đầu GATEWAY, không persist circuit state. Counter theo incident; log gắn process/client và incident để không hiểu là một sự cố toàn cluster.
- Probe chỉ xảy ra khi có traffic. Không có request thì không có generation nền và không thể biết Gateway phục hồi ngay.

## 6. Identity và thay đổi nghiệp vụ có chủ đích

Web: scope bắt đầu trong worker từ `job.owner_username`, không dựa vào HTTP request đã kết thúc. Auth vẫn qua `get_current_user`/RBAC hiện tại; job owner do backend lưu. Watcher dùng `system_watcher` đã có nhưng phải được map như service actor, không gán giả thành người dùng thật. CLI có actor cấu hình rõ. Admin connection test dùng username admin đã xác thực.

Mọi Gateway request gồm text, JSON, retry, mini-batch và probe đều giữ actor/job/operation. Department được giải quyết bằng directory phía hệ thống ghi sổ; không nhận phòng ban tự khai từ frontend. Chưa biết catalog/department mapping thì ghi là chưa verified, không tự tạo.

Trong Gateway mode, lỗi Gateway typed đi xuyên RAG/classifier/runner tới worker/watcher. Không được biến lỗi auth/outage thành nhãn `Tin trung lập`, `NONE` hoặc job complete. Legacy provider mode giữ hành vi hiện có. Lỗi JSON thực sự từ model vẫn theo repair policy nghiệp vụ hiện hữu, nhưng không được dùng để kích hoạt direct route.

## 7. Usage/audit: dùng lại SQLite, không dựng ledger mới

`UsageTracker` hiện ghi `gemini_usage_log` trong `classification_jobs.db`. Đề xuất mở rộng additive ngay bảng này, không database thứ hai: `event_kind`, `operation_id`, `attempt_id`, `actor`, `route`, `response_id`, `incident_id`, `model_requested`, `model_actual`, `outcome`, `error_category`, `usage_known`. Migration idempotent, giữ row cũ là legacy; chốt tên/type khi viết migration test trước code.

- `operation_id` mới cho mỗi helper invocation; `attempt_id` mới cho mỗi network attempt, có uniqueness. Job retry là invocation mới, không tái dùng ID để che request mới.
- Event kinds: attempt_started/attempt_finished hoặc hai record tương đương được liên kết cùng attempt; unique theo `(attempt_id, event_kind)`, không unique một mình attempt ID nếu ghi hai event. STARTED ghi trước network; FINISHED ghi sau response/error; query usage chỉ cộng một FINISHED có usage cho mỗi attempt. Đường legacy tiếp tục cộng legacy rows đúng một lần.
- STARTED chưa có FINISHED sau crash = outcome unknown, không tự retry hoặc tính token bằng 0 như một kết quả đã biết. Không hứa ghi sổ hoàn hảo khi crash/disk hỏng sau provider response.
- Ghi token ở client cho Gateway mode để bao phủ cả initial/repair/admin/direct/probe. Runner bỏ phần ghi trùng ở mode này; không lấy `_last_usage` mutable làm nguồn truth Gateway. Metrics cũng lấy cùng event đã hoàn tất, không cộng thêm ở runner.
- Query `total_calls`, token/cost theo FINAL/legacy phù hợp; STARTED/failed/unknown không bị tính như generation thành công. Nếu lỗi vẫn có usage do provider trả, giữ usage có chứng cứ, không bỏ chi phí chỉ vì parsing thất bại.
- Direct có route `direct_vertex`, không giả mạo Gateway response ID/SpendLogs. Requested alias và actual model tách nhau; giá chưa map = cost unknown, không quảng cáo zero cost.
- Gateway SpendLogs là bằng chứng external; đối soát bằng response ID khi có. Chưa biết external flush/ETL thì chưa claim persisted/ingested.
- Không lưu prompt, response content, JWT/key, service-account nội dung hoặc raw error body vào audit. Không gửi extra metadata không nằm trong Gateway contract đã xác nhận.
- Lỗi persistence không nằm trong catch retry transport và không gây generation lại. Nếu audit không ghi được trước request thì chặn request; nếu lỗi sau response thì báo accounting failure/outcome cần điều tra, không gửi thêm request để thử lại ghi sổ.

## 8. Log và thông báo

Sự kiện chuyển tuyến, direct attempt, probe, recovery dùng logging hiện có, dữ liệu đã lọc, incident/process/attempt ID. Không thêm SMTP hoặc sự kiện mail khi đổi tuyến. `NotificationService.send_success/send_error` vẫn hoạt động theo chính sách pipeline; job fail có thể phát thông báo lỗi pipeline hiện hữu, khác với alert chuyển tuyến.

Mặc định `setup_logging` hiện là 10 MiB và 7 backup. Nhiều process ghi cùng một RotatingFileHandler path không được giả định an toàn: khi triển khai dùng tên file theo role/PID hoặc đường logging tập trung đã có, không thêm dịch vụ mới. Web và standalone worker cần wiring log riêng; watcher đã gọi setup. Nếu không ghi được file/audit trong Gateway mode thì phải báo readiness lỗi/chặn inference thay vì lặng lẽ coi console-only đạt yêu cầu audit.

## 9. Reload và vận hành

Gateway fields cấu hình bằng env ở phase đầu, không thêm UI editor. Settings GET phải mask secret; PUT không tự thay endpoint/key qua field chưa được cho phép. Khi model/backend thay đổi hợp lệ, client mới chỉ nhận request mới; request đang chạy dùng snapshot cũ, không đổi key/project giữa chừng. Watcher reload phải reset state và client theo fingerprint không chứa secret trong log; các process độc lập cần reload/restart riêng, không hứa hot reload xuyên process.

Compose hiện đã nạp `.env`; không thêm service Gateway. Kiểm mount credential khi triển khai: Gateway-only không cần direct key mount; không tự sửa hoặc xóa mount/data trong giai đoạn này. Rollback dùng backend legacy được xác nhận và giữ migration additive; không drop bảng/volume.

## 10. Bằng chứng cần thu

Unit/state → wire HTTP loopback → authenticated API→job→worker → full DMS suite → isolated two-proxy fault tests → provider-real có phê duyệt → SpendLogs reconciliation. Chỉ provider-real sau khi có endpoint/key/model/mapping/TLS và giới hạn chi phí. Không stop Gateway production để diễn tập.

Fault matrix áp dụng riêng cho text và JSON: baseline, một proxy down, LB down fallback tắt, LB down fallback bật, probe lỗi quyền/quota, recovery, sự cố thứ hai. Dùng real helpers, fake provider; restore trong finally và kiểm health/recovery. Có thử nhiều user/worker để bắt lẫn context. UI job complete không thay thế việc kiểm response/route/usage.
