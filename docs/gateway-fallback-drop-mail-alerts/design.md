## Context

Hai commit `1106e8b` và `77dc703` trên nhánh `Tuan-develop` gộp ba việc đi cùng nhau: routing qua Gateway, tự đổi đường khi Gateway chết, và gửi thư báo động qua SMTP. Thay đổi này tách việc thứ ba ra khỏi hai việc đầu.

Chỗ nối duy nhất giữa mail và fallback là hàm `_notify()` trong `src/llm.py` và hai lời gọi của nó:

```
_FallbackClient._count_failure()   ──┐
                                     ├──▶  _notify()  ──▶  notification.send_alert()
_FallbackClient._back_to_gateway() ──┘                          │
                                                                ▼
                                                    NotificationService._send_email()
                                                        ├─ MAIL_TRANSPORT == "smtp"  ← gỡ
                                                        └─ Graph API                 ← giữ
```

Còn lại là hai khối rời hẳn nhau:

```
ĐƯỜNG BÁO CÁO CỦA PIPELINE (có sẵn, không đụng)
  pipeline.py:337 → NotificationService(auth) → send_success / send_error → Graph

ĐƯỜNG ĐI CỦA LỆNH GỌI LLM (không đụng)
  _FallbackClient → _GatewayClient | _build_vertex_client()
```

Ràng buộc: `NotificationService` đường Graph tuy chưa từng gửi được thư nào (ba biến `AZURE_*` đều rỗng tính đến 12/09/2026) nhưng vẫn là đường dành cho production và `pipeline.py` đang gọi. Nó nằm ngoài phạm vi thay đổi này.

## Goals / Non-Goals

**Goals:**

- Phát biểu thành spec toàn bộ hành vi fallback đang chạy, để lần sau có căn cứ kiểm.
- Gỡ sạch đường gửi thư SMTP, không để lại code chết.
- Sau khi gỡ, `src/llm.py` không còn bất kỳ lý do nào để nạp `notification`.
- Bộ test không còn đọc credential thật trong `.env`.

**Non-Goals:**

- Không sửa logic đổi đường. Spec mô tả đúng cái đang chạy, không đề xuất hành vi mới.
- Không dựng đường báo động thay thế (webhook, Teams, cờ trong nhật ký chạy). Nếu cần thì là change riêng — xem Open Questions.
- Không đụng `NotificationService` đường Graph, không đụng routing Gateway.
- Không sửa CI. Việc đó thuộc change `fix-ci-missing-pytest`.

## Decisions

**1. Spec `gateway-fallback` mô tả nguyên trạng, không kèm cải tiến.**

Fallback đã chạy và đã có 8 test phủ. Viết spec theo đúng code hiện tại biến đống hành vi ngầm thành hợp đồng kiểm được, mà không mang thêm rủi ro. Phương án khác — vừa spec vừa sửa cho "đúng hơn" — trộn hai loại thay đổi vào một lần và làm mất khả năng dùng spec này làm mốc so sánh.

Một điều đáng chú ý: ràng buộc `FALLBACK_FAIL_THRESHOLD <= max_retry` hiện chỉ sống trong assert của `test_nguong_khong_duoc_lon_hon_so_lan_thu_lai`. Vi phạm nó thì lô đầu tiên của sự cố vẫn bị bỏ — đúng cái mà fallback sinh ra để tránh — và không ai thấy được khi chạy. Nâng nó thành requirement là phần giá trị nhất của việc lập spec này.

**2. Gỡ cả `send_alert()`, không chỉ gỡ `_send_email_smtp()`.**

Phương án hẹp hơn (mức A đã cân nhắc) là bỏ SMTP mà giữ `send_alert()` chạy qua Graph. Loại vì `AZURE_*` rỗng nghĩa là mọi lời gọi trả `False` trong im lặng: code còn nguyên mà không bao giờ làm được việc gì. Đó là dạng tệ nhất — nhìn thì tưởng có báo động.

**3. Không dựng đường báo động thay thế trong change này.**

`log_fallback()` đã ghi WARNING vào `RotatingFileHandler` (`pipeline.py:57`) kèm mốc thời gian, số thứ tự từng lượt đi đường thẳng, câu lỗi thật từ Gateway, và tổng kết lúc quay về. Bằng chứng không mất, chỉ mất khâu tự đẩy ra ngoài. Thêm webhook ngay bây giờ là kéo phạm vi ra ngoài cái đã chốt.

**4. Đổi tên `tests/test_routing_mail.py` thành `tests/test_routing.py`.**

Sau khi bỏ mục Mail (dòng 188–343), file chỉ còn 7 test routing. Giữ tên cũ thì tên file nói dối về nội dung. Dùng `git mv` để giữ lịch sử.

**5. Xoá cái pin `MAIL_TRANSPORT="graph"` trong `test_automation.py` thay vì giữ cho chắc.**

Cái pin đó tồn tại vì `MAIL_TRANSPORT` mặc định là `"smtp"`. Biến đó biến mất thì pin tham chiếu tới thuộc tính không còn tồn tại. Trả nguyên hàm test về bản trước `1106e8b` là diff nhỏ nhất và đúng nhất.

