import os
import random
import pandas as pd
from pathlib import Path

FEEDBACK_POOL = [
    "Đèn led búp Rạng Đông 9W bị hỏng đui, nhờ bảo hành.",
    "Chất lượng đèn bàn học RD chống cận thị cực kỳ tốt, ánh sáng rất dịu.",
    "Đại lý đề xuất Rạng Đông hỗ trợ kệ trưng bày bóng đèn trang trí.",
    "Gửi catalogue bóng đèn thông minh mới nhất cho cửa hàng qua email nhé.",
    "Đơn hàng đèn âm trần D AT04 giao bị thiếu mất 5 hộp, vui lòng kiểm tra.",
    "Khi nào chương trình khuyến mãi quay số may mắn Tết 2026 trả thưởng?",
    "Đại lý muốn làm biển quảng cáo alu ngoài trời thương hiệu Rạng Đông.",
    "Giá đèn đường LED của Rạng Đông hơi cao khó chào thầu công trình.",
    "Đợt hàng này bị móp méo hộp rất nhiều do bên vận chuyển xếp chồng quá nặng.",
    "Khách báo phát hiện cửa hàng bên cạnh bán đèn nhái Rạng Đông, đề nghị hãng kiểm tra.",
    "Đèn tuýp LED bán nguyệt Rạng Đông 36W độ sáng rất đạt, đại lý bán chạy.",
    "Đăng nhập trang portal đại lý DMS hay bị lỗi timeout không tải được trang.",
    "Rạng Đông nên cải tiến thiết kế tai treo của đèn âm trần cho chắc chắn hơn.",
    "Đèn pha LED 50W của hãng đối thủ MPE đang có chương trình chiết khấu thêm 5%.",
    "Giao hàng trễ hẹn 2 ngày làm lỡ tiến độ thi công của nhà thầu.",
    "Cần bảng giá mới nhất áp dụng từ tháng này cho đại lý cấp 2.",
    "Phản hồi trung lập của khách hàng về chất lượng bóng đèn led trụ.",
    "Rạng Đông nên nghiên cứu làm thêm dòng đèn thông minh điều khiển bằng ứng dụng điện thoại.",
    "Chính sách bảo hành đổi trả nhanh chóng, đại lý rất hài lòng.",
    "Kênh phân phối đang bị chồng chéo vùng miền, đại lý khác bán lấn sân.",
    "Đèn led âm trần 7w Rạng Đông bị chết nguồn sau 1 tháng sử dụng.",
    "Thiết kế của đèn trang trí thả trần Rạng Đông rất hiện đại và sang trọng.",
    "Gửi cho mình tờ rơi quảng cáo sản phẩm đèn năng lượng mặt trời.",
    "Cửa hàng cần hỗ trợ 1 kệ bóng thử đèn để kích thích khách mua hàng.",
    "Mức chiết khấu cho dòng sản phẩm LED gia dụng hiện tại là bao nhiêu?",
    "Thủ tục đăng ký tham gia app DMS Rạng Đông cho cửa hàng mới như thế nào?",
    "Đề xuất làm bảng hiệu bạt hiflex miễn phí cho các đại lý nhỏ ở quê.",
    "Logistics đợt này làm việc rất tốt, hàng đóng gói cẩn thận không bị móp méo.",
    "Nghi ngờ có hàng giả đèn LED bulb công suất lớn trôi nổi trên thị trường miền Tây.",
    "Không có phản hồi gì thêm, chất lượng sản phẩm ổn định.",
    "Đèn LED bulb 12w Rạng Đông bị cháy nguồn sau 2 ngày lắp đặt.",
    "Đèn pha chống nước Rạng Đông lắp ngoài trời chịu mưa nắng cực bền bỉ.",
    "Nhân viên thị trường Rạng Đông hỗ trợ đại lý rất nhiệt tình, chu đáo.",
    "Hãng Rạng Đông giao nhầm mã đèn âm trần viền vàng thành viền bạc, cần đổi gấp.",
    "Đại lý Điện Quang đang chạy chương trình khuyến mãi mua 10 tặng 1 bóng tuýp LED."
]

def generate_files():
    downloads_dir = Path("C:/Users/RD03590/Downloads")
    downloads_dir.mkdir(parents=True, exist_ok=True)
    
    # Deterministic generation but varied feedback for each of the 10 files
    # Set seed so they look consistent if regenerated, but have different contents
    random.seed(42)
    
    for i in range(1, 11):
        filename = f"test_feedback_{i}.xlsx"
        filepath = downloads_dir / filename
        
        # Sample 10 feedbacks from the pool
        feedbacks = random.sample(FEEDBACK_POOL, 10)
        
        data = {
            "Nội dung phản hồi": feedbacks
        }
        
        df = pd.DataFrame(data)
        df.to_excel(filepath, index=False)
        print(f"Generated test file {i}/10: {filepath}")

if __name__ == "__main__":
    generate_files()
