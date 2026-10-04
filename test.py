import os
import json
from google import genai
from google.genai import types
from dotenv import load_dotenv

# 1. Nạp API Key (Đảm bảo bạn đã điền GEMINI_API_KEY vào file .env)
load_dotenv()

# 2. Khởi tạo Client
client = genai.Client()

# 3. Định nghĩa cấu trúc JSON Output mong muốn (Sử dụng chữ IN HOA cho type)
WRITER_OUTPUT_FORMAT = {
    "type": "OBJECT",
    "properties": {
        "tom_tat_va_doi_chieu": {
            "type": "STRING",
            "description": "Bản tóm tắt tổng hợp từ các bài báo và chỉ ra các điểm mâu thuẫn, lỗi logic chéo giữa chúng nếu có."
        },
        "tts_text": {
            "type": "STRING",
            "description": "Kịch bản bài viết hoàn chỉnh, trau chuốt, sẵn sàng để đọc chuyển thành giọng nói (TTS)."
        },
        "hashtags": {
            "type": "ARRAY",
            "items": {"type": "STRING"},
            "description": "Danh sách chứa từ 3 đến 5 hashtag liên quan đến nội dung bài viết."
        }
    },
    "required": ["tom_tat_va_doi_chieu", "tts_text", "hashtags"]
}

# 4. Dữ liệu bài báo thô giả lập (Có chứa thông tin mâu thuẫn để test khả năng check lỗi)
bai_bao_1 = "Bài báo A: Công ty X vừa ra mắt mẫu điện thoại Alpha giá 10 triệu đồng vào sáng nay, sản phẩm có pin 5000mAh."
bai_bao_2 = "Bài báo B: Sự kiện ra mắt điện thoại Alpha của công ty X gây thất vọng vì giá bán thực tế lên tới 15 triệu, cao hơn tin đồn, dung lượng pin đạt 4500mAh."
bai_bao_3 = "Bài báo C: Điện thoại Alpha vừa ra mắt của công ty X sử dụng viên pin dung lượng 5000mAh và có mức giá chính thức là 15 triệu đồng."

# Gom các bài báo lại thành ngữ cảnh
ngu_canh_dau_vao = f"""
[BÀI BÁO 1]: {bai_bao_1}
[BÀI BÁO 2]: {bai_bao_2}
[BÀI BÁO 3]: {bai_bao_3}
"""

print("--- 🚀 Đang gửi dữ liệu test đến Gemini 3 Flash ---")

try:
    # 5. Gọi API Gemini 3 Flash
    response = client.models.generate_content(
        model='gemini-3.1-flash-lite',
        contents=[
            "Bạn là một Agent kiểm thử dữ liệu và biên tập viên cao cấp. Hãy thực hiện các việc sau:\n"
            "1. Tóm tắt nội dung chính từ các bài báo được cung cấp.\n"
            "2. Đối chiếu chéo (Fact-check) và chỉ rõ các điểm mâu thuẫn về thông tin (ví dụ: Giá bán, Dung lượng pin) giữa các bài báo.\n"
            "3. Viết lại một kịch bản hoàn chỉnh (tts_text) bằng tiếng Việt mượt mà để phát thanh.\n"
            "4. Tạo từ 3 đến 5 hashtag phù hợp.",
            ngu_canh_dau_vao
        ],
        config=types.GenerateContentConfig(
            temperature=0.2, # Giữ nhiệt độ thấp để lập luận chính xác, bám sát bài viết
            response_mime_type="application/json", # Ép kiểu trả về là JSON
            response_schema=WRITER_OUTPUT_FORMAT, # Truyền cấu trúc mong muốn vào
        ),
    )

    # 6. In kết quả thô nhận được từ API
    print("\n--- ⚡ KẾT QUẢ ĐÃ ÉP ĐỊNH DẠNG JSON ---")
    print(response.text)

    # Test thử xem cấu trúc JSON có chuẩn không bằng cách nạp vào dict của Python
    data = json.loads(response.text)
    print("\n✅ Thành công! Dữ liệu trả về đúng cấu trúc JSON mong muốn.")

except Exception as e:
    print(f"\n❌ Có lỗi xảy ra: {e}")