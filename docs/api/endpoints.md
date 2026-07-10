# 📋 REST API Endpoints — DMS Feedback Classification Service

Tài liệu tham chiếu đầy đủ tất cả các endpoint REST API, được nhóm theo module. Xem [overview.md](./overview.md) để hiểu cơ chế xác thực và định dạng lỗi.

**Ký hiệu phân quyền:**
- 🔓 `none` — Không cần xác thực
- 👤 `user` — Cần đăng nhập (bất kỳ role nào)
- 🔑 `admin` — Chỉ dành cho quản trị viên

---

## 1. Auth — `/api/auth`

### POST /api/auth/login

Đăng nhập và lấy JWT token.

| Thuộc tính | Giá trị |
|---|---|
| **Method** | `POST` |
| **Path** | `/api/auth/login` |
| **Auth** | 🔓 Không cần |

**Request Body:**

```json
{
  "username": "admin",
  "password": "your_password"
}
```

| Trường | Kiểu | Bắt buộc | Mô tả |
|---|---|---|---|
| `username` | string | ✅ | Tên đăng nhập |
| `password` | string | ✅ | Mật khẩu |

**Response `200 OK`:**

```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "refresh_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "user": {
    "username": "admin",
    "display_name": "Administrator",
    "role": "admin"
  }
}
```

**cURL:**

```bash
curl -X POST http://localhost:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username": "admin", "password": "your_password"}'
```

---

### POST /api/auth/refresh

Làm mới `access_token` khi hết hạn.

| Thuộc tính | Giá trị |
|---|---|
| **Method** | `POST` |
| **Path** | `/api/auth/refresh` |
| **Auth** | 🔓 Không cần |

**Request Body:**

```json
{
  "refresh_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
}
```

**Response `200 OK`:**

```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
}
```

**cURL:**

```bash
curl -X POST http://localhost:8000/api/auth/refresh \
  -H "Content-Type: application/json" \
  -d '{"refresh_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."}'
```

---

### GET /api/auth/me

Lấy thông tin người dùng đang đăng nhập.

| Thuộc tính | Giá trị |
|---|---|
| **Method** | `GET` |
| **Path** | `/api/auth/me` |
| **Auth** | 👤 user |

**Response `200 OK`:**

```json
{
  "username": "nguyen.van.a",
  "display_name": "Nguyễn Văn A",
  "role": "user",
  "created_at": "2026-01-15T08:00:00Z"
}
```

**cURL:**

```bash
curl http://localhost:8000/api/auth/me \
  -H "Authorization: Bearer <access_token>"
```

---

### POST /api/auth/logout

Đăng xuất và vô hiệu hóa token hiện tại.

| Thuộc tính | Giá trị |
|---|---|
| **Method** | `POST` |
| **Path** | `/api/auth/logout` |
| **Auth** | 👤 user |

**Response `200 OK`:**

```json
{
  "success": true
}
```

**cURL:**

```bash
curl -X POST http://localhost:8000/api/auth/logout \
  -H "Authorization: Bearer <access_token>"
```

---

### POST /api/auth/change-password

Đổi mật khẩu của người dùng đang đăng nhập.

| Thuộc tính | Giá trị |
|---|---|
| **Method** | `POST` |
| **Path** | `/api/auth/change-password` |
| **Auth** | 👤 user |

**Request Body:**

```json
{
  "old_password": "current_password",
  "new_password": "new_secure_password"
}
```

| Trường | Kiểu | Bắt buộc | Mô tả |
|---|---|---|---|
| `old_password` | string | ✅ | Mật khẩu hiện tại |
| `new_password` | string | ✅ | Mật khẩu mới |

**Response `200 OK`:**

```json
{
  "success": true
}
```

**cURL:**

```bash
curl -X POST http://localhost:8000/api/auth/change-password \
  -H "Authorization: Bearer <access_token>" \
  -H "Content-Type: application/json" \
  -d '{"old_password": "current_password", "new_password": "new_secure_password"}'
```

---

## 2. Classification — `/api/classify` & `/api/jobs`

### POST /api/classify/text

Phân loại một đoạn văn bản phản hồi trực tiếp (inline classification).

| Thuộc tính | Giá trị |
|---|---|
| **Method** | `POST` |
| **Path** | `/api/classify/text` |
| **Auth** | 👤 user |

**Request Body:**

```json
{
  "text": "Sản phẩm giao chậm, đóng gói bị móp méo, tôi rất thất vọng.",
  "mode": "full"
}
```

