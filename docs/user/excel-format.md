# 📊 Định dạng File Excel

Tài liệu này mô tả chi tiết cấu trúc file Excel mà hệ thống **DMS Feedback Classification Service** yêu cầu ở đầu vào (Input) và tạo ra ở đầu ra (Output). Đọc kỹ phần này trước khi chuẩn bị dữ liệu để đảm bảo pipeline xử lý đúng và không bị lỗi.

---

## 2.1 File đầu vào (Input)

### Cột bắt buộc

File Input **bắt buộc** phải có ít nhất một cột chứa **nội dung văn bản phản hồi**. Hệ thống tự động phát hiện cột này theo hai cách:

**Cách 1 — Khớp tên cột (ưu tiên hơn):** Hệ thống quét dòng tiêu đề và tìm cột có tên chứa bất kỳ chuỗi nào sau đây (không phân biệt hoa thường, có dấu hoặc không dấu):

| Tên cột được nhận diện |
| :--- |
| `Nội dung` |
| `noi dung` |
| `Nội dung vấn đề` |
| `noi dung van de` |
| `Nội dung phản hồi` |
| `noi dung phan hoi` |

**Cách 2 — Heuristic tự động (dự phòng):** Nếu không tìm thấy tên cột phù hợp, hệ thống chấm điểm từng cột dựa trên công thức:

```
Score = (Độ dài trung bình × 0.7) + (Tỷ lệ có dấu cách × 20) + (Tỷ lệ không phải số × 30)
```

Cột có điểm cao nhất sẽ được chọn làm cột văn bản. Đây là cơ chế dự phòng — **khuyến nghị đặt tên cột đúng quy ước** để tránh nhầm lẫn.

> **Lưu ý:** Hệ thống quét tối đa 10 dòng đầu tiên để phát hiện hàng tiêu đề. Nếu file có nhiều hàng trống ở đầu, hãy xóa bớt trước khi upload.

---

### Cột tùy chọn

Ngoài cột văn bản phản hồi, file có thể chứa bất kỳ cột nào khác. Hệ thống **giữ nguyên toàn bộ cột gốc** và chỉ chèn thêm cột kết quả vào bên cạnh. Các cột thường gặp trong thực tế:

| Cột tùy chọn | Kiểu dữ liệu | Ghi chú |
| :--- | :--- | :--- |
| `Tên khách hàng` / `Tên đại lý` | Văn bản | Hệ thống không đọc, chỉ giữ nguyên |
| `Ngày phản hồi` | Ngày tháng (date) | Hệ thống không đọc, chỉ giữ nguyên |
| `Mã phản hồi` / `ID` | Số hoặc văn bản | Hệ thống không đọc, chỉ giữ nguyên |
| `Khu vực` / `Vùng` | Văn bản | Hệ thống không đọc, chỉ giữ nguyên |
| `Nhân viên xử lý` | Văn bản | Hệ thống không đọc, chỉ giữ nguyên |
| `Sản phẩm` (người dùng nhập) | Văn bản | Hệ thống **không dùng** — tự khớp bằng RAG |

> **Quan trọng:** Nếu file đã có cột tên `Sản phẩm`, `Dòng SP`, `Model` do người dùng nhập thủ công, hệ thống sẽ dịch chuyển những cột đó sang phải và **ghi đè bằng kết quả RAG** để đảm bảo nhất quán. Hãy đổi tên những cột tự nhập đó trước khi upload.

---

## 2.2 Ví dụ file đầu vào

Dưới đây là ví dụ 3 dòng dữ liệu hợp lệ mà pipeline có thể xử lý tốt:

| Mã PH | Ngày | Khu vực | Nội dung phản hồi |
| :--- | :--- | :--- | :--- |
| FB-001 | 01/07/2026 | Hà Nội | Bóng đèn LED Rạng Đông mua 2 tháng trước đã bị đứt bóng, chất lượng kém quá, đổi trả mãi không xong |
| FB-002 | 02/07/2026 | TP.HCM | Đại lý cạnh tranh đang bán Philips giá rẻ hơn Rạng Đông 15%, xin hãng xem lại chiết khấu |
| FB-003 | 03/07/2026 | Đà Nẵng | Anh ơi cho em xin catalogue và bảng giá bóng đèn tròn để gửi cho khách xem |

