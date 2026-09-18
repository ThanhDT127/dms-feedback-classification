## ADDED Requirements

### Requirement: Bật fallback có điều kiện

Fallback SHALL chỉ tồn tại trên nhánh `GEMINI_BACKEND=gateway`. Trong nhánh đó, hệ thống SHALL chỉ bọc client Gateway bằng lớp fallback khi `FALLBACK_ENABLED` bật VÀ `sa-key.json` tồn tại. Thiếu đường dự phòng MUST NOT chặn agent khởi động, vì mất fallback chỉ đưa hệ thống về đúng hành vi trước khi có nó.

#### Scenario: Backend không phải gateway thì không có fallback

- **WHEN** `GEMINI_BACKEND` không đặt hoặc khác `gateway`
- **THEN** hệ thống đi nhánh Vertex hoặc AI Studio sẵn có
- **AND** lớp fallback không tham gia, bất kể `FALLBACK_ENABLED` đặt gì

#### Scenario: Mặc định tắt

- **WHEN** `GEMINI_BACKEND=gateway` và biến `FALLBACK_ENABLED` không được đặt
- **THEN** client trả về là `_GatewayClient` trần, không bọc fallback
- **AND** mọi hành vi của agent giống hệt bản trước khi có fallback

#### Scenario: Bật nhưng thiếu khoá dịch vụ

- **WHEN** `FALLBACK_ENABLED=True` nhưng `sa-key.json` không tồn tại ở gốc dự án
- **THEN** hệ thống ghi ERROR nói rõ fallback đã bị tắt và sự cố Gateway sẽ làm bỏ lô như trước
- **AND** agent vẫn khởi động bình thường với client Gateway trần

#### Scenario: Bật đầy đủ

- **WHEN** `FALLBACK_ENABLED=True` và `sa-key.json` tồn tại
- **THEN** client Gateway được bọc bởi lớp fallback với ngưỡng `FALLBACK_FAIL_THRESHOLD` và chu kỳ thăm dò `FALLBACK_RETRY_AFTER_S`

---

### Requirement: Phân loại lỗi ba nhánh

Hệ thống SHALL phân biệt ba loại lỗi từ Gateway — mất kết nối, vượt hạn mức, lỗi yêu cầu — và CHỈ loại mất kết nối mới được kích hoạt đổi đường.

#### Scenario: Lỗi mất kết nối

- **WHEN** Gateway ném lỗi có chứa dấu hiệu không kết nối được
- **THEN** lỗi được phân loại là `unreachable`
- **AND** lỗi này được tính vào bộ đếm lỗi liên tiếp

#### Scenario: Lỗi vượt hạn mức giữ nguyên đường lùi lịch

- **WHEN** Gateway trả lỗi chứa `429`, `rate limit`, hoặc `resource_exhausted`
- **THEN** lỗi được phân loại là `rate_limit`
- **AND** bộ đếm lỗi liên tiếp KHÔNG tăng
- **AND** cơ chế lùi lịch thử lại sẵn có của `call_llm_batch` xử lý lỗi này

#### Scenario: Lỗi yêu cầu không được coi là mất kết nối

- **WHEN** Gateway trả lỗi 400 hoặc lỗi yêu cầu khác
- **THEN** lỗi được phân loại là `request_error`
- **AND** hệ thống KHÔNG đổi đường, vì gọi thẳng cũng sai y hệt và chỉ tốn thêm một lượt

#### Scenario: Thứ tự kiểm là một phần hợp đồng

- **WHEN** một câu lỗi chứa đồng thời dấu hiệu vượt hạn mức và dấu hiệu mất kết nối
- **THEN** lỗi được phân loại là `rate_limit`, vì nhánh hạn mức được kiểm trước
- **AND** hệ thống KHÔNG đổi đường trong trường hợp này

---

### Requirement: Đổi đường ở đúng ngưỡng lỗi liên tiếp

Hệ thống SHALL đếm lỗi mất kết nối **liên tiếp**, không đếm tổng, và chuyển sang gọi thẳng nhà cung cấp khi bộ đếm chạm `FALLBACK_FAIL_THRESHOLD`. Một lượt hỏng lẻ là chuyện thường; N lượt liên tiếp mới là tuyến chết.

"Liên tiếp" ở đây SHALL hiểu là **không có lượt Gateway thành công nào xen giữa** — chứ không phải các lần hỏng phải sát nhau. Chỉ lượt thành công mới đặt lại bộ đếm; lỗi `rate_limit` và `request_error` không tăng mà cũng không đặt lại nó.

#### Scenario: Một lỗi lẻ không đổi đường

