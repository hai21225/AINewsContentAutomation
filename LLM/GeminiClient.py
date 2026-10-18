import os

from google import genai
from google.genai import errors, types

from Errors import (
    ConfigurationError,
    ExternalServiceError,
    is_transient_status,
)
from LLM.BaseLlmClient import BaseLlmClient
from Retry import RetryPolicy

try:  # httpx là phụ thuộc gián tiếp của google-genai
    import httpx
except ImportError:  # pragma: no cover - chỉ thiếu khi môi trường bị cắt gọt
    httpx = None


DEFAULT_TIMEOUT_SECONDS = 120
DEFAULT_MAX_RETRIES = 2
BASE_BACKOFF_SECONDS = 2.0
MAX_BACKOFF_SECONDS = 8.0


def _strip_unsupported_schema_keys(schema):
    """Loại bỏ các key schema mà Gemini Developer API không hỗ trợ.

    API của Gemini từ chối ``additionalProperties``, trong khi schema dùng
    chung cho nhiều provider vẫn cần key này.
    """
    if isinstance(schema, dict):
        return {
            key: _strip_unsupported_schema_keys(value)
            for key, value in schema.items()
            if key not in ("additionalProperties", "additional_properties")
        }

    if isinstance(schema, list):
        return [_strip_unsupported_schema_keys(item) for item in schema]

    return schema


