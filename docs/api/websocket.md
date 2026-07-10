# 🔄 WebSocket API — DMS Feedback Classification Service

DMS cung cấp hai WebSocket endpoint để nhận dữ liệu **real-time** mà không cần polling: stream nhật ký hệ thống và theo dõi tiến trình phân loại theo từng job.

---

## Hai Endpoint WebSocket

| Endpoint | Mục đích |
|---|---|
| `ws://{host}:8000/ws/logs` | Stream nhật ký hệ thống real-time |
| `ws://{host}:8000/ws/jobs/{job_id}` | Theo dõi tiến trình phân loại của một job |

---

## Xác thực

WebSocket không hỗ trợ HTTP header tùy chỉnh trong quá trình handshake theo tiêu chuẩn trình duyệt. Do đó, DMS truyền `access_token` qua **query parameter**:

```
ws://{host}:8000/ws/logs?token=<access_token>
ws://{host}:8000/ws/jobs/{job_id}?token=<access_token>
```

> [!WARNING]
> Token truyền qua URL có thể bị lưu trong server log. Luôn sử dụng **HTTPS/WSS** (`wss://`) trong môi trường production để mã hóa toàn bộ kết nối bao gồm URL.

**Khi token hết hạn hoặc không hợp lệ**, server sẽ đóng kết nối với **close code `4001`**:

```
WebSocket closed: code=4001, reason="Token hết hạn hoặc không hợp lệ"
```

Client cần bắt close code này, làm mới token qua `POST /api/auth/refresh`, rồi kết nối lại.

---

## Endpoint 1: `/ws/logs` — Stream nhật ký hệ thống

Kết nối này stream liên tục các bản ghi log từ toàn bộ dịch vụ DMS: thao tác file, kết nối SharePoint, gọi LLM, v.v.

### Định dạng message

Mỗi message là một JSON object trên một dòng:

```json
{
  "timestamp": "2026-07-10T07:35:22.413Z",
  "level": "INFO",
  "message": "Job job_20260710_abc123 bắt đầu xử lý 450 dòng"
}
```

| Trường | Kiểu | Mô tả |
|---|---|---|
| `timestamp` | string (ISO 8601) | Thời điểm ghi log (UTC) |
| `level` | string | Mức độ log: `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL` |
| `message` | string | Nội dung log |

### Ví dụ các message log

```json
{ "timestamp": "2026-07-10T07:35:20.001Z", "level": "INFO",    "message": "Kết nối SharePoint thành công" }
{ "timestamp": "2026-07-10T07:35:21.142Z", "level": "INFO",    "message": "Tải xuống 3 file mới từ SharePoint" }
{ "timestamp": "2026-07-10T07:35:22.413Z", "level": "INFO",    "message": "Job job_20260710_abc123 bắt đầu xử lý 450 dòng" }
{ "timestamp": "2026-07-10T07:35:25.780Z", "level": "DEBUG",   "message": "Gemini API call #1: 512 tokens" }
{ "timestamp": "2026-07-10T07:36:01.203Z", "level": "WARNING", "message": "Gemini rate limit gần đạt, throttling 2s" }
{ "timestamp": "2026-07-10T07:40:10.999Z", "level": "ERROR",   "message": "Không thể kết nối SharePoint: timeout sau 30s" }
```

---

## Endpoint 2: `/ws/jobs/{job_id}` — Tiến trình phân loại

Kết nối này stream trạng thái real-time của một job phân loại cụ thể. Server gửi message mỗi khi có cập nhật (sau mỗi batch xử lý hoặc khi trạng thái thay đổi).

**URL ví dụ:**

```
ws://localhost:8000/ws/jobs/job_20260710_abc123?token=<access_token>
```

### Định dạng message — Progress update

Gửi định kỳ trong quá trình xử lý:

```json
{
  "type": "progress",
  "step": 3,
  "step_name": "Phân loại nhãn chính",
  "step_status": "running",
  "processed": 90,
  "total": 450,
  "pct": 20.0,
  "elapsed_sec": 68,
  "eta_sec": 272
}
```

| Trường | Kiểu | Mô tả |
|---|---|---|
| `type` | string | Luôn là `"progress"` |
| `step` | integer | Số thứ tự bước hiện tại (bắt đầu từ 1) |
| `step_name` | string | Tên bước đang thực hiện |
| `step_status` | string | Trạng thái bước: `"running"`, `"done"`, `"skipped"` |
| `processed` | integer | Số dòng đã xử lý xong |
| `total` | integer | Tổng số dòng cần xử lý |
| `pct` | float | Phần trăm hoàn thành (0–100) |
| `elapsed_sec` | integer | Thời gian đã chạy (giây) |
| `eta_sec` | integer | Ước tính thời gian còn lại (giây) |

### Định dạng message — Hoàn thành

Gửi một lần khi job hoàn thành thành công:

```json
{
  "type": "done",
  "status": "completed",
  "processed": 450,
  "total": 450,
  "pct": 100.0,
  "elapsed_sec": 340,
  "output_file": "feedback_july_classified.xlsx"
}
```

### Định dạng message — Lỗi

Gửi một lần nếu job thất bại:

```json
{
  "type": "error",
  "message": "Lỗi kết nối Gemini API: quota exceeded",
  "step": 5,
  "processed": 210,
  "total": 450
}
```

### Định dạng message — Bị hủy

Gửi nếu người dùng cancel job qua REST API:

```json
{
  "type": "cancelled",
  "processed": 120,
  "total": 450,
  "pct": 26.7
}
```

### Tóm tắt các giá trị `type`

| `type` | Ý nghĩa | Kết nối tiếp? |
|---|---|---|
| `progress` | Cập nhật tiến trình | ✅ Tiếp tục lắng nghe |
| `done` | Job hoàn thành | ❌ Đóng kết nối |
| `error` | Job thất bại | ❌ Đóng kết nối |
| `cancelled` | Job bị hủy | ❌ Đóng kết nối |

---

## Ví dụ kết nối bằng JavaScript

### Kết nối cơ bản đến `/ws/logs`

```javascript
const token = localStorage.getItem('access_token');
const ws = new WebSocket(`ws://localhost:8000/ws/logs?token=${token}`);

ws.onopen = () => {
  console.log('[WS] Kết nối stream log thành công');
};

ws.onmessage = (event) => {
  const log = JSON.parse(event.data);
  // { timestamp, level, message }
  console.log(`[${log.level}] ${log.timestamp} — ${log.message}`);
};

ws.onerror = (error) => {
  console.error('[WS] Lỗi WebSocket:', error);
};

ws.onclose = (event) => {
  if (event.code === 4001) {
    console.warn('[WS] Token hết hạn — cần làm mới token và kết nối lại');
    refreshTokenAndReconnect();
  } else {
    console.log(`[WS] Kết nối đóng: code=${event.code}`);
  }
};
```

---

### Theo dõi tiến trình job với tự động reconnect

```javascript
class JobProgressWatcher {
  constructor(jobId, onProgress, onDone, onError) {
    this.jobId = jobId;
    this.onProgress = onProgress;
    this.onDone = onDone;
    this.onError = onError;
    this.ws = null;
    this.reconnectDelay = 2000; // ms
    this.maxReconnects = 5;
    this.reconnectCount = 0;
  }