- **WHEN** Gateway hỏng 1 lần rồi lượt sau thành công
- **THEN** hệ thống vẫn ở trên đường Gateway
- **AND** bộ đếm lỗi liên tiếp được đặt lại về 0

#### Scenario: Lỗi khác loại xen giữa không xoá tiến trình đếm

- **WHEN** hệ thống gặp lỗi mất kết nối, rồi một lỗi vượt hạn mức, rồi lại lỗi mất kết nối, và không lượt nào thành công
- **THEN** bộ đếm lỗi mất kết nối liên tiếp giữ giá trị 2
- **AND** một lỗi mất kết nối nữa sẽ chạm ngưỡng 3 và kích hoạt đổi đường

#### Scenario: Chạm ngưỡng thì đổi đường

- **WHEN** Gateway ném lỗi mất kết nối đúng `FALLBACK_FAIL_THRESHOLD` lần liên tiếp
- **THEN** hệ thống chuyển sang gọi thẳng nhà cung cấp
- **AND** mọi lượt sau đó đi thẳng cho tới khi thăm dò thành công

---

### Requirement: Lượt gây đổi đường không được bỏ

Hệ thống SHALL chạy lại ngay trên đường thẳng chính cái lượt vừa làm bộ đếm chạm ngưỡng. Đây là toàn bộ lý do tồn tại của fallback: diễn tập 10/09/2026 cho thấy mất `gateway-lb` thì CRM thử ba lần trong 21–24 giây rồi **bỏ cả lô**.

#### Scenario: Lô đầu tiên của sự cố vẫn xong

- **WHEN** lượt gọi thứ `FALLBACK_FAIL_THRESHOLD` thất bại và kích hoạt đổi đường
- **THEN** lượt đó được gọi lại qua client dự phòng
- **AND** kết quả trả về cho bên gọi như một lượt thành công bình thường
- **AND** không lô nào bị bỏ vì sự cố này

#### Scenario: Ngưỡng không được vượt quá số lần thử lại

- **WHEN** `FALLBACK_FAIL_THRESHOLD` lớn hơn `max_retry` mặc định của `call_llm_batch`
- **THEN** cấu hình đó là SAI và phải bị chặn bởi một phép kiểm tự động
- **AND** lý do: `call_llm_batch` bỏ cuộc trước khi bộ đếm kịp chạm ngưỡng, nên lô đầu tiên của sự cố vẫn bị bỏ trong im lặng

---

### Requirement: Dựng client dự phòng thất bại thì lỗi nổi lên

Client dự phòng SHALL được dựng lười — chỉ khi cần dùng lần đầu — và dựng đúng một lần rồi giữ lại. Nếu việc dựng thất bại, lỗi SHALL nổi lên cho bên gọi thay vì bị nuốt, để `call_llm_batch` xử theo cơ chế thử lại sẵn có.

#### Scenario: Dựng lười và dùng lại

- **WHEN** hệ thống đi đường thẳng nhiều lượt liên tiếp
- **THEN** client dự phòng được dựng ở lượt đầu tiên
- **AND** các lượt sau dùng lại chính client đó

#### Scenario: Mất khoá dịch vụ giữa chừng

- **WHEN** `sa-key.json` tồn tại lúc khởi động nhưng biến mất trước khi sự cố Gateway xảy ra
- **THEN** việc dựng client dự phòng ném lỗi và lỗi đó nổi lên bên gọi
- **AND** hệ quả là lô đang xử lý có thể bị bỏ sau khi hết số lần thử lại — đúng tình huống fallback sinh ra để tránh, nhưng không tự khắc phục được vì không còn đường nào để đi

---

### Requirement: An toàn khi nhiều luồng dùng chung client

Bộ đếm và cờ trạng thái SHALL nằm dưới một khoá. CRM chạy 3 worker song song dùng chung một client; thiếu khoá thì cả ba cùng vượt ngưỡng, cùng dựng client dự phòng và cùng phát báo động.

#### Scenario: Nhiều luồng chỉ đổi đường một lần

- **WHEN** nhiều worker song song cùng gặp lỗi mất kết nối vượt ngưỡng
- **THEN** việc chuyển trạng thái sang đường thẳng xảy ra đúng một lần
- **AND** client dự phòng được dựng đúng một lần

#### Scenario: Thăm dò không được bảo vệ bởi khoá

- **WHEN** nhiều worker cùng tới hạn thăm dò trong lúc đang đi đường thẳng
- **THEN** nhiều lượt thăm dò có thể chạy song song, mỗi lượt trả giá một lần chờ hết thời gian kết nối
- **AND** việc quay về Gateway vẫn chỉ xảy ra một lần, vì thao tác quay về thoát sớm nếu trạng thái đã đổi
- **AND** đây là hành vi đã biết và chấp nhận: lãng phí thời gian chờ, không sai kết quả

