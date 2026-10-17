
import requests
import urllib.parse

# 1. Điền thông tin cấu hình
api_key = ""
topic = """
A hand-drawn human brain connected to a computer chip,
with small icons representing artificial intelligence,
robots, data, and digital technology.
"""

prompt = """
A simple hand-drawn whiteboard illustration of artificial intelligence,
a human brain connected to a computer chip, small doodle icons of robots
and digital technology, black marker outlines, imperfect hand-drawn strokes,
minimalist educational explainer drawing, pure white background,
simple 2D line art, a few blue and orange accent colors,
clear shapes, high contrast, centered composition,
consistent sketch style, suitable for whiteboard animation,
no photorealism, no 3D, no gradients, no realistic textures,
no shadows, no text, no letters, no watermark.
"""
model = "lykon/dreamshaper-8-lcm" # Hoặc mô hình khác như 'turbo', 'flux-realism',...
width = 1024
height = 576
seed = 42

# 2. Mã hóa mô tả prompt để hợp lệ trên URL
encoded_prompt = urllib.parse.quote(prompt)

# 3. Tạo URL gọi API
url = f"https://gen.pollinations.ai/image/{encoded_prompt}?model={model}&width={width}&height={height}&seed={seed}&key={api_key}"

# 4. Tải ảnh về và lưu thành file
print("Đang tạo ảnh...")
response = requests.get(url)

if response.status_code == 200:
    with open("generated_image.jpg", "wb") as file:
        file.write(response.content)
    print("Thành công! Ảnh đã được lưu tại: generated_image.jpg")
else:
    print(f"Lỗi {response.status_code}: {response.text}")