| Trường | Kiểu | Bắt buộc | Mô tả |
|---|---|---|---|
| `text` | string | ✅ | Văn bản phản hồi cần phân loại |
| `mode` | string | ❌ | Chế độ phân loại: `"full"` (mặc định) hoặc `"quick"` |

**Response `200 OK`:**

```json
{
  "label": "Giao hàng",
  "sub_label": "Chậm trễ",
  "product": "Mì tôm Hảo Hảo",
  "sentiment": "negative",
  "confidence": 0.92,
  "summary": "Khách hàng phàn nàn về việc giao hàng chậm và đóng gói kém chất lượng.",
  "keywords": ["giao chậm", "đóng gói", "móp méo"]
}
```

**cURL:**

```bash
curl -X POST http://localhost:8000/api/classify/text \
  -H "Authorization: Bearer <access_token>" \
  -H "Content-Type: application/json" \
  -d '{"text": "Sản phẩm giao chậm, đóng gói bị móp méo.", "mode": "full"}'
```

---

### POST /api/classify/file

Upload file (CSV/Excel) để phân loại hàng loạt. Trả về `job_id` để theo dõi tiến trình.

| Thuộc tính | Giá trị |
|---|---|
| **Method** | `POST` |
| **Path** | `/api/classify/file` |
| **Auth** | 👤 user |
| **Content-Type** | `multipart/form-data` |

**Form Data:**

| Trường | Kiểu | Bắt buộc | Mô tả |
|---|---|---|---|
| `file` | file | ✅ | File `.csv` hoặc `.xlsx` chứa phản hồi |

**Response `202 Accepted`:**

```json
{
  "job_id": "job_20260710_abc123",
  "status": "queued",
  "filename": "feedback_july.csv",
  "total_rows": 450
}
```

**cURL:**

```bash
curl -X POST http://localhost:8000/api/classify/file \
  -H "Authorization: Bearer <access_token>" \
  -F "file=@/path/to/feedback.csv"
```

---

### GET /api/jobs

Lấy danh sách tất cả job phân loại của người dùng hiện tại.

| Thuộc tính | Giá trị |
|---|---|
| **Method** | `GET` |
| **Path** | `/api/jobs` |
| **Auth** | 👤 user |

**Response `200 OK`:**

```json
[
  {
    "job_id": "job_20260710_abc123",
    "filename": "feedback_july.csv",
    "status": "running",
    "processed": 120,
    "total": 450,
    "pct": 26.7,
    "created_at": "2026-07-10T07:30:00Z",
    "updated_at": "2026-07-10T07:35:00Z"
  },
  {
    "job_id": "job_20260709_xyz789",
    "filename": "feedback_june.csv",
    "status": "completed",
    "processed": 300,
    "total": 300,
    "pct": 100.0,
    "created_at": "2026-07-09T14:00:00Z",
    "updated_at": "2026-07-09T14:45:00Z"
  }
]
```

**cURL:**

```bash
curl http://localhost:8000/api/jobs \
  -H "Authorization: Bearer <access_token>"
```

---

### GET /api/jobs/{id}

Lấy chi tiết một job cụ thể.

| Thuộc tính | Giá trị |
|---|---|
| **Method** | `GET` |
| **Path** | `/api/jobs/{id}` |
| **Auth** | 👤 user |

**Path Parameters:**

| Tham số | Kiểu | Mô tả |
|---|---|---|
| `id` | string | ID của job |

**Response `200 OK`:**

```json
{
  "job_id": "job_20260710_abc123",
  "filename": "feedback_july.csv",
  "status": "running",
  "processed": 120,
  "total": 450,
  "pct": 26.7,
  "current_step": "Phân loại nhãn chính",
  "output_file": null,
  "error": null,
  "created_at": "2026-07-10T07:30:00Z",
  "updated_at": "2026-07-10T07:35:00Z"
}
```

**Các giá trị `status`:**

| Giá trị | Mô tả |
|---|---|
| `queued` | Đang chờ trong hàng đợi |
| `running` | Đang xử lý |
| `paused` | Đã tạm dừng |
| `completed` | Hoàn thành thành công |
| `cancelled` | Đã hủy bởi người dùng |
| `failed` | Thất bại do lỗi |

**cURL:**

```bash
curl http://localhost:8000/api/jobs/job_20260710_abc123 \
  -H "Authorization: Bearer <access_token>"
```

---

### POST /api/jobs/{id}/cancel

Hủy job đang chạy hoặc đang chờ.

| Thuộc tính | Giá trị |
|---|---|
| **Method** | `POST` |
| **Path** | `/api/jobs/{id}/cancel` |
| **Auth** | 👤 user |

**Response `200 OK`:**

```json
{
  "success": true,
  "job_id": "job_20260710_abc123",
  "status": "cancelled"
}
```

