# DMS Gateway + fallback — Specification

**Status: DRAFT / NOT IMPLEMENTED.** Đây là hợp đồng đề xuất cho DMS, không phải mô tả đã triển khai của CRM. SHALL/MUST là bắt buộc để nghiệm thu; MUST NOT là cấm. Các mặc định và policy tham chiếu [design](../../design.md); tiến độ ở [tasks](../../tasks.md).

## ADDED Requirements

### Requirement: GW-01 — Kích hoạt tường minh và cấu hình hợp lệ

DMS SHALL chỉ đi Gateway khi `GEMINI_BACKEND=gateway`. Backend mặc định SHALL giữ nguyên. Gateway mode MUST có endpoint inference đầy đủ, key hợp lệ, model alias và identity; direct fallback MUST mặc định tắt. Alias SHALL được giữ nguyên, không lower-case hoặc sửa token.

#### Scenario: Backend legacy
- **WHEN** backend là `vertex` hoặc `apikey`
- **THEN** routing hiện có được giữ, không tự gọi Gateway và không áp fallback mới.

#### Scenario: Gateway-only không có credential Google
- **WHEN** endpoint/key/model/identity hợp lệ và fallback tắt
- **THEN** không yêu cầu, nạp hoặc gọi direct Google credential; không làm yếu các kiểm tra JWT/SharePoint khác.

#### Scenario: Thiếu cấu hình hoặc HTTP không được cho phép
- **WHEN** Gateway config không hợp lệ, key có whitespace/template delimiter, hoặc URL HTTP mà `GATEWAY_ALLOW_INSECURE_HTTP=false`
- **THEN** chặn trước network; lỗi được lọc, không đưa key vào log/response.

#### Scenario: Bật fallback nhưng thiếu direct configuration
- **WHEN** cấu hình/file `GCP_SERVICE_ACCOUNT_JSON` hoặc project/model bắt buộc bị thiếu
- **THEN** tắt fallback hiệu lực và báo rõ; Gateway-only vẫn có thể chạy, không chuyển sang AI Studio.
- **AND** file tồn tại không được báo là đã xác minh provider readiness.

#### Scenario: Ngưỡng sai
- **WHEN** fallback bật và threshold không dương, lớn hơn `max_retry`, hoặc probe interval không hữu hạn/dương
- **THEN** từ chối cấu hình tại runtime, không chỉ kiểm default trong test.

### Requirement: GW-02 — Routing text/JSON qua transport duy nhất

`generate` và `generate_json` SHALL dùng endpoint được cấu hình, giữ response `.text`/`.usage`, prompt và semantics temperature. Không redirect, retry POST ngầm hoặc thay model family để vượt lỗi.

#### Scenario: Kiểm wire
- **WHEN** caller gọi text hoặc JSON qua Gateway mode
- **THEN** transport nhận đúng URL, model alias, messages, temperature, auth và identity.
- **AND** JSON request có định dạng structured output tương thích đã kiểm chứng; không tự đổi sang unconstrained mode khi server từ chối.

#### Scenario: Redirect / response sai shape
- **WHEN** server trả redirect hoặc payload không hợp lệ
- **THEN** không gửi key tới URL khác, không direct fallback; trả lỗi đã lọc.

#### Scenario: Timeout không thể chứng minh pre-send
- **WHEN** request timeout/reset sau gửi hoặc transport không xác định được giai đoạn
- **THEN** đánh dấu outcome unknown, không tự replay qua Gateway/direct hoặc requeue job như lỗi mạng chắc chắn chưa gửi.

### Requirement: GW-03 — Danh tính theo execution scope

Mọi Gateway request SHALL gửi `X-User` và body `user` từ actor server-side. Actor, job nếu có và operation SHALL truyền bằng tham số từng call, không lưu actor vào mutable singleton. Theo yêu cầu thu gọn: không dùng ContextVar; các scenario identity watcher/CLI/admin và interleave/executor dưới đây là hợp đồng thiết kế, test riêng được hoãn theo yêu cầu người dùng, không phải đã verified.

#### Scenario: Upload đã xác thực
- **WHEN** người dùng upload rồi HTTP request kết thúc, worker nhận job
- **THEN** identity lấy từ `owner_username` do backend đã lưu; retry/probe vẫn giữ actor đó.

