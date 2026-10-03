import asyncio
import edge_tts

# Đoạn văn bản của bạn
TEXT = """
TP HCM dự kiến rào chắn một phần khu vực ga Bến Thành từ cuối tháng 10 để thi công metro số 2. Việc này khiến nhiều tuyến đường ở trung tâm phải điều chỉnh lưu thông. Phạm vi công trường dự kiến kéo dài từ đường Phạm Hồng Thái đến khu vực Hàm Nghi. Rào chắn bắt đầu từ nửa cuối tháng 10 và sẽ kéo dài khoảng ba tháng. Sau đó, phạm vi thi công sẽ tiếp tục được điều chỉnh theo tiến độ từng hạng mục. Toàn bộ quá trình thi công tại khu vực này dự kiến kéo dài hơn ba năm. Khi lập công trường, đường Phạm Hồng Thái sẽ tạm đóng đoạn từ trước giao lộ Lê Thánh Tôn đến Trương Định. Các đoạn đường Lý Tự Trọng và Trương Định quanh khu vực này dự kiến được tổ chức cho xe chạy hai chiều để phân luồng. Xe trên đường Lê Thánh Tôn vẫn có thể rẽ vào Phạm Hồng Thái, hướng về ngã sáu Phù Đổng qua tuyến đường tạm rộng 11 m bố trí bên ngoài hàng rào công trường. Trên đường Hàm Nghi, một phần lòng đường cũng bị thu hẹp nhưng vẫn duy trì giao thông và hoạt động của trạm trung chuyển xe buýt. Đường Nam Kỳ Khởi Nghĩa dự kiến được tổ chức hai chiều đoạn Nguyễn Thái Bình - Hàm Nghi nhằm giảm áp lực cho khu vực ga Bến Thành"""

# Chọn giọng đọc tiếng Việt của Microsoft
# Giọng nữ Miền Bắc: vi-VN-HoaiMyNeural
# Giọng nam Miền Bắc: vi-VN-NamMinhNeural
VOICE = "vi-VN-HoaiMyNeural"
OUTPUT_FILE = "space_data_center.mp3"


async def generate_speech():
    communicate = edge_tts.Communicate(TEXT, VOICE)
    await communicate.save(OUTPUT_FILE)
    print(f"Đã tạo xong file âm thanh: {OUTPUT_FILE}")


if __name__ == "__main__":
    asyncio.run(generate_speech())