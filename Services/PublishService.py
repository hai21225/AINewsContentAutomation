"""Chuẩn bị và đăng video cuối lên nền tảng mạng xã hội (bước ``publish``).

Nguyên tắc (bám yêu cầu của skill về publish):

- **Đăng bài là tác dụng phụ.** Mặc định ``mode = "prepare"`` chỉ ghi bản kê
  (manifest) ra ``output/publish/<article_id>.json`` để xem/đăng tay, KHÔNG gọi
  mạng. Muốn đăng thật phải bật ``mode = "publish"``.
- **Không tự nhận đã đăng**: chỉ đặt ``status = "published"`` khi API trả về
  ``id``. Thiếu id -> lỗi, không đoán.
- **Chống đăng trùng**: trước khi POST, tra các video gần đây của Page và bỏ qua
  nếu đã có video cùng tiêu đề (dùng khi lần chạy trước đã đăng nhưng bị lỗi
  giữa đường).
- **Không retry lệnh POST**: upload là tác dụng phụ, thử lại có thể tạo bài
  trùng. Chỉ retry bước tra cứu (GET); POST lỗi thì để lần chạy resume xử lý
  tiếp (kèm tra trùng ở trên).

Nền tảng hiện hỗ trợ: ``facebook`` (Graph API, đăng video lên Page).

Vì sao dùng ``source`` (multipart) thay vì Resumable Upload API::

    Tài liệu Page Videos (Graph v26.0) liệt kê tham số ``source`` — "The video,
    encoded as form data. This field is required." — dùng trực tiếp Page access
    token, chỉ cần một lần ``POST /{page_id}/videos``. Resumable Upload còn cần
    thêm App ID + User access token (``/{app_id}/uploads``), nhiều bước và nhiều
    credential hơn. Video của pipeline chỉ vài MB nên multipart là đủ; file vượt
    ``max_file_bytes`` sẽ báo lỗi rõ để chuyển sang Resumable Upload.
"""

import json
import os
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

SUPPORTED_PLATFORMS = ("facebook",)
MODES = ("prepare", "publish", "off")

#: Tiêu đề Facebook trả về có thể bị cắt; so khớp sau khi chuẩn hoá khoảng trắng.
MAX_EXISTING_LOOKUP = 25