#### Scenario: Client giả danh
- **WHEN** frontend tự gửi `X-User`, username hoặc department khác người dùng đã xác thực
- **THEN** các giá trị đó không thay thế actor/department đáng tin cậy.

#### Scenario: Watcher, CLI và admin
- **WHEN** watcher chạy
- **THEN** dùng service actor `system_watcher` và mapping phê duyệt, không giả danh người dùng.
- **WHEN** CLI chạy hoặc admin test connection
- **THEN** lần lượt dùng actor cấu hình rõ hoặc username admin đã xác thực; thiếu actor thì chặn, không tự lấy admin mặc định.

#### Scenario: Hai user đồng thời và thread con
- **WHEN** helper của hai actor interleave qua worker/executor/retry
- **THEN** wire headers/body và audit từng request đều thuộc đúng actor/job; không rò sang scope tiếp theo.

### Requirement: GW-04 — Phân loại lỗi và ngân sách request

Chỉ DNS failure, connection refused hoặc connect timeout **có bằng chứng trước gửi** SHALL đủ điều kiện fallback. Unknown exception, TLS/config error, lỗi identity/quyền/model, quota, parse và persistence MUST NOT kích hoạt direct. Tổng Gateway attempt trong một helper SHALL không vượt `max_retry`; direct attempt SHALL không vượt một lần trên mỗi helper invocation.

#### Scenario: Auth/policy và quota
- **WHEN** Gateway trả 400/401/403/404/422 hoặc policy/model error
- **THEN** fail closed, không retry mù hoặc direct.
- **WHEN** Gateway trả 429/budget/quota rejection
- **THEN** chỉ bounded backoff theo budget, không direct; Retry-After không được tạo thời gian chờ vô hạn.

#### Scenario: HTTP 5xx hoặc outcome không rõ
- **WHEN** nhận 5xx mà không có hợp đồng ingress chứng minh chưa gửi upstream
- **THEN** không suy ra unreachable an toàn; giữ outcome unknown và không tự replay.

#### Scenario: Lỗi sau inference
- **WHEN** response parse hoặc ghi SQLite/log thất bại
- **THEN** không có generation thêm do lỗi đó; giữ audit có thể lưu được, báo lỗi cần điều tra.

#### Scenario: Direct lỗi
- **WHEN** direct attempt thất bại hoặc direct client không dựng được
- **THEN** lỗi nổi lên; helper không thử direct lần hai và không vào vòng Gateway/direct vô hạn.
- **AND** một helper mới sau đó có budget riêng, không bị cờ đã-fallback toàn process chặn vĩnh viễn.

### Requirement: GW-05 — Ngưỡng chuyển tuyến và cứu request hiện tại

Counter unreachable SHALL ở phạm vi client/process, được bảo vệ bằng khóa. Thành công Gateway reset counter. Lỗi quota/request không tăng/reset nhưng request đó vẫn fail closed. Chạm threshold SHALL mở incident đúng một lần và thử direct cho request làm chạm ngưỡng nếu fallback hiệu lực.

#### Scenario: Lỗi lẻ rồi thành công
- **WHEN** có unreachable rồi Gateway success
- **THEN** counter về 0, không chuyển tuyến nếu chưa chạm threshold.

#### Scenario: Lỗi loại khác xen giữa
- **WHEN** unreachable, quota, unreachable mà chưa có Gateway success
- **THEN** chỉ hai unreachable được đếm; request quota không được đi direct.

#### Scenario: Chạm ngưỡng ở attempt cuối
- **WHEN** threshold bằng `max_retry`, Gateway liên tục unreachable và direct provider sẵn sàng
- **THEN** helper hiện tại trả kết quả direct thật, không bỏ helper trước khi thử fallback.
- **AND** nếu provider direct cũng lỗi thì job có thể thất bại; không có cam kết mọi lô luôn thành công.

#### Scenario: Fallback tắt
- **WHEN** Gateway unreachable hết attempt budget
- **THEN** không phát sinh direct request và lỗi nổi lên tầng job.

### Requirement: GW-06 — Probe và phục hồi có cùng chính sách bảo mật

