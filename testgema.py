
import os
from google import genai
from google.genai import types
from dotenv import load_dotenv
load_dotenv()
# Đọc API key từ biến môi trường
api_key = os.getenv("GEMINI_API_KEY")

if not api_key:
    raise ValueError(
        "Không tìm thấy GEMINI_API_KEY. "
        "Hãy kiểm tra file .env hoặc biến môi trường."
    )

client = genai.Client(api_key=api_key)

MODEL = "gemma-4-26b-a4b-it"
#MODEL = "gemma-4-31b-it"

prompt = """
Hãy cho tôi 1 đoạn văn ngắn về lợi ích của việc học tiếng Anh.
"""

try:
    print(f"Đang kiểm tra model: {MODEL}")

    response = client.models.generate_content(
        model=MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            temperature=0.2,
            response_mime_type="application/json",
            automatic_function_calling=types.AutomaticFunctionCallingConfig(
                disable=True
            ),
        ),
    )

    print("\n=== KẾT QUẢ ===")
    print(response.text)

except Exception as e:
    print(f"\n=== TEST THẤT BẠI ===")
    print(f"Loại lỗi: {type(e).__name__}")
    print(f"Chi tiết: {e}")

finally:
    client.close()
