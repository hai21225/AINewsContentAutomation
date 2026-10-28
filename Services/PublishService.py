"""Chuẩn bị và đăng video cuối lên nền tảng mạng xã hội (bước ``publish``).

Nguyên tắc (bám yêu cầu của skill về publish):

- **Đăng bài là tác dụng phụ.** Mặc định ``mode = "prepare"`` chỉ ghi bản kê
  (manifest) ra ``output/publish/<article_id>.json`` để xem/đăng tay, KHÔNG gọi
  mạng. Muốn đăng thật phải bật ``mode = "publish"``.
- **Không tự nhận đã đăng**: chỉ đặt ``status = "published"`` khi API trả về
  ``id``. Thiếu id -> lỗi, không đoán.
- **Chống đăng trùng**: trước khi upload, tra các video gần đây của Page/kênh
  và bỏ qua nếu đã có video cùng tiêu đề (dùng khi lần chạy trước đã đăng
  nhưng bị lỗi giữa đường).
- **Không retry bước upload**: upload là tác dụng phụ, thử lại có thể tạo bài
  trùng. Chỉ retry bước tra cứu (GET), lấy token và khởi tạo phiên upload;
  upload lỗi thì để lần chạy resume xử lý tiếp (kèm tra trùng ở trên).

Nền tảng hiện hỗ trợ: ``facebook`` (Graph API, đăng video lên Page) và
``youtube`` (YouTube Data API v3, đăng lên kênh của chủ refresh token).

Cấu hình một hay nhiều nền tảng::

    "platform": "facebook"                  # kiểu cũ: đúng một nền tảng
    "platforms": ["facebook", "youtube"]    # ưu tiên nếu có

Mỗi nền tảng có kết quả riêng trong ``record["results"]``; các trường cấp cao
nhất (``post_id``, ``post_url``, ``deduplicated``) giữ nguyên nghĩa cũ và phản
ánh nền tảng đầu tiên, để bản kê cũ vẫn đọc được.

Vì sao Facebook dùng ``source`` (multipart) thay vì Resumable Upload API::

    Tài liệu Page Videos (Graph v26.0) liệt kê tham số ``source`` — "The video,
    encoded as form data. This field is required." — dùng trực tiếp Page access
    token, chỉ cần một lần ``POST /{page_id}/videos``. Resumable Upload còn cần
    thêm App ID + User access token (``/{app_id}/uploads``), nhiều bước và nhiều
    credential hơn. Video của pipeline chỉ vài MB nên multipart là đủ; file vượt
    ``max_file_bytes`` sẽ báo lỗi rõ để chuyển sang Resumable Upload.

YouTube thì ngược lại: ``videos.insert`` bắt buộc dùng resumable upload cho file
lớn. Một lần đăng gồm 2 bước: ``POST`` khởi tạo phiên (trả ``Location``) rồi
``PUT`` toàn bộ file vào ``Location`` đó. ``PUT`` không retry trong cùng lần
chạy; nếu phản hồi bị mất, lần chạy sau sẽ tra tiêu đề trước khi đăng lại.
"""

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

from Errors import (
    ConfigurationError,
    ExternalServiceError,
    MediaError,
    ValidationError,
    is_transient_status,
)
from Retry import RetryPolicy, retry_after_seconds

DEFAULT_PLATFORM = "facebook"
DEFAULT_MODE = "prepare"
DEFAULT_OUTPUT_DIR = "output/publish"
DEFAULT_API_VERSION = "v26.0"

#: Đăng video dùng host riêng (graph-video); tra cứu dùng host thường.
DEFAULT_GRAPH_URL = "https://graph.facebook.com"
DEFAULT_GRAPH_VIDEO_URL = "https://graph-video.facebook.com"

DEFAULT_TIMEOUT_SECONDS = 600

#: Ngưỡng upload một lần. Lớn hơn nên dùng Resumable Upload API.
DEFAULT_MAX_FILE_BYTES = 1024 ** 3

#: YouTube: endpoint OAuth2 và Data API v3.
DEFAULT_YT_TOKEN_URL = "https://oauth2.googleapis.com/token"
DEFAULT_YT_API_URL = "https://www.googleapis.com/youtube/v3"
DEFAULT_YT_UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"

#: 25 = News & Politics (mã danh mục mặc định của YouTube).
DEFAULT_YT_CATEGORY_ID = "25"

#: Mặc định để ``unlisted`` cho an toàn: video có trên kênh nhưng không công
#: khai. Đổi thành ``public`` trong Config/publish.json khi đã kiểm tra kỹ.
DEFAULT_YT_PRIVACY_STATUS = "unlisted"

#: YouTube cho phép video tới 256 GB hoặc 12 giờ (tuỳ điều kiện nào tới trước).
DEFAULT_YT_MAX_FILE_BYTES = 256 * 1024 ** 3

YOUTUBE_PRIVACY_STATUSES = ("public", "unlisted", "private")
YOUTUBE_TITLE_LIMIT = 100
YOUTUBE_DESCRIPTION_LIMIT = 5000
YOUTUBE_TAG_LIMIT = 100
YOUTUBE_TAGS_TOTAL_LIMIT = 500

