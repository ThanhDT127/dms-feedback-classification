# 🏷️ Tra cứu Nhãn Phân loại

Tài liệu này là sổ tay tra cứu đầy đủ cho **hệ thống 21 nhãn phân loại** của DMS Feedback Classification Service. Mỗi câu phản hồi được Gemini LLM phân tích và gán nhãn theo **8 nhóm lớn** với tổng cộng **21 nhãn chi tiết**. Hiểu rõ ranh giới của từng nhãn giúp bạn kiểm tra kết quả pipeline, phát hiện nhãn sai, và trao đổi chính xác với đội kỹ thuật khi cần điều chỉnh.

---

## 📋 Bảng tóm tắt 8 nhóm × 21 nhãn

| # | Nhóm lớn | Nhãn chi tiết | Tổng |
| :--- | :--- | :--- | :---: |
| 1 | 📦 Sản phẩm | Báo lỗi · Báo CL tốt · Y/c cải tiến · Đề xuất SPM | 4 |
| 2 | 🛠️ Yêu cầu công cụ BH | Bảng giá, Catalogue · Bảng biển · Kệ bóng, thử đèn,… · Khác | 4 |
| 3 | 💰 Giá, cơ chế RD | Tốt/ ko tốt · Trả thưởng · Đề xuất | 3 |
| 4 | 🔧 Dịch vụ | Bảo hành · HTPP · Hàng hoá | 3 |
| 5 | ⚠️ Hàng giả | Hàng giả | 1 |
| 6 | 🌐 Website | Website | 1 |
| 7 | 🏢 Đối thủ cạnh tranh | Hãng · Hoạt động · CTKM, giá, cơ chế · TT SP | 4 |
| 8 | 📋 Tin trung lập | Tin trung lập | 1 |
| | | **Tổng cộng** | **21** |

---

## 1. 📦 Nhóm Sản phẩm

Nhóm này bao gồm tất cả phản hồi liên quan đến **chất lượng, thiết kế, và tính năng** của sản phẩm Rạng Đông hiện có, hoặc đề xuất sản phẩm mới. Nhóm màu **vàng nhạt** (`#FFE699`) trong file Excel kết quả.

| Nhãn | Định nghĩa | Ví dụ feedback |
| :--- | :--- | :--- |
| **Báo lỗi** | Sản phẩm vật lý bị lỗi kỹ thuật, hỏng hóc, cháy, chập, không sáng, rò điện, nứt vỡ, đứt bóng. **Không** dùng cho phàn nàn thiết kế vỏ mỏng hay kích thước phích cắm | *"Bóng đèn Rạng Đông mua 3 tuần đã cháy, bộ phận khách hàng cho đổi mãi chưa xong"* |
| **Báo CL tốt** | Khen ngợi chất lượng sản phẩm: bền, sáng tốt, ổn định điện áp, khách hàng tin dùng lâu dài | *"Bóng led dùng 2 năm vẫn sáng đẹp, không hao điện, khách hàng rất hài lòng, tiếp tục ủng hộ Rạng Đông"* |
| **Y/c cải tiến** | Góp ý hoặc phàn nàn về thiết kế, kích thước, bao bì của sản phẩm **đang tồn tại**. Ví dụ: dây dài thêm, phích cắm nhỏ lại, vỏ dày hơn. **Phân biệt** với Đề xuất SPM: Y/c cải tiến là chỉnh sửa sản phẩm hiện có | *"Cái chân phích của bóng tròn hơi to, cắm vào ổ điện vướng cái kế bên, mong hãng điều chỉnh lại"* |
| **Đề xuất SPM** | Đề nghị Rạng Đông sản xuất hoặc phân phối thêm dòng sản phẩm **mới chưa từng có**. Ví dụ: ra thêm đèn thông minh, sản xuất dây điện, ra thêm loại bóng đặc thù | *"Rạng Đông có kế hoạch ra mắt đèn LED âm trần có điều khiển từ xa không? Khách hàng hỏi nhiều lắm"* |

---

## 2. 🛠️ Nhóm Yêu cầu công cụ bán hàng

Nhóm này gồm các yêu cầu **hỗ trợ vật tư và công cụ** để đại lý và nhân viên bán hàng có thể tư vấn, trưng bày và thuyết phục khách hàng tốt hơn. Nhóm màu **xanh lá nhạt** (`#C6E0B4`).