**cURL:**

```bash
curl -X POST http://localhost:8000/api/jobs/job_20260710_abc123/cancel \
  -H "Authorization: Bearer <access_token>"
```

---

### POST /api/jobs/{id}/pause

Tạm dừng job đang chạy.

| Thuộc tính | Giá trị |
|---|---|
| **Method** | `POST` |
| **Path** | `/api/jobs/{id}/pause` |
| **Auth** | 👤 user |

**Response `200 OK`:**

```json
{
  "success": true,
  "job_id": "job_20260710_abc123",
  "status": "paused"
}
```

**cURL:**

```bash
curl -X POST http://localhost:8000/api/jobs/job_20260710_abc123/pause \
  -H "Authorization: Bearer <access_token>"
```

---

### POST /api/jobs/{id}/resume

Tiếp tục job đã bị tạm dừng.

| Thuộc tính | Giá trị |
|---|---|
| **Method** | `POST` |
| **Path** | `/api/jobs/{id}/resume` |
| **Auth** | 👤 user |

**Response `200 OK`:**

```json
{
  "success": true,
  "job_id": "job_20260710_abc123",
  "status": "running"
}
```

**cURL:**

```bash
curl -X POST http://localhost:8000/api/jobs/job_20260710_abc123/resume \
  -H "Authorization: Bearer <access_token>"
```

---

## 3. Files — `/api/files`

### GET /api/files

Liệt kê tất cả file đầu vào và đầu ra trên server.

| Thuộc tính | Giá trị |
|---|---|
| **Method** | `GET` |
| **Path** | `/api/files` |
| **Auth** | 👤 user |

**Response `200 OK`:**

```json
{
  "input": [
    {
      "filename": "feedback_july.csv",
      "size_bytes": 204800,
      "uploaded_at": "2026-07-10T07:25:00Z"
    }
  ],
  "output": [
    {
      "filename": "feedback_june_classified.xlsx",
      "size_bytes": 512000,
      "created_at": "2026-07-09T14:45:00Z"
    }
  ]
}
```

**cURL:**

```bash
curl http://localhost:8000/api/files \
  -H "Authorization: Bearer <access_token>"
```

---

### GET /api/files/output/{filename}/download

Tải xuống file kết quả phân loại.

| Thuộc tính | Giá trị |
|---|---|
| **Method** | `GET` |
| **Path** | `/api/files/output/{filename}/download` |
| **Auth** | 👤 user |

**Path Parameters:**

| Tham số | Kiểu | Mô tả |
|---|---|---|
| `filename` | string | Tên file output cần tải |

**Response:** `200 OK` với body là nội dung file nhị phân (`application/octet-stream` hoặc `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`).

**cURL:**

```bash
curl -OJ http://localhost:8000/api/files/output/feedback_june_classified.xlsx/download \
  -H "Authorization: Bearer <access_token>"
```

---

### DELETE /api/files/input/{filename}

Xóa file đầu vào khỏi server.

| Thuộc tính | Giá trị |
|---|---|
| **Method** | `DELETE` |
| **Path** | `/api/files/input/{filename}` |
| **Auth** | 👤 user |

**Path Parameters:**

| Tham số | Kiểu | Mô tả |
|---|---|---|
| `filename` | string | Tên file input cần xóa |

**Response `200 OK`:**

```json
{
  "success": true,
  "filename": "feedback_july.csv"
}
```

**cURL:**

```bash
curl -X DELETE http://localhost:8000/api/files/input/feedback_july.csv \
  -H "Authorization: Bearer <access_token>"
```

---

### POST /api/files/sync-sharepoint

Đồng bộ file với SharePoint: tải file mới từ SharePoint về server (downloads) và đẩy file output lên SharePoint (uploads).

| Thuộc tính | Giá trị |
|---|---|
| **Method** | `POST` |
| **Path** | `/api/files/sync-sharepoint` |
| **Auth** | 👤 user |

**Response `200 OK`:**

```json
{
  "message": "Đồng bộ SharePoint hoàn tất",
  "uploads": [
    "feedback_june_classified.xlsx"
  ],
  "downloads": [
    "feedback_q3_raw.csv"
  ]
}
```

**cURL:**

```bash
curl -X POST http://localhost:8000/api/files/sync-sharepoint \
  -H "Authorization: Bearer <access_token>"
```

---

## 4. Metrics — `/api/metrics` & `/api/health`

### GET /api/metrics

Lấy thống kê sức khỏe hệ thống: số job đã chạy, tỷ lệ thành công, tài nguyên sử dụng.