---

## 2.3 File đầu ra (Output)

Sau khi pipeline xử lý xong, hệ thống tạo ra file Excel kết quả với **cấu trúc hai hàng tiêu đề** (Double Header): hàng 1 là **Nhóm lớn** (Major Category, tô màu nhận diện), hàng 2 là **Nhãn chi tiết** (Minor Label). Dữ liệu bắt đầu từ hàng 3.

### Cột được chèn ngay sau cột văn bản phản hồi

Các cột sau được chèn **trực tiếp ngay sau** cột nội dung phản hồi, giữ nguyên vị trí hàng:

| Tên cột | Kiểu dữ liệu | Mô tả |
| :--- | :--- | :--- |
| **Sản phẩm** | Văn bản | Tên danh mục sản phẩm Rạng Đông được nhận diện từ nội dung phản hồi (ví dụ: `Bóng đèn LED Bulb`). Trống nếu không khớp được sản phẩm nào |
| **Dòng SP** | Văn bản | Dòng sản phẩm cấp cao hơn (ví dụ: `Chiếu sáng dân dụng`, `Chiếu sáng công nghiệp`). Được lấy từ bảng phân chia nhóm sản phẩm |
| **Model** | Văn bản | Mã model chính xác theo danh mục chính thức (ví dụ: `LED BULB A60 9W`). Trống nếu RAG không khớp đủ điểm tin cậy |
| **Lớp** | Văn bản | *Placeholder cũ — luôn để trống. Giữ lại để tương thích cấu trúc file* |
| **Điểm** | Văn bản | *Placeholder cũ — luôn để trống. Giữ lại để tương thích cấu trúc file* |

> **Hãng:** Đối với phản hồi về đối thủ cạnh tranh, tên thương hiệu đối thủ được ghi trong cột nhãn nhóm **Đối thủ cạnh tranh > Hãng** (đánh dấu `x`), không phải trong cột Sản phẩm/Model.

---

### 21 cột nhãn phân loại

Các cột nhãn được thêm vào **cuối bảng**, sau tất cả cột gốc. Mỗi cột tương ứng với một nhãn phân loại. Giá trị:
- **`x`** — nhãn này được kích hoạt cho dòng phản hồi
- *(trống)* — nhãn không áp dụng

Bảng tổng hợp 21 cột nhãn theo nhóm (với mã màu tiêu đề):

| Nhóm lớn (hàng 1) | Màu nền | Nhãn chi tiết (hàng 2) |
| :--- | :--- | :--- |
| **Sản phẩm** | Vàng nhạt `#FFE699` | Báo lỗi |
| | | Báo CL tốt |
| | | Y/c cải tiến |
| | | Đề xuất SPM |
| **Yêu cầu công cụ BH** | Xanh lá nhạt `#C6E0B4` | Bảng giá, Catalogue |
| | | Bảng biển |
| | | Kệ bóng, thử đèn,… |
| | | Khác |
| **Giá, cơ chế RD** | Xanh dương nhạt `#BDD7EE` | Tốt/ ko tốt |
| | | Trả thưởng |
| | | Đề xuất |
| **Dịch vụ** | Cam nhạt `#F8CBAD` | Bảo hành |
| | | HTPP |
| | | Hàng hoá |
| **Hàng giả** | Cam đậm `#F4B183` | Hàng giả |
| **Website** | Xanh tím nhạt `#D9E1F2` | Website |
| **Đối thủ cạnh tranh** | Xám `#C9C9C9` | Hãng |
| | | Hoạt động |
| | | CTKM, giá, cơ chế |
| | | TT SP |
| **Tin trung lập** | Vàng `#FFD966` | Tin trung lập |

---

### Cột bổ trợ sau cùng

| Tên cột | Kiểu dữ liệu | Mô tả |
| :--- | :--- | :--- |
| **Sentiment** | Văn bản | Cảm xúc tổng quan: `Tích cực`, `Tiêu cực`, hoặc trống nếu trung lập/không xác định |
| **LLM_Extracted** | Văn bản | Đoạn văn bản thô mà Gemini trích xuất được, dùng để audit và kiểm tra kết quả phân loại |
| **BM25_Score** | Số thực | Điểm tin cậy của bộ khớp RAG BM25 (thường từ 0–50). Điểm cao hơn = khớp sản phẩm chắc chắn hơn. Ngưỡng chấp nhận mặc định: 5.0 |