---

### Requirement: Thăm dò định kỳ để quay về Gateway

Khi đang đi đường thẳng, hệ thống SHALL thăm dò Gateway bằng một lượt gọi thật, nhưng chỉ sau mỗi `FALLBACK_RETRY_AFTER_S` giây — thăm dò định kỳ chứ không thăm dò mọi lượt, vì mọi lượt thì mọi lượt phải trả giá một lần chờ hết thời gian kết nối.

#### Scenario: Thăm dò thành công thì quay về

- **WHEN** đã qua `FALLBACK_RETRY_AFTER_S` giây và lượt thăm dò qua Gateway thành công
- **THEN** hệ thống quay về gọi qua Gateway
- **AND** bộ đếm lỗi liên tiếp được đặt lại về 0
- **AND** kết quả của lượt thăm dò được dùng làm kết quả trả về, không gọi lại lần nữa

#### Scenario: Thăm dò hỏng thì im lặng đi tiếp

- **WHEN** lượt thăm dò qua Gateway thất bại
- **THEN** hệ thống tiếp tục đi đường thẳng
- **AND** lần thất bại này KHÔNG được tính vào bộ đếm lỗi liên tiếp
- **AND** mốc thời gian thăm dò được đặt lại để chờ đủ chu kỳ tiếp theo

---

### Requirement: Nhật ký đủ để dựng lại khoảng sự cố

Trong lúc đi đường thẳng, Gateway KHÔNG ghi sổ. Hệ thống SHALL tự ghi đủ để sau này dựng lại được khoảng đó mà không phải chờ hoá đơn. Nhật ký MUST đi vào file handler xoay vòng đã có, không chỉ ra stdout.

#### Scenario: Ghi từng lượt đi đường thẳng

- **WHEN** một lượt được gọi qua client dự phòng
- **THEN** hệ thống ghi WARNING có tiền tố `[FALLBACK]` kèm số thứ tự lượt và lý do
- **AND** bản ghi có mốc thời gian

#### Scenario: Ghi câu lỗi thật lúc đổi đường

- **WHEN** hệ thống chuyển sang đường thẳng
- **THEN** hệ thống ghi câu lỗi cuối cùng nhận được từ Gateway
- **AND** lý do: lỗi phân giải tên (container chết) và lỗi từ chối kết nối (cổng chết) đòi hai cách xử lý khác nhau khi lên server; không ghi thì không phân biệt được

#### Scenario: Ghi tổng kết lúc quay về

- **WHEN** hệ thống quay về Gateway
- **THEN** hệ thống ghi độ dài sự cố tính bằng giây, đo từ thời điểm đổi đường của **chính sự cố này**
- **AND** hệ thống ghi số lượt đã đi đường thẳng **tính dồn từ đầu tiến trình**, không phải của riêng sự cố này

#### Scenario: Sự cố thứ hai báo số lượt dồn

- **WHEN** một tiến trình trải qua sự cố thứ nhất 5 lượt đường thẳng, quay về, rồi sự cố thứ hai 3 lượt
- **THEN** bản ghi tổng kết của sự cố thứ hai báo 8 lượt, không phải 3
- **AND** độ dài sự cố vẫn đo đúng riêng sự cố thứ hai

---

### Requirement: Báo cáo sự cố chỉ qua nhật ký

Hệ thống SHALL NOT tự gửi thư khi đổi đường hoặc khi quay về. Nghĩa vụ báo cáo của fallback dừng ở nhật ký; việc biết có sự cố là do người vận hành chủ động đọc log.

#### Scenario: Đổi đường không phát sinh thư

- **WHEN** hệ thống chuyển sang đường thẳng
- **THEN** không lời gọi gửi thư nào được thực hiện
- **AND** `src/llm.py` không nạp `notification` vì bất kỳ lý do gì

#### Scenario: Quay về không phát sinh thư

- **WHEN** hệ thống quay về Gateway
- **THEN** không lời gọi gửi thư nào được thực hiện

#### Scenario: Đường Graph của pipeline không bị ảnh hưởng

- **WHEN** `pipeline.py` chạy xong một lượt và gọi `NotificationService.send_success` hoặc `send_error`
- **THEN** các hàm đó vẫn hoạt động qua Microsoft Graph như trước
- **AND** không tồn tại biến `MAIL_TRANSPORT` hay `SMTP_*` nào để rẽ nhánh
- **AND** bộ test không đọc credential thật từ `.env` và không gửi thư ra server thật