| Nhãn | Định nghĩa | Ví dụ feedback |
| :--- | :--- | :--- |
| **Bảng giá, Catalogue** | Yêu cầu gửi bảng giá niêm yết, catalogue giấy hoặc file PDF/mềm để giới thiệu sản phẩm cho khách hàng cuối | *"Anh ơi cho em xin catalogue mới nhất và bảng giá bóng đèn, em cần gửi cho mấy khách đang hỏi"* |
| **Bảng biển** | Yêu cầu hỗ trợ làm và lắp đặt biển quảng cáo, biển hiệu cửa hàng, đèn LED thương hiệu, POSM treo ngoài mặt tiền | *"Cửa hàng em chuyên bán Rạng Đông nhưng vẫn chưa có biển hiệu, xin hãng hỗ trợ làm biển để tăng nhận diện thương hiệu"* |
| **Kệ bóng, thử đèn,…** | Yêu cầu cung cấp kệ trưng bày sản phẩm, tủ thử bóng, bảng demo để khách hàng nhìn thấy và thử trực tiếp tại điểm bán | *"Shop em cần thêm kệ trưng bày bóng đèn, khách vào hay hỏi nhưng không có chỗ để tivi thì khó thuyết phục mua"* |
| **Khác** | Yêu cầu công cụ bán hàng khác ngoài 3 loại trên: áo đồng phục nhân viên, tờ rơi, sổ tay, poster khuyến mãi, túi giấy thương hiệu | *"Cho em xin thêm tờ rơi khuyến mãi tháng 7 và vài cái áo đồng phục Rạng Đông cho nhân viên"* |

---

## 3. 💰 Nhóm Giá, cơ chế RD

Nhóm này bao gồm tất cả phản hồi liên quan đến **giá bán lẻ, chiết khấu đại lý, chương trình thưởng** và chính sách giá tổng thể của Rạng Đông. Nhóm màu **xanh dương nhạt** (`#BDD7EE`).

> **Phân biệt với nhóm Đối thủ:** Nếu feedback so sánh giá Rạng Đông với đối thủ, gán cả nhãn `Tốt/ ko tốt` (nhóm này) **và** nhãn `CTKM, giá, cơ chế` (nhóm Đối thủ).

| Nhãn | Định nghĩa | Ví dụ feedback |
| :--- | :--- | :--- |
| **Tốt/ ko tốt** | Nhận xét trực tiếp về mức giá hoặc chiết khấu của Rạng Đông: quá đắt, khó bán do giá cao so với thị trường, hoặc khen giá cạnh tranh tốt | *"Bóng Rạng Đông giá cao quá so với hàng Trung Quốc cùng watt, đại lý khó bán lắm, anh ơi xem lại giúp"* |
| **Trả thưởng** | Phàn nàn hoặc hỏi về việc nhận tiền thưởng chậm, chương trình quay số C2TD, tích điểm đổi quà, hoặc hỏi về nợ thưởng chưa thanh toán | *"Chương trình thưởng quý 1 em chưa nhận được tiền, hỏi mãi bộ phận kinh doanh nói đang xử lý, tháng này là tháng 4 rồi"* |
| **Đề xuất** | Đề nghị thay đổi chính sách chiết khấu tổng thể, đề xuất thêm chương trình khuyến mãi, hoặc điều chỉnh cơ cấu giá để tăng sức cạnh tranh | *"Anh ơi kiến nghị hãng xem xét tăng chiết khấu thêm 2-3% cho đại lý cấp 2, hiện tại biên lợi nhuận quá mỏng"* |

---

## 4. 🔧 Nhóm Dịch vụ

Nhóm này bao gồm phản hồi liên quan đến **chất lượng dịch vụ hậu mãi, kênh phân phối và logistics**. Nhóm màu **cam nhạt** (`#F8CBAD`).