  connect() {
    const token = localStorage.getItem('access_token');
    const url = `ws://localhost:8000/ws/jobs/${this.jobId}?token=${token}`;
    this.ws = new WebSocket(url);

    this.ws.onopen = () => {
      console.log(`[WS] Theo dõi job ${this.jobId}`);
      this.reconnectCount = 0; // reset khi kết nối lại thành công
    };

    this.ws.onmessage = (event) => {
      const msg = JSON.parse(event.data);

      switch (msg.type) {
        case 'progress':
          this.onProgress(msg);
          break;

        case 'done':
          this.onDone(msg);
          this.ws.close(); // Đóng kết nối sau khi hoàn thành
          break;

        case 'error':
          this.onError(msg);
          this.ws.close();
          break;

        case 'cancelled':
          console.warn('[WS] Job đã bị hủy');
          this.ws.close();
          break;
      }
    };

    this.ws.onclose = async (event) => {
      if (event.code === 4001) {
        // Token hết hạn — làm mới rồi kết nối lại
        console.warn('[WS] Token hết hạn, đang làm mới...');
        try {
          await this.refreshToken();
          this.reconnect();
        } catch {
          this.onError({ message: 'Không thể làm mới token' });
        }
      } else if (event.code !== 1000 && this.reconnectCount < this.maxReconnects) {
        // Mất kết nối không mong muốn — thử lại
        this.reconnect();
      }
    };

    this.ws.onerror = () => {
      // Lỗi sẽ được xử lý qua onclose
    };
  }

  reconnect() {
    this.reconnectCount++;
    const delay = this.reconnectDelay * this.reconnectCount;
    console.log(`[WS] Thử kết nối lại lần ${this.reconnectCount} sau ${delay}ms...`);
    setTimeout(() => this.connect(), delay);
  }

  async refreshToken() {
    const refreshToken = localStorage.getItem('refresh_token');
    const res = await fetch('/api/auth/refresh', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh_token: refreshToken }),
    });
    if (!res.ok) throw new Error('Refresh failed');
    const { access_token } = await res.json();
    localStorage.setItem('access_token', access_token);
  }

  disconnect() {
    if (this.ws) this.ws.close(1000, 'Client disconnect');
  }
}

// --- Sử dụng ---
const watcher = new JobProgressWatcher(
  'job_20260710_abc123',
  (msg) => console.log(`Tiến trình: ${msg.pct}% (${msg.processed}/${msg.total})`),
  (msg) => console.log('Hoàn thành! File:', msg.output_file),
  (msg) => console.error('Lỗi:', msg.message)
);

watcher.connect();

// Dừng theo dõi thủ công (nếu cần):
// watcher.disconnect();
```

---

## Lưu ý quan trọng

### Tự động reconnect

Client **nên** tự động thử kết nối lại khi mất kết nối do sự cố mạng thoáng qua. Khuyến nghị:

- Sử dụng **exponential backoff**: tăng dần thời gian chờ giữa các lần thử (ví dụ: 2s, 4s, 8s, …)
- Giới hạn số lần thử (ví dụ: tối đa 5 lần) trước khi thông báo lỗi cho người dùng
- Phân biệt **close code** để xử lý đúng tình huống:

| Close Code | Ý nghĩa | Hành động |
|---|---|---|
| `1000` | Đóng bình thường (job xong) | Không reconnect |
| `1001` | Server tắt | Reconnect sau vài giây |
| `1006` | Mất kết nối đột ngột | Reconnect với backoff |
| `4001` | Token hết hạn / không hợp lệ | Refresh token rồi reconnect |

### Token hết hạn

Khi nhận close code `4001`:

1. Gọi `POST /api/auth/refresh` với `refresh_token` từ localStorage
2. Lưu `access_token` mới vào localStorage
3. Kết nối lại WebSocket với token mới

Nếu `refresh_token` cũng hết hạn (7 ngày), redirect người dùng về trang đăng nhập.

### Production — dùng WSS

Trong môi trường production, **bắt buộc** sử dụng giao thức bảo mật `wss://`:

```javascript
// Development
const url = `ws://localhost:8000/ws/jobs/${jobId}?token=${token}`;

// Production
const url = `wss://dms.company.com/ws/jobs/${jobId}?token=${token}`;
```

---

> **Xem thêm:**
> - [Tổng quan API & xác thực](./overview.md)
> - [Tài liệu REST Endpoints](./endpoints.md)