class PublishService:
    """Ghi bản kê và (tuỳ chế độ) đăng video cuối lên Page Facebook."""

    def __init__(self, config=None, output_dir=None, log_service=None,
                 session=None, sleep=None, retry_config=None,
                 page_id=None, access_token=None, environ=None):
        config = config or {}

        self._config = config
        self._platform = config.get("platform", DEFAULT_PLATFORM)

        if self._platform not in SUPPORTED_PLATFORMS:
            raise ConfigurationError(
                f"Nền tảng publish chưa được hỗ trợ: {self._platform}. "
                f"Hiện có: {', '.join(SUPPORTED_PLATFORMS)}."
            )

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

        self._session = session or requests.Session()
        self._retry = RetryPolicy.from_config(retry_config or {}, sleep=sleep)

    # ------------------------------------------------------------------
    # Thuộc tính / khả dụng
    # ------------------------------------------------------------------

    @property
    def platform(self):
        return self._platform

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

        return bool(self._page_id and self._access_token)

    def ensure_configured(self):
        """Raise ``ConfigurationError`` nếu chế độ ``publish`` mà thiếu credential."""
        if self.is_configured():
            return

        missing = []

        if not self._page_id:
            missing.append(self._page_id_env)

        if not self._access_token:
            missing.append(self._access_token_env)

        raise ConfigurationError(
            "Thiếu thông tin đăng bài Facebook: "
            + ", ".join(missing)
            + ". Đặt biến môi trường này trong .env (khi dùng mode 'publish')."
        )

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
            description=None, duration_seconds=None):
        """Ghi bản kê; ở chế độ ``publish`` thì đăng thật lên Page.

        Args:
            article_id: id bài viết (dùng làm tên file bản kê).
            video_path: video cuối do ``MediaService`` tạo ra.
            title: tiêu đề video; thiếu thì lấy tên file video.
            hashtags: danh sách hashtag có sẵn từ bước ``write``.
            description: phần mô tả/bài đăng; thiếu thì ghép từ hashtag.
            duration_seconds: thời lượng video (chỉ để ghi vào bản kê).

        Returns:
            dict: bản kê kèm ``manifest_path``.
        """
        self._require_article_id(article_id)
        video = self._validate_video(video_path)

        title = self._clean_text(title) or video.stem
        hashtags = self._clean_hashtags(hashtags)
        description = self._clean_text(description) or " ".join(hashtags)

        record = {
            "article_id": article_id,
            "platform": self._platform,
            "mode": self._mode,
            "status": "prepared",
            "title": title,
            "description": description,
            "hashtags": hashtags,
            "video_path": str(video),
            "video_bytes": video.stat().st_size,
            "video_duration_seconds": duration_seconds,
            "page_id": self._page_id if self._mode == "publish" else None,
            "post_id": None,
            "post_url": None,
            "deduplicated": False,
            "prepared_at": _now(),
            "published_at": None,
        }

        if self._mode == "publish":
            record.update(self._publish(record))

        manifest = self._write_manifest(article_id, record)

        self._log_info(
            "publish_recorded",
            f"Bài {article_id}: publish '{record['status']}' ({self._mode}).",
            article_id=article_id,
            platform=self._platform,
            mode=self._mode,
            status=record["status"],
            post_id=record["post_id"],
        )

        return {**record, "manifest_path": str(manifest)}

    # ------------------------------------------------------------------
    # Đăng thật (Facebook)
    # ------------------------------------------------------------------

    def _publish(self, record):
        self.ensure_configured()

        if self._check_existing:
            existing = self._find_existing(record["title"])

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
                    "post_url": self._post_url(existing),
                    "deduplicated": True,
                    "published_at": _now(),
                }

        post_id = self._upload(record)

        return {
            "status": "published",
            "post_id": post_id,
            "post_url": self._post_url(post_id),
            "deduplicated": False,
            "published_at": _now(),
        }

    def _find_existing(self, title):
        """Tìm video cùng tiêu đề trên Page; trả id hoặc None."""
        url = f"{self._graph_url}/{self._api_version}/{self._page_id}/videos"
        params = {
            "fields": "id,title,created_time",
            "limit": MAX_EXISTING_LOOKUP,
            "access_token": self._access_token,
        }

        response = self._retry.run(
            lambda: self._request("GET", url, params=params),
            event_name="publish_lookup_retry",
            description="tra video đã đăng trên Page",
            service=self._platform,
            log=self._log,
        )

        for item in self._decode(response).get("data") or []:
            existing = str(item.get("title") or "").strip()

            if existing and existing == title:
                return str(item.get("id"))

        return None

    def _upload(self, record):
        """POST video lên Page; trả về id do Facebook xác nhận."""
        url = (
            f"{self._graph_video_url}/{self._api_version}/"
            f"{self._page_id}/videos"
        )
        data = {
            "access_token": self._access_token,
            "title": record["title"],
            "description": record["description"],
            "published": "true" if self._published else "false",
        }

        video = Path(record["video_path"])

        # KHÔNG retry: upload là tác dụng phụ, thử lại có thể tạo bài trùng.
        with open(video, "rb") as handle:
            response = self._request(
                "POST",
                url,
                data=data,
                files={"source": (video.name, handle, "video/mp4")},
            )

        post_id = self._decode(response).get("id")

        if not post_id:
            raise ExternalServiceError(
                "Facebook không trả về id video sau khi đăng.",
                service=self._platform,
            )

        return str(post_id)

    def _post_url(self, post_id):
        return f"https://www.facebook.com/{post_id}"

    # ------------------------------------------------------------------
    # HTTP
    # ------------------------------------------------------------------

    def _request(self, method, url, **kwargs):
        """Một lần gọi HTTP; raise ``ExternalServiceError`` có cờ retryable."""
        call = self._session.get if method == "GET" else self._session.post

        try:
            response = call(url, timeout=self._timeout, **kwargs)
        except requests.RequestException as error:
            raise ExternalServiceError(
                f"Không gọi được {url}: {type(error).__name__}.",
                service=self._platform,
                retryable=True,
            ) from error

        if response.status_code < 400:
            return response

        raise ExternalServiceError(
            f"HTTP {response.status_code} khi gọi {url}: "
            f"{self._error_message(response)}".strip(),
            service=self._platform,
            status=response.status_code,
            retryable=is_transient_status(response.status_code),
            retry_after=retry_after_seconds(response.headers),
        )

    @staticmethod
    def _decode(response):
        try:
            payload = response.json()
        except ValueError as error:
            raise ExternalServiceError(
                "Phản hồi Facebook không phải JSON hợp lệ.",
                service="facebook",
            ) from error

        if not isinstance(payload, dict):
            raise ExternalServiceError(
                "Phản hồi Facebook không phải object JSON.",
                service="facebook",
            )

        return payload

    @staticmethod
    def _error_message(response):
        """Rút thông báo lỗi người đọc được từ payload Graph API."""
        try:
            payload = response.json()
        except ValueError:
            return (response.text or "").strip()[:200]

        error = payload.get("error") if isinstance(payload, dict) else None

        if not isinstance(error, dict):
            return ""

        message = str(error.get("message") or "").strip()
        code = error.get("code")

        if message and code is not None:
            return f"{message} (code {code})"

        return message

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
                f"Facebook chỉ nhận video .mp4, nhận được: {path.name}"
            )

        size = path.stat().st_size

        if size > self._max_file_bytes:
            raise ValidationError(
                f"Video {size} bytes vượt giới hạn upload một lần "
                f"({self._max_file_bytes} bytes); cần dùng Resumable Upload API."
            )

        return path

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


def _remove_quietly(path):
    try:
        Path(path).unlink(missing_ok=True)
    except OSError:  # pragma: no cover - không xoá được thì bỏ qua
        pass
