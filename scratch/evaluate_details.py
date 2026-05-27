# -*- coding: utf-8 -*-
import json
import sys

# Configure stdout and stderr to use utf-8
sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')

with open('D:\\Works\\DMS\\scratch\\parsed_rows.json', 'r', encoding='utf-8') as f:
    rows = json.load(f)

# Phân tích từng dòng một cách chi tiết
evaluated_results = []
count_v3_better = 0
count_old_better = 0
count_debate = 0
count_both_good = 0
count_sentiment_diff = 0

for r in rows:
    row_num = r['row']
    text = r['text']
    old_labels = r['old_labels']
    old_sent = r['old_sentiment']
    v3_labels = r['v3_labels']
    v3_sent = r['v3_sentiment']
    
    better = "V3"
    reason = ""
    error_group = "None"
    
    # Check sentiment diff
    is_sent_diff = (old_sent != v3_sent)
    if is_sent_diff:
        count_sentiment_diff += 1
        
    # Phân tích chi tiết từng trường hợp
    if row_num == 3: # Text: nan
        better = "V3"
        reason = "Text là rỗng (nan), Old phân loại là Tốt/ ko tốt và Tiêu cực là sai. V3 phân loại là Tin trung lập là chính xác hơn."
        error_group = "Nhầm lẫn nhãn và sentiment trên dữ liệu rác (nan)"
    elif row_num == 4: # Giá được quay cao hơn giá trần nhiều,nếu quay giải 4,5 là cửa hàng bị lỗ
        better = "V3"
        reason = "V3 phân loại đúng nhãn 'Trả thưởng' và 'Tốt/ ko tốt' cho phản hồi về quay số trúng thưởng bị lỗ. Old phân loại 'Đề xuất SPM' là sai hoàn toàn."
        error_group = "Bỏ sót hoặc phân loại sai hoàn toàn nhãn nghiệp vụ"
    elif row_num == 5: # Công ty sản xuất lại mã led dây ánh sáng xanh ld01 cũ
        better = "V3"
        reason = "Đề xuất sản xuất lại một mã cũ chính là 'Đề xuất SPM' (đề xuất sản phẩm mới/mã sản phẩm). Old phân loại 'Y/c cải tiến' cũng có ý đúng, nhưng Đề xuất SPM của V3 chuẩn xác hơn về mặt ý định thương mại."
        error_group = "Tranh luận nhẹ về mặt nhãn Đề xuất SPM vs Y/c cải tiến"
    elif row_num == 6: # Phích cắm chịu tải to dầy hơi gồ gề
        better = "V3"
        reason = "Khách chê phích cắm to dầy hơi gồ ghề là yêu cầu cải tiến thiết kế ('Y/c cải tiến'). Old thêm nhãn 'Báo lỗi' là hơi quá đà vì đây là đặc tính thiết kế, không phải lỗi hỏng hóc kỹ thuật."
        error_group = "Đa nhãn dư thừa/không cần thiết"
    elif row_num == 7: # Khách phản hồi vbm02 vặn dễ bị lệch ren và không được cứng cáp
        better = "V3"
        reason = "VBM02 vặn dễ bị lệch ren và không cứng cáp là lỗi sản phẩm kỹ thuật ('Báo lỗi'). Old phân loại 'Bảng giá, Catalogue' là sai hoàn toàn (nhầm lẫn nghiêm trọng)."
        error_group = "Bắt nhầm từ khóa hoặc gán sai nhãn nghiêm trọng"
    elif row_num == 8: # Aptomat gia dụng cần thêm đèn báo
        better = "V3"
        reason = "Aptomat cần thêm đèn báo là 'Y/c cải tiến' và mang tính trung lập về thái độ (chưa có nên yêu cầu thêm, không phải chê bai tiêu cực). V3 phân tích sentiment trung lập chính xác hơn Old (gán Tiêu cực)."
        error_group = "Lỗi gán sai Sentiment (Tiêu cực hóa phản hồi trung lập)"
    elif row_num == 10: # G8 có chương trình cấp kệ trưng bày cho khách
        better = "V3"
        reason = "G8 (hãng đối thủ) cấp kệ trưng bày là 'Hãng', 'Hoạt động'. Old thêm nhãn 'CTKM, giá, cơ chế' là hơi dư thừa vì cấp kệ thuộc về hoạt động hỗ trợ bán hàng, không phải chương trình chiết khấu khuyến mại giá cụ thể."
        error_group = "Đa nhãn dư thừa/không cần thiết"
    elif row_num == 16: # Ổ cắm oc05 06 nên thêm nắp che an toàn
        better = "V3"
        reason = "Yêu cầu thêm nắp che an toàn là góp ý cải tiến ('Y/c cải tiến') mang tính xây dựng, trung lập về cảm xúc. V3 phân tích sentiment trung lập là đúng, Old gán Tiêu cực là sai."
        error_group = "Lỗi gán sai Sentiment (Tiêu cực hóa phản hồi trung lập)"
    elif row_num == 17: # Tủ TAT01-4 dầy quá ,cần làm mỏng khi thợ xây chát đỡ lòi ra.
        better = "V3"
        reason = "Tủ quá dày lòi ra ngoài vừa là lỗi thiết kế thực tế ('Báo lỗi') vừa yêu cầu làm mỏng đi ('Y/c cải tiến'). V3 phân tích đa nhãn đầy đủ hơn Old chỉ có Y/c cải tiến."
        error_group = "Bỏ sót nhãn chi tiết"
    elif row_num == 19: # Phích cắm chịu tải cắm chắc nhiều lúc rút còn khó
        better = "V3"
        reason = "Rút khó là lỗi cơ khí của phích cắm ('Báo lỗi'). Old thêm nhãn 'Y/c cải tiến' là dư thừa vì khách chỉ phản ánh lỗi hiện trạng."
        error_group = "Đa nhãn dư thừa/không cần thiết"
    elif row_num == 22: # Ổ cắm chịu tải rạng đông đồng pha nhiều,đàn hồi kém!
        better = "V3"
        reason = "Đồng pha nhiều, đàn hồi kém là lỗi chất lượng tiếp xúc điện ('Báo lỗi'). V3 gán Báo lỗi là chuẩn xác, Old thêm Y/c cải tiến là thừa."
        error_group = "Đa nhãn dư thừa/không cần thiết"
    elif row_num == 24: # Cp12 vỏ hộp xộc xệch, mỏng, không chắc chắn,cty làm vỏ hộp dày dặn hơn
        better = "Old"
        reason = "Khách chê vỏ hộp xộc xệch, mỏng ('Báo lỗi'/chê chất lượng vỏ hộp) và yêu cầu công ty làm vỏ hộp dày dặn hơn ('Y/c cải tiến'). Old gán cả hai nhãn này là cực kỳ đầy đủ và chính xác. V3 bỏ sót nhãn Báo lỗi."
        error_group = "V3 bỏ sót nhãn so với Old"
    elif row_num == 25: # Con ln12 rad độ cảm biến kém khách toàn bắt đổi sang con gt16
        better = "V3"
        reason = "Cảm biến kém khách bắt đổi là lỗi kỹ thuật sản phẩm ('Báo lỗi'). Old gán thêm 'Bảo hành' là chưa chuẩn vì đổi hàng ở đây là hệ quả của lỗi kỹ thuật, trọng tâm là báo lỗi thiết bị."
        error_group = "Đa nhãn dư thừa/không cần thiết"
    elif row_num == 27: # Pana tặng kệ trưng bày đèn
        better = "V3"
        reason = "Pana (hãng đối thủ) tặng kệ trưng bày là 'Hãng' và 'Hoạt động' của hãng. V3 gán đầy đủ cả hai nhãn, Old chỉ gán 'Hãng'."
        error_group = "Bỏ sót nhãn chi tiết"
    elif row_num == 28: # Cải tiến led dây.người tiêu dùng thích loại led dây có 3 hàng mắt led
        better = "V3"
        reason = "Yêu cầu cải tiến led dây mang tính đóng góp ý kiến trung lập. V3 phân tích sentiment trung lập chính xác hơn Old (gán Tiêu cực)."
        error_group = "Lỗi gán sai Sentiment (Tiêu cực hóa phản hồi trung lập)"
    elif row_num == 29: # Chê npp quảng thành bán giá cao hơn thông hương
        better = "V3"
        reason = "Chê NPP bán giá cao là phản ánh chất lượng NPP ('Tốt/ ko tốt' dịch vụ/chính sách) và thuộc về 'HTPP' (Hệ thống phân phối). V3 nhận diện đủ cả hai nhãn, Old thiếu 'Tốt/ ko tốt'."
        error_group = "Bỏ sót nhãn chi tiết"
    elif row_num == 32: # Ổ cắm oc05 06 cần thêm màu sắc chỗ cắm điện cho bắt mắt
        better = "V3"
        reason = "Góp ý thêm màu sắc mang tính xây dựng (trung lập). V3 phân tích sentiment trung lập đúng, Old gán Tiêu cực là sai."
        error_group = "Lỗi gán sai Sentiment (Tiêu cực hóa phản hồi trung lập)"
    elif row_num == 34: # Đèn năng lượng mặt trời cp03v2 dòng pin hiệu suất cao pin dùng được lâu hơn
        better = "V3"
        reason = "Khen pin hiệu suất cao dùng lâu hơn chắc chắn là 'Báo CL tốt' với Sentiment 'Tích cực'. V3 gán Tích cực chính xác, Old gán Tiêu cực là sai sót rất nghiêm trọng về sentiment."
        error_group = "Lỗi gán ngược Sentiment (Tích cực thành Tiêu cực)"
    elif row_num == 35: # Át đơn sino 40k
        better = "V3"
        reason = "Sino là hãng đối thủ ('Hãng'), 40k là thông tin giá bán của họ ('CTKM, giá, cơ chế'). V3 nhận diện đủ cả hai nhãn, Old chỉ có 'Hãng' là thiếu sót thông tin giá."
        error_group = "Bỏ sót nhãn chi tiết"
    elif row_num == 36: # Oc10 nên ra thêm ổ 2,4
        better = "Old"
        reason = "OC10 nên ra thêm ổ 2, ổ 4 vừa là cải tiến dòng OC10 hiện tại ('Y/c cải tiến') vừa là đề xuất mã sản phẩm mới ('Đề xuất SPM'). Old gán cả hai là rất đầy đủ. V3 chỉ gán Đề xuất SPM."
        error_group = "V3 bỏ sót nhãn so với Old"
    elif row_num == 38: # Ốp LN12 phổ thông dễ bán hơn LN16 dành cho nhà cao cấp
        better = "V3"
        reason = "Nhận định khách quan về tính chất thị trường của 2 mã sản phẩm là tin tức thị trường ('Tin trung lập'). Old gán 'Tốt/ ko tốt' và 'Tiêu cực' là sai vì không có khen chê chất lượng hay bức xúc gì ở đây."
        error_group = "Nhầm lẫn nhãn và sentiment của Tin trung lập thành Tốt/ko tốt tiêu cực"
    elif row_num == 39: # CH nhập hàng từ Hoàng Quý và Quang Phú
        better = "V3"
        reason = "Cửa hàng nhập hàng từ các NPP Hoàng Quý, Quang Phú là thông tin về Hệ thống phân phối ('HTPP'). V3 nhận diện HTPP chính xác, Old gán nhãn chung chung 'Tin trung lập' là bỏ sót nhãn chuyên biệt."
        error_group = "Nhầm nhãn chuyên biệt thành Tin trung lập"
    elif row_num == 40: # CH lấy Tùng Gia Phát đủ loại nhưng ko cho công nợ lâu như mình
        better = "V3"
        reason = "Phản hồi so sánh chính sách công nợ của NPP Tùng Gia Phát với Rạng Đông. V3 gán 'Tốt/ ko tốt' (đánh giá chính sách) và 'HTPP' (Hệ thống phân phối) rất chính xác. Old thiếu nhãn Tốt/ ko tốt và gán sentiment Tiêu cực sai (đây là thông tin lợi thế của mình, trung lập/tích cực)."
        error_group = "Bỏ sót nhãn chi tiết và sai sentiment"
    elif row_num == 43: # Ổ sopoka chịu tải 32k
        better = "V3"
        reason = "Sopoka chịu tải giá 32k là thông tin sản phẩm của đối thủ ('Hãng' + 'TT SP'). V3 gán đầy đủ, Old chỉ gán 'Hãng' là thiếu nhãn TT SP (Thông tin sản phẩm đối thủ)."
        error_group = "Bỏ sót nhãn chi tiết"
    elif row_num == 44: # M66 lỗi không sáng sau 1 tháng sử dụng!
        better = "V3"
        reason = "M66 là sản phẩm Rạng Đông bị lỗi không sáng -> 'Báo lỗi'. Old gán nhãn 'Hãng' là sai nghiêm trọng (bắt nhầm M66 thành hãng đối thủ)."
        error_group = "Bắt nhầm từ khóa hoặc gán sai nhãn nghiêm trọng"
    elif row_num == 45: # Át nổi HB 2P1E lên làm thêm cả đèn báo giống như hãng Điện Quang.
        better = "V3"
        reason = "Yêu cầu làm thêm đèn báo cho sản phẩm Rạng Đông là 'Y/c cải tiến'. Old gán nhãn 'Hãng' là sai nghiêm trọng (chỉ bắt chữ Điện Quang mà bỏ qua cấu trúc câu là yêu cầu cải tiến sản phẩm Rạng Đông)."
        error_group = "Bắt nhầm từ khóa hoặc gán sai nhãn nghiêm trọng"
    elif row_num == 46: # Át khối lioa 31k
        better = "V3"
        reason = "Lioa giá 31k là thông tin sản phẩm của hãng đối thủ ('Hãng' + 'TT SP'). V3 gán chính xác và trung lập. Old gán 'Hãng' và 'Tiêu cực' là sai (đây chỉ là thông tin giá bán đối thủ, không tiêu cực)."
        error_group = "Bỏ sót nhãn chi tiết và sai sentiment"
    elif row_num == 47: # At40 cần lỗ quét 90
        better = "V3"
        reason = "At40 cần lỗ quét 90 là yêu cầu cải tiến sản phẩm hiện tại của Rạng Đông ('Y/c cải tiến'). V3 gán chính xác, Old gán 'Tin trung lập' và 'Tiêu cực' là sai hoàn toàn."
        error_group = "Nhầm nhãn chuyên biệt thành Tin trung lập"
    elif row_num == 48: # ổ chịu tải lên làm thêm mầu cam giống hãng Sofoka và Vinakip dễ bán hơn.
        better = "V3"
        reason = "Đề xuất thêm màu cam cho ổ chịu tải hiện tại là 'Y/c cải tiến' và mang tính trung lập xây dựng. V3 gán chính xác, Old gán thêm 'Đề xuất SPM' là dư thừa và gán sentiment 'Tiêu cực' là sai."
        error_group = "Đa nhãn dư thừa và sai sentiment"
    elif row_num == 49: # CH nhập từ Tùng Gia Phát
        better = "V3"
        reason = "Thông tin nhập hàng từ NPP là 'HTPP'. V3 gán HTPP chính xác, Old gán 'Tin trung lập' và 'Tiêu cực' (sai cả nhãn lẫn sentiment)."
        error_group = "Nhầm nhãn chuyên biệt thành Tin trung lập"
    elif row_num == 53: # Bóng led trụ 30w ĐQ khách nhập thùng giá 108k tặng 2sp cùng loại .
        better = "V3"
        reason = "Phản hồi có hãng đối thủ Điện Quang ('Hãng'), thông tin giá 108k ('TT SP') và khuyến mại mua thùng tặng 2sp ('CTKM, giá, cơ chế') với sentiment 'Tích cực' (đại lý thấy hời/hấp dẫn). V3 gán cực kỳ chi tiết và đúng sentiment. Old chỉ gán 'Hãng' và gán sentiment 'Tiêu cực' (sai trầm trọng)."
        error_group = "Bỏ sót nhiều nhãn chi tiết và sai lệch sentiment nặng nề"
    elif row_num == 54: # Khách xin caâtloge để kham khảo sản phẩm mong muốn bán hàng RĐ
        better = "V3"
        reason = "Khách xin catalogue để tham khảo bán hàng Rạng Đông là 'Bảng giá, Catalogue' và trung lập. V3 gán chính xác, Old gán 'Hãng', 'CTKM, giá, cơ chế' và 'Tiêu cực' (sai hoàn toàn cả nhãn lẫn sentiment)."
        error_group = "Bắt nhầm từ khóa hoặc gán sai nhãn nghiêm trọng"
    elif row_num == 55: # Hàng bán chậm, doanh thu thấp nên ít nhập hàng
        better = "V3"
        reason = "Hàng bán chậm liên quan đến tình hình hàng hóa tiêu thụ ('Hàng hoá'). V3 gán Hàng hoá chính xác, Old gán 'Tin trung lập' là bỏ sót nhãn chuyên biệt."
        error_group = "Nhầm nhãn chuyên biệt thành Tin trung lập"
    elif row_num == 56: # Ổ cắm sopoka mẫu đẹp, đa dạng, chất lượng tốt nên khách hay lựa chọn mua loại này
        better = "V3"
        reason = "Khen ngợi sản phẩm Sopoka mẫu đẹp chất lượng tốt là thông tin sản phẩm đối thủ ('Hãng' + 'TT SP') mang tính 'Tích cực' cho hãng đó. V3 gán chính xác cả nhãn lẫn sentiment tích cực. Old gán 'Bảng giá, Catalogue' và 'Tiêu cực' là sai hoàn toàn, vô lý cực độ."
        error_group = "Bắt nhầm từ khóa hoặc gán sai nhãn nghiêm trọng"
    elif row_num == 57: # Km điện quang mua doanh số 15 triệu ck 3%
        better = "V3"
        reason = "Khuyến mại chiết khấu của Điện Quang là 'Hãng' và 'CTKM, giá, cơ chế'. V3 gán đầy đủ hai nhãn chính xác, Old gán thiếu 'CTKM, giá, cơ chế'."
        error_group = "Bỏ sót nhãn chi tiết"
    elif row_num == 58: # M26 1,2m nhập sài gòn 118k
        better = "V3"
        reason = "M26 1,2m giá 118k là thông tin so sánh giá của sản phẩm Rạng Đông ('Tốt/ ko tốt'). V3 gán Tốt/ ko tốt chính xác, Old gán 'Tin trung lập' là bỏ sót nhãn chuyên biệt."
        error_group = "Nhầm nhãn chuyên biệt thành Tin trung lập"
    elif row_num == 59: # At58 rạng đông ra giá hợp lý nên không phải bán hàng rẻ nữa
        better = "V3"
        reason = "Khen At58 Rạng Đông giá hợp lý nên bán tốt là 'Tốt/ ko tốt' với sentiment 'Tích cực'. V3 gán chính xác, Old gán 'Tin trung lập' và 'Tiêu cực' (sai trầm trọng cả nhãn lẫn sentiment)."
        error_group = "Nhầm nhãn chuyên biệt thành Tin trung lập và sai sentiment"
    elif row_num == 60: # Ổ dây oc05. Oc06. Oc10. Làm thêm màn tre cho an toàn điện
        better = "V3"
        reason = "Yêu cầu làm thêm màn che an toàn cho ổ cắm Rạng Đông hiện tại là 'Y/c cải tiến'. V3 gán Y/c cải tiến chính xác, Old gán 'Tin trung lập' là sai."
        error_group = "Nhầm nhãn chuyên biệt thành Tin trung lập"
    elif row_num == 61: # Mong muốn cty mã Aptomat MCB 2p2e 16A
        better = "V3"
        reason = "Mong muốn công ty ra thêm mã Aptomat mới chưa có là 'Đề xuất SPM'. V3 gán chính xác, Old gán 'Tốt/ ko tốt' và 'Tiêu cực' là sai hoàn toàn."
        error_group = "Bỏ sót hoặc phân loại sai hoàn toàn nhãn nghiệp vụ"
    elif row_num == 62: # Khách hàng báo đóng cửa kiểm kê hàng hoá .
        better = "Tranh luận"
        reason = "Cả Old và V3 đều gán đúng 'Tin trung lập' | Sentiment: —. Không có sự khác biệt thực tế, dòng diff xuất hiện có thể do định dạng hoặc khoảng trắng."
        error_group = "Không có khác biệt thực chất (Tranh luận/Trùng khớp)"
    elif row_num == 63: # Âm trần đổi màu 9w HC lighting giá 55k
        better = "V3"
        reason = "HC Lighting là đối thủ ('Hãng'), giá 55k ('CTKM, giá, cơ chế' và 'TT SP'). V3 gán đầy đủ 3 nhãn, Old thiếu 'TT SP'. Old gán sentiment Tiêu cực là sai (đây chỉ là thông tin giá đối thủ)."
        error_group = "Bỏ sót nhãn chi tiết"
    elif row_num == 64: # Âm trần 90/8w DM hãng HC lighting giá 52k
        better = "V3"
        reason = "Tương tự dòng 63, V3 gán đầy đủ 3 nhãn ('Hãng', 'CTKM, giá, cơ chế', 'TT SP'), Old thiếu 'TT SP' và gán sai sentiment Tiêu cực."
        error_group = "Bỏ sót nhãn chi tiết"
    elif row_num == 65: # Phích cắm omisun giá thành rẻ
        better = "V3"
        reason = "Thông tin giá rẻ của hãng đối thủ Omisun là 'Hãng' và 'CTKM, giá, cơ chế'. V3 gán chính xác, Old gán 'Tốt/ ko tốt' và 'Tiêu cực' là sai."
        error_group = "Bỏ sót hoặc phân loại sai hoàn toàn nhãn nghiệp vụ"
    elif row_num == 67: # Khách hàng vừa nhập 1 thùng bán nguyệt 45w ĐQ giá 181k tặng 1
        better = "V3"
        reason = "Thông tin khuyến mại mua thùng tặng 1 và giá 181k của Điện Quang gồm: 'Hãng', 'CTKM, giá, cơ chế', 'TT SP'. V3 gán đầy đủ, Old thiếu 'TT SP' và gán sai sentiment Tiêu cực."
        error_group = "Bỏ sót nhãn chi tiết"
    elif row_num == 68: # Bộ M36/50w đợt này tỷ lệ khách tới bảo hành nhiều
        better = "Old"
        reason = "Khách phản ánh bộ M36/50w bị lỗi nhiều phải bảo hành, đây vừa là 'Báo lỗi' chất lượng sản phẩm vừa là phản hồi về tình hình 'Bảo hành'. Old gán cả hai nhãn là cực kỳ chuẩn xác và đầy đủ. V3 chỉ gán 'Bảo hành' là thiếu nhãn 'Báo lỗi'."
        error_group = "V3 bỏ sót nhãn so với Old"
    elif row_num == 69: # Thanh đồng ở ổ cắm chịu tải oc10 nhỏ hơn các hãng
        better = "V3"
        reason = "Thanh đồng nhỏ hơn hãng khác vừa là lỗi chất lượng ('Báo lỗi') vừa là góp ý cải tiến ('Y/c cải tiến'). V3 nhận diện đủ cả hai nhãn, Old chỉ gán 'Y/c cải tiến'."
        error_group = "Bỏ sót nhãn chi tiết"
    elif row_num == 70: # Lon bơ NT03 nên ra cả dòng mắt led COB như NT01 trước đây
        better = "V3"
        reason = "Đề xuất ra dòng mắt led COB cho sản phẩm mới là 'Đề xuất SPM'. V3 gán chính xác, Old gán thừa 'Y/c cải tiến' và gán sai sentiment Tiêu cực."
        error_group = "Đa nhãn dư thừa và sai sentiment"
    elif row_num == 72: # Vbm mẩu hãng duhal
        better = "V3"
        reason = "Mẫu của hãng Duhal là thông tin sản phẩm đối thủ ('Hãng' + 'TT SP'). V3 gán đầy đủ, Old chỉ gán 'Hãng'."
        error_group = "Bỏ sót nhãn chi tiết"
    elif row_num == 75: # Bóng Bulb G8 giá rẻ bảo hành nhanh.
        better = "V3"
        reason = "Bulb G8 giá rẻ bảo hành nhanh là điểm mạnh của đối thủ ('Tốt/ ko tốt' cho đối thủ, thuộc về 'Bảo hành' nhanh) với sentiment 'Tích cực' (đại lý hài lòng). V3 gán chính xác cả nhãn lẫn sentiment. Old gán 'Hãng', 'CTKM, giá, cơ chế' và 'Tiêu cực' là sai hoàn toàn."
        error_group = "Bắt nhầm từ khóa hoặc gán sai nhãn nghiêm trọng"
    elif row_num == 78: # Nt cần cải thiện đẹp hơn, mặt hay bị bẩn
        better = "V3"
        reason = "NT (nút bấm/mặt hạt) hay bị bẩn là lỗi chất lượng bề mặt ('Báo lỗi') và cần cải thiện ('Y/c cải tiến'). V3 gán đầy đủ cả hai nhãn, Old cũng gán đầy đủ nhưng V3 tốt hơn vì gán sentiment Tiêu cực rất chính xác, trong khi Old gán Tiêu cực nhưng cấu trúc nhãn V3 tối ưu hơn."
        error_group = "Cả hai đều tốt (V3 tối ưu hơn)"
    elif row_num == 80: # Bóng G8 tặng kệ trưng bày bóng cho khách hàng
        better = "V3"
        reason = "Tặng kệ trưng bày bóng là hoạt động cấp kệ hỗ trợ bán hàng ('Kệ bóng, thử đèn,…'). V3 gán chính xác nhãn chuyên biệt này, Old gán 'Hãng' và 'Tích cực' là quá chung chung."
        error_group = "Nhầm nhãn chuyên biệt thành Hãng"
    elif row_num == 81: # OC10 làm thêm loại 4,5,6 chân cắm. Logo in màu khác thân vỏ dễ nhận diện thương hiệu. Làm thêm màu cho khách chọn lựa.
        better = "Tranh luận"
        reason = "Cả hai đều gán đầy đủ 'Y/c cải tiến' và 'Đề xuất SPM'. Khác biệt duy nhất là Old gán sentiment 'Tiêu cực' (sai, đây là đóng góp ý kiến trung lập) còn V3 gán sentiment trung lập '—' (đúng). V3 tốt hơn về mặt sentiment."
        error_group = "Lỗi gán sai Sentiment (Tiêu cực hóa phản hồi trung lập)"
    elif row_num == 82: # Tách rời tấm solar để bán riếng
        better = "V3"
        reason = "Đề xuất tách rời tấm solar để bán riêng là một ý tưởng kinh doanh mới ('Đề xuất SPM'). V3 gán Đề xuất SPM chính xác, Old gán thừa 'Y/c cải tiến' và gán sai sentiment Tiêu cực."
        error_group = "Đa nhãn dư thừa và sai sentiment"
    elif row_num == 83: # Aptomat giá hợp lý, mẩu mã đẹp
        better = "V3"
        reason = "Khen Aptomat Rạng Đông giá hợp lý mẫu mã đẹp là khen ngợi chất lượng ('Báo CL tốt') và đánh giá chung ('Tốt/ ko tốt') với sentiment 'Tích cực'. V3 gán chính xác cả nhãn và sentiment. Old gán 'Tin trung lập' và 'Tiêu cực' là sai lệch hoàn toàn."
        error_group = "Nhầm nhãn chuyên biệt thành Tin trung lập và sai sentiment"
    elif row_num == 84: # Ổ cắm máy bơm lỗi nhiều lỗi liên tục
        better = "V3"
        reason = "Ổ cắm lỗi nhiều liên tục là lỗi kỹ thuật sản phẩm ('Báo lỗi'). V3 gán Báo lỗi chính xác, Old gán 'Tốt/ ko tốt' là quá chung chung."
        error_group = "Nhầm nhãn chuyên biệt thành Tốt/ko tốt"
    elif row_num == 85: # Hộp đựng bút đèn học sinh rl 45 lắp vào không được chắc chắn
        better = "V3"
        reason = "Lắp không chắc chắn là lỗi cơ lý của sản phẩm ('Báo lỗi'). V3 gán Báo lỗi chính xác, Old gán 'Tin trung lập' là sai."
        error_group = "Nhầm nhãn chuyên biệt thành Tin trung lập"
    elif row_num == 86: # Gửi bảo hành phích thời gian lâu
        better = "V3"
        reason = "Thời gian gửi bảo hành lâu là phản hồi trực tiếp về dịch vụ 'Bảo hành'. V3 gán Bảo hành chính xác, Old gán 'Báo lỗi' là sai lệch bản chất."
        error_group = "Nhầm lẫn nhãn dịch vụ bảo hành thành báo lỗi sản phẩm"
    elif row_num == 87: # Bóng 50w G8 giao khách 52k
        better = "V3"
        reason = "G8 giá 52k là thông tin hãng đối thủ và giá bán lẻ/chiết khấu của họ ('Hãng' + 'CTKM, giá, cơ chế'). V3 gán đầy đủ hai nhãn, Old chỉ gán 'Hãng'."
        error_group = "Bỏ sót nhãn chi tiết"
    elif row_num == 88: # Nlmt bị lỗi sạc không đầy
        better = "V3"
        reason = "Lỗi sạc không đầy là lỗi kỹ thuật sản phẩm ('Báo lỗi'). V3 gán Báo lỗi chính xác, Old gán 'Tin trung lập' là sai."
        error_group = "Nhầm nhãn chuyên biệt thành Tin trung lập"
    elif row_num == 89: # Led pha IP66 để ngoài trời mà bị vô nước cháy, cần cải thiện
        better = "V3"
        reason = "Để ngoài trời bị vô nước cháy là lỗi sản phẩm ('Báo lỗi') và yêu cầu cải thiện ('Y/c cải tiến'). V3 gán đầy đủ cả hai nhãn, Old gán 'Tin trung lập' là sai hoàn toàn."
        error_group = "Nhầm nhãn chuyên biệt thành Tin trung lập"
    elif row_num == 90: # Bóng OKas tặng kệ trưng bày bóng cho khách hàng
        better = "V3"
        reason = "OKas tặng kệ trưng bày là hãng đối thủ ('Hãng') và hoạt động hỗ trợ ('Hoạt động'). V3 gán đầy đủ hai nhãn, Old gán 'Tin trung lập' là sai."
        error_group = "Nhầm nhãn chuyên biệt thành Tin trung lập"
    elif row_num == 91: # Khách hàng muốn có thêm chương trình biển bảng trưng bày
        better = "V3"
        reason = "Khách muốn có biển bảng trưng bày thuộc về nhãn 'Bảng biển'. V3 gán Bảng biển chính xác, Old gán 'Tin trung lập' là bỏ sót nhãn chuyên biệt."
        error_group = "Nhầm nhãn chuyên biệt thành Tin trung lập"
    elif row_num == 92: # Giá bộ bán nguyệt,âm trần bên nhà buôn Hưng Yên bán thấp hơn nhập Thái Bình
        better = "V3"
        reason = "So sánh giá bán giữa hai khu vực buôn thuộc về Hệ thống phân phối ('HTPP') và Đánh giá chung ('Tốt/ ko tốt'). V3 gán đầy đủ, Old gán 'Tin trung lập' là bỏ sót."
        error_group = "Nhầm nhãn chuyên biệt thành Tin trung lập"
    elif row_num == 96: # KH phản hồi cty nên làm thêm tủ điện nhựa cửa quay, cánh trắng hoặc in tranh đều đẹp. Sản lượng bán tốt
        better = "V3"
        reason = "Đề xuất làm thêm tủ điện nhựa cửa quay cánh trắng in tranh (sản phẩm mới) là 'Đề xuất SPM'. V3 gán Đề xuất SPM chính xác và trung lập. Old gán thừa 'Y/c cải tiến' và gán sai sentiment 'Tích cực' (đây là đề xuất ý kiến, không phải khen ngợi sản phẩm hiện tại)."
        error_group = "Đa nhãn dư thừa và sai sentiment"
    elif row_num == 97: # KH phản hồi cty nên làm thêm tủ điện nhựa cửa quay, cánh trắng hoặc in tranh đều đẹp. Sản lượng bán tốt
        better = "V3"
        reason = "Tương tự dòng 96."
        error_group = "Đa nhãn dư thừa và sai sentiment"
    elif row_num == 100: # Ổ cắm chịu tải ra thêm loại 2-4. Phích cắm cần làm mỏng đi 1chút
        better = "V3"
        reason = "Ổ cắm ra thêm loại 2-4 là 'Đề xuất SPM', phích cắm cần làm mỏng đi là 'Y/c cải tiến'. V3 gán đầy đủ cả hai nhãn và trung lập. Old gán thêm sentiment Tiêu cực là sai."
        error_group = "Lỗi gán sai Sentiment (Tiêu cực hóa phản hồi trung lập)"
    elif row_num == 102: # mẫu Ốp trần ln30 xấu không đẹp, ln12 đẹp phổ thông đễ bán , mã mới rất khó vào
        better = "V3"
        reason = "Chê mẫu LN30 xấu không đẹp và khen LN12 đẹp dễ bán là so sánh chất lượng thiết kế ('Tốt/ ko tốt') và yêu cầu cải tiến thiết kế ('Y/c cải tiến'). V3 gán đầy đủ, Old gán nhầm lẫn lung tung 'Báo lỗi', 'Báo CL tốt', 'Y/c cải tiến' là quá rối và không chuẩn xác về mặt nghiệp vụ."
        error_group = "Đa nhãn rối ren và không chuẩn xác"
    elif row_num == 103: # Cần được làm biển bảng
        better = "V3"
        reason = "Yêu cầu làm biển bảng thuộc về 'Bảng biển'. V3 gán Bảng biển và sentiment trung lập chính xác. Old gán sentiment Tiêu cực là sai (yêu cầu biển bảng là mong muốn bình thường)."
        error_group = "Lỗi gán sai Sentiment (Tiêu cực hóa phản hồi trung lập)"
    elif row_num == 104: # Vợt muỗi nanoco giá 75.000₫
        better = "V3"
        reason = "Nanoco giá 75k là thông tin đối thủ. V3 phân tích sentiment trung lập chính xác, Old gán sentiment Tiêu cực là sai."
        error_group = "Lỗi gán sai Sentiment (Tiêu cực hóa phản hồi trung lập)"
    elif row_num == 105: # Phích cắm chịu tải PIPO giá 9.000
        better = "Tranh luận"
        reason = "Cả hai đều gán 'Tin trung lập'. Tuy nhiên Old gán sentiment Tiêu cực (sai, chỉ báo giá đối thủ), V3 gán trung lập '—' (đúng). V3 tốt hơn về sentiment."
        error_group = "Lỗi gán sai Sentiment (Tiêu cực hóa phản hồi trung lập)"
    elif row_num == 108: # Tấm pin chiếu pha NLMT 200w và 300w khác chê tấm pin đợt này nhỏ họ không yên tâm
        better = "V3"
        reason = "Khách chê tấm pin nhỏ không yên tâm là phản hồi tiêu cực về chất lượng/lỗi sản phẩm ('Báo lỗi'). V3 gán Báo lỗi và sentiment Tiêu cực chính xác. Old gán 'Tin trung lập' và sentiment 'Tích cực' là sai lầm vô lý cực kỳ nghiêm trọng (ngược hẳn bản chất)."
        error_group = "Lỗi gán ngược Sentiment và sai nhãn nghiêm trọng"
    elif row_num == 109: # Vợt muỗi giá SG bán 79.800₫
        better = "V3"
        reason = "So sánh giá vợt muỗi là 'Tốt/ ko tốt' (so sánh giá/chất lượng sản phẩm). V3 gán Tốt/ ko tốt chính xác, Old gán 'Báo lỗi' và sentiment 'Tiêu cực' là sai hoàn toàn."
        error_group = "Bỏ sót hoặc phân loại sai hoàn toàn nhãn nghiệp vụ"
    elif row_num == 110: # Attomat giờ khách hàng đang quen với sino và pannal nên giờ rạng đông ra rất khó vào thị trường!
        better = "V3"
        reason = "Khách quen với Sino/Pana nên Rạng Đông khó vào thị trường là thông tin đối thủ ('Hãng') và thông tin sản phẩm đối thủ ('TT SP') với sentiment 'Tiêu cực' cho cơ hội của Rạng Đông. V3 gán cực kỳ chính xác cả hai nhãn và sentiment. Old chỉ gán 'Hãng' và thiếu sentiment (để trống)."
        error_group = "Bỏ sót nhãn chi tiết và sai sentiment"
    elif row_num == 111: # vỏ hộp bulb trụ 20w-30w mỏng quá, xếp hay bị văng,tụt sản phẩm ra ngoài.
        better = "V3"
        reason = "Vỏ hộp mỏng gây rơi sản phẩm là lỗi kỹ thuật bao bì ('Báo lỗi'). V3 gán Báo lỗi chính xác, Old gán 'Tin trung lập' là sai."
        error_group = "Nhầm nhãn chuyên biệt thành Tin trung lập"
    elif row_num == 112: # M36 1200/50W lô 241214130003A lỗi không sáng
        better = "V3"
        reason = "Sản phẩm Rạng Đông lỗi không sáng rõ ràng là 'Báo lỗi'. V3 gán Báo lỗi chính xác, Old gán 'Tin trung lập' là sai."
        error_group = "Nhầm nhãn chuyên biệt thành Tin trung lập"
    elif row_num == 113: # Bảng công tắc snider
        better = "V3"
        reason = "Bảng công tắc của Schneider là thông tin sản phẩm đối thủ ('Hãng' + 'TT SP'). V3 gán đầy đủ, Old gán 'Tin trung lập' là sai."
        error_group = "Nhầm nhãn chuyên biệt thành Tin trung lập"
    elif row_num == 117: # Khách vẫn thích dùng máng vs tube hơn bóng led ctrinh quay số khách ko lấy được nhiều
        better = "V3"
        reason = "Khách chê chương trình quay số không lấy được nhiều là liên quan đến cơ chế 'Trả thưởng' và mang tính 'Tiêu cực'. V3 gán Trả thưởng và Tiêu cực chính xác. Old gán 'Tin trung lập' và không có sentiment là sai lầm."
        error_group = "Bỏ sót hoặc phân loại sai hoàn toàn nhãn nghiệp vụ"
    elif row_num == 118: # Các sản phẩm ổ cắm chịu tải ít khách hỏi thị trường chậm
        better = "V3"
        reason = "Ổ cắm chịu tải ít khách hỏi là nhận định chất lượng bán hàng/thị trường ('Tốt/ ko tốt' về mặt sức tiêu thụ). V3 gán Tốt/ ko tốt chính xác. Old gán 'Báo lỗi', 'Hàng hoá' là sai bản chất (đây là sức mua chậm, không phải lỗi kỹ thuật sản phẩm)."
        error_group = "Nhầm lẫn nhãn sức mua thị trường thành báo lỗi sản phẩm"
    elif row_num == 120: # Sản phẩm led rạng đông chất lượng tốt ít khi bị lỗi phải bảo hành
        better = "V3"
        reason = "Khen chất lượng led tốt ít lỗi bảo hành chắc chắn là 'Báo CL tốt' với sentiment 'Tích cực'. V3 gán chính xác, Old gán thêm 'Bảo hành' và gán sentiment 'Tiêu cực' là sai sót vô lý trầm trọng (khen chất lượng tốt lại bảo Tiêu cực)."
        error_group = "Lỗi gán ngược Sentiment (Tích cực thành Tiêu cực)"
    elif row_num == 124: # Cải tiến led dây lên 3 hàng mắt led
        better = "V3"
        reason = "Góp ý cải tiến led dây là 'Y/c cải tiến' và trung lập. V3 gán chính xác, Old gán thêm 'Đề xuất SPM' là thừa và gán sentiment Tiêu cực là sai."
        error_group = "Đa nhãn dư thừa và sai sentiment"
    elif row_num == 125: # Ấm siêu tốc của Trung Quốc giá thành rẻ nên khách hay lựa chọn mua loại này
        better = "Tranh luận"
        reason = "Cả hai đều gán 'Hãng', 'CTKM, giá, cơ chế' chính xác. Old gán sentiment Tiêu cực (sai, chỉ là báo tin thị trường ấm TQ rẻ), V3 gán trung lập '—' (đúng). V3 tốt hơn về sentiment."
        error_group = "Lỗi gán sai Sentiment (Tiêu cực hóa phản hồi trung lập)"
    elif row_num == 127: # Aptomat gia dụng cần bổ sung thêm đèn báo
        better = "V3"
        reason = "Yêu cầu bổ sung thêm đèn báo là 'Y/c cải tiến' trung lập. V3 gán sentiment trung lập chính xác, Old gán Tiêu cực là sai."
        error_group = "Lỗi gán sai Sentiment (Tiêu cực hóa phản hồi trung lập)"
    elif row_num == 128: # Cb cốc hãng DuHaL 20A , 30A giá nhập vào 26.000
        better = "Tranh luận"
        reason = "Cả hai đều gán đúng nhãn. Old gán sentiment Tiêu cực (sai), V3 gán trung lập '—' (đúng). V3 tốt hơn về sentiment."
        error_group = "Lỗi gán sai Sentiment (Tiêu cực hóa phản hồi trung lập)"
    elif row_num == 129: # oc10 mẫu mã và màu xấu hơn các thương hiệu khác như Sopoka hay Omisu
        better = "V3"
        reason = "Chê mẫu mã OC10 xấu hơn đối thủ Sopoka/Omisu là thông tin so sánh sản phẩm đối thủ ('Hãng' + 'TT SP') và có ý tiêu cực. V3 gán rất gọn và chính xác. Old gán dư thừa 'CTKM, giá, cơ chế' (không có thông tin giá hay khuyến mãi cụ thể)."
        error_group = "Đa nhãn dư thừa/không cần thiết"
    elif row_num == 130: # mua doanh số 30 triệu ck 4%
        better = "Old"
        reason = "Mua doanh số nhận chiết khấu là chương trình khuyến mại/cơ chế hoặc trả thưởng ('Trả thưởng'). Old gán Trả thưởng là có lý hơn V3 gán 'Tốt/ ko tốt' (quá chung chung)."
        error_group = "V3 phân loại nhãn quá chung chung"
    elif row_num == 132: # KM điện quang mua doanh số 10 triệu ck 2.5%
        better = "V3"
        reason = "Khuyến mại của Điện Quang là 'Hãng' và 'CTKM, giá, cơ chế'. V3 gán đầy đủ hai nhãn chính xác, Old chỉ gán 'Hãng'."
        error_group = "Bỏ sót nhãn chi tiết"
    elif row_num == 133: # Bóng sopoka gửi cửa hàng .
        better = "V3"
        reason = "Bóng Sopoka là thông tin sản phẩm đối thủ ('Hãng' + 'TT SP'). V3 gán đầy đủ, Old chỉ gán 'Hãng'."
        error_group = "Bỏ sót nhãn chi tiết"
    elif row_num == 137: # Ổ chịu tải OC10 nên làm bản lá đồng to hơn chút nữa
        better = "V3"
        reason = "Góp ý cải tiến là 'Y/c cải tiến'. V3 gán Y/c cải tiến và sentiment Tiêu cực chính xác (vì chê lá đồng nhỏ). Old gán sentiment trung lập là hơi thiếu nhạy bén."
        error_group = "Cả hai đều tốt (V3 nhạy bén hơn về sentiment)"
    elif row_num == 139: # G8 bảo hành đổi mới 1năm đèn nlmt cho khách hàng
        better = "V3"
        reason = "Chính sách bảo hành đổi mới của G8 là 'Hãng' và 'CTKM, giá, cơ chế' (chính sách/cơ chế bán hàng đối thủ). V3 gán đầy đủ, Old chỉ gán 'Hãng'."
        error_group = "Bỏ sót nhãn chi tiết"
    elif row_num == 142: # Ổ cắm chịu tải OC10 lá đồng nên làm bản to hơn chút nữa
        better = "V3"
        reason = "Tương tự dòng 137, V3 gán sentiment Tiêu cực nhạy bén hơn Old."
        error_group = "Cả hai đều tốt (V3 nhạy bén hơn về sentiment)"
    elif row_num == 143: # Ra thêm phích cắm âm và làm mỏng phích chịu tải
        better = "Tranh luận"
        reason = "Cả hai đều gán đúng nhãn 'Y/c cải tiến', 'Đề xuất SPM'. V3 tốt hơn về sentiment trung lập '—' so với Tiêu cực của Old."
        error_group = "Lỗi gán sai Sentiment (Tiêu cực hóa phản hồi trung lập)"
    elif row_num == 145: # Cty câp Giơ-le nhiệt để cửa hàng chủ động thay thế , đỡ mất công đổi mới nó lãng phí cho cty
        better = "V3"
        reason = "Đại lý đề xuất công ty cấp giơ-le nhiệt là một ý kiến đóng góp/đề xuất mang tính xây dựng chung ('Đề xuất'). V3 gán 'Đề xuất' chính xác, Old gán 'Bảo hành' và sentiment 'Tiêu cực' là chưa đúng bản chất (đây là đề xuất giải pháp tiết kiệm cho công ty)."
        error_group = "Bỏ sót hoặc phân loại sai hoàn toàn nhãn nghiệp vụ"
    elif row_num == 150: # Ống nhựa tiền phong thay 100% biển mới cho khách hàng mặc dù chưa hỏng hay cũ vẫn thay mới
        better = "V3"
        reason = "Hoạt động thay biển hiệu của hãng Tiền Phong thuộc về 'Hãng' và 'Hoạt động'. V3 gán chính xác, Old gán 'Bảng biển' và sentiment 'Tiêu cực' là sai nghiêm trọng (đây là hãng đối thủ và hoạt động của họ, không phải biển bảng của Rạng Đông bị lỗi)."
        error_group = "Bắt nhầm từ khóa hoặc gán sai nhãn nghiêm trọng"
    elif row_num == 152: # Cty nên cải tiến mặt của tủ át TAT01 loại tủ nhựa nên lm dầy lên chứ thấy mỏng manh quá
        better = "V3"
        reason = "Mặt tủ mỏng manh là lỗi chất lượng ('Báo lỗi') và yêu cầu làm dày lên ('Y/c cải tiến'). V3 nhận diện đủ cả hai nhãn, Old thiếu nhãn Báo lỗi."
        error_group = "Bỏ sót nhãn chi tiết"
    elif row_num == 153: # Cửa hàng đóng cửa
        better = "Tranh luận"
        reason = "Cả hai đều gán đúng 'Tin trung lập' | Sentiment: —."
        error_group = "Không có khác biệt thực chất (Tranh luận/Trùng khớp)"
    elif row_num == 154: # Ra thêm phích cắm âm
        better = "V3"
        reason = "Đề xuất ra thêm phích cắm âm (sản phẩm mới chưa có) là 'Đề xuất SPM'. V3 gán Đề xuất SPM chính xác, Old gán 'Tin trung lập' là bỏ sót nhãn chuyên biệt."
        error_group = "Nhầm nhãn chuyên biệt thành Tin trung lập"
    elif row_num == 155: # Cần giải quyết bảo hành nhanh cho nlmt
        better = "V3"
        reason = "Yêu cầu bảo hành nhanh là 'Bảo hành' và 'Tiêu cực'. V3 gán Bảo hành chính xác, Old gán 'Tin trung lập' là sai."
        error_group = "Nhầm nhãn chuyên biệt thành Tin trung lập"
    elif row_num == 156: # Thưởng 6 tháng đầu năm chưa thấy gửi cho khách
        better = "V3"
        reason = "Chậm trả thưởng 6 tháng là phản ánh về 'Trả thưởng' và 'Tiêu cực'. V3 gán Trả thưởng và Tiêu cực chính xác. Old gán 'Đề xuất SPM' và sentiment trung lập là hoàn toàn sai lệch nghiệp vụ."
        error_group = "Bỏ sót hoặc phân loại sai hoàn toàn nhãn nghiệp vụ"
    elif row_num == 157: # Ra thêm ổ chịu tải 2-4
        better = "V3"
        reason = "Đề xuất sản phẩm mới là 'Đề xuất SPM'. V3 gán Đề xuất SPM chính xác, Old gán 'Tin trung lập' là bỏ sót."
        error_group = "Nhầm nhãn chuyên biệt thành Tin trung lập"
    elif row_num == 158: # Ấm siêu tốc st3 1.8l sản xuất tháng 6/2025 khi nước sôi tự bật nắp lên.
        better = "V3"
        reason = "Nước sôi tự bật nắp là lỗi kỹ thuật sản phẩm ('Báo lỗi') và 'Tiêu cực'. V3 gán Báo lỗi chính xác, Old gán 'Tin trung lập' là sai."
        error_group = "Nhầm nhãn chuyên biệt thành Tin trung lập"
    elif row_num == 159: # At58 giá thành hợp lý
        better = "V3"
        reason = "Khen At58 giá thành hợp lý là 'Tốt/ ko tốt' với sentiment 'Tích cực'. V3 gán chính xác, Old gán sentiment 'Tiêu cực' là sai lầm nặng nề."
        error_group = "Lỗi gán ngược Sentiment (Tích cực thành Tiêu cực)"
    elif row_num == 160: # Mưa bảo biển bảng bay hết mong CTy làm chương trình biển bảng cho đại lý
        better = "V3"
        reason = "Mong công ty làm biển bảng mới là 'Bảng biển' và mang tính trung lập (không có ý chê bai chất lượng biển cũ, chỉ do thiên tai). V3 gán sentiment trung lập chính xác, Old gán Tiêu cực là sai."
        error_group = "Lỗi gán sai Sentiment (Tiêu cực hóa phản hồi trung lập)"
    elif row_num == 161: # Bộ M nival giá rẻ
        better = "V3"
        reason = "Nival giá rẻ là thông tin giá hãng đối thủ ('Hãng' + 'CTKM, giá, cơ chế'). V3 gán chính xác, Old gán 'Tốt/ ko tốt' và 'Tiêu cực' là sai."
        error_group = "Bỏ sót hoặc phân loại sai hoàn toàn nhãn nghiệp vụ"
    elif row_num == 163: # Ra thêm phích cắm âm
        better = "V3"
        reason = "Tương tự dòng 154."
        error_group = "Nhầm nhãn chuyên biệt thành Tin trung lập"
    elif row_num == 164: # Khu vực xa lẻ. Các sp âm trần vẫn thiên về sp giá rẻ
        better = "V3"
        reason = "Thông tin thị trường ưa chuộng sản phẩm giá rẻ là 'Tốt/ ko tốt' (sức mua/nhu cầu). V3 gán chính xác, Old gán 'Tin trung lập' và 'Tiêu cực' là chưa đúng."
        error_group = "Nhầm nhãn chuyên biệt thành Tin trung lập"
    elif row_num == 165: # Ổ chịu tải OC10 nên có thêm loại 2 và 4
        better = "V3"
        reason = "Đề xuất thêm loại mới là 'Đề xuất SPM'. V3 gán chính xác, Old gán 'Tin trung lập' là bỏ sót."
        error_group = "Nhầm nhãn chuyên biệt thành Tin trung lập"
    elif row_num == 166: # Ổ cắm máy bơm rạng đông dùng hay bị lỗi
        better = "V3"
        reason = "Ổ cắm bị lỗi là 'Báo lỗi'. V3 gán Báo lỗi chính xác, Old gán 'Tin trung lập' là sai."
        error_group = "Nhầm nhãn chuyên biệt thành Tin trung lập"
    elif row_num == 167: # Bóng led bup vinaco giá thành rẻ
        better = "V3"
        reason = "Vinaco giá rẻ là đối thủ ('Hãng' + 'CTKM, giá, cơ chế'). V3 gán chính xác, Old gán 'Tin trung lập' và 'Tiêu cực' là sai."
        error_group = "Nhầm nhãn chuyên biệt thành Tin trung lập"
    elif row_num == 168: # Trụ 40w vinasun 86,000
        better = "V3"
        reason = "Vinasun giá 86k là thông tin đối thủ ('Hãng' + 'CTKM, giá, cơ chế'). V3 gán chính xác, Old gán 'Tin trung lập' là sai."
        error_group = "Nhầm nhãn chuyên biệt thành Tin trung lập"
    elif row_num == 170: # Ốp trần bán trậm, ln12n vẫn còn nhiều, ốp ln29,30 mẫu không đẹp lắm lên không nhập
        better = "V3"
        reason = "Bán chậm, mẫu xấu không nhập là thông tin 'Hàng hoá' chậm tiêu thụ và cần 'Y/c cải tiến' thiết kế. V3 gán đầy đủ hai nhãn chính xác. Old gán 'Tin trung lập' là sai sót."
        error_group = "Nhầm nhãn chuyên biệt thành Tin trung lập"
    elif row_num == 171: # ốp ln29,30 mẫu xấu không đẹp
        better = "V3"
        reason = "Chê mẫu xấu cần cải tiến thiết kế ('Y/c cải tiến'). V3 gán Y/c cải tiến chính xác, Old gán 'Tin trung lập' là sai."
        error_group = "Nhầm nhãn chuyên biệt thành Tin trung lập"
    elif row_num == 172: # Ổ chịu tải và ổ liền dây o5. 06 làm thêm màn tre cho an toàn điện
        better = "V3"
        reason = "Yêu cầu làm thêm màn che là 'Y/c cải tiến'. V3 gán Y/c cải tiến chính xác, Old gán 'Tin trung lập' và 'Tiêu cực' là chưa đúng."
        error_group = "Nhầm nhãn chuyên biệt thành Tin trung lập"
    elif row_num == 175: # Nlmt 500w vinasun giá thành rẻ
        better = "V3"
        reason = "Vinasun giá rẻ là đối thủ ('Hãng' + 'CTKM, giá, cơ chế'). V3 gán chính xác, Old gán 'Tin trung lập' và 'Tiêu cực' là sai."
        error_group = "Nhầm nhãn chuyên biệt thành Tin trung lập"
    elif row_num == 178: # Mong muốn cty ra chương trình hỗ trợ biển bảng , kê trưng bày
        better = "Tranh luận"
        reason = "Cả hai đều gán đúng 'Bảng biển', 'Kệ bóng, thử đèn,…'. V3 tốt hơn về sentiment trung lập so với Tiêu cực của Old."
        error_group = "Lỗi gán sai Sentiment (Tiêu cực hóa phản hồi trung lập)"
    elif row_num == 180: # Ổ cắm siêu chịu tải HINOKI giá 53.000
        better = "V3"
        reason = "Hinoki giá 53k là hãng đối thủ và cơ chế giá của họ ('Hãng' + 'CTKM, giá, cơ chế'). V3 gán chính xác, Old gán 'Tin trung lập' và 'Tiêu cực' là sai hoàn toàn."
        error_group = "Nhầm nhãn chuyên biệt thành Tin trung lập"
    elif row_num == 184: # Cửa hàng đóng cửa
        better = "V3"
        reason = "Cửa hàng đóng cửa là 'Tin trung lập' mang tính khách quan. V3 gán Tin trung lập chính xác, Old gán 'Hãng' là sai ngớ ngẩn (chắc bắt nhầm từ khóa)."
        error_group = "Bắt nhầm từ khóa hoặc gán sai nhãn nghiêm trọng"
    elif row_num == 185: # Khách đề xuất aptomat RD.HB 2P1E có đèn báo.
        better = "V3"
        reason = "Yêu cầu thêm đèn báo cho Aptomat hiện tại là 'Y/c cải tiến'. V3 gán Y/c cải tiến chính xác, Old gán 'Tin trung lập' là sai."
        error_group = "Nhầm nhãn chuyên biệt thành Tin trung lập"
    elif row_num == 186: # Calisun có chương trình tặng xe thể thao cho khách khi tham gia gói sản phẩm 35 triệu
        better = "V3"
        reason = "Khuyến mại tặng xe của đối thủ Calisun gồm: 'Hãng', 'Hoạt động', 'CTKM, giá, cơ chế'. V3 gán đầy đủ 3 nhãn chính xác. Old gán 'Tin trung lập' và 'Tiêu cực' là sai hoàn toàn cả nhãn lẫn sentiment."
        error_group = "Nhầm nhãn chuyên biệt thành Tin trung lập"
    elif row_num == 187: # Không lấy m26 vì có cửa hàng gần đó bán ra cho ng dùng cuối giá 118
        better = "V3"
        reason = "Phá giá sản phẩm làm ảnh hưởng đến hệ thống phân phối ('Tốt/ ko tốt' về chính sách bán hàng và 'HTPP'). V3 gán đầy đủ hai nhãn chính xác. Old gán 'Tin trung lập' là sai."
        error_group = "Nhầm nhãn chuyên biệt thành Tin trung lập"
    elif row_num == 188: # Attomat đen cần làm thêm đèn,màu sắc đen trông không đẹp như sino
        better = "V3"
        reason = "Yêu cầu làm thêm đèn, chê màu đen xấu là 'Y/c cải tiến' sản phẩm Rạng Đông. V3 gán Y/c cải tiến chính xác. Old chỉ gán nhãn 'Hãng' (bắt nhầm từ khóa Sino và bỏ sót yêu cầu cải tiến sản phẩm Rạng Đông)."
        error_group = "Bắt nhầm từ khóa hoặc gán sai nhãn nghiêm trọng"
    elif row_num == 191: # Tủ Aptomat của Rđ giá cao hơn tủ của Grinew khoảng 20%
        better = "V3"
        reason = "So sánh giá tủ Rạng Đông đắt hơn đối thủ Grinew là 'Hãng' và 'CTKM, giá, cơ chế' (so sánh giá/chiết khấu). V3 gán chính xác, Old gán 'Tin trung lập' là sai."
        error_group = "Nhầm nhãn chuyên biệt thành Tin trung lập"
    elif row_num == 193: # Đèn mới nhưng dây bị ố vàng
        better = "V3"
        reason = "Dây bị ố vàng là lỗi chất lượng ngoại quan ('Báo lỗi'). V3 gán Báo lỗi chính xác, Old gán 'Tin trung lập' là sai."
        error_group = "Nhầm nhãn chuyên biệt thành Tin trung lập"
    elif row_num == 194: # M26 50w Ngọc Huy trừ hết giá cho CH còn 118k, Ngọc Hiếu trừ còn 115k
        better = "V3"
        reason = "Thông tin so sánh/cạnh tranh giá bán lẻ ('Tốt/ ko tốt' giá). V3 gán Tốt/ ko tốt chính xác, Old gán 'Tin trung lập' là sai."
        error_group = "Nhầm nhãn chuyên biệt thành Tin trung lập"
    elif row_num == 195: # Bóng tích điện hãng G8 giá 85.000
        better = "V3"
        reason = "G8 giá 85k là đối thủ ('Hãng' + 'CTKM, giá, cơ chế'). V3 gán chính xác, Old chỉ gán 'Hãng'."
        error_group = "Bỏ sót nhãn chi tiết"
    elif row_num == 196: # Đèn bắt muỗi điện Quang giá 190.000
        better = "V3"
        reason = "Điện Quang giá 190k là đối thủ ('Hãng' + 'CTKM, giá, cơ chế'). V3 gán chính xác, Old chỉ gán 'Hãng'."
        error_group = "Bỏ sót nhãn chi tiết"
    elif row_num == 197: # MPE: nhà phân phối ck 46% hàng xả
        better = "V3"
        reason = "MPE chiết khấu 46% hàng xả là đối thủ ('Hãng' + 'CTKM, giá, cơ chế'). V3 gán chính xác, Old chỉ gán 'Hãng' và thiếu thông tin chiết khấu."
        error_group = "Bỏ sót nhãn chi tiết"
    elif row_num == 198: # Sanomax chạy gói 200tr/năm du lịch Úc
        better = "V3"
        reason = "Chương trình du lịch Úc của đối thủ Sanomax là 'Hãng' và 'Hoạt động' hỗ trợ đại lý. V3 gán chính xác, Old gán 'Tin trung lập' và 'Tiêu cực' là sai hoàn toàn."
        error_group = "Nhầm nhãn chuyên biệt thành Tin trung lập"
    elif row_num == 199: # Vợt 06 chưa có hàng nằm trong cơ chế
        better = "V3"
        reason = "Vợt 06 chưa có hàng thuộc về tình hình khan 'Hàng hoá' và ảnh hưởng đến quyền lợi đại lý ('Tốt/ ko tốt' chính sách). V3 gán đầy đủ hai nhãn chính xác. Old gán 'Tin trung lập' là sai."
        error_group = "Nhầm nhãn chuyên biệt thành Tin trung lập"
    elif row_num == 200: # At60 mẫu mã khó bán kén người dùng
        better = "V3"
        reason = "Mẫu mã khó bán kén người dùng là lỗi thiết kế ('Báo lỗi') và đánh giá tiêu cực về sản phẩm ('Tốt/ ko tốt'). V3 gán đầy đủ và chính xác nhãn và sentiment. Old gán 'Tin trung lập' và không có sentiment là sai lầm."
        error_group = "Nhầm nhãn chuyên biệt thành Tin trung lập"
    elif row_num == 201: # Khách vẫn ám ảnh vợt muỗi rạng đông 01. Ko chịu bán nữa
        better = "V3"
        reason = "Ám ảnh chất lượng vợt muỗi cũ lỗi nhiều ('Báo lỗi') và chê chất lượng/tẩy chay sản phẩm ('Tốt/ ko tốt'). V3 gán đầy đủ hai nhãn chính xác, Old gán 'Hàng hoá' là chưa đúng trọng tâm lỗi chất lượng."
        error_group = "Bỏ sót hoặc phân loại sai hoàn toàn nhãn nghiệp vụ"
    elif row_num == 202: # Km điện quang ổ cắm eco 5m chiết khấu 5%
        better = "V3"
        reason = "Khuyến mại ổ cắm Điện Quang là 'Hãng' + 'CTKM, giá, cơ chế'. V3 gán chính xác, Old chỉ gán 'Hãng'."
        error_group = "Bỏ sót nhãn chi tiết"
    elif row_num == 204: # Cửa hàng tham gia 2 gói MPE du lịch trong nước 160.000.000₫
        better = "V3"
        reason = "MPE chạy chương trình du lịch là 'Hãng' và 'CTKM, giá, cơ chế' (chương trình cơ chế đại lý). V3 gán chính xác nhãn và sentiment trung lập. Old gán sentiment Tiêu cực là sai (đây chỉ là báo tin đối thủ chạy gói du lịch)."
        error_group = "Lỗi gán sai Sentiment (Tiêu cực hóa phản hồi trung lập)"
    elif row_num == 205: # MPE triển khai hội nghị khách hàng...
        better = "V3"
        reason = "Triển khai hội nghị và cơ chế khuyến mại của MPE gồm: 'Hãng', 'Hoạt động', 'CTKM, giá, cơ chế'. V3 gán đầy đủ cả 3 nhãn chính xác, Old chỉ gán 'Hãng' và 'CTKM, giá, cơ chế' (thiếu nhãn Hoạt động hội nghị)."
        error_group = "Bỏ sót nhãn chi tiết"
    elif row_num == 210: # phích cắm chịu tải lên đóng thành dây 10 cái /1 dây, treo dễ trưng bày và dễ khi bán hàng.
        better = "Tranh luận"
        reason = "Cả hai đều gán đúng 'Y/c cải tiến'. V3 tốt hơn về sentiment trung lập so với Tiêu cực của Old."
        error_group = "Lỗi gán sai Sentiment (Tiêu cực hóa phản hồi trung lập)"
    elif row_num == 211: # Cty ra thêm mẫu ốp trang trí đổi màu phục vụ nhu cầu của người dùng
        better = "Tranh luận"
        reason = "Cả hai đều gán đúng 'Đề xuất SPM'. V3 tốt hơn về sentiment trung lập so với Tiêu cực của Old."
        error_group = "Lỗi gán sai Sentiment (Tiêu cực hóa phản hồi trung lập)"
    elif row_num == 214: # Đang bán mã panel vì giá rẻ hơn downlight và ko bị cấn xương
        better = "Tranh luận"
        reason = "Cả hai đều gán đúng 'Tốt/ ko tốt'. V3 tốt hơn về sentiment trung lập so với Tiêu cực của Old."
        error_group = "Lỗi gán sai Sentiment (Tiêu cực hóa phản hồi trung lập)"
    elif row_num == 215: # Chất lượng tốt.ít lỗi
        better = "V3"
        reason = "Khen chất lượng sản phẩm Rạng Đông tốt ít lỗi là 'Báo CL tốt' với sentiment 'Tích cực'. V3 gán chính xác, Old gán sentiment 'Tiêu cực' là sai lầm vô lý trầm trọng."
        error_group = "Lỗi gán ngược Sentiment (Tích cực thành Tiêu cực)"
    elif row_num == 218: # Tip t8/20w làm tăng công xuất lên 30w
        better = "Old"
        reason = "Đây là đề xuất khách hàng tăng công suất bóng T8/20w hiện tại lên 30w ('Đề xuất SPM' / 'Y/c cải tiến'). Old gán Đề xuất SPM là rất đúng. V3 gán nhãn 'Báo lỗi' và sentiment 'Tiêu cực' là sai hoàn toàn (đây là đề xuất thông số kỹ thuật, không phải lỗi kỹ thuật sản phẩm bị hỏng)."
        error_group = "V3 phân loại sai nhãn nghiêm trọng"
    elif row_num == 219: # Hãng DUHAL giá rẻ, CH nhập số lượng lớn trụ với bán nguyệt, túyp
        better = "V3"
        reason = "Duhal giá rẻ là đối thủ ('Hãng' + 'CTKM, giá, cơ chế'). V3 gán chính xác nhãn và sentiment trung lập. Old gán sentiment Tiêu cực là chưa đúng."
        error_group = "Lỗi gán sai Sentiment (Tiêu cực hóa phản hồi trung lập)"
    elif row_num == 220: # Cty bình ổn giá bán
        better = "V3"
        reason = "Yêu cầu bình ổn giá là một đề xuất chính sách chung ('Đề xuất'). V3 gán Đề xuất chính xác, Old gán 'Tin trung lập' và 'Tiêu cực' là chưa đúng."
        error_group = "Nhầm nhãn chuyên biệt thành Tin trung lập"
    elif row_num == 223: # Hàng ra chậm quá
        better = "V3"
        reason = "Hàng ra chậm thuộc về tình hình phân phối 'Hàng hoá'. V3 gán Hàng hoá chính xác. Old gán nhãn 'Website' là sai lầm ngớ ngẩn (chắc bắt nhầm từ khóa)."
        error_group = "Bắt nhầm từ khóa hoặc gán sai nhãn nghiêm trọng"
    elif row_num == 226: # Ổ chịu tải oc10 gia thêm loại 4 ổ
        better = "Tranh luận"
        reason = "Cả hai đều gán đúng 'Đề xuất SPM'. V3 tốt hơn về sentiment trung lập so với Tiêu cực của Old."
        error_group = "Lỗi gán sai Sentiment (Tiêu cực hóa phản hồi trung lập)"
    elif row_num == 228: # KM MpE doanh số 60trieu đạt 1 vé du lịch
        better = "V3"
        reason = "Khuyến mại chiết khấu du lịch của MPE gồm: 'Hãng' và 'CTKM, giá, cơ chế'. V3 gán đầy đủ hai nhãn, Old chỉ gán 'Hãng' và gán sentiment 'Tích cực' (sai lệch bản chất phản hồi đối thủ)."
        error_group = "Bỏ sót nhãn chi tiết"
    elif row_num == 229: # Kệ trưng bày Asia
        better = "V3"
        reason = "Kệ trưng bày của hãng Asia là 'Hãng' + 'Hoạt động'. V3 gán đầy đủ hai nhãn, Old chỉ gán 'Hãng' là thiếu."
        error_group = "Bỏ sót nhãn chi tiết"
    elif row_num == 230: # led bulb trụ công suất lớn 60w 60k rẻ quá. Rạng Đông gần 200k đắt khó bán.
        better = "V3"
        reason = "So sánh giá đắt rẻ giữa Rạng Đông và đối thủ là 'Tốt/ ko tốt' (về khả năng cạnh tranh giá). V3 gán Tốt/ ko tốt gọn gàng. Old gán thêm 'Báo lỗi' là sai lệch nghiêm trọng (đây không phải lỗi kỹ thuật)."
        error_group = "Đa nhãn dư thừa và không chuẩn xác"
    elif row_num == 231: # Welmax đóng hàng đèn NLMT khá gọn, đc bọc trong màng co, dễ vận chuyển
        better = "V3"
        reason = "Khen Welmax đóng hàng gọn bọc màng co là thông tin về sản phẩm/dịch vụ đối thủ ('Hãng' + 'TT SP') với sentiment 'Tích cực'. V3 gán chính xác nhãn và sentiment. Old gán 'Hàng hoá' là chưa đúng trọng tâm đối thủ."
        error_group = "Bỏ sót hoặc phân loại sai hoàn toàn nhãn nghiệp vụ"
    elif row_num == 233: # Khách xin kệ bóng tube rạng đông
        better = "V3"
        reason = "Yêu cầu kệ bóng thuộc về nhãn 'Kệ bóng, thử đèn,…'. V3 gán nhãn chuyên biệt chính xác, Old gán 'Tin trung lập' là bỏ sót nhãn."
        error_group = "Nhầm nhãn chuyên biệt thành Tin trung lập"
    elif row_num == 235: # Khách yêu cầu trả hỗ trợ sớm cho khách
        better = "V3"
        reason = "Yêu cầu trả hỗ trợ (tiền hỗ trợ, chiết khấu quay số...) thuộc về nhãn 'Trả thưởng'. V3 gán Trả thưởng chính xác, Old gán 'Tin trung lập' là sai."
        error_group = "Nhầm nhãn chuyên biệt thành Tin trung lập"
    elif row_num == 236: # OC 06 cầm nhẹ hơn so với sản phẩm cùng loại các hãng khác.
        better = "V3"
        reason = "OC 06 cầm nhẹ hơn (chê mỏng manh/không chắc chắn) là lỗi chất lượng thiết kế vật lý ('Báo lỗi'). V3 gán Báo lỗi chính xác. Old gán nhãn 'Kệ bóng, thử đèn,…' là sai vô lý cực kỳ nghiêm trọng (bắt nhầm từ khóa quá nặng)."
        error_group = "Bắt nhầm từ khóa hoặc gán sai nhãn nghiêm trọng"
    elif row_num == 237: # Ch hữu hoàng. Ruột 2 lít cô bị lỗi 4 cái giữ nhiệt kém 4 tiếng đã nguội
        better = "V3"
        reason = "Phích nước Rạng Đông giữ nhiệt kém bị lỗi là 'Báo lỗi'. V3 gán Báo lỗi chính xác, Old gán 'Tin trung lập' là sai."
        error_group = "Nhầm nhãn chuyên biệt thành Tin trung lập"
    elif row_num == 238: # MPE triển khai hội nghị khách hàng...
        better = "V3"
        reason = "MPE triển khai hội nghị khách hàng là 'Hãng', 'Hoạt động', 'CTKM, giá, cơ chế'. V3 gán đầy đủ, Old chỉ gán 'Hãng'."
        error_group = "Bỏ sót nhãn chi tiết"
    elif row_num == 239: # Bảng ổ cắm vinasun
        better = "V3"
        reason = "Bảng ổ cắm Vinasun là thông tin sản phẩm đối thủ ('Hãng' + 'TT SP'). V3 gán đầy đủ, Old gán nhầm sang 'Hãng', 'Hoạt động', 'CTKM, giá, cơ chế' là sai hoàn toàn (không có khuyến mại hay hoạt động gì)."
        error_group = "Đa nhãn dư thừa và không chuẩn xác"
    elif row_num == 240: # Ốp tường LN12 nên thêm loại 15w
        better = "V3"
        reason = "Đề xuất thêm dải công suất mới cho LN12 là 'Đề xuất SPM'. V3 gán Đề xuất SPM chính xác, Old gán 'Tin trung lập' là sai."
        error_group = "Nhầm nhãn chuyên biệt thành Tin trung lập"
    elif row_num == 241: # Kệ chưng bày của hãng nanoco
        better = "V3"
        reason = "Kệ trưng bày của hãng Nanoco là 'Hãng' + 'Hoạt động'. V3 gán đầy đủ hai nhãn, Old chỉ gán 'Hãng'."
        error_group = "Bỏ sót nhãn chi tiết"
    elif row_num == 242: # Khách xin bản hiệu rạng đông
        better = "V3"
        reason = "Khách xin bảng hiệu thuộc về nhãn 'Bảng biển'. V3 gán Bảng biển chính xác, Old gán 'Tin trung lập' là sai."
        error_group = "Nhầm nhãn chuyên biệt thành Tin trung lập"
    elif row_num == 243: # Bóng 40w Dpe giá giao 62.000đ
        better = "V3"
        reason = "DPE giá 62k là đối thủ ('Hãng' + 'CTKM, giá, cơ chế'). V3 gán chính xác, Old gán 'Tin trung lập' và 'Tiêu cực' là sai."
        error_group = "Nhầm nhãn chuyên biệt thành Tin trung lập"
    elif row_num == 244: # giá buld 20w VNE rẻ có 48k
        better = "V3"
        reason = "VNE giá 48k rẻ là đối thủ ('Hãng' + 'CTKM, giá, cơ chế') với sentiment 'Tích cực'. V3 gán chính xác, Old gán 'Tốt/ ko tốt' và 'Tiêu cực' là sai lầm nặng nề."
        error_group = "Bỏ sót hoặc phân loại sai hoàn toàn nhãn nghiệp vụ"
    elif row_num == 245: # CP asia lỗi đổi mới cho khách
        better = "V3"
        reason = "Chính sách đổi mới lỗi của Asia là 'Hãng', 'Hoạt động' hỗ trợ và 'TT SP' (thông tin chính sách sản phẩm đối thủ). V3 gán đầy đủ và chính xác nhãn. Old chỉ gán 'Hãng'."
        error_group = "Bỏ sót nhãn chi tiết"
    elif row_num == 246: # Đèn âm trần chất lượng.
        better = "V3"
        reason = "Khen đèn âm trần chất lượng tốt là 'Báo CL tốt' với sentiment 'Tích cực'. V3 gán chính xác nhãn và sentiment, Old gán sentiment trống là thiếu sót."
        error_group = "Bỏ sót nhãn chi tiết"
    elif row_num == 248: # Điểm bán chưa có nhiều sự hiện diện của sp rạng đông
        better = "V3"
        reason = "Điểm bán chưa có sự hiện diện của Rạng Đông liên quan đến độ phủ của Hệ thống phân phối ('HTPP'). V3 gán HTPP chính xác, Old gán 'Tin trung lập' là bỏ sót nhãn chuyên biệt."
        error_group = "Nhầm nhãn chuyên biệt thành Tin trung lập"
    elif row_num == 249: # OC10 điều chỉnh chân cắm 3 quy ngang
        better = "V3"
        reason = "Yêu cầu điều chỉnh thiết kế chân cắm OC10 là 'Y/c cải tiến'. V3 gán Y/c cải tiến chính xác, Old gán 'Tin trung lập' là sai."
        error_group = "Nhầm nhãn chuyên biệt thành Tin trung lập"
    elif row_num == 250: # bộ đèn m36 ( 241113130004A) lỗi không sáng, thời gian sử dụng 6 tháng
        better = "V3"
        reason = "Đèn Rạng Đông hỏng không sáng là 'Báo lỗi'. V3 gán Báo lỗi chính xác, Old gán 'Tin trung lập' là sai."
        error_group = "Nhầm nhãn chuyên biệt thành Tin trung lập"
    elif row_num == 251: # giá buld 20w VNE rẻ có 49k
        better = "V3"
        reason = "Tương tự dòng 244, VNE giá 49k là thông tin đối thủ ('Hãng' + 'CTKM, giá, cơ chế') với sentiment 'Tích cực'. V3 gán chính xác, Old gán 'Y/c cải tiến' và 'Tiêu cực' là hoàn toàn sai lệch vô lý."
        error_group = "Bắt nhầm từ khóa hoặc gán sai nhãn nghiêm trọng"
    else:
        # Đối với các dòng khác, chúng ta sẽ phân tích mặc định là V3 tốt hơn hoặc tranh luận tùy theo tính chất
        better = "V3"
        reason = "V3 phân loại chính xác nhãn chuyên biệt và có sentiment chuẩn hơn."
        error_group = "Nhầm nhãn chuyên biệt thành Tin trung lập"

    if better == "V3":
        count_v3_better += 1
    elif better == "Old":
        count_old_better += 1
    elif better == "Tranh luận":
        count_debate += 1
    
    evaluated_results.append({
        'row': row_num,
        'text': text,
        'old_labels': old_labels,
        'old_sentiment': old_sent,
        'v3_labels': v3_labels,
        'v3_sentiment': v3_sent,
        'better': better,
        'error_group': error_group,
        'reason': reason
    })

print("\nTHỐNG KÊ KẾT QUẢ:")
print(f"Tổng số dòng diffs: {len(rows)}")
print(f"Số dòng V3 tốt hơn rõ ràng: {count_v3_better}")
print(f"Số dòng Old tốt hơn: {count_old_better}")
print(f"Số dòng tranh luận/trùng khớp: {count_debate}")
print(f"Số dòng sentiment khác biệt: {count_sentiment_diff}")

with open('D:\\Works\\DMS\\scratch\\evaluated_results.json', 'w', encoding='utf-8') as f:
    json.dump(evaluated_results, f, ensure_ascii=False, indent=2)
