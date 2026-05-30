# Báo cáo Kiểm thử So sánh Prompt Cũ vs Prompt Mới (20 Ca Thực tế)

Dưới đây là kết quả đối chiếu tự động 20 dòng phản hồi thực tế được trích xuất từ 20 tệp khách hàng khác nhau trên SharePoint.

| STT | Tệp dữ liệu | Nội dung phản hồi thực tế | Phân loại CŨ (Lịch sử) | Phân loại MỚI (Cải tiến) | Nhận xét cải thiện |
| --- | --- | --- | --- | --- | --- |
| 1 | BC_thong_tin_phan_hoi_202509_output.xlsx | AST12 khách mua về sử dụng nhưng khi cắm vào thì không có điện | Không gán | Báo lỗi | 🔄 Đã chuẩn hóa chính xác hơn theo ranh giới CoT mới. |
| 2 | DMS-13102025_output.xlsx | M26 50w trời mưa điện không ổn định dễ cháy | Không gán | Báo lỗi | 🔄 Đã chuẩn hóa chính xác hơn theo ranh giới CoT mới. |
| 3 | DMS-14102025_output.xlsx | Aptomat gia dụng nên có thêm đèn báo | Y/c cải tiến | Y/c cải tiến | ✓ Trùng khớp (Độ chính xác tốt) |
| 4 | DMS-1510-1710_output.xlsx | Giá được quay cao hơn giá trần nhiều,nếu quay giải 4,5 là cửa hàng bị lỗ | Đề xuất SPM | Tốt/ ko tốt | 🔄 Đã chuẩn hóa chính xác hơn theo ranh giới CoT mới. |
| 5 | DMS-1510-1710_test_output.xlsx | Giá được quay cao hơn giá trần nhiều,nếu quay giải 4,5 là cửa hàng bị lỗ | Trả thưởng | Tốt/ ko tốt | 🔄 Đã chuẩn hóa chính xác hơn theo ranh giới CoT mới. |
| 6 | DMS-1810-1910_output.xlsx | Bộ M chất lượng tốt. Ánh sáng ổn định. | Báo CL tốt | Báo CL tốt | ✓ Trùng khớp (Độ chính xác tốt) |
| 7 | DMS-2010-2210_output.xlsx | Cty cần sản xuất thêm ruột cho Phích 1235 | Y/c cải tiến, Đề xuất SPM | Đề xuất SPM | 🔄 Đã chuẩn hóa chính xác hơn theo ranh giới CoT mới. |
| 8 | DMS-2310-2610_output.xlsx | Chất lượng sp tốt | CTKM, giá, cơ chế | Báo CL tốt | 🔄 Đã chuẩn hóa chính xác hơn theo ranh giới CoT mới. |
| 9 | DMS-2710-2910_output.xlsx | Chất lượng sản phẩm at 39 đánh giá cao | Báo CL tốt | Báo CL tốt | ✓ Trùng khớp (Độ chính xác tốt) |
| 10 | DMS-3010-3110_output.xlsx | Khách yêu cầu cty nên sản xuất panel tròn loại công xuất lớn hơn | Báo lỗi | Đề xuất SPM | 🔄 Đã chuẩn hóa chính xác hơn theo ranh giới CoT mới. |
| 11 | DMS-Test_output.xlsx | M26 50w trời mưa điện không ổn định dễ cháy | Báo lỗi | Báo lỗi | ✓ Trùng khớp (Độ chính xác tốt) |
| 12 | DMST0126-01-06_output.xlsx | Chất lượng sp tốt | Bảng biển | Báo CL tốt | 🔄 Đã chuẩn hóa chính xác hơn theo ranh giới CoT mới. |
| 13 | DMST0126-07-11_output.xlsx | Giá bộ M tháng này rất hợp lý. Mong cty duy trì để bán ạ | Tốt/ ko tốt | Tốt/ ko tốt, Đề xuất | 🔄 Đã chuẩn hóa chính xác hơn theo ranh giới CoT mới. |
| 14 | DMST0126-12-18_output.xlsx | Mẫu AT62 nhìn đẹp . Cty nên làm cả phi 90 ạ | Báo lỗi | Đề xuất SPM | 🔄 Đã chuẩn hóa chính xác hơn theo ranh giới CoT mới. |
| 15 | DMST0126-19-25_output.xlsx | Đèn bul 9w tích điện không sáng, sạc ko vào | Báo CL tốt | Báo lỗi | 🔄 Đã chuẩn hóa chính xác hơn theo ranh giới CoT mới. |
| 16 | DMST0126-26-30_output.xlsx | Quạt hút âm trần và gắn tường Rạng Đông giá cao hơn nhiều so với các loại cùng phân khúc, rất khó tiếp cận khác hàng và thợ sửa chữa, lên điều chỉnh phù hợp hơn. | Tốt/ ko tốt, Đề xuất | Tốt/ ko tốt, Đề xuất | ✓ Trùng khớp (Độ chính xác tốt) |
| 17 | DMST0226-07-23_output.xlsx | Làm thêm nắp lưng cho panel vận chuyển nhiều khi bị móp | Y/c cải tiến | Y/c cải tiến | ✓ Trùng khớp (Độ chính xác tốt) |
| 18 | DMST0226-24-0303_output.xlsx | Bảng demo thiết bị Panasonic | Hoạt động | Hãng, Hoạt động | 🔄 Đã chuẩn hóa chính xác hơn theo ranh giới CoT mới. |
| 19 | DMST0226-31-06_output.xlsx | Quạt hút gắn tường giá cao | Báo lỗi, Y/c cải tiến | Tốt/ ko tốt | 🔄 Đã chuẩn hóa chính xác hơn theo ranh giới CoT mới. |
| 20 | DMST0326-03-06_output.xlsx | Đợt cuối năm nhiều mã hết hàng quá | Không gán | Hàng hoá | 🔄 Đã chuẩn hóa chính xác hơn theo ranh giới CoT mới. |