| Thuộc tính | Giá trị |
|---|---|
| **Method** | `GET` |
| **Path** | `/api/metrics` |
| **Auth** | 👤 user |

**Response `200 OK`:**

```json
{
  "jobs_total": 128,
  "jobs_completed": 121,
  "jobs_failed": 4,
  "jobs_running": 2,
  "success_rate_pct": 96.8,
  "avg_processing_time_sec": 47.3,
  "total_rows_classified": 38420,
  "uptime_seconds": 86400,
  "memory_usage_mb": 312.5,
  "cpu_usage_pct": 18.2
}
```

**cURL:**

```bash
curl http://localhost:8000/api/metrics \
  -H "Authorization: Bearer <access_token>"
```

---

### GET /api/metrics/usage

Thống kê chi tiết việc sử dụng Gemini API theo khoảng thời gian. Chỉ dành cho admin.

| Thuộc tính | Giá trị |
|---|---|
| **Method** | `GET` |
| **Path** | `/api/metrics/usage` |
| **Auth** | 🔑 admin |

**Query Parameters:**

| Tham số | Kiểu | Mặc định | Mô tả |
|---|---|---|---|
| `period` | string | `week` | Khoảng thời gian: `day`, `week`, `month` |

**Response `200 OK`:**

```json
{
  "period": "week",
  "from": "2026-07-04T00:00:00Z",
  "to": "2026-07-10T23:59:59Z",
  "total_requests": 4280,
  "total_tokens_input": 1284000,
  "total_tokens_output": 856000,
  "total_tokens": 2140000,
  "estimated_cost_usd": 2.14,
  "daily_breakdown": [
    { "date": "2026-07-04", "requests": 580, "tokens": 290000 },
    { "date": "2026-07-05", "requests": 620, "tokens": 310000 }
  ]
}
```

**cURL:**

```bash
curl "http://localhost:8000/api/metrics/usage?period=week" \
  -H "Authorization: Bearer <access_token>"
```

---

### GET /api/health

Kiểm tra trạng thái hoạt động của dịch vụ. Không yêu cầu xác thực — dùng cho health check của container orchestration (Docker, Kubernetes).

| Thuộc tính | Giá trị |
|---|---|
| **Method** | `GET` |
| **Path** | `/api/health` |
| **Auth** | 🔓 Không cần |

**Response `200 OK`:**

```json
{
  "status": "ok",
  "version": "2.1.0",
  "config_assets": {
    "products_loaded": 142,
    "labels_loaded": 18,
    "prompt_template": "v3"
  }
}
```

**cURL:**

```bash
curl http://localhost:8000/api/health
```

---

## 5. Settings — `/api/settings`

### GET /api/settings

Lấy cấu hình hiện tại của dịch vụ. Chỉ dành cho admin.

| Thuộc tính | Giá trị |
|---|---|
| **Method** | `GET` |
| **Path** | `/api/settings` |
| **Auth** | 🔑 admin |

**Response `200 OK`:**

```json
{
  "gemini_model": "gemini-2.0-flash",
  "gemini_temperature": 0.2,
  "gemini_max_tokens": 1024,
  "sharepoint_site_url": "https://company.sharepoint.com/sites/dms",
  "sharepoint_input_folder": "FeedbackInput",
  "sharepoint_output_folder": "FeedbackOutput",
  "batch_size": 10,
  "max_concurrent_jobs": 3,
  "log_level": "INFO",
  "auto_sync_sharepoint": true,
  "auto_sync_interval_minutes": 60
}
```

**cURL:**

```bash
curl http://localhost:8000/api/settings \
  -H "Authorization: Bearer <access_token>"
```

---

### POST /api/settings

Cập nhật cấu hình dịch vụ. Chỉ dành cho admin. Chỉ cần gửi các trường cần thay đổi.

| Thuộc tính | Giá trị |
|---|---|
| **Method** | `POST` |
| **Path** | `/api/settings` |
| **Auth** | 🔑 admin |

**Request Body (partial update):**

```json
{
  "gemini_temperature": 0.1,
  "batch_size": 20,
  "log_level": "DEBUG"
}
```

**Response `200 OK`:**

```json
{
  "success": true,
  "updated_fields": ["gemini_temperature", "batch_size", "log_level"],
  "settings": {
    "gemini_model": "gemini-2.0-flash",
    "gemini_temperature": 0.1,
    "gemini_max_tokens": 1024,
    "batch_size": 20,
    "log_level": "DEBUG"
  }
}
```

**cURL:**

```bash
curl -X POST http://localhost:8000/api/settings \
  -H "Authorization: Bearer <access_token>" \
  -H "Content-Type: application/json" \
  -d '{"gemini_temperature": 0.1, "batch_size": 20}'
```

