## 1. Chốt mốc so sánh trước khi sửa

- [x] 1.1 Chạy `pytest` trong venv sạch cài đúng `requirements.txt` + `pytest`, ghi lại kết quả gốc (kỳ vọng: `1 failed, 34 passed`, cái failed là `test_automation.py::test_self_healing_fallback` — hỏng sẵn, không thuộc change này)
- [x] 1.2 `grep -rn "send_alert\|MAIL_TRANSPORT\|SMTP_\|_notify" --include=*.py --include=*.example .` và lưu danh sách để đối chiếu lúc xong

## 2. Cắt chỗ nối giữa fallback và mail

- [x] 2.1 Trong `src/llm.py`: xoá hàm `_notify()` (dòng 292–300)
- [x] 2.2 Trong `src/llm.py`: xoá lời gọi `_notify(...)` trong `_count_failure()` (≈dòng 263), giữ nguyên hai dòng `log_fallback` ngay trên nó
- [x] 2.3 Trong `src/llm.py`: xoá lời gọi `_notify(...)` trong `_back_to_gateway()` (≈dòng 280), giữ nguyên `log_fallback` ngay trên nó
- [x] 2.4 Xác nhận `src/llm.py` không còn chuỗi `notification` ở bất kỳ đâu, kể cả trong import tại chỗ

## 3. Gỡ đường gửi thư SMTP

- [x] 3.1 Trong `src/notification.py`: xoá hàm `send_alert()` (dòng 14–28)
- [x] 3.2 Trong `src/notification.py`: xoá phương thức `_send_email_smtp()` (dòng 37–65)
- [x] 3.3 Trong `src/notification.py`: xoá nhánh rẽ `if config.MAIL_TRANSPORT == "smtp": return self._send_email_smtp(...)` (dòng 78–79)
- [x] 3.4 Trong `src/notification.py`: xoá `import smtplib` (dòng 2) và `from email.message import EmailMessage` (dòng 4)
- [x] 3.5 Xác nhận `NotificationService.__init__`, `_send_email` đường Graph, `send_success`, `send_error` còn nguyên vẹn

## 4. Gỡ cấu hình

- [x] 4.1 Trong `src/config.py`: xoá `MAIL_TRANSPORT` và 5 biến `SMTP_*` (dòng 54–61), giữ nguyên khối `FALLBACK_*` ngay bên dưới
- [x] 4.2 Trong `.env.example`: xoá khối `MAIL_TRANSPORT` + 5 khoá SMTP (dòng 23–32) cùng phần chú thích của chúng
- [x] 4.3 Kiểm tra chú thích còn lại trong `.env.example` không còn nhắc tới `MAIL_TRANSPORT` (dòng 2 có nhắc — sửa lại cho đúng)

## 5. Dọn test

- [x] 5.1 Trong `tests/test_routing_mail.py`: xoá mục "Mail" (dòng 188–343) gồm lớp `_SmtpGia`, hai hàm trợ giúp `_dat_config` / `_voi_smtp_gia`, và 6 test `test_mail_*`
- [x] 5.2 Dọn import không còn dùng ở đầu `tests/test_routing_mail.py` sau khi cắt
- [x] 5.3 Sửa `main()` ở cuối file nếu nó liệt kê tên các test vừa xoá
- [x] 5.4 `git mv tests/test_routing_mail.py tests/test_routing.py`
- [x] 5.6 Trong `tests/test_fallback.py`: hai test `test_many_threads_switch_once` và `test_probe_returns_to_gateway` đang đếm số lần gọi `llm._notify` để kiểm "đổi đường đúng một lần". Chuyển sang đếm `llm.log_fallback` lọc theo tiền tố `DOI DUONG` / `VE DUONG CU` — cùng một sự kiện, nhưng đo bằng thứ còn tồn tại sau khi bỏ mail
- [x] 5.7 Trong `tests/test_fallback.py`: bỏ dòng chặn `llm._notify = lambda ...` trong `main()` cùng comment của nó; lý do tồn tại của nó (sợ gửi thư thật) đã bị change này xoá
- [x] 5.5 Trong `tests/test_automation.py`: trả `test_notification_service_send_email` về bản trước `1106e8b` — bỏ `transport_before` / `config.MAIL_TRANSPORT = "graph"` / khối `try-finally`, giữ lại phần thân test

## 6. Kiểm chứng

- [x] 6.1 Chạy lại grep ở 1.2 — kỳ vọng không còn kết quả nào ngoài `.env` thật (không theo git)
- [x] 6.2 Chạy `pytest` — kỳ vọng `1 failed, 28 passed`, vẫn đúng cái `test_self_healing_fallback` hỏng sẵn, không có failure mới
- [x] 6.3 Xác nhận `tests/test_fallback.py` vẫn 8/8 xanh: việc gỡ mail không đụng logic đổi đường
- [x] 6.4 `python -c "import sys; sys.path.insert(0,'src'); import llm"` chạy được khi **chưa cài** `msal` — chứng minh `llm.py` đã đứt hẳn khỏi `notification`
- [x] 6.5 `openspec validate gateway-fallback-drop-mail-alerts --strict`

## 7. Bàn giao

- [x] 7.1 Ghi vào phần bàn giao: trên máy production phải xoá tay `MAIL_TRANSPORT` và 5 biến `SMTP_*` khỏi `.env` thật (file này không theo git)
- [x] 7.2 Báo người vận hành: từ nay sự cố Gateway chỉ thấy trong log xoay vòng, không còn thư tự động
