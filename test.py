
import requests
import urllib.parse

# 1. Điền thông tin cấu hình
api_key = "sk_aZAI65QpSwIAANVT51NrBykPeTO1W8GX"  # Thay bằng Secret Key (sk_...) của bạn
prompt = "tạo bức ảnh có 1 hòn đảo, trên đảo có cây dừa, xung quanh là biển, khung cảnh tươi sáng"
model = "flux"  # Hoặc mô hình khác như 'turbo', 'flux-realism',...
width = 1024
height = 1024
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
