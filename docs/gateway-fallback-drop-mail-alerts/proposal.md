## Why

Cơ chế tự đổi đường khi Gateway chết đã chạy trong `src/llm.py` từ 12/09/2026 nhưng **chưa từng có spec**: hành vi của nó chỉ tồn tại trong code và trong comment. Hệ quả là không ai phát biểu được "đúng" nghĩa là gì, nên không ai kiểm được nó còn đúng hay không — ví dụ ràng buộc `FALLBACK_FAIL_THRESHOLD <= max_retry` hiện chỉ sống trong một assert của test, không có chỗ nào ghi rằng đó là yêu cầu.

Cùng lúc, nhánh báo động qua SMTP gắn vào fallback cần gỡ. Nó mặc định `MAIL_TRANSPORT=smtp`, nghĩa là bộ test đọc thẳng credential thật trong `.env` và có thể gửi thư thật ra server thật — hiện phải vá bằng một dòng pin trong `tests/test_automation.py`. Gỡ nhánh này thì phải nói rõ fallback còn lại nghĩa vụ báo cáo gì, mà muốn nói rõ thì phải có spec. Hai việc là một.

## What Changes

**Lập spec cho cơ chế fallback** (không đổi code): phát biểu thành yêu cầu những gì `_FallbackClient` đang làm — ngưỡng lỗi liên tiếp, phân loại lỗi ba nhánh, thăm dò định kỳ để quay về, an toàn đa luồng, và ràng buộc ngưỡng ≤ số lần thử lại.

**Gỡ đường gửi thư SMTP** (phương án B đã chốt):

- Bỏ `send_alert()` và `_send_email_smtp()` khỏi `src/notification.py`, bỏ nhánh rẽ `if config.MAIL_TRANSPORT == "smtp"` trong `_send_email()`.
- Bỏ `MAIL_TRANSPORT` và 5 biến `SMTP_*` khỏi `src/config.py` và `.env.example`.
- Bỏ `_notify()` cùng 2 chỗ gọi trong `src/llm.py` (lúc đổi đường và lúc quay về).
- Bỏ mục "Mail" trong `tests/test_routing_mail.py` (6 test + lớp `_SmtpGia`); trả `test_notification_service_send_email` trong `tests/test_automation.py` về nguyên bản vì cái pin `MAIL_TRANSPORT="graph"` không còn lý do tồn tại.

**Không đụng tới**: `NotificationService` đường Graph và hai hàm `send_success` / `send_error` mà `pipeline.py:337` đang dùng; toàn bộ phần routing Gateway (`_GatewayClient`, `docker-compose.override.yml`, mục "Routing" của `test_routing_mail.py`); logic đổi đường của `_FallbackClient` — `tests/test_fallback.py` phải sửa cách ĐO nhưng không đổi điều nó khẳng định.

**BREAKING** ở mức vận hành, không ở mức API: sau thay đổi này sự cố Gateway **không còn tự đẩy tin ra ngoài**. Bằng chứng vẫn được giữ đủ — `log_fallback()` ghi WARNING vào `RotatingFileHandler` (`pipeline.py:57`, 10MB × 5 file) từng lượt đi đường thẳng, kèm câu lỗi cuối cùng từ Gateway — nhưng phải có người chủ động mở log mới biết. Đây là cái giá đã biết trước của phương án B, không phải sơ suất.

## Capabilities

### New Capabilities

- `gateway-fallback`: tự chuyển sang gọi thẳng nhà cung cấp khi Gateway mất kết nối N lần liên tiếp, tự quay về khi Gateway sống lại, và ghi nhật ký đủ để dựng lại khoảng sự cố. Bao gồm cả nghĩa vụ báo cáo sau khi bỏ mail.

### Modified Capabilities

Không có. `openspec/specs/` hiện rỗng — chưa capability nào được spec, kể cả đường gửi thư. Phần mail bị gỡ vì vậy nằm trong phạm vi `gateway-fallback` (mục nghĩa vụ báo cáo) chứ không tạo delta cho spec nào khác.

## Impact

| File | Việc |
|------|------|
| `src/llm.py` | bỏ `_notify()` (dòng 292–300) + 2 chỗ gọi (≈263, ≈280) |
| `src/notification.py` | bỏ dòng 2, 4, 14–28, 37–65, 78–79 |
| `src/config.py` | bỏ dòng 54–61 (`MAIL_TRANSPORT`, `SMTP_*`) |
| `.env.example` | bỏ dòng 23–32 |
| `tests/test_routing_mail.py` | bỏ dòng 188–343 (mục Mail); đổi tên file thành `test_routing.py` |
| `tests/test_automation.py` | trả dòng 78–96 về bản trước `1106e8b` |
| `tests/test_fallback.py` | đổi 2 test khỏi đếm `_notify` sang đếm `log_fallback`; bỏ dòng chặn thư trong `main()` |

Phụ thuộc: bỏ được `smtplib` và `email.message` (đều là stdlib nên requirements.txt không đổi). Không thêm dependency nào.

Không liên quan tới nhánh CI đỏ: CI đang hỏng vì `requirements.txt` thiếu `pytest` từ commit `0f66bb8` (exit 127, `pytest: command not found`), cộng thêm `test_automation.py::test_self_healing_fallback` đã hỏng sẵn từ trước `9ee9a11`. Cả hai được xử lý ở một change riêng — thay đổi này **không** làm CI xanh trở lại và cũng không làm nó đỏ thêm.
