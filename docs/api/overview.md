# 🔗 API Overview — DMS Feedback Classification Service

Tài liệu này mô tả tổng quan về REST API của **DMS Feedback Classification Service** — dịch vụ phân loại phản hồi khách hàng tự động dựa trên Large Language Model (LLM) và tích hợp SharePoint.

---

## Base URL

| Môi trường | URL |
|---|---|
| Development (local) | `http://localhost:8000` |
| Production | `http://{host}:8000` |

Tất cả các endpoint REST đều nằm dưới prefix `/api`:

```
http://{host}:8000/api/...
```

WebSocket endpoints nằm trực tiếp tại gốc:

```
ws://{host}:8000/ws/...
```

---

## Xác thực (Authentication)

DMS sử dụng cơ chế **JWT Bearer Token** với hai loại token:

| Token | Thời hạn | Mục đích |
|---|---|---|
| `access_token` | 30 phút | Đính kèm vào mọi request được bảo vệ |
| `refresh_token` | 7 ngày | Lấy `access_token` mới khi hết hạn |

### Luồng xác thực

```
1. Client gọi POST /api/auth/login với {username, password}
        ↓
2. Server trả về {access_token, refresh_token, user}
        ↓
3. Client lưu cả hai token vào localStorage
        ↓
4. Mọi request tiếp theo đính kèm header:
       Authorization: Bearer <access_token>
        ↓
5. Khi server trả về 401 (token hết hạn):
       POST /api/auth/refresh với {refresh_token}
        ↓
6. Server trả về access_token mới → Client lưu lại và thử request
```

### Ví dụ đăng nhập

```bash
curl -X POST http://localhost:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username": "admin", "password": "your_password"}'
```

**Response:**

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

### Ví dụ sử dụng token

```bash
curl http://localhost:8000/api/auth/me \
  -H "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
```

### Làm mới token

```bash
curl -X POST http://localhost:8000/api/auth/refresh \
  -H "Content-Type: application/json" \
  -d '{"refresh_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."}'
```

---

## Request Headers

Mọi request đến API đều cần các header sau:

| Header | Giá trị | Bắt buộc |
|---|---|---|
| `Content-Type` | `application/json` | Khi gửi JSON body |
| `Content-Type` | `multipart/form-data` | Khi upload file |
| `Authorization` | `Bearer <access_token>` | Với mọi endpoint được bảo vệ |

---

## Phân quyền (Roles)

DMS có hai cấp độ phân quyền:

| Role | Mô tả | Quyền truy cập |
|---|---|---|
| `user` | Người dùng thông thường | Phân loại văn bản, quản lý file, xem job |
| `admin` | Quản trị viên | Toàn quyền, bao gồm quản lý user, settings, usage analytics |

- Các endpoint chỉ dành cho `admin` sẽ trả về `403 Forbidden` nếu người dùng có role `user` cố truy cập.
- Mọi endpoint đều yêu cầu đăng nhập trước (trừ `/api/health`).

---

## Mã lỗi (Error Codes)

API trả về lỗi theo định dạng JSON chuẩn:

```json
{
  "detail": "Mô tả lỗi tại đây"
}
```

| HTTP Status | Ý nghĩa | Ví dụ |
|---|---|---|
| `200` | Thành công | Request xử lý thành công |
| `400` | Dữ liệu đầu vào không hợp lệ | Thiếu trường bắt buộc, sai định dạng |
| `401` | Chưa xác thực hoặc token hết hạn | Không có header `Authorization` hoặc token đã expire |
| `403` | Không có quyền | Người dùng `user` cố truy cập endpoint `admin` |
| `404` | Không tìm thấy | Job ID, filename, hoặc username không tồn tại |
| `409` | Đã tồn tại (trùng lặp) | Tạo user với username đã được sử dụng |
| `500` | Lỗi server nội bộ | Lỗi kết nối LLM, SharePoint, hoặc lỗi xử lý không mong đợi |

---

## Tổng quan các nhóm API

| Nhóm | Prefix | Mô tả |
|---|---|---|
| Auth | `/api/auth` | Đăng nhập, đăng xuất, quản lý token |
| Classification | `/api/classify`, `/api/jobs` | Phân loại văn bản và file, theo dõi job |
| Files | `/api/files` | Quản lý file đầu vào/đầu ra, đồng bộ SharePoint |
| Metrics | `/api/metrics`, `/api/health` | Giám sát hệ thống, thống kê sử dụng Gemini |
| Settings | `/api/settings` | Cấu hình dịch vụ |
| Users | `/api/users` | Quản lý tài khoản người dùng |
| WebSocket | `/ws/logs`, `/ws/jobs/{id}` | Stream log và tiến trình real-time |

---

> **Xem thêm:**
> - [Tài liệu đầy đủ các endpoint REST](./endpoints.md)
> - [Tài liệu WebSocket API](./websocket.md)
