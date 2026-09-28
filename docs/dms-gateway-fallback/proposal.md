# DMS Gateway + fallback — Proposal

**Trạng thái:** DRAFT — chỉ đặc tả, chưa triển khai, chưa bật Gateway hoặc direct fallback.

**Mốc đối chiếu:** nhánh `ChiThanh`, commit `63cc633ee185ab8d7fed73fbf9fdf12ebf5150d4`. Các đường dẫn code dưới đây tính từ gốc repo. Khi triển khai phải kiểm tra lại HEAD và thay đổi local.

## Why

DMS hiện gọi Google trực tiếp bằng `GeminiClient`, trong khi đã có địa chỉ ingress HTTPS được cung cấp: `https://apigateway.rangdong.com.vn:50888/`. Cần một chế độ Gateway dùng chung cho các lượt text/JSON, giữ danh tính người thực hiện và khả năng đối soát usage. Khi Gateway không kết nối được, DMS có thể gọi trực tiếp Vertex **chỉ nếu người vận hành chủ động bật chính sách đó**.

Tài liệu [CRM tham khảo](../gateway-fallback-drop-mail-alerts/proposal.md) mô tả gỡ SMTP trên một codebase khác. DMS chưa có các lớp `_GatewayClient`/`_FallbackClient` và không có SMTP transport tương ứng. Vì vậy đây là **tích hợp mới cho DMS**, không phải áp nguyên patch gỡ mail của CRM. Giữ nguyên bộ tham khảo, không sao chép dấu hoàn tất hoặc kết quả test CRM sang DMS.

## Bằng chứng hiện tại và giới hạn

| Hạng mục | Đã quan sát / đọc được | Chưa chứng minh |
|---|---|---|
| Ingress cung cấp | Trong lượt kiểm tra trước, `/` trả trang Gateway Status và `/edge-health` trả 200 | Generation, model access, TLS, SpendLogs, ETL; không phải kiểm tra runtime mới của bộ tài liệu này |
| Discovery | `/v1/models`, `/gateway/v1/models`, `/health` từng trả 404 | Inference không hoạt động; có thể các route discovery không được expose |
| Backend DMS | `service/src/dms/settings.py` chỉ chấp nhận `vertex`/`apikey` | Không có backend Gateway đã triển khai |
| LLM | Text ở `pipeline/rag_product.py`, JSON ở `pipeline/issue_classifier.py`, đều qua `gemini_client.py` | Routing/attribution Gateway |
| Chạy nền | API lưu `owner_username`; worker chạy job; watcher có actor `system_watcher` | Truyền actor tới Gateway; ánh xạ phòng ban |
| Usage | `usage_tracker.py` ghi SQLite `gemini_usage_log` | Đối soát từng attempt, response ID và route |
| Notification | `notification.py` dùng Teams, sau đó Graph nếu Teams không gửi được | Không đọc credential hoặc xác minh khả năng gửi thật trong lần soạn này |

## What Changes — phạm vi đề xuất

1. Thêm `GEMINI_BACKEND=gateway`; giữ backend mặc định và hành vi legacy khi chưa chuyển chế độ.
2. Dùng `requests` đã có để gọi endpoint OpenAI-compatible được cấu hình chính xác; không thêm SDK hay transport framework.
3. Giữ `GeminiResponse.text` và `.usage`; hỗ trợ cả `generate` và `generate_json`, không đổi prompt, nhãn, Excel hoặc model family để lấy kết quả test xanh.
4. Truyền actor/job/operation theo ngữ cảnh thực thi riêng, kể cả background threads; gửi `X-User` và JSON `user` từ backend đáng tin cậy.
5. Direct Vertex fallback mặc định tắt. Khi bật, có ngưỡng lỗi và probe phục hồi; chỉ dùng với lỗi chắc chắn chưa gửi được inference. Không dùng lỗi quyền, hạn mức, model policy hoặc timeout không rõ kết quả để bypass Gateway.
6. Trong Gateway mode, lỗi hạ tầng/quyền phải nổi lên trạng thái job; không được bị nuốt thành `NONE`/nhãn mặc định rồi báo hoàn tất.
7. Ghi audit route/attempt và usage thực qua cơ chế SQLite hiện có; loại bỏ double-count giữa ghi tại client và tổng hợp tại runner trong Gateway mode.
8. Không tạo cảnh báo SMTP/mail/webhook mới cho chuyển tuyến. Log là kênh thông báo sự cố của fallback; thông báo kết quả pipeline Teams/Graph vẫn giữ nguyên.
9. Bổ sung kiểm thử offline, auth-to-worker, cạnh tranh luồng, failure/recovery và kế hoạch kiểm chứng live tách riêng.

## Không làm

- Không đổi hay deploy Gateway server, key catalog, proxy, database, ETL hoặc Dashboard.
- Không bật feature, sửa `.env`, đọc service-account/key thật, gửi thư hoặc gọi model tính phí trong giai đoạn đặc tả.
- Không thêm Redis, circuit-breaker library, hàng đợi, SMTP, trang quản trị mới hoặc plugin registry.
- Không tích hợp OCR/embedding mới: chưa thấy call path này trong các entrypoint DMS được khảo sát; rà soát lại trước khi tuyên bố phủ toàn bộ.
- Không hứa exactly-once inference, không bảo đảm mọi lô luôn thành công, không coi log là hóa đơn.
- Không commit/push hoặc sửa tài liệu CRM.

## Khác biệt có chủ đích so với CRM

| CRM tham khảo | DMS đề xuất | Lý do |
|---|---|---|
| Có sẵn fallback; chỉ bỏ mail | Thêm Gateway mode, fallback tùy chọn; không thêm SMTP | Hiện trạng DMS khác |
| Dò chuỗi lỗi rộng | Dùng typed transport/status; unknown fail closed | Tránh bypass quyền và gọi trùng |
| Mọi probe lỗi đều tiếp tục direct | Probe gặp lỗi quyền/quota/policy phải đóng tuyến direct và báo lỗi | Chính sách nhất quán trước/sau sự cố |
| Khóa file cố định `sa-key.json` ở root | Dùng `GCP_SERVICE_ACCOUNT_JSON` hiện có | Tương thích mount DMS, không hard-code đường dẫn CRM |
| Tổng fallback cộng dồn khó hiểu | Counter theo sự cố, tách số attempted/succeeded | Không mang lỗi báo cáo cũ sang |
| Có thể probe đồng thời | Một probe đang chạy trên mỗi client/process | Giới hạn request và độ trễ |
| Log đủ mô tả sự cố | Log vận hành + bản ghi usage/attempt cục bộ | Direct không có Gateway SpendLogs |

## Tác động và điều kiện nghiệm thu

Thay đổi được giới hạn theo chế độ Gateway nhưng có các điểm nối bắt buộc ở client, settings, auth/background, catch blocks, usage và test. Danh sách chi tiết ở [design](design.md); yêu cầu chuẩn ở [spec](specs/dms-gateway-fallback/spec.md); trình tự ở [tasks](tasks.md).

Offline chỉ được coi hoàn tất khi text/JSON, worker/watcher/admin test, fail-closed và direct-fallback được kiểm bằng transport giả, không đọc credential thật hoặc có provider egress. Production còn phụ thuộc endpoint/model/key/actor mapping/TLS, kiểm thử qua Gateway thật và đối soát SpendLogs; offline PASS không thay thế các điều kiện này.

## Quyết định còn mở trước khi bật thật

- Inference path chính xác: không suy diễn `/v1` hoặc `/gateway/v1` từ trang status.
- Model alias và model thực, catalog agent/Virtual Key của DMS; không tự tạo mã agent mới.
- Mapping username/phòng ban, actor watcher và actor CLI được phép.
- TLS hoặc ranh giới mạng riêng được phê duyệt cho URL HTTP hiện tại.
- Có bật direct fallback không; project/model/quyền trực tiếp có đúng nguồn chi phí được phê duyệt không.
- Chấp nhận mất cảnh báo chủ động riêng cho fallback và retention/audit cục bộ.

Đây là những điều kiện kích hoạt/live-test, không ngăn viết test offline với endpoint loopback và dữ liệu giả.