**6. `test_fallback.py` đổi đại lượng đo, không đổi điều khẳng định.**

Hai test đang đếm số lần gọi `_notify` để kiểm "một sự cố sinh đúng một lần đổi đường". Thư chỉ là
đại lượng thay thế; sự kiện thật là dòng log `DOI DUONG` / `VE DUONG CU`. Chuyển sang đếm
`log_fallback` lọc theo tiền tố giữ nguyên điều đang kiểm mà đo bằng thứ còn tồn tại — và chặt hơn
bản cũ, vì nay đo thẳng cái sự kiện thay vì đo hệ quả của nó.

Dòng `llm._notify = lambda *a, **k: None` trong `main()` bị bỏ hẳn. Comment của nó ghi rõ lý do tồn
tại: "khong chan thi bo kiem gui thu that vao hop thu nguoi that -- da xay ra mot lan khi viet file
nay." Sau change này không còn đường nào để gửi, nên tấm chắn đó không còn việc.

## Risks / Trade-offs

**[Sự cố Gateway không còn tự báo ra ngoài] → Nhật ký giữ đủ bằng chứng; đây là cái giá đã biết và đã chốt của phương án B.** Diễn tập 12/09/2026 cho thấy fallback làm việc quá tốt — 60/60 lô chạy xong, 0 lô ném lỗi ra ngoài — nên nếu không đọc log thì sự cố trôi qua không ai biết. Ghi rõ ở đây để người sau không tưởng là sơ suất.

**[Bỏ sót một tham chiếu `SMTP_*` hoặc `MAIL_TRANSPORT`] → Grep toàn repo sau khi sửa; danh sách đầy đủ 6 file đã liệt kê trong proposal.** `config.py` đọc bằng `os.getenv` nên biến sót lại không gây lỗi lúc chạy, chỉ âm thầm nằm đó.

**[`test_self_healing_fallback` vẫn đỏ sau change này] → Đúng như dự kiến; nó hỏng sẵn từ trước `9ee9a11` và thuộc change CI.** Không được dùng nó làm tín hiệu đánh giá change này. Mốc so sánh đúng: trước change `1 failed, 34 passed`, sau change `1 failed, 28 passed` (6 test mail bị gỡ theo).

**[Lỡ tay revert luôn phần routing] → Sửa tay theo danh sách dòng, không dùng `git revert 1106e8b`.** Commit đó gộp cả routing lẫn mail; revert cả cục sẽ kéo mất `_GatewayClient` và `docker-compose.override.yml`.

## Migration Plan

Không có dữ liệu cần chuyển. Các bước triển khai:

1. Gỡ code theo `tasks.md`.
2. Trên máy chạy production, bỏ `MAIL_TRANSPORT` và 5 biến `SMTP_*` khỏi `.env` thật. File `.env` không theo git nên việc này phải làm tay.
3. Báo cho người vận hành rằng từ nay sự cố Gateway phải xem trong log xoay vòng ở thư mục log của pipeline.

Lui về: `git revert` chính commit của change này. Không có trạng thái ngoài code nên lui về là sạch.

## Open Questions

- **`_fallback_calls` không bao giờ được đặt lại** (`llm.py:193` khởi tạo, `:237` tăng, không có dòng reset nào). `_back_to_gateway` đặt lại `_consecutive_failures` nhưng không đặt lại cái này, nên bản ghi tổng kết lúc quay về báo con số **dồn từ đầu tiến trình**, trong khi câu chữ `"%d luot da di duong thang"` đọc ra là của riêng sự cố vừa rồi. Sự cố thứ hai trở đi sẽ báo lệch. Spec đã ghi đúng nguyên trạng và có scenario khoá lại hành vi này. Sửa thì chỉ tốn một dòng, nhưng nằm ngoài phạm vi đã chốt của phương án B — để thành change riêng.
- **Nhánh thăm dò đọc `_on_fallback` và `_last_probe_at` ngoài khoá** (`llm.py:205`). Ba worker có thể cùng thăm dò một lúc, mỗi đứa trả giá một lần chờ hết thời gian kết nối. Không sai kết quả vì `_back_to_gateway` thoát sớm khi trạng thái đã đổi, chỉ lãng phí. Đã ghi thành scenario để người sau không tưởng là sót.
- Có cần đường báo động thay thế không, và nếu có thì đẩy đi đâu? Webhook Teams là ứng viên rẻ nhất vì không cần credential Azure. Để sau khi hệ thống chạy ổn rồi quyết.
- Hạn mức ẢO của Gateway và hạn mức THẬT của Google hiện chưa phân biệt được từ phía agent, nên `classify_error` gộp cả hai vào `rate_limit`. Không thuộc phạm vi change này nhưng ghi lại vì spec vừa chốt hành vi gộp đó.
- `VERTEX_PROJECT` trỏ `crm-test-508114` trong khi `dim_agent` ghi sổ cho `crm-500509`. Chốt 12/09/2026 là giai đoạn phát triển chấp nhận lệch, phải xem lại trước khi lên server. Nội dung thư bị gỡ có nhắc tới project này — gỡ xong thì chỗ nhắc duy nhất còn lại là comment trong `config.py`.
