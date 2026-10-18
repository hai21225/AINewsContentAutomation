import os
import urllib.parse

import requests

from Errors import (
    ConfigurationError,
    ExternalServiceError,
    is_transient_status,
)
from ImageGeneration.BaseImageClient import BaseImageClient
from Redaction import redact
from Retry import RetryPolicy, retry_after_seconds


DEFAULT_BASE_URL = "https://gen.pollinations.ai/image"
DEFAULT_TIMEOUT_SECONDS = 180
DEFAULT_WIDTH = 1920
DEFAULT_HEIGHT = 1080
DEFAULT_SEED = 42
DEFAULT_MAX_ATTEMPTS = 1
DEFAULT_BACKOFF_SECONDS = 2.0
DEFAULT_MAX_BACKOFF_SECONDS = 15.0
ERROR_BODY_PREVIEW = 300


class PollinationsImageClient(BaseImageClient):
    """Sinh ảnh qua Pollinations image API.

    Trả về bytes ảnh; lớp gọi (``ImageService``) chịu trách nhiệm kiểm tra
    định dạng và lưu file.
    """

    provider_name = "pollinations"

    def __init__(self, provider_config, model_config, log_service=None,
                 session=None, sleep=None, retry_config=None):
        self._log = log_service
        self._provider_config = provider_config
        self._model_config = model_config

        self._base_url = str(
            provider_config.get("base_url", DEFAULT_BASE_URL)
        ).rstrip("/")

        self._api_key_env = provider_config.get("api_key_env", "POLLINATIONS_API_KEY")
        self._api_key = os.getenv(self._api_key_env)

        self._model = model_config.get("model")

        if not self._model:
            raise ConfigurationError(
                "Thiếu khoá cấu hình 'model' cho image provider pollinations."
            )

        self._options = model_config.get("options") or {}
        self._timeout = int(
            self._options.get(
                "timeout",
                provider_config.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS),
            )
        )
        self._session = session or requests.Session()

        # Mặc định không thử lại; bật bằng retry_config chung hoặc cấu hình
        # riêng của provider/model (ví dụ Config/Image/models.json).
        self._retry = RetryPolicy.from_config(
            {
                "max_attempts": DEFAULT_MAX_ATTEMPTS,
                "backoff_seconds": DEFAULT_BACKOFF_SECONDS,
                "max_backoff_seconds": DEFAULT_MAX_BACKOFF_SECONDS,
            },
            retry_config,
            provider_config,
            model_config,
            sleep=sleep,
        )

        if not self._api_key:
            raise ConfigurationError(
                f"Thiếu biến môi trường {self._api_key_env}."
            )

    @property
    def retry_policy(self):
        return self._retry

    def generate(self, prompt, **options):
        if not prompt or not prompt.strip():
            raise ValueError("Prompt không được để trống.")

        params_config = {**self._options, **options}

        params = {
            "model": self._model,
            "width": params_config.get("width", DEFAULT_WIDTH),
            "height": params_config.get("height", DEFAULT_HEIGHT),
            "seed": params_config.get("seed", DEFAULT_SEED),
            "key": self._api_key,
        }
        params = {
            key: value for key, value in params.items() if value is not None
        }

        timeout = params_config.get("timeout", self._timeout)

        encoded_prompt = urllib.parse.quote(prompt.strip(), safe="")
        url = f"{self._base_url}/{encoded_prompt}"

        return self._retry.run(
            lambda: self._fetch(url, params, timeout),
            event_name="image_retry",
            description="Pollinations image",
            service=self.provider_name,
            fields={"model": self._model},
            log=self._log,
        )

    def _fetch(self, url, params, timeout):
        """Một lần gọi API; raise ExternalServiceError khi lỗi."""
        try:
            response = self._session.get(url, params=params, timeout=timeout)
        except requests.RequestException as error:
            # Không đưa URL vào thông báo: URL chứa API key trong query string.
            raise ExternalServiceError(
                f"Không gọi được Pollinations: {type(error).__name__}.",
                service=self.provider_name,
                model=self._model,
                retryable=True,
            ) from error

        if response.status_code >= 400:
            raise ExternalServiceError(
                f"Pollinations trả về HTTP {response.status_code}: "
                f"{self._body_preview(response)}",
                service=self.provider_name,
                model=self._model,
                status=response.status_code,
                retryable=is_transient_status(response.status_code),
                retry_after=retry_after_seconds(response.headers),
            )

        content_type = response.headers.get("Content-Type", "").lower()

        if not content_type.startswith("image/"):
            raise ExternalServiceError(
                "Pollinations không trả về dữ liệu ảnh. "
                f"Content-Type: {content_type or '(trống)'}; "
                f"Body: {self._body_preview(response)}",
                service=self.provider_name,
                model=self._model,
                retryable=True,
            )

        if not response.content:
            raise ExternalServiceError(
                "Pollinations trả về dữ liệu ảnh rỗng.",
                service=self.provider_name,
                model=self._model,
                retryable=True,
            )

        self._log_info(
            "image_generated",
            bytes=len(response.content),
            content_type=content_type,
        )

        return response.content

    @staticmethod
    def _body_preview(response):
        try:
            body = response.text
        except Exception:  # pragma: no cover - body không decode được
            return "(không đọc được body)"
        body = " ".join(body.split())
        return redact(body[:ERROR_BODY_PREVIEW])

    def _log_info(self, event, message="", **fields):
        if self._log is not None:
            self._log.info(
                event,
                message,
                provider=self.provider_name,
                model=self._model,
                **fields,
            )