---

## 6. Users — `/api/users`

### GET /api/users

Lấy danh sách tất cả người dùng trong hệ thống. Chỉ dành cho admin.

| Thuộc tính | Giá trị |
|---|---|
| **Method** | `GET` |
| **Path** | `/api/users` |
| **Auth** | 🔑 admin |

**Response `200 OK`:**

```json
[
  {
    "username": "admin",
    "display_name": "Administrator",
    "role": "admin",
    "created_at": "2026-01-01T00:00:00Z",
    "last_login": "2026-07-10T07:00:00Z"
  },
  {
    "username": "nguyen.van.a",
    "display_name": "Nguyễn Văn A",
    "role": "user",
    "created_at": "2026-03-15T08:00:00Z",
    "last_login": "2026-07-09T14:30:00Z"
  }
]
```

**cURL:**

```bash
curl http://localhost:8000/api/users \
  -H "Authorization: Bearer <access_token>"
```

---

### POST /api/users

Tạo người dùng mới. Chỉ dành cho admin.

| Thuộc tính | Giá trị |
|---|---|
| **Method** | `POST` |
| **Path** | `/api/users` |
| **Auth** | 🔑 admin |

**Request Body:**

```json
{
  "username": "tran.thi.b",
  "password": "secure_password_123",
  "role": "user",
  "display_name": "Trần Thị B"
}
```

| Trường | Kiểu | Bắt buộc | Mô tả |
|---|---|---|---|
| `username` | string | ✅ | Tên đăng nhập (duy nhất trong hệ thống) |
| `password` | string | ✅ | Mật khẩu ban đầu |
| `role` | string | ✅ | `"user"` hoặc `"admin"` |
| `display_name` | string | ❌ | Tên hiển thị |

**Response `201 Created`:**

```json
{
  "username": "tran.thi.b",
  "display_name": "Trần Thị B",
  "role": "user",
  "created_at": "2026-07-10T08:00:00Z"
}
```

**Lỗi `409 Conflict`** nếu username đã tồn tại:

```json
{
  "detail": "Username 'tran.thi.b' đã tồn tại"
}
```

**cURL:**

```bash
curl -X POST http://localhost:8000/api/users \
  -H "Authorization: Bearer <access_token>" \
  -H "Content-Type: application/json" \
  -d '{"username": "tran.thi.b", "password": "secure_password_123", "role": "user", "display_name": "Trần Thị B"}'
```

---

### PUT /api/users/{username}

Cập nhật thông tin người dùng. Chỉ dành cho admin.

| Thuộc tính | Giá trị |
|---|---|
| **Method** | `PUT` |
| **Path** | `/api/users/{username}` |
| **Auth** | 🔑 admin |

**Path Parameters:**

| Tham số | Kiểu | Mô tả |
|---|---|---|
| `username` | string | Tên đăng nhập của người dùng cần cập nhật |

**Request Body (partial update):**

```json
{
  "display_name": "Trần Thị Bình",
  "role": "admin",
  "password": "new_password_optional"
}
```

**Response `200 OK`:**

```json
{
  "username": "tran.thi.b",
  "display_name": "Trần Thị Bình",
  "role": "admin",
  "updated_at": "2026-07-10T09:00:00Z"
}
```

**cURL:**

```bash
curl -X PUT http://localhost:8000/api/users/tran.thi.b \
  -H "Authorization: Bearer <access_token>" \
  -H "Content-Type: application/json" \
  -d '{"display_name": "Trần Thị Bình", "role": "admin"}'
```

---

### DELETE /api/users/{username}

Xóa người dùng khỏi hệ thống. Chỉ dành cho admin.

| Thuộc tính | Giá trị |
|---|---|
| **Method** | `DELETE` |
| **Path** | `/api/users/{username}` |
| **Auth** | 🔑 admin |

**Path Parameters:**

| Tham số | Kiểu | Mô tả |
|---|---|---|
| `username` | string | Tên đăng nhập của người dùng cần xóa |

**Response `200 OK`:**

```json
{
  "success": true,
  "username": "tran.thi.b"
}
```

**Lỗi `404 Not Found`** nếu không tìm thấy user:

```json
{
  "detail": "Người dùng 'tran.thi.b' không tồn tại"
}
```

**cURL:**

```bash
curl -X DELETE http://localhost:8000/api/users/tran.thi.b \
  -H "Authorization: Bearer <access_token>"
```

---

> **Xem thêm:**
> - [Tổng quan API & xác thực](./overview.md)
> - [WebSocket API](./websocket.md)