class GeminiClient(BaseLlmClient):
    """Client gọi Gemini qua Google Gen AI SDK.

    Chịu trách nhiệm: retry có backoff + jitter, tôn trọng ``Retry-After``,
    fallback sang các model dự phòng, và phân loại lỗi tạm thời/vĩnh viễn.
    Việc validate nội dung trả về thuộc về agent.
    """

    provider_name = "gemini"

    @property
    def model(self):
        return self._model

    @property
    def fallback_models(self):
        return list(self._fallback_models)

    @property
    def retry_policy(self):
        return self._retry

    def __init__(self, provider_config, model_config, log_service=None,
                 client=None, sleep=None, retry_config=None):
        self._log = log_service
        self._model = self._require_key(model_config, "model")
        self._options = model_config.get("options") or {}
        self._fallback_models = self._normalize_fallback_models(model_config)
        self._retry = RetryPolicy.from_config(
            {
                "max_attempts": DEFAULT_MAX_RETRIES,
                "backoff_seconds": BASE_BACKOFF_SECONDS,
                "max_backoff_seconds": MAX_BACKOFF_SECONDS,
            },
            retry_config,
            provider_config,
            model_config,
            sleep=sleep,
        )
        self._timeout_seconds = int(
            model_config.get(
                "timeout_seconds",
                provider_config.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS),
            )
        )
        self.last_used_model = None

        if client is not None:
            # Cho phép inject client giả trong test, không cần API key thật.
            self._client = client
            return

        api_key_env = provider_config.get("api_key_env", "GEMINI_API_KEY")
        api_key = os.getenv(api_key_env)

        if not api_key:
            raise ConfigurationError(
                f"Không tìm thấy API key trong biến môi trường '{api_key_env}'."
            )

        self._client = self._build_client(api_key)

    # ------------------------------------------------------------------
    # Khởi tạo
    # ------------------------------------------------------------------

    @staticmethod
    def _require_key(config, key):
        value = config.get(key)
        if not value:
            raise ConfigurationError(
                f"Thiếu khoá cấu hình '{key}' cho provider gemini."
            )
        return value

    def _build_client(self, api_key):
        kwargs = {"api_key": api_key}

        http_options = self._build_http_options()

        if http_options is not None:
            kwargs["http_options"] = http_options

        return genai.Client(**kwargs)

    def _build_http_options(self):
        """Tạo HttpOptions với timeout; trả None nếu SDK không hỗ trợ."""
        http_options_class = getattr(types, "HttpOptions", None)

        if http_options_class is None:
            self._log_info(
                "llm_timeout_unsupported",
                "SDK không hỗ trợ HttpOptions, bỏ qua cấu hình timeout.",
            )
            return None

        try:
            return http_options_class(timeout=self._timeout_seconds * 1000)
        except TypeError:  # pragma: no cover - khác biệt phiên bản SDK
            return None

    def _normalize_fallback_models(self, model_config):
        """Chuẩn hoá ``fallback_model`` về list[str].

        Cấu hình cũ dùng chuỗi (``"fallback_model": "gemma-..."``) trong khi
        code cũ lại lặp qua chuỗi đó theo từng ký tự.
        """
        raw = model_config.get(
            "fallback_models", model_config.get("fallback_model", [])
        )

        if raw is None or raw == "":
            return []

        if isinstance(raw, str):
            models = [raw]
        elif isinstance(raw, (list, tuple)):
            models = list(raw)
        elif isinstance(raw, dict):
            nested = raw.get("models")
            if not isinstance(nested, (list, tuple)):
                raise ConfigurationError(
                    "fallback_model dạng object phải có khoá 'models' là list."
                )
            models = list(nested)
        else:
            raise ConfigurationError(
                f"fallback_model không hợp lệ: {type(raw).__name__}."
            )

        cleaned = []
        for model in models:
            name = str(model).strip()
            if name and name != self._model and name not in cleaned:
                cleaned.append(name)

        return cleaned

    # ------------------------------------------------------------------
    # Gọi model
    # ------------------------------------------------------------------

    def generate(self, prompt, output_format=None):
        last_error = None

        for index, model in enumerate([self._model, *self._fallback_models]):
            if index > 0:
                self._log_info(
                    "llm_fallback_model",
                    model=model,
                    error=str(last_error),
                )

            try:
                text = self._generate_with_retries(model, prompt, output_format)
            except ExternalServiceError as error:
                last_error = error
                continue

            self.last_used_model = model

            if index > 0:
                self._log_info("llm_fallback_succeeded", model=model)

            return text

        if last_error is not None:
            raise last_error

        raise ExternalServiceError(
            "Không có model nào xử lý thành công.",
            service=self.provider_name,
            model=self._model,
            retryable=False,
        )

    def _generate_with_retries(self, model, prompt, output_format):
        return self._retry.run(
            lambda: self._generate_with_model(model, prompt, output_format),
            event_name="llm_retry",
            description=f"model '{model}'",
            service=self.provider_name,
            fields={"model": model},
            log=self._log,
        )

    def _generate_with_model(self, model, prompt, output_format):
        config_args = {
            "temperature": self._options.get("temperature", 0.2)
        }

        if output_format is not None:
            config_args["response_mime_type"] = "application/json"
            config_args["response_schema"] = _strip_unsupported_schema_keys(
                output_format
            )

        try:
            response = self._client.models.generate_content(
                model=model,
                contents=prompt,
                config=types.GenerateContentConfig(**config_args),
            )
        except errors.APIError as exc:
            raise self._api_error(exc, model) from exc
        except (TimeoutError, ConnectionError) as exc:
            raise ExternalServiceError(
                f"Lỗi kết nối khi gọi model '{model}': {type(exc).__name__}",
                service=self.provider_name,
                model=model,
                retryable=True,
            ) from exc
        except Exception as exc:
            if self._is_network_error(exc):
                raise ExternalServiceError(
                    f"Lỗi mạng khi gọi model '{model}': {type(exc).__name__}",
                    service=self.provider_name,
                    model=model,
                    retryable=True,
                ) from exc
            raise

        if not response.text:
            raise ExternalServiceError(
                f"Model '{model}' trả về nội dung rỗng.",
                service=self.provider_name,
                model=model,
                retryable=True,
            )

        return response.text

    # ------------------------------------------------------------------
    # Phân loại lỗi
    # ------------------------------------------------------------------

    def _api_error(self, exc, model):
        status = getattr(exc, "code", None)
        retryable = isinstance(status, int) and is_transient_status(status)

        detail = getattr(exc, "message", None) or str(exc)

        return ExternalServiceError(
            f"Gemini trả về lỗi HTTP {status} cho model '{model}': "
            f"{self._shorten(detail)}",
            service=self.provider_name,
            model=model,
            status=status,
            retryable=retryable,
            retry_after=self._retry_after_from(exc),
        )

    @staticmethod
    def _shorten(text, limit=200):
        text = " ".join(str(text).split())
        if len(text) <= limit:
            return text
        return text[: limit - 3] + "..."

    @staticmethod
    def _retry_after_from(exc):
        """Đọc header Retry-After nếu SDK phơi ra response."""
        response = getattr(exc, "response", None)
        headers = getattr(response, "headers", None)

        if headers is None:
            headers = getattr(exc, "headers", None)

        if not headers:
            return None

        try:
            value = headers.get("retry-after")
        except AttributeError:  # pragma: no cover - headers không phải mapping
            return None

        if value is None:
            return None

        try:
            return max(0.0, float(value))
        except (TypeError, ValueError):
            # Retry-After dạng HTTP-date: bỏ qua, dùng backoff mặc định.
            return None

    @staticmethod
    def _is_network_error(exc):
        if httpx is None:
            return False
        return isinstance(exc, httpx.HTTPError)

    def _log_info(self, event, message="", **fields):
        if self._log is not None:
            self._log.info(
                event,
                message,
                provider=self.provider_name,
                model=self._model,
                **fields,
            )
