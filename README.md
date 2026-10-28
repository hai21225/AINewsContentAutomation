# AI News Content Automation

Biến tin tức từ RSS thành **video whiteboard ngắn có thuyết minh**, rồi chuẩn bị
hoặc đăng lên mạng xã hội.

## Quy trình

```
collect_articles → select_articles → write → tts → audio_duration
       → image_plan → images → render → mux → publish
```

- **write**: viết lời đọc (TTS) + tiêu đề + hashtag, có kiểm tra chéo.
- **tts / audio_duration**: sinh audio lời đọc và đo thời lượng thật.
- **image_plan / images**: chia cảnh và sinh ảnh line-art cho từng cảnh.
- **render**: dựng mỗi cảnh thành một clip whiteboard (engine ngoài).
- **mux**: ghép các clip + audio thành video cuối bằng ffmpeg.
- **publish**: ghi bản kê và (tuỳ chế độ) đăng video lên Facebook Page và/hoặc
  YouTube.

Mỗi bước được lưu trạng thái trong `state/pipeline_state.json`, nên lần chạy sau
chỉ làm lại phần còn thiếu. Dùng `--fresh` để chạy lại từ đầu.

## Cài đặt

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
pip install -r requirements-dev.txt   # cho test
```

Đăng ký biến môi trường:

```powershell
Copy-Item .env.example .env
# rồi điền GEMINI_API_KEY, POLLINATIONS_API_KEY, ...
```

Cần thêm **ffmpeg/ffprobe** trong PATH (hoặc đặt `FFMPEG_PATH` / `FFPROBE_PATH`),
và engine whiteboard ở `Lib/WhiteBoardEngine/whiteboard-video-engine` (đặt
`WHITEBOARD_CLI` nếu CLI nằm chỗ khác).

## Chạy

```powershell
python main.py --dry-run          # kiểm tra cấu hình, không gọi mạng
python main.py                    # chạy đầy đủ
python main.py --limit 1          # thử nhanh một bài
python main.py --no-mux           # dừng trước khi ghép video
python main.py --fresh            # bỏ trạng thái cũ, chạy lại từ đầu
python youtube_auth.py            # một lần: lấy refresh token YouTube
```

Cờ liên quan: `--no-images`, `--no-render`, `--no-mux`, `--no-publish`,
`--fail-fast`, `--log-level`.

Mã thoát: `0` thành công, `1` có bài thất bại, `2` pipeline không chạy được.

## Cấu hình

Mọi cấu hình nằm trong `Config/`:

| File | Nội dung |
| --- | --- |
| `rss.json` | Danh sách feed đầu vào |
| `pipeline.json` | Retry, workflow, trạng thái job |
| `logging.json` | Mức log |
| `tts.json` | Provider TTS và voice |
| `Agents/*.json` | Model + prompt cho từng agent |
| `Image/*.json` | Model sinh ảnh và provider |
| `render.json` | Engine whiteboard, fps, thời lượng mỗi cảnh |
| `media.json` | Thư mục video cuối, sai số thời lượng |
| `publish.json` | Nền tảng, chế độ và thông tin đăng bài |

## Đăng bài (bước `publish`)

Bước này **mặc định không đăng gì**: `Config/publish.json` để `"mode": "prepare"`
— chỉ ghi bản kê `output/publish/<article_id>.json` (tiêu đề, mô tả, hashtag,
đường dẫn video) rồi dừng.

Nền tảng được chọn bằng `platform` (một nền tảng, kiểu cũ) hoặc `platforms`
(nhiều nền tảng, ưu tiên khi có):

```json
"platforms": ["facebook", "youtube"]
```

Khi đăng nhiều nền tảng, bản kê có `results` cho từng nền tảng (`status`,
`post_id`, `post_url`, `deduplicated`, `published_at`). `status` chung là
`published` (tất cả xong), `partial` (một phần lỗi) hoặc `failed`. Các trường
`post_id` / `post_url` ở cấp ngoài giữ nghĩa cũ: phản ánh nền tảng đầu tiên.
Nền tảng lỗi không chặn nền tảng còn lại; bản kê vẫn được ghi để lần chạy sau
chỉ đăng phần còn thiếu.

### Facebook Page

1. Tạo Meta app và một Facebook Page.
2. Cấp các quyền `pages_show_list`, `pages_read_engagement`,
   `pages_manage_posts` cho user token.
3. Điền `PAGE_ID` và `PAGE_ACCESS_TOKEN` vào `.env` (tên biến phải khớp
   `page_id_env` / `access_token_env` trong `Config/publish.json`).
4. Đổi `"mode"` trong `Config/publish.json` thành `"publish"` và để
   `"platform": "facebook"` (hoặc `"platforms": ["facebook"]`).

`PAGE_ACCESS_TOKEN` có thể là **user token**: user token đọc được Page nhưng
Facebook trả lỗi `#210 A page access token is required` khi đăng video. Service
tự đổi user token thành Page access token một lần (cần quyền
`pages_show_list`) rồi mới upload. Nếu muốn dùng Page token dựng sẵn, đặt thêm
`"page_token_env": "TÊN_BIẾN"` trong `facebook` của `Config/publish.json`.

Bài đăng Facebook gồm: mô tả gốc của bài (`des`) + hashtag + dòng
`Nguồn: <source_url>`.

### YouTube

YouTube dùng YouTube Data API v3 (`videos.insert` + resumable upload) và OAuth2
**refresh token** (không dán access token tay mỗi lần).

1. Tạo project Google Cloud, bật **YouTube Data API v3**.
2. Tạo OAuth client loại **Desktop app**, tải file JSON về
   `yt-secret/client_secret.json` (thư mục này đã nằm trong `.gitignore`).
3. **Chạy một lần** trên máy có trình duyệt để lấy refresh token:

   ```powershell
   python youtube_auth.py
   ```

   Script mở trình duyệt, xin quyền `youtube.upload` + `youtube.readonly`, rồi
   ghi `yt-secret/token.json` và cập nhật `YT_CLIENT_ID`, `YT_CLIENT_SECRET`,
   `YT_REFRESH_TOKEN` trong `.env`. Không cần tự dán token.

   - Máy không có trình duyệt: `python youtube_auth.py --no-browser` rồi dán
     URL in ra vào trình duyệt.
   - Đã có token nhưng muốn cấp lại: `python youtube_auth.py --force`.
   - Chỉ ghi token file, không sửa `.env`: `--no-env`.
4. Bật YouTube: `"platforms": ["youtube"]` hoặc `["facebook", "youtube"]`,
   rồi đặt `"mode": "publish"`.

Nếu `YT_REFRESH_TOKEN` trống (hoặc không giống token Google), service đọc refresh
token từ `youtube.token_file` (mặc định `yt-secret/token.json`) do script trên
tạo.

Cấu hình trong khối `youtube`:

| Khoá | Ý nghĩa |
| --- | --- |
| `privacy_status` | `unlisted` (mặc định), `public`, `private` |
| `category_id` | Mã danh mục YouTube; `25` = News & Politics |
| `check_existing` | Tra video cùng tiêu đề trên kênh trước khi đăng |
| `self_declared_made_for_kids` | Khai báo nội dung dành cho trẻ em |
| `token_file` | File refresh token dự phòng khi env trống |
| `max_file_bytes` | Giới hạn file (mặc định 256 GB) |
| `timeout_seconds` | Timeout mỗi request upload |


Video dùng chung tiêu đề/mô tả/hashtag của pipeline. Hashtag được bỏ dấu `#`
thành `tags`; tiêu đề cắt ở 100 ký tự, mô tả cắt ở 5000 ký tự, tổng `tags` tối
đa 500 ký tự (giới hạn của YouTube).

Lưu ý quota: `check_existing` gọi `search.list` (100 đơn vị quota/lần, hạn mức
mặc định 10.000/ngày). Chạy nhiều bài/ngày thì có thể tắt.

Upload không được retry trong cùng một lần chạy (tránh tạo video trùng khi phản
hồi bị mất). Nếu lỗi giữa đường, chạy lại pipeline: bước `publish` tự tra tiêu
đề trước khi đăng lại.

### Chế độ

Các chế độ: `"prepare"` (ghi bản kê), `"publish"` (đăng thật), `"off"` (tắt hẳn).

Đổi `mode` hoặc danh sách nền tảng làm thay đổi "dấu vân tay" đầu vào của bước,
nên state cũ sẽ tự động chạy lại bước `publish` (không đăng trùng nhờ tra tiêu đề
đã có).

## Test

```powershell
python -m pytest
```

Test không gọi mạng: LLM, RSS, TTS, ảnh và API mạng xã hội đều được giả lập.