DIRECT SHALL probe theo traffic sau interval bằng request thật. Mỗi client chỉ có một probe in-flight. Probe success SHALL dùng ngay kết quả và quay về Gateway; probe không được bypass các kiểm tra lỗi của GW-04.

#### Scenario: Chưa tới hạn / không có traffic
- **WHEN** chưa tới probe deadline
- **THEN** request hợp lệ có thể đi direct theo policy đã bật.
- **WHEN** không có traffic
- **THEN** không tạo model request nền chỉ để thăm dò.

#### Scenario: Probe success
- **WHEN** Gateway trả response thành công cho probe
- **THEN** trả kết quả đó, reset trạng thái/counter và không gọi direct hoặc generation thứ hai.

#### Scenario: Probe unreachable
- **WHEN** probe thất bại chắc chắn trước gửi
- **THEN** cập nhật deadline, tiếp tục DIRECT và cho request đó tối đa một direct attempt.

#### Scenario: Probe gặp quyền/quota/unknown
- **WHEN** probe gặp lỗi không đủ điều kiện direct theo GW-04
- **THEN** request đó không direct; đóng DIRECT về GATEWAY, reset counter unreachable và ghi lý do.
- **AND** request mới phải qua Gateway; request direct đã gửi trước kết quả probe không được coi là có thể thu hồi.

### Requirement: GW-07 — State đa luồng và nhiều sự cố

Thao tác state SHALL atomic, network không giữ khóa. Direct client SHALL được dựng lười và tái sử dụng sau thành công. Response cũ MUST NOT đóng/reset incident mới. Không hứa circuit đồng bộ giữa process.

#### Scenario: Đồng thời vượt ngưỡng
- **WHEN** nhiều worker cùng hoàn tất lỗi đủ ngưỡng
- **THEN** chỉ một transition và một incident; chỉ một direct client được publish thành công.

#### Scenario: Đồng thời tới hạn probe
- **WHEN** nhiều worker vào DIRECT đúng probe deadline
- **THEN** chỉ một worker probe, các worker khác giữ route snapshot hợp lệ, không lẫn identity.

#### Scenario: Sự cố thứ hai
- **WHEN** có incident, recovery rồi incident mới
- **THEN** counters attempted/succeeded và duration thuộc riêng incident mới; ID không bị tái dùng.
- **AND** late response incident cũ không thay state incident mới.

### Requirement: GW-08 — Fail-closed đến trạng thái nghiệp vụ

Trong Gateway mode, lỗi GW-04 SHALL đi xuyên RAG/classifier/runner tới job state, không biến thành dữ liệu phân loại giả. Cancel/auth/checkpoint semantics hiện hữu SHALL được giữ.

#### Scenario: Hạ tầng lỗi khi RAG hoặc JSON classification
- **WHEN** helper không có kết quả hợp lệ do lỗi Gateway/direct
- **THEN** không đánh dấu công việc đó complete với `NONE`/nhãn trung lập thay thế.
- **AND** không chạy hàng loạt per-row generation chỉ vì lỗi auth/hạ tầng.

#### Scenario: Lỗi không retryable
- **WHEN** lỗi quyền/cấu hình/unknown-outcome/persistence tới worker hoặc watcher
- **THEN** không tự requeue hoặc retry file làm phát lại inference; trạng thái lỗi được ghi đã lọc.

#### Scenario: Model trả JSON thiếu dòng
- **WHEN** thực sự nhận response nhưng parse/coverage thiếu theo logic hiện hữu
- **THEN** giữ cơ chế repair nghiệp vụ đã được kiểm thử, mọi helper mới có audit riêng; không đổi direct route do parse failure.

### Requirement: GW-09 — Audit và usage không đếm đôi

Gateway mode SHALL ghi attempt tại ranh giới client bằng SQLite hiện có. STARTED/FINISHED có operation/attempt/actor/job/route/incident/model/outcome; FINISHED mang response ID và usage khi có. Migration additive và query legacy SHALL không mất dữ liệu. Không tạo Gateway SpendLogs giả cho direct.

#### Scenario: Ghi trước network và hoàn tất
- **WHEN** helper chuẩn bị gửi request
- **THEN** ghi STARTED thành công trước network, sau đó FINISHED cho kết quả thực; mỗi `(attempt_id,event_kind)` duy nhất.
- **AND** STARTED không bị cộng thành một lượt usage thành công; FINISHED được cộng tối đa một lần.

#### Scenario: Retry, JSON repair và admin test
- **WHEN** nhiều attempt/helper phát sinh
- **THEN** ghi từng attempt đúng route/actor; runner không ghi lại cùng usage bằng `_last_usage`.

#### Scenario: Thiếu usage / crash
- **WHEN** provider không trả usage hoặc có STARTED không FINISHED sau crash
- **THEN** thể hiện unknown, không bịa zero tokens/cost hoặc automatically replay.
- **AND** chi phí chưa mapping không được trình bày như miễn phí.

#### Scenario: Direct reconciliation
- **WHEN** direct inference thành công
- **THEN** usage có nguồn `direct_vertex`, không có claim Gateway SpendLogs; model/project mapping phải đúng cấu hình được duyệt.
- **WHEN** đối soát Gateway
- **THEN** đọc SpendLogs thật theo response ID và kiểm actor/token; app SQLite hoặc HTTP 200 riêng lẻ không đủ.

#### Scenario: Migration / duplicate insert
- **WHEN** migration chạy lại hoặc cùng event được ghi lại
- **THEN** không mất rows legacy, không cộng trùng; query tổng và by-type giữ semantics đã định nghĩa.

### Requirement: GW-10 — Log an toàn, không thêm mail fallback

Fallback SHALL có log vận hành đã lọc, per-incident duration/counters và attempt correlation. MUST NOT lưu key/JWT/prompt/raw response/error body. Không nối transition/recovery vào SMTP, Graph hoặc webhook alert mới. Notification pipeline hiện hữu SHALL giữ.

#### Scenario: Chuyển tuyến/phục hồi
- **WHEN** state thay đổi
- **THEN** ghi log đúng một transition tương ứng; không gọi notification chỉ vì state đó.

#### Scenario: Pipeline thật sự thất bại
- **WHEN** flow hiện hữu gọi `send_error`
- **THEN** vẫn được dùng Teams/Graph theo cấu hình cũ; đây không phải alert chuyển tuyến mới.

#### Scenario: Log/audit không ghi được
- **WHEN** runtime Gateway không có kênh file/audit bắt buộc hoạt động
- **THEN** báo lỗi readiness hoặc accounting thích hợp; console-only không được coi là đã đạt hợp đồng lưu vết.
- **AND** nhiều process không ghi xoay vòng cùng path mà chưa có giải pháp an toàn.

### Requirement: GW-11 — Settings, reload và bí mật

Settings GET SHALL mask key Gateway trong cả model fields lẫn raw env fallback. Save config Gateway SHALL không tự generation hoặc báo generation verified. Reload SHALL không dùng key/endpoint cũ cho request mới sau khi client mới đã thay thế.

#### Scenario: Settings invalid
- **WHEN** Settings validation thất bại và API trả raw env fallback
- **THEN** `GATEWAY_API_KEY` vẫn được mask hoàn toàn; exception không chứa secret.

#### Scenario: Admin test / reload
- **WHEN** admin chủ động test
- **THEN** dùng actor admin, audit thật và báo route; direct thành công không được gắn nhãn Gateway đã phục hồi.
- **WHEN** config đổi giữa request
- **THEN** request in-flight giữ snapshot; request mới dùng client/config mới; không hứa reload xuyên process chưa thực hiện.

### Requirement: GW-12 — Kiểm chứng tách biệt khỏi production

Test offline SHALL không đọc `.env`/service account thật, không gửi SMTP/Graph/provider traffic; dữ liệu và key giả. Live test và fault injection SHALL cần phê duyệt riêng cùng giới hạn chi phí/phạm vi.

#### Scenario: Fault matrix
- **WHEN** kiểm chứng Gateway integration trên stack cô lập
- **THEN** chạy riêng text/JSON với baseline, một proxy down, toàn LB down (fallback tắt/bật), recovery và sự cố lặp lại; restore trong finally và đọc lại health/route.

#### Scenario: Phân tầng kết luận
- **WHEN** unit/mock pass nhưng chưa có live artifacts
- **THEN** báo offline verified, live/SpendLogs/ETL chưa xác minh; không dùng test count CRM làm kết quả DMS.
- **AND** không stop Gateway production hay sửa server để lấy PASS.
