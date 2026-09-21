# Hướng dẫn sử dụng DMS Gateway & Fallback

Tài liệu hướng dẫn cấu hình, vận hành và xử lý sự cố cho module LLM Gateway và cơ chế Fallback trong hệ thống DMS Feedback Classification.

---

## 1. Cấu hình biến môi trường (`.env`)

Để kích hoạt kết nối qua LLM Gateway, cấu hình các biến sau trong file `service/.env`:

```dotenv
# Kích hoạt backend Gateway
GEMINI_BACKEND=gateway

# Endpoint OpenAI-compatible của API Gateway (bắt buộc đường dẫn chat completions cụ thể)
GATEWAY_CHAT_COMPLETIONS_URL=https://apigateway.rangdong.com.vn:50888/v1/chat/completions

# API Key / Virtual Key cấp cho DMS trên Gateway
GATEWAY_API_KEY=your-gateway-api-key-here

# Model alias được định tuyến trên Gateway
GATEWAY_MODEL=gemini-2.5-flash

# Cho phép HTTP không mã hóa (chỉ dùng cho mạng nội bộ/LAN tin cậy)
GATEWAY_ALLOW_INSECURE_HTTP=true

# Cơ chế Fallback về Direct Vertex AI khi Gateway sự cố (mặc định: false)
FALLBACK_ENABLED=false
FALLBACK_FAIL_THRESHOLD=3
FALLBACK_RETRY_AFTER_S=60

# Timeout & retry cho mỗi request
GEMINI_TIMEOUT_SECONDS=120.0
MAX_RETRY=3
BASE_WAIT=2.0
```

### Lưu ý bảo mật:
- Khi `FALLBACK_ENABLED=true`, hệ thống cần có cấu hình GCP hợp lệ (`GCP_PROJECT_ID`, `GCP_LOCATION`, `GCP_SERVICE_ACCOUNT_JSON`, `GEMINI_MODEL`). Nếu thiếu, fallback sẽ tự động tắt để đảm bảo an toàn.
- `GATEWAY_API_KEY` luôn được che (mask) trên giao diện Settings API và không bao giờ xuất hiện trong log.

---

## 2. Kiểm tra kết nối

### Qua Giao diện Web / API:
1. Đăng nhập vào DMS Dashboard với tài khoản quản trị (Admin).
2. Vào trang **Settings** -> kiểm tra mục Gateway.
3. Bấm **Test Connection** (hoặc gọi API `POST /api/settings/test-connection`).
4. Hệ thống sẽ trả về route thực tế được dùng (`gateway` hoặc `direct_vertex`) và trạng thái xác thực.

### Qua CLI / Script offline:
Chạy suite test kiểm tra Gateway để xác nhận tính toàn vẹn:
```bash
python service/tests/offline_runner.py service/tests/test_gateway*.py -q
```

---

## 3. Nhật ký vận hành (Logging & Monitoring)

- Tất cả các thao tác gọi qua Gateway được ghi vào logger `dms.gateway`.
- Log file được phân tách theo từng process (Web, Worker, Watcher) tại thư mục `logs/` để tránh xung đột ghi xoay vòng.

### Các sự kiện quan trọng trong log:
- **`fallback_open`**: Gateway mất kết nối liên tiếp đạt ngưỡng `FALLBACK_FAIL_THRESHOLD`. Tuyến tạm thời chuyển sang direct Vertex AI.
- **`fallback_closed`**: Gateway đã phục hồi (`recovered`) sau vòng thăm dò (probe) thành công. Tuyến quay lại Gateway.

*Lưu ý:* Khi chuyển tuyến hoặc probe, hệ thống **không gửi email/Teams alert** để tránh spam thông báo; trạng thái được theo dõi qua nhật ký log tập trung.

---

## 4. Kiểm toán và Thống kê sử dụng (Usage & Audit)

Mỗi lần gọi Gateway đều ghi nhận sự kiện vào SQLite (`classification_jobs.db`, bảng `gemini_usage_log`):
- `route`: `gateway` hoặc `direct_vertex`.
- `actor`: Định danh người dùng thực hiện (hoặc worker/service).
- `response_id`: ID phản hồi từ Gateway để đối soát với SpendLogs của Gateway.
- `model_actual`, `usage_json`: Thông tin token thực tế do Gateway trả về.
- Các request đang chạy (`attempt_started`) và hoàn thành (`attempt_finished`) được đối soát chặt chẽ, không đếm trùng lặp.

---

## 5. Xử lý sự cố thường gặp

1. **Lỗi `401 / 403 (auth)`**:
   - Kiểm tra lại `GATEWAY_API_KEY` trên Gateway.
2. **Lỗi `429 (quota)`**:
   - Gateway hoặc upstream hết hạn mức. Hệ thống sẽ tự động chờ theo header `Retry-After` (tối đa 60s) trước khi thử lại.
3. **Lỗi `unreachable / connection refused`**:
   - Kiểm tra kết nối mạng tới `GATEWAY_CHAT_COMPLETIONS_URL`.
   - Nếu `FALLBACK_ENABLED=true`, sau 3 lần lỗi liên tiếp hệ thống sẽ tự động chuyển sang gọi trực tiếp Vertex AI.