---

## 2.4 Quy tắc về file

Tuân thủ các quy tắc sau để tránh lỗi pipeline:

| Quy tắc | Chi tiết |
| :--- | :--- |
| **Định dạng bắt buộc** | File phải là `.xlsx` (Excel 2007 trở lên). Không chấp nhận `.xls`, `.csv`, `.ods` |
| **Số lượng sheet** | File phải có ít nhất 1 sheet. Pipeline đọc **sheet đầu tiên** (index 0). Các sheet còn lại bị bỏ qua |
| **Số dòng dữ liệu** | Tối thiểu 1 dòng dữ liệu (không tính hàng tiêu đề). File rỗng hoặc chỉ có tiêu đề sẽ bị bỏ qua |
| **Số dòng tối đa (khuyến nghị)** | **500 dòng/file** để tránh timeout API. Với file lớn hơn, hãy chia nhỏ thành nhiều file |
| **Kích thước file** | File quá nhỏ (< 1 KB sau khi đọc header) sẽ bị cảnh báo và có thể bị bỏ qua |
| **Mã hóa ký tự** | File phải lưu theo chuẩn UTF-8 hoặc Unicode. Tránh dùng encoding cũ (ANSI, Windows-1252) |
| **Không được mở đồng thời** | Không mở file bằng Excel trong khi đang upload lên SharePoint để tránh file bị khóa |

---

## 2.5 Lỗi thường gặp

### ❌ Lỗi: Không tìm thấy cột nội dung

**Biểu hiện:** Log hiển thị `Could not detect text column` hoặc `No suitable text column found`.

**Nguyên nhân:** Cột văn bản phản hồi có tên không khớp với bất kỳ từ khóa nhận diện nào, và thuật toán heuristic cũng không tìm thấy cột văn bản đủ tiêu chí.

**Cách khắc phục:**
- Đổi tên cột chứa nội dung phản hồi thành `Nội dung` hoặc `Nội dung phản hồi`
- Kiểm tra xem cột có thực sự chứa văn bản dài (không phải toàn số, ngày tháng)

---

### ❌ Lỗi: File rỗng hoặc không có dòng dữ liệu

**Biểu hiện:** Log hiển thị `Workbook has no data rows` hoặc pipeline kết thúc ngay lập tức với 0 dòng xử lý.

**Nguyên nhân:** File chỉ có hàng tiêu đề, không có dòng dữ liệu nào phía dưới.

**Cách khắc phục:** Kiểm tra lại file — đảm bảo có ít nhất 1 dòng dữ liệu bên dưới hàng tiêu đề.

---

### ❌ Lỗi: File quá nhỏ hoặc bị hỏng

**Biểu hiện:** Log hiển thị `File too small`, `Cannot open workbook`, hoặc `openpyxl error`.

**Nguyên nhân:** File bị hỏng khi tải lên, hoặc file thực chất không phải định dạng `.xlsx` (ví dụ: file `.csv` được đổi đuôi thành `.xlsx`).

**Cách khắc phục:**
- Mở file bằng Excel để kiểm tra — nếu Excel báo lỗi, file bị hỏng
- Lưu lại file từ Excel gốc theo định dạng `Excel Workbook (*.xlsx)`
- Tránh đổi đuôi file thủ công

---

### ⚠️ Cảnh báo: Kết quả sản phẩm trống

**Biểu hiện:** Cột `Sản phẩm`, `Dòng SP`, `Model` đều trống với nhiều dòng.

**Nguyên nhân:** Nội dung phản hồi không đề cập rõ tên sản phẩm, hoặc từ ngữ quá viết tắt/sai chính tả khiến RAG không khớp được.

**Cách khắc phục:** Đây không phải lỗi hệ thống — kết quả phân loại nhãn vẫn được thực hiện bình thường. Nếu muốn cải thiện tỷ lệ khớp sản phẩm, liên hệ admin để cập nhật từ khóa trong `kw_map.json`.

---

*← [Quay lại trang chủ tài liệu](../README.md) | Tiếp theo: [Tra cứu nhãn phân loại →](labels-reference.md)*