| Nhãn | Định nghĩa | Ví dụ feedback |
| :--- | :--- | :--- |
| **Bảo hành** | Phàn nàn về quy trình đổi/trả bảo hành chậm, thủ tục phức tạp, thái độ nhân viên trung tâm bảo hành, hoặc hỏi về chính sách bảo hành sản phẩm. Tập trung vào **chất lượng dịch vụ bảo hành**, không phải lỗi sản phẩm | *"Em gửi bóng bị hỏng từ tuần trước, nhân viên bảo hành bảo 7 ngày nhưng giờ vẫn chưa thấy trả, khách hàng phàn nàn liên tục"* |
| **HTPP** | Tranh chấp kênh phân phối: đại lý C1 hoặc C2 bán lấn sang vùng khác, phá giá niêm yết, gây rối thị trường | *"Đại lý C1 ở Hà Nam đang bán hàng xuống tận Ninh Bình của em với giá thấp hơn giá nhập của em, đề nghị hãng can thiệp ngay"* |
| **Hàng hoá** | Vấn đề kho vận và giao nhận: giao thiếu hàng so với đơn đặt, giao chậm so với cam kết, đóng gói bị vỡ hỏng trong quá trình vận chuyển, giao nhầm mã sản phẩm | *"Đơn hàng ngày 28 đặt 200 bộ đèn, hôm nay nhận hàng chỉ có 180 bộ, thiếu 20 bộ, anh kiểm tra lại kho giúp em"* |

---

## 5. ⚠️ Nhóm Hàng giả

Nhóm này chỉ có **1 nhãn duy nhất** cho tất cả các phản hồi liên quan đến hàng nhái, hàng giả mạo thương hiệu Rạng Đông. Nhóm màu **cam đậm** (`#F4B183`).

| Nhãn | Định nghĩa | Ví dụ feedback |
| :--- | :--- | :--- |
| **Hàng giả** | Nghi ngờ hoặc phát hiện sản phẩm nhái, giả mạo thương hiệu Rạng Đông lưu thông trên thị trường. Bao gồm: bao bì giống Rạng Đông nhưng chất lượng kém, sản phẩm đóng giả, hoặc nhìn giống nhái thương hiệu | *"Khách mang vào 1 cái bóng bảo là Rạng Đông nhưng logo hơi khác, chữ không sắc nét, bên trong linh kiện cũng khác — nghi là hàng giả, anh ơi báo bộ phận pháp lý giúp em"* |

---

## 6. 🌐 Nhóm Website

Nhóm này dành riêng cho phản hồi về **phần mềm và hệ thống kỹ thuật số** của Rạng Đông, bao gồm app DMS, cổng đại lý (portal), và hệ thống quản lý đơn hàng. Nhóm màu **xanh tím nhạt** (`#D9E1F2`).

| Nhãn | Định nghĩa | Ví dụ feedback |
| :--- | :--- | :--- |
| **Website** | Lỗi phần mềm: app DMS bị lỗi, cổng đại lý không đăng nhập được, không đặt được đơn hàng online, tính năng bị treo hoặc hiển thị sai dữ liệu | *"App DMS sáng nay vào không được, cứ báo lỗi kết nối, em cần đặt đơn gấp mà không đặt được, nhờ IT kiểm tra giúp"* |

---

## 7. 🏢 Nhóm Đối thủ cạnh tranh

Nhóm này kích hoạt khi phản hồi **đề cập đến tên thương hiệu đối thủ cạnh tranh** của Rạng Đông. Hệ thống tự động phát hiện tên brand từ danh sách đối thủ đã cấu hình. Nhóm màu **xám** (`#C9C9C9`).

> **Cột Hãng trong Output:** Khi phát hiện brand đối thủ, tên thương hiệu cụ thể (ví dụ: `Philips`, `Điện Quang`) được LLM ghi nhận vào trường nội bộ và hiển thị qua API — xem tài liệu API để biết thêm. Trong Excel output, chỉ cột nhãn `Hãng` được đánh dấu `x`.

Các brand đối thủ được nhận diện phổ biến: **Philips, Sopoka, Asia, Paragon, Duhal, Điện Quang, Nanoco, Camel, Rạng Đông (giả)**, v.v. (danh sách đầy đủ trong `kw_map.json`).

| Nhãn | Định nghĩa | Ví dụ feedback |
| :--- | :--- | :--- |
| **Hãng** | Chỉ đề cập tên thương hiệu đối thủ, không nói rõ hoạt động hay giá. Đây là nhãn nền — thường đi kèm với 1 trong 3 nhãn còn lại của nhóm | *"Anh ơi, Philips vừa cho nhân viên xuống thăm đại lý em, em thấy cần báo anh biết"* |
| **Hoạt động** | Mô tả hoạt động marketing, sự kiện, chương trình thăm viếng, tặng quà, roadshow, hội nghị đại lý của đối thủ | *"Điện Quang vừa tổ chức hội nghị đại lý tặng tủ lạnh cho ai đặt hàng nhiều, nhiều đại lý của em đang bị lôi kéo"* |
| **CTKM, giá, cơ chế** | Thông tin về giá bán, chiết khấu, khuyến mại đặc biệt hoặc chính sách thưởng của đối thủ | *"Duhal đang bán bóng T8 cùng watt với giá thấp hơn Rạng Đông 20%, chiết khấu đại lý cũng cao hơn, khó cạnh tranh quá"* |
| **TT SP** | Thông tin về thông số kỹ thuật, mẫu mã, thiết kế, tính năng mới của sản phẩm đối thủ | *"Bóng Paragon ra mẫu mới vỏ nhôm tản nhiệt trông sang hơn, khách cứ hỏi Rạng Đông có loại tương tự không"* |

---

## 8. 📋 Nhóm Tin trung lập

Nhóm này là **nhãn dự phòng** — chỉ kích hoạt khi câu phản hồi không chứa thông tin nghiệp vụ nào có thể phân loại vào 7 nhóm trên. Nhóm màu **vàng** (`#FFD966`).

> **Quy tắc Guardrail:** Nếu một câu đã được gán bất kỳ nhãn nghiệp vụ nào (từ nhóm 1–7), nhãn `Tin trung lập` **sẽ tự động bị xóa bỏ** dù LLM có đề xuất. Đây là quy tắc hậu xử lý bằng code để đảm bảo tính nhất quán.

| Nhãn | Định nghĩa | Ví dụ feedback |
| :--- | :--- | :--- |
| **Tin trung lập** | Câu chào hỏi thông thường, câu phản hồi không chứa nội dung nghiệp vụ, câu vô nghĩa, hoặc văn bản quá ngắn không đủ thông tin để phân loại | *"Chào anh, anh có khỏe không?"* / *"Cảm ơn anh"* / *"Ok ạ"* / *"Không có ý kiến gì"* |

---

## 📌 Quy tắc đa nhãn

Một câu phản hồi **có thể được gán nhiều nhãn đồng thời** nếu nội dung đề cập nhiều vấn đề. Đây là thiết kế có chủ đích — phản ánh thực tế rằng đại lý thường gộp nhiều phàn nàn trong cùng một tin nhắn.

**Ví dụ feedback đa nhãn:**
> *"Bóng LED 9W bị đứt rất nhanh, mới dùng 1 tháng thôi. Mà bảo hành đổi trả mãi chưa xong. Philips bên cạnh bán loại tương tự rẻ hơn mà bền hơn."*

Câu này sẽ được gán **3 nhãn**:
- ✅ `Báo lỗi` (bóng bị đứt)
- ✅ `Bảo hành` (đổi trả chậm)
- ✅ `Hãng` + `TT SP` (nhắc đến Philips và so sánh sản phẩm)

**Quy tắc ưu tiên:**
1. Ưu tiên nhãn **cụ thể hơn** khi hai nhãn có ranh giới chồng lấp — ví dụ: `Bảo hành` thay vì `Dịch vụ` chung chung
2. Nếu không chắc chắn giữa 2 nhãn tương đương, chọn nhãn **đề cập nổi bật nhất** trong câu
3. **Không giới hạn số nhãn** — một câu có thể có 1 đến nhiều nhãn tùy nội dung

---

## 🔍 Nhãn đối thủ — Quy tắc kích hoạt đặc biệt

Các nhãn thuộc nhóm **Đối thủ cạnh tranh** chỉ được phép kích hoạt khi hệ thống (LLM hoặc RAG) phát hiện được ít nhất một tên brand đối thủ rõ ràng trong nội dung phản hồi. Đây là quy tắc Guardrail bắt buộc:

```
NẾU không phát hiện brand đối thủ:
    → Tất cả 4 nhãn nhóm Đối thủ = trống (không kích hoạt)

NẾU phát hiện brand đối thủ (ví dụ: "Philips"):
    → Nhãn "Hãng" = x (luôn bật)
    → Nhãn "Hoạt động" / "CTKM, giá, cơ chế" / "TT SP" = x (nếu nội dung đề cập)
```

Danh sách brand đối thủ được quản lý trong file `kw_map.json` trên SharePoint. Admin có thể cập nhật danh sách này qua Web Dashboard hoặc tải trực tiếp lên SharePoint — hệ thống sẽ hot-reload trong vòng 1 chu kỳ poll (mặc định 5 phút).

---

*← [Quay lại: Định dạng file Excel](excel-format.md) | [Quay lại trang chủ tài liệu](../README.md)*
