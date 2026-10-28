# AI News Content Automation

Lấy tin tức từ RSS → viết kịch bản → đọc giọng người (TTS) → vẽ storyboard whiteboard → render video → ghép âm thanh → đăng lên YouTube.

Quy trình:

```
collect → select → write → tts → audio_duration
   → image_plan → images → render → mux → publish
```

Mỗi bước lưu trạng thái trong `state/pipeline_state.json`; lần chạy sau chỉ làm phần còn thiếu. Dùng `--fresh` để chạy lại từ đầu.

## Cài đặt

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
pip install -r requirements-dev.txt   # nếu muốn chạy test
```

Biến môi trường:

```powershell
Copy-Item .env.example .env
# sửa .env: GEMINI_API_KEY, POLLINATIONS_API_KEY, YT_*, ...
```

Phụ thuộc bên ngoài (cài tay):
- **ffmpeg/ffprobe** trong PATH (hoặc `FFMPEG_PATH` / `FFPROBE_PATH`) — dùng ghép video.
- **WhiteBoardEngine** tại `Lib/WhiteBoardEngine/whiteboard-video-engine` (tạo `.venv` rồi `pip install -e .`), hoặc đặt `WHITEBOARD_CLI`.

## Chạy

```powershell
python main.py --dry-run     # kiểm tra cấu hình, không gọi mạng
python main.py               # chạy đầy đủ
python main.py --limit 1     # thử nhanh 1 bài
python main.py --fresh       # bỏ trạng thái cũ, chạy lại từ đầu
python youtube_auth.py       # 1 lần: lấy refresh token YouTube (OAuth loopback)
```

Cờ hay dùng: `--no-images`, `--no-render`, `--no-mux`, `--no-publish`, `--fail-fast`, `--log-level`.

Mã thoát: `0` thành công, `1` có bài thất bại, `2` pipeline không chạy được.

Test:

```powershell
python -m pytest
```

## Model & nền tảng

| Thành phần | Mô hình / nhà cung cấp | Cấu hình |
| --- | --- | --- |
| LLM viết bài | Gemini (`gemini-3.5-flash-lite`, `gemini-3.1-flash-lite`, `gemini-3.8-flash`), Gemma-4-31B-it (fallback), Ollama `llama3.1:8b` | `Config/LLM/models.json` |
| TTS đọc bài | edge-tts (`vi-VN-HoaiMyNeural`) hoặc Google TTS | `Config/tts.json` |
| Sinh ảnh | Flux qua Pollinations | `Config/Image/models.json` |
| Render video | WhiteBoardEngine (hand-drawn whiteboard) | `Config/render.json` |

Mặc định video xuất ra **1080×1920 (9:16)** — phù hợp YouTube Shorts. Muốn quay lại 16:9, đổi `render.json` về `1920×1080`.

## Cấu hình (`Config/`)

- `rss.json`: danh sách feed tin tức.
- `pipeline.json`: retry, workflow, trạng thái job.
- `Agents/*.json`: model + prompt cho từng agent (writer, checker, selector, image_planner).
- `Image/*.json`: model ảnh và provider.
- `render.json`: engine whiteboard, fps, `width`/`height`, thời lượng mỗi cảnh.
- `media.json`: thư mục video cuối, sai số thời lượng.
- `publish.json`: nền tảng, chế độ đăng.

## Đăng YouTube

Mặc định `"mode": "prepare"` chỉ ghi bản kê `output/publish/<article_id>.json`, không đăng thật. Để đăng:

```json
"platforms": ["youtube"],
"mode": "publish"
```

1. Tạo project Google Cloud, bật **YouTube Data API v3**.
2. Tạo OAuth client **Desktop app**, lưu `yt-secret/client_secret.json`.
3. Chạy `python youtube_auth.py` một lần để lấy refresh token (ghi `yt-secret/token.json` + cập nhật `.env`).
4. Khối `youtube` trong `publish.json`: `privacy_status` (`public`/`unlisted`/`private`), `category_id` (25 = News), `check_existing`, ...

YouTube không có cờ riêng cho Shorts: video **dọc/vuông ≤ 3 phút** sẽ tự được xếp vào Shorts. Không retry upload trong 1 lần chạy (tránh video trùng); nếu lỗi, chạy lại pipeline, bước publish sẽ tra tiêu đề trước khi đăng lại.