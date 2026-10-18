import requests

from Errors import (
    ConfigurationError,
    ExternalServiceError,
    is_transient_status,
)
from LLM.BaseLlmClient import BaseLlmClient
from Retry import RetryPolicy


DEFAULT_BASE_URL = "http://localhost:11434"
DEFAULT_TIMEOUT_SECONDS = 300
DEFAULT_MAX_RETRIES = 1
DEFAULT_BACKOFF_SECONDS = 1.0
DEFAULT_MAX_BACKOFF_SECONDS = 8.0


class OllamaClient(BaseLlmClient):
    """Client gọi Ollama local (``/api/generate``).

    Nhận cùng chữ ký cấu hình với các client khác
    (``provider_config, model_config``) để factory có thể tạo thống nhất.
    """

    provider_name = "ollama"

    @property
    def model(self):
        return self._model

    def __init__(self, provider_config, model_config, log_service=None,
                 session=None, sleep=None, retry_config=None):
        self._log = log_service
        self._base_url = str(
            provider_config.get("base_url", DEFAULT_BASE_URL)
        ).rstrip("/")
        self._model = model_config.get("model")

        if not self._model:
            raise ConfigurationError(
                "Thiếu khoá cấu hình 'model' cho provider ollama."
            )

        self._options = model_config.get("options") or {}
        self._timeout = int(
            model_config.get(
                "timeout_seconds",
                provider_config.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS),
            )
        )
        self._retry = RetryPolicy.from_config(
            {
                "max_attempts": DEFAULT_MAX_RETRIES,
                "backoff_seconds": DEFAULT_BACKOFF_SECONDS,
                "max_backoff_seconds": DEFAULT_MAX_BACKOFF_SECONDS,
            },
            retry_config,
            provider_config,
            model_config,
            sleep=sleep,
        )
        self._session = session or requests.Session()
        self.last_used_model = None

    @property
    def retry_policy(self):
        return self._retry

    def generate(self, prompt, output_format=None):
        payload = {
            "model": self._model,
            "prompt": prompt,
            "stream": False,
            "options": self._options,
        }

        if output_format is not None:
            payload["format"] = output_format

        return self._retry.run(
            lambda: self._generate_once(payload),
            event_name="llm_retry",
            description=f"model '{self._model}'",
            service=self.provider_name,
            fields={"model": self._model},
            log=self._log,
        )

    def _generate_once(self, payload):
        data = self._post(payload)
        response_text = data.get("response")

        if not isinstance(response_text, str) or not response_text.strip():
            raise ExternalServiceError(
                f"Ollama model '{self._model}' trả về nội dung rỗng.",
                service=self.provider_name,
                model=self._model,
                retryable=True,
            )

        self.last_used_model = self._model
        return response_text

    def _post(self, payload):
        url = f"{self._base_url}/api/generate"

        try:
            response = self._session.post(
                url,
                json=payload,
                timeout=self._timeout,
            )
        except requests.RequestException as error:
            raise ExternalServiceError(
                f"Không gọi được Ollama tại {self._base_url}: "
                f"{type(error).__name__}",
                service=self.provider_name,
                model=self._model,
                retryable=True,
            ) from error

        if response.status_code >= 400:
            raise ExternalServiceError(
                f"Ollama trả về HTTP {response.status_code}.",
                service=self.provider_name,
                model=self._model,
                status=response.status_code,
                retryable=is_transient_status(response.status_code),
            )

        try:
            return response.json()
        except ValueError as error:
            raise ExternalServiceError(
                "Ollama trả về JSON không hợp lệ.",
                service=self.provider_name,
                model=self._model,
                retryable=True,
            ) from error