SUPPORTED_PLATFORMS = ("facebook", "youtube")
MODES = ("prepare", "publish", "off")

_SERVICE_LABELS = {"facebook": "Facebook", "youtube": "YouTube"}

#: Tiêu đề Facebook trả về có thể bị cắt; so khớp sau khi chuẩn hoá khoảng trắng.
MAX_EXISTING_LOOKUP = 25


class PublishService:
    """Ghi bản kê và (tuỳ chế độ) đăng video cuối lên Facebook và/hoặc YouTube."""

    def __init__(self, config=None, output_dir=None, log_service=None,
                 session=None, sleep=None, retry_config=None,
                 page_id=None, access_token=None, client_id=None,
                 client_secret=None, refresh_token=None, environ=None):
        config = config or {}

        self._config = config
        self._platforms = self._parse_platforms(config)

        self._mode = config.get("mode", DEFAULT_MODE)

        if self._mode not in MODES:
            raise ConfigurationError(
                f"Chế độ publish không hợp lệ: {self._mode}. "
                f"Hợp lệ: {', '.join(MODES)}."
            )

        self._output_dir = Path(
            output_dir or config.get("output_dir", DEFAULT_OUTPUT_DIR)
        )
        self._log = log_service
        self._environ = os.environ if environ is None else environ

        facebook = config.get("facebook", {}) or {}
        self._facebook = facebook
        self._api_version = facebook.get("api_version", DEFAULT_API_VERSION)
        self._graph_url = str(
            facebook.get("graph_url", DEFAULT_GRAPH_URL)
        ).rstrip("/")
        self._graph_video_url = str(
            facebook.get("graph_video_url", DEFAULT_GRAPH_VIDEO_URL)
        ).rstrip("/")
        self._page_id_env = facebook.get("page_id_env", "FB_PAGE_ID")
        self._access_token_env = facebook.get(
            "access_token_env", "FB_PAGE_ACCESS_TOKEN"
        )
        #: Tuỳ chọn: biến môi trường chứa Page access token dựng sẵn.
        #: Không đặt thì service tự đổi user token -> Page token (một lần).
        self._page_token_env = facebook.get("page_token_env")
        self._published = bool(facebook.get("published", True))
        self._check_existing = bool(facebook.get("check_existing", True))
        self._max_file_bytes = int(
            facebook.get("max_file_bytes", DEFAULT_MAX_FILE_BYTES)
        )
        self._timeout = float(
            facebook.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS)
        )

        self._page_id = page_id or self._read_env(self._page_id_env)
        self._access_token = access_token or self._read_env(
            self._access_token_env
        )
        self._page_token_cache = None
        self._page_token_checked = False

        youtube = config.get("youtube", {}) or {}
        self._youtube = youtube
        self._yt_client_id_env = youtube.get(
            "client_id_env", "YT_CLIENT_ID"
        )
        self._yt_client_secret_env = youtube.get(
            "client_secret_env", "YT_CLIENT_SECRET"
        )
        self._yt_refresh_token_env = youtube.get(
            "refresh_token_env", "YT_REFRESH_TOKEN"
        )
        #: Tuỳ chọn: file token do ``youtube_auth.py`` ghi; dùng khi env trống.
        self._yt_token_file = youtube.get("token_file")
        self._yt_token_url = str(
            youtube.get("token_url", DEFAULT_YT_TOKEN_URL)
        )
        self._yt_api_url = str(
            youtube.get("api_url", DEFAULT_YT_API_URL)
        ).rstrip("/")
        self._yt_upload_url = str(
            youtube.get("upload_url", DEFAULT_YT_UPLOAD_URL)
        ).rstrip("/")
        self._yt_privacy_status = str(
            youtube.get("privacy_status", DEFAULT_YT_PRIVACY_STATUS)
        ).strip().lower()

        if self._yt_privacy_status not in YOUTUBE_PRIVACY_STATUSES:
            raise ConfigurationError(
                "youtube.privacy_status không hợp lệ: "
                f"{self._yt_privacy_status}. "
                f"Hợp lệ: {', '.join(YOUTUBE_PRIVACY_STATUSES)}."
            )

        self._yt_category_id = str(
            youtube.get("category_id", DEFAULT_YT_CATEGORY_ID)
        )
        self._yt_made_for_kids = bool(
            youtube.get("self_declared_made_for_kids", False)
        )
        self._yt_check_existing = bool(youtube.get("check_existing", True))
        self._yt_max_file_bytes = int(
            youtube.get("max_file_bytes", DEFAULT_YT_MAX_FILE_BYTES)
        )
        self._yt_timeout = float(
            youtube.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS)
        )

        self._yt_client_id = client_id or self._read_env(
            self._yt_client_id_env
        )
        self._yt_client_secret = client_secret or self._read_env(
            self._yt_client_secret_env
        )
        self._yt_refresh_token = refresh_token or self._read_env(
            self._yt_refresh_token_env
        )

        if self._yt_token_file and "youtube" in self._platforms:
            file_token = self._read_youtube_token_file(self._yt_token_file)

            if file_token and not _looks_like_refresh_token(
                self._yt_refresh_token
            ):
                if self._yt_refresh_token:
                    self._log_warning(
                        "publish_youtube_env_token_suspicious",
                        "Giá trị refresh token trong biến môi trường không "
                        "giống token Google; dùng token trong file.",
                        path=str(self._yt_token_file),
                    )

                self._yt_refresh_token = file_token

        self._yt_token_cache = None
        self._yt_token_expiry = 0.0

        if (
            "youtube" in self._platforms
            and self._yt_refresh_token
            and not _looks_like_refresh_token(self._yt_refresh_token)
        ):
            self._log_warning(
                "publish_youtube_token_suspicious",
                "Refresh token YouTube không đúng định dạng của Google; "
                "chạy 'python youtube_auth.py --force' trên máy có trình duyệt.",
            )

        self._session = session or requests.Session()
        self._retry = RetryPolicy.from_config(retry_config or {}, sleep=sleep)

    # ------------------------------------------------------------------
    # Thuộc tính / khả dụng
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_platforms(config):
        """Đọc ``platforms`` (ưu tiên) hoặc ``platform`` kiểu cũ."""
        platforms = config.get("platforms")

        if platforms is None:
            platforms = config.get("platform", DEFAULT_PLATFORM)

        if isinstance(platforms, str):
            platforms = [platforms]

        if not isinstance(platforms, (list, tuple)) or not platforms:
            raise ConfigurationError(
                "Cấu hình publish cần 'platform' hoặc 'platforms' không rỗng."
            )

        cleaned = []

        for item in platforms:
            if not isinstance(item, str) or not item.strip():
                raise ConfigurationError(
                    f"Tên nền tảng publish không hợp lệ: {item!r}."
                )

            name = item.strip().lower()

            if name not in SUPPORTED_PLATFORMS:
                raise ConfigurationError(
                    f"Nền tảng publish chưa được hỗ trợ: {name}. "
                    f"Hiện có: {', '.join(SUPPORTED_PLATFORMS)}."
                )

            if name not in cleaned:
                cleaned.append(name)

        return tuple(cleaned)

    @property
    def platform(self):
        """Tên nền tảng đang bật; nhiều nền tảng thì nối bằng dấu phẩy."""
        return ",".join(self._platforms)

    @property
    def platforms(self):
        return self._platforms

    @property
    def mode(self):
        return self._mode

    @property
    def output_dir(self):
        return self._output_dir

    @property
    def api_version(self):
        return self._api_version

    def is_configured(self):
        """True nếu đủ thông tin để chạy đúng chế độ hiện tại."""
        if self._mode != "publish":
            return True

        return not self._missing_credentials()

    def ensure_configured(self):
        """Raise ``ConfigurationError`` nếu chế độ ``publish`` mà thiếu credential."""
        if self._mode != "publish":
            return

        missing = self._missing_credentials()

        if not missing:
            return

        raise ConfigurationError(
            f"Thiếu thông tin đăng bài ({', '.join(self._platforms)}): "
            + ", ".join(missing)
            + ". Đặt biến môi trường này trong .env (khi dùng mode 'publish')."
        )

    def _missing_credentials(self):
        """Danh sách tên biến môi trường còn thiếu, theo từng nền tảng."""
        missing = []

        if "facebook" in self._platforms:
            if not self._page_id:
                missing.append(self._page_id_env)

            if not self._access_token:
                missing.append(self._access_token_env)

        if "youtube" in self._platforms:
            if not self._yt_client_id:
                missing.append(self._yt_client_id_env)

            if not self._yt_client_secret:
                missing.append(self._yt_client_secret_env)

            if not self._yt_refresh_token:
                missing.append(self._yt_refresh_token_env)

        return missing

    def _read_youtube_token_file(self, path):
        """Đọc refresh token từ file do ``youtube_auth.py`` tạo."""
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            self._log_warning(
                "publish_youtube_token_file_unreadable",
                "Không đọc được file token YouTube.",
                path=str(path),
                error=type(error).__name__,
            )
            return None

        token = data.get("refresh_token") if isinstance(data, dict) else None

        if isinstance(token, str) and token.strip():
            return token.strip()

        self._log_warning(
            "publish_youtube_token_file_invalid",
            "File token YouTube không có refresh_token.",
            path=str(path),
        )
        return None

    # ------------------------------------------------------------------
    # Facebook: đổi token
    # ------------------------------------------------------------------

    def _resolve_page_token(self):
        """Lấy Page access token để đăng bài (cache trong tiến trình).

        Facebook chỉ cho đăng nội dung **thay mặt Page** bằng Page token. Nếu
        ``PAGE_ACCESS_TOKEN`` là user token (trường hợp phổ biến khi lấy token
        trong Graph API Explorer), service tự đổi sang Page token nhờ quyền
        ``pages_show_list``. Trả ``None`` nếu không lấy được (khi đó dùng lại
        token đã cấu hình).
        """
        if self._page_token_checked:
            return self._page_token_cache

        self._page_token_checked = True
        explicit = (
            self._read_env(self._page_token_env) if self._page_token_env else None
        )

        if explicit:
            self._page_token_cache = explicit
            return explicit

        if not (self._page_id and self._access_token):
            return None

        url = f"{self._graph_url}/{self._api_version}/{self._page_id}"
        params = {"fields": "access_token", "access_token": self._access_token}

        try:
            response = self._retry.run(
                lambda: self._request(
                    "GET", url, service="facebook", params=params
                ),
                event_name="publish_page_token_retry",
                description="đổi Page access token",
                service="facebook",
                log=self._log,
            )
            token = self._decode(response, "facebook").get("access_token")
        except ExternalServiceError as error:
            self._log_warning(
                "publish_page_token_failed",
                "Không đổi được user token thành Page token.",
                error=str(error)[:200],
            )
            return None

        if not isinstance(token, str) or not token.strip():
            return None

        self._log_info(
            "publish_page_token_resolved",
            "Đã đổi user token thành Page access token.",
            page_id=self._page_id,
        )
        self._page_token_cache = token.strip()
        return self._page_token_cache

    # ------------------------------------------------------------------
    # Đường dẫn
    # ------------------------------------------------------------------

    def manifest_path(self, article_id):
        """Đường dẫn bản kê của một bài (chống path traversal)."""
        self._require_article_id(article_id)
        return self._output_dir / f"{article_id}.json"

    # ------------------------------------------------------------------
    # Chạy một bài
    # ------------------------------------------------------------------

    def run(self, article_id, video_path, title=None, hashtags=None,
            description=None, duration_seconds=None, source_url=None):
        """Ghi bản kê; ở chế độ ``publish`` thì đăng thật lên các nền tảng.

        Args:
            article_id: id bài viết (dùng làm tên file bản kê).
            video_path: video cuối do ``MediaService`` tạo ra.
            title: tiêu đề video; thiếu thì lấy tên file video.
            hashtags: danh sách hashtag có sẵn từ bước ``write``.
            description: phần mô tả/bài đăng (thường là mô tả gốc của bài);
                thiếu thì bài đăng chỉ gồm hashtag.
            duration_seconds: thời lượng video (chỉ để ghi vào bản kê).
            source_url: link bài gốc, được ghi vào bài đăng để dẫn nguồn.

        Returns:
            dict: bản kê kèm ``manifest_path``.

        Raises:
            ExternalServiceError: khi đăng thất bại. Bản kê vẫn được ghi (kèm
                ``results`` từng nền tảng) để lần chạy sau biết nền tảng nào đã
                xong; lỗi của nền tảng duy nhất được ném nguyên trạng.
        """
        self._require_article_id(article_id)
        video = self._validate_video(video_path)

        title = self._clean_text(title) or video.stem
        hashtags = self._clean_hashtags(hashtags)
        source_url = self._clean_text(source_url) or None
        description = self._compose_caption(description, hashtags, source_url)

        record = {
            "article_id": article_id,
            "platform": self.platform,
            "platforms": list(self._platforms),
            "mode": self._mode,
            "status": "prepared",
            "title": title,
            "description": description,
            "source_url": source_url,
            "hashtags": hashtags,
            "video_path": str(video),
            "video_bytes": video.stat().st_size,
            "video_duration_seconds": duration_seconds,
            "page_id": (
                self._page_id
                if "facebook" in self._platforms and self._mode == "publish"
                else None
            ),
            "results": {},
            "post_id": None,
            "post_url": None,
            "deduplicated": False,
            "prepared_at": _now(),
            "published_at": None,
        }

        errors = {}

        if self._mode == "publish":
            errors = self._publish(record)

        manifest = self._write_manifest(article_id, record)

        self._log_info(
            "publish_recorded",
            f"Bài {article_id}: publish '{record['status']}' ({self._mode}).",
            article_id=article_id,
            platform=self.platform,
            mode=self._mode,
            status=record["status"],
            post_id=record["post_id"],
        )

        if errors:
            self._raise_publish_errors(article_id, errors, manifest)

        return {**record, "manifest_path": str(manifest)}

    # ------------------------------------------------------------------
    # Đăng thật
    # ------------------------------------------------------------------

    def _publish(self, record):
        """Đăng lần lượt từng nền tảng; một nền tảng lỗi không chặn nền tảng khác."""
        self.ensure_configured()
        errors = {}

        for platform in self._platforms:
            try:
                if platform == "facebook":
                    outcome = self._publish_facebook(record)
                else:
                    outcome = self._publish_youtube(record)
            except ExternalServiceError as error:
                errors[platform] = error
                record["results"][platform] = {
                    "status": "failed",
                    "post_id": None,
                    "post_url": None,
                    "deduplicated": False,
                    "published_at": None,
                    "error": str(error)[:300],
                }
                self._log_warning(
                    "publish_platform_failed",
                    f"Đăng lên {platform} thất bại.",
                    article_id=record["article_id"],
                    platform=platform,
                    error=str(error)[:200],
                )
                continue

            record["results"][platform] = outcome

            self._log_info(
                "publish_platform_published",
                f"Đã đăng lên {platform}.",
                article_id=record["article_id"],
                platform=platform,
                post_id=outcome.get("post_id"),
                deduplicated=outcome.get("deduplicated"),
            )

        self._summarize(record)
        return errors

    def _summarize(self, record):
        """Gộp kết quả từng nền tảng vào các trường cấp cao nhất của bản kê."""
        results = record["results"]
        published = [
            result for result in results.values()
            if result.get("status") == "published"
        ]

        if len(published) == len(self._platforms):
            record["status"] = "published"
        elif published:
            record["status"] = "partial"
        else:
            record["status"] = "failed"

        #: Trường kiểu cũ: phản ánh nền tảng đầu tiên trong ``platforms``.
        primary = results.get(self._platforms[0], {})
        record["post_id"] = primary.get("post_id")
        record["post_url"] = primary.get("post_url")
        record["deduplicated"] = bool(primary.get("deduplicated"))
        record["published_at"] = primary.get("published_at")

    @staticmethod
    def _raise_publish_errors(article_id, errors, manifest):
        #: Một nền tảng: giữ nguyên lỗi gốc (status/retryable/hint) cho caller.
        if len(errors) == 1:
            raise next(iter(errors.values()))

        detail = "; ".join(
            f"{platform}: {error}" for platform, error in errors.items()
        )

        raise ExternalServiceError(
            f"Bài {article_id}: đăng thất bại trên "
            f"{', '.join(errors)}. {detail} (bản kê: {manifest})",
            service="publish",
            retryable=any(error.retryable for error in errors.values()),
        )

    # ------------------------------------------------------------------
    # Facebook
    # ------------------------------------------------------------------

    def _publish_facebook(self, record):
        if self._check_existing:
            existing = self._find_existing_facebook(record["title"])

            if existing:
                self._log_warning(
                    "publish_duplicate_skipped",
                    "Page đã có video cùng tiêu đề; bỏ qua để không đăng trùng.",
                    title=record["title"],
                    post_id=existing,
                )

                return {
                    "status": "published",
                    "post_id": existing,
                    "post_url": self._facebook_post_url(existing),
                    "deduplicated": True,
                    "published_at": _now(),
                }

        post_id = self._upload_facebook(record)

        return {
            "status": "published",
            "post_id": post_id,
            "post_url": self._facebook_post_url(post_id),
            "deduplicated": False,
            "published_at": _now(),
        }

    def _find_existing_facebook(self, title):
        """Tìm video cùng tiêu đề trên Page; trả id hoặc None."""
        url = f"{self._graph_url}/{self._api_version}/{self._page_id}/videos"
        params = {
            "fields": "id,title,created_time",
            "limit": MAX_EXISTING_LOOKUP,
            "access_token": self._access_token,
        }

        response = self._retry.run(
            lambda: self._request(
                "GET", url, service="facebook", params=params
            ),
            event_name="publish_lookup_retry",
            description="tra video đã đăng trên Page",
            service="facebook",
            log=self._log,
        )

        for item in self._decode(response, "facebook").get("data") or []:
            existing = str(item.get("title") or "").strip()

            if existing and existing == title:
                return str(item.get("id"))

        return None

    def _upload_facebook(self, record):
        """POST video lên Page; trả về id do Facebook xác nhận."""
        url = (
            f"{self._graph_video_url}/{self._api_version}/"
            f"{self._page_id}/videos"
        )
        data = {
            "access_token": self._posting_token(),
            "title": record["title"],
            "description": record["description"],
            "published": "true" if self._published else "false",
        }

        video = Path(record["video_path"])

        return self._post_video(url, data, video)

    def _post_video(self, url, data, video):
        """Một lần upload video; KHÔNG retry để tránh đăng trùng."""
        with open(video, "rb") as handle:
            response = self._request(
                "POST",
                url,
                service="facebook",
                data=data,
                files={"source": (video.name, handle, "video/mp4")},
            )

        post_id = self._decode(response, "facebook").get("id")

        if not post_id:
            raise ExternalServiceError(
                "Facebook không trả về id video sau khi đăng.",
                service="facebook",
            )

        return str(post_id)

    def _posting_token(self):
        """Token dùng để đăng: ưu tiên Page token, không lấy được thì dùng token cấu hình."""
        return self._resolve_page_token() or self._access_token

    @staticmethod
    def _facebook_post_url(post_id):
        return f"https://www.facebook.com/{post_id}"

    # ------------------------------------------------------------------
    # YouTube
    # ------------------------------------------------------------------

    def _publish_youtube(self, record):
        token = self._youtube_access_token()

        if self._yt_check_existing:
            existing = self._find_existing_youtube(token, record["title"])

            if existing:
                self._log_warning(
                    "publish_duplicate_skipped",
                    "Kênh đã có video cùng tiêu đề; bỏ qua để không đăng trùng.",
                    platform="youtube",
                    title=record["title"],
                    post_id=existing,
                )

                return {
                    "status": "published",
                    "post_id": existing,
                    "post_url": self._youtube_post_url(existing),
                    "deduplicated": True,
                    "published_at": _now(),
                }

        video_id = self._upload_youtube(token, record)

        return {
            "status": "published",
            "post_id": video_id,
            "post_url": self._youtube_post_url(video_id),
            "deduplicated": False,
            "published_at": _now(),
        }

    def _youtube_access_token(self):
        """Đổi refresh token -> access token (cache tới gần lúc hết hạn)."""
        if self._yt_token_cache and time.time() < self._yt_token_expiry:
            return self._yt_token_cache

        data = {
            "client_id": self._yt_client_id,
            "client_secret": self._yt_client_secret,
            "refresh_token": self._yt_refresh_token,
            "grant_type": "refresh_token",
        }

        response = self._retry.run(
            lambda: self._request(
                "POST", self._yt_token_url, service="youtube", data=data
            ),
            event_name="publish_youtube_token_retry",
            description="lấy access token YouTube",
            service="youtube",
            log=self._log,
        )

        payload = self._decode(response, "youtube")
        token = payload.get("access_token")

        if not isinstance(token, str) or not token.strip():
            raise ExternalServiceError(
                "Google không trả về access_token khi đổi refresh token.",
                service="youtube",
            )

        try:
            expires_in = float(payload.get("expires_in", 3600))
        except (TypeError, ValueError):
            expires_in = 3600.0

        self._yt_token_cache = token.strip()
        self._yt_token_expiry = time.time() + max(0.0, expires_in - 60)
        self._log_info(
            "publish_youtube_token",
            "Đã lấy access token YouTube.",
            expires_in=expires_in,
        )
        return self._yt_token_cache

    def _find_existing_youtube(self, token, title):
        """Tra video của chính kênh theo tiêu đề; trả videoId hoặc None."""
        url = f"{self._yt_api_url}/search"
        params = {
            "part": "snippet",
            "forMine": "true",
            "type": "video",
            "q": title,
            "maxResults": MAX_EXISTING_LOOKUP,
        }
        headers = {"Authorization": f"Bearer {token}"}

        response = self._retry.run(
            lambda: self._request(
                "GET", url, service="youtube", params=params, headers=headers
            ),
            event_name="publish_youtube_lookup_retry",
            description="tra video đã đăng trên kênh YouTube",
            service="youtube",
            log=self._log,
        )

        for item in self._decode(response, "youtube").get("items") or []:
            snippet = item.get("snippet") or {}
            existing = str(snippet.get("title") or "").strip()

            if not existing or existing != title:
                continue

            identifier = item.get("id") or {}
            video_id = (
                identifier.get("videoId") if isinstance(identifier, dict) else None
            )

            if video_id:
                return str(video_id)

        return None

    def _upload_youtube(self, token, record):
        """Khởi tạo phiên resumable rồi PUT file; trả videoId do YouTube xác nhận."""
        video = Path(record["video_path"])
        size = video.stat().st_size
        body = json.dumps(
            self._youtube_payload(record), ensure_ascii=False
        ).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=UTF-8",
            "X-Upload-Content-Type": "video/mp4",
            "X-Upload-Content-Length": str(size),
        }

        init = self._retry.run(
            lambda: self._request(
                "POST",
                self._yt_upload_url,
                service="youtube",
                params={"uploadType": "resumable", "part": "snippet,status"},
                data=body,
                headers=headers,
            ),
            event_name="publish_youtube_init_retry",
            description="khởi tạo phiên upload YouTube",
            service="youtube",
            log=self._log,
        )

        location = _header(init, "Location")

        if not location:
            raise ExternalServiceError(
                "YouTube không trả về Location cho phiên upload.",
                service="youtube",
            )

        #: PUT là bước tạo video: không retry trong cùng lần chạy.
        response = self._put_video(location, video, size, token)
        video_id = self._decode(response, "youtube").get("id")

        if not video_id:
            raise ExternalServiceError(
                "YouTube không trả về id video sau khi upload.",
                service="youtube",
            )

        return str(video_id)

    def _put_video(self, location, video, size, token):
        with open(video, "rb") as handle:
            return self._request(
                "PUT",
                location,
                service="youtube",
                data=handle,
                headers={
                    "Content-Type": "video/mp4",
                    "Content-Length": str(size),
                    "Authorization": f"Bearer {token}",
                },
                timeout=self._yt_timeout,
            )

    def _youtube_payload(self, record):
        """Body ``videos.insert``: snippet + status, đã cắt theo giới hạn YouTube."""
        title = record["title"]

        if len(title) > YOUTUBE_TITLE_LIMIT:
            self._log_warning(
                "publish_youtube_title_truncated",
                f"Tiêu đề dài hơn {YOUTUBE_TITLE_LIMIT} ký tự, đã cắt bớt.",
                article_id=record["article_id"],
                original_length=len(title),
            )
            title = title[:YOUTUBE_TITLE_LIMIT].rstrip()

        description = record["description"] or ""

        if len(description) > YOUTUBE_DESCRIPTION_LIMIT:
            self._log_warning(
                "publish_youtube_description_truncated",
                f"Mô tả dài hơn {YOUTUBE_DESCRIPTION_LIMIT} ký tự, đã cắt bớt.",
                article_id=record["article_id"],
                original_length=len(description),
            )
            description = description[:YOUTUBE_DESCRIPTION_LIMIT]

        snippet = {
            "title": title,
            "description": description,
            "categoryId": self._yt_category_id,
        }

        tags = self._youtube_tags(record.get("hashtags") or [])

        if tags:
            snippet["tags"] = tags

        return {
            "snippet": snippet,
            "status": {
                "privacyStatus": self._yt_privacy_status,
                "selfDeclaredMadeForKids": self._yt_made_for_kids,
            },
        }

    @staticmethod
    def _youtube_tags(hashtags):
        """Hashtag -> tags của YouTube (bỏ '#', cắt theo giới hạn 100/500 ký tự)."""
        tags = []
        total = 0

        for item in hashtags:
            tag = str(item).lstrip("#").strip()

            if not tag or tag in tags:
                continue

            tag = tag[:YOUTUBE_TAG_LIMIT]

            if total + len(tag) + 1 > YOUTUBE_TAGS_TOTAL_LIMIT:
                break

            tags.append(tag)
            total += len(tag) + 1

        return tags

    @staticmethod
    def _youtube_post_url(video_id):
        return f"https://youtu.be/{video_id}"

    # ------------------------------------------------------------------
    # HTTP
    # ------------------------------------------------------------------

    def _request(self, method, url, *, service=None, timeout=None, **kwargs):
        """Một lần gọi HTTP; raise ``ExternalServiceError`` có cờ retryable."""
        calls = {
            "GET": self._session.get,
            "POST": self._session.post,
            "PUT": self._session.put,
        }
        call = calls.get(method)

        if call is None:  # pragma: no cover - lỗi lập trình, không phải cấu hình
            raise ValueError(f"HTTP method không hỗ trợ: {method}")

        try:
            response = call(
                url,
                timeout=self._timeout if timeout is None else timeout,
                **kwargs,
            )
        except requests.RequestException as error:
            raise ExternalServiceError(
                f"Không gọi được {url}: {type(error).__name__}.",
                service=service,
                retryable=True,
            ) from error

        if response.status_code < 400:
            return response

        detail = self._error_message(response, service)

        if service == "facebook":
            detail += self._permission_hint(response)
        elif service == "youtube":
            detail += self._youtube_hint(response)

        raise ExternalServiceError(
            f"HTTP {response.status_code} khi gọi {url}: {detail}".strip(),
            service=service,
            status=response.status_code,
            retryable=is_transient_status(response.status_code),
            retry_after=retry_after_seconds(response.headers),
        )

    @staticmethod
    def _decode(response, service=None):
        label = _SERVICE_LABELS.get(service, service or "dịch vụ")

        try:
            payload = response.json()
        except ValueError as error:
            raise ExternalServiceError(
                f"Phản hồi {label} không phải JSON hợp lệ.",
                service=service,
            ) from error

        if not isinstance(payload, dict):
            raise ExternalServiceError(
                f"Phản hồi {label} không phải object JSON.",
                service=service,
            )

        return payload

    @staticmethod
    def _error_message(response, service=None):
        """Rút thông báo lỗi người đọc được từ payload Graph API / Google API."""
        try:
            payload = response.json()
        except ValueError:
            return (response.text or "").strip()[:200]

        if not isinstance(payload, dict):
            return ""

        error = payload.get("error")

        if isinstance(error, dict):
            message = str(error.get("message") or "").strip()
            code = error.get("code")

            if message and code is not None:
                return f"{message} (code {code})"

            return message

        if service == "youtube":
            #: OAuth2 endpoint trả {"error": "invalid_grant", "error_description": ...}
            message = str(error or "").strip()
            description = str(payload.get("error_description") or "").strip()

            if message and description:
                return f"{message}: {description}"

            return description or message

        return ""

    @staticmethod
    def _permission_hint(response):
        """Gợi ý sửa lỗi quyền Facebook, trả chuỗi rỗng nếu không phải lỗi quyền."""
        try:
            payload = response.json()
        except ValueError:
            return ""

        error = payload.get("error") if isinstance(payload, dict) else None

        if not isinstance(error, dict):
            return ""

        code = error.get("code")
        message = str(error.get("message") or "").lower()

        if code == 210 or "page access token is required" in message:
            return (
                " Gợi ý: PAGE_ACCESS_TOKEN đang là user token; cần Page access "
                "token (service tự đổi nếu user token có quyền pages_show_list)."
            )

        if "no permission to publish" in message or "publish_actions" in message:
            return (
                " Gợi ý: token thiếu quyền pages_manage_posts; cấp lại quyền "
                "trong Graph API Explorer rồi cập nhật .env."
            )

        return ""

    @staticmethod
    def _youtube_hint(response):
        """Gợi ý sửa lỗi YouTube thường gặp, trả chuỗi rỗng nếu không rõ."""
        try:
            payload = response.json()
        except ValueError:
            return ""

        error = payload.get("error") if isinstance(payload, dict) else None
        text = json.dumps(error, ensure_ascii=False).lower() if error else ""

        if response.status_code == 401 or "invalid_grant" in text:
            return (
                " Gợi ý: refresh token hết hạn/bị thu hồi hoặc chưa có token "
                "thật; chạy 'python youtube_auth.py --force' để lấy lại."
            )

        if "accessnotconfigured" in text or "has not been used" in text:
            return (
                " Gợi ý: bật YouTube Data API v3 cho project Google Cloud "
                "đang dùng."
            )

        if "insufficient" in text or "scope" in text:
            return (
                " Gợi ý: refresh token thiếu quyền youtube.upload "
                "(và youtube.readonly nếu bật check_existing)."
            )

        if response.status_code == 403 and "quota" in text:
            return " Gợi ý: đã hết quota YouTube Data API trong ngày."

        return ""

    # ------------------------------------------------------------------
    # Bản kê
    # ------------------------------------------------------------------

    def _write_manifest(self, article_id, record):
        path = self.manifest_path(article_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(f"{path.name}.part")

        try:
            with open(temp, "w", encoding="utf-8") as file:
                json.dump(record, file, ensure_ascii=False, indent=4)
                file.write("\n")

            temp.replace(path)
        except BaseException:
            _remove_quietly(temp)
            raise

        return path

    # ------------------------------------------------------------------
    # Kiểm tra dữ liệu
    # ------------------------------------------------------------------

    def _validate_video(self, video_path):
        if not video_path:
            raise ValidationError("Thiếu đường dẫn video để đăng.")

        path = Path(video_path)

        if not path.is_file():
            raise MediaError(f"Không tìm thấy video để đăng: {path}")

        if path.stat().st_size == 0:
            raise MediaError(f"Video rỗng, không đăng được: {path}")

        if path.suffix.lower() != ".mp4":
            raise ValidationError(
                f"Chỉ nhận video .mp4 để đăng, nhận được: {path.name}"
            )

        size = path.stat().st_size
        platform, limit = self._upload_limit()

        if size > limit:
            self._raise_size_error(platform, limit, size)

        return path

    def _upload_limit(self):
        """Giới hạn upload nhỏ nhất trong các nền tảng đang bật."""
        limits = []

        if "facebook" in self._platforms:
            limits.append(("facebook", self._max_file_bytes))

        if "youtube" in self._platforms:
            limits.append(("youtube", self._yt_max_file_bytes))

        return min(limits, key=lambda item: item[1])

    @staticmethod
    def _raise_size_error(platform, limit, size):
        if platform == "facebook":
            raise ValidationError(
                f"Video {size} bytes vượt giới hạn upload một lần "
                f"({limit} bytes); cần dùng Resumable Upload API."
            )

        raise ValidationError(
            f"Video {size} bytes vượt giới hạn upload của YouTube "
            f"({limit} bytes)."
        )

    @staticmethod
    def _require_article_id(article_id):
        if not article_id or Path(str(article_id)).name != str(article_id):
            raise ValidationError("id không hợp lệ để tạo tên bản kê publish.")

    @staticmethod
    def _clean_text(value):
        if not isinstance(value, str):
            return ""

        return " ".join(value.split())

    @staticmethod
    def _compose_caption(description, hashtags, source_url=None):
        """Ghép bài đăng cuối: mô tả + hashtag + dòng dẫn nguồn.

        Hashtag chỉ được thêm khi chuỗi hashtag chưa nằm trong mô tả, để bài
        đăng không lặp lại khi mô tả đã có sẵn hashtag.
        """
        parts = []
        text = PublishService._clean_text(description)

        if text:
            parts.append(text)

        tags = " ".join(hashtags or [])

        if tags and tags not in text:
            parts.append(tags)

        if source_url:
            parts.append(f"Nguồn: {source_url}")

        return "\n\n".join(parts)

    @staticmethod
    def _clean_hashtags(hashtags):
        if not isinstance(hashtags, (list, tuple)):
            return []

        cleaned = []

        for item in hashtags:
            if not isinstance(item, str):
                continue

            tag = item.strip()

            if not tag:
                continue

            if not tag.startswith("#"):
                tag = f"#{tag}"

            if tag not in cleaned:
                cleaned.append(tag)

        return cleaned

    def _read_env(self, name):
        value = self._environ.get(name)

        if isinstance(value, str) and value.strip():
            return value.strip()

        return None

    # ------------------------------------------------------------------
    # Log
    # ------------------------------------------------------------------

    def _log_info(self, event, message="", **fields):
        if self._log is not None:
            self._log.info(event, message, **fields)

    def _log_warning(self, event, message="", **fields):
        if self._log is not None:
            self._log.warning(event, message, **fields)


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _looks_like_refresh_token(value):
    """Google refresh token luôn bắt đầu bằng ``1/`` và khá dài."""
    return isinstance(value, str) and value.startswith("1/") and len(value) >= 30


def _header(response, name):
    """Đọc header không phân biệt hoa thường (fake response dùng dict thường)."""
    headers = getattr(response, "headers", None) or {}

    try:
        value = headers.get(name)
    except AttributeError:  # pragma: no cover - headers không phải mapping
        return None

    if value:
        return value

    wanted = name.lower()

    for key, value in headers.items():
        if str(key).lower() == wanted and value:
            return value

    return None


def _remove_quietly(path):
    try:
        Path(path).unlink(missing_ok=True)
    except OSError:  # pragma: no cover - không xoá được thì bỏ qua
        pass
