"""Chính sách retry dùng chung cho mọi lời gọi dịch vụ ngoài.

Trước Phase 2, mỗi nơi tự cài một vòng lặp thử lại riêng (``ResearchService``,
``GeminiClient``, ``OllamaClient``) với công thức backoff khác nhau. Module này
gom về một chỗ: cùng công thức exponential backoff + jitter, cùng cách tôn
trọng header ``Retry-After`` và cùng một hợp đồng cấu hình.

Quy ước:

- Chỉ thử lại ``ExternalServiceError`` có ``retryable=True``. Lỗi vĩnh viễn
  (401, 404, request sai...) được ném lại ngay, không tốn thời gian chờ.
- ``max_attempts`` là **tổng** số lần gọi, không phải số lần thử lại thêm
  (``max_attempts=3`` nghĩa là gọi tối đa 3 lần). Khoá ``max_retries`` cũ
  được chấp nhận như bí danh để không phá cấu hình hiện có.
- Retry nằm ở tầng hẹp nhất có thể (một request), để tránh nhân số lần thử
  qua nhiều tầng lồng nhau.
"""

import random
import time

from Errors import ConfigurationError, ExternalServiceError

DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_BACKOFF_SECONDS = 1.0
DEFAULT_MAX_BACKOFF_SECONDS = 8.0
DEFAULT_JITTER_RATIO = 0.5

#: Các khoá cấu hình được đọc từ dict.
_CONFIG_KEYS = (
    "max_attempts",
    "backoff_seconds",
    "max_backoff_seconds",
    "jitter_ratio",
)


def retry_after_seconds(headers):
    """Đọc ``Retry-After`` (theo giây) từ headers của response.

    Trả ``None`` nếu không có, không đọc được, hoặc là định dạng HTTP-date
    (khi đó caller dùng backoff mặc định thay vì đoán ngày giờ).
    """
    if not headers:
        return None

    value = None

    for key in ("Retry-After", "retry-after"):
        try:
            value = headers.get(key)
        except AttributeError:  # pragma: no cover - headers không phải mapping
            return None

        if value is not None:
            break

    if value is None:
        return None

    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return None


class RetryPolicy:
    """Cấu hình + thực thi việc thử lại có giới hạn.

    ``sleep`` và ``random_source`` có thể được tiêm vào để test không phải
    chờ thật và kết quả backoff có thể kiểm tra tất định.
    """

    def __init__(self, max_attempts=DEFAULT_MAX_ATTEMPTS,
                 backoff_seconds=DEFAULT_BACKOFF_SECONDS,
                 max_backoff_seconds=DEFAULT_MAX_BACKOFF_SECONDS,
                 jitter_ratio=DEFAULT_JITTER_RATIO, sleep=None,
                 random_source=None):
        self._max_attempts = self._positive_int(max_attempts, "max_attempts")
        self._backoff_seconds = self._non_negative(
            backoff_seconds, "backoff_seconds"
        )
        self._max_backoff_seconds = self._non_negative(
            max_backoff_seconds, "max_backoff_seconds"
        )
        self._jitter_ratio = self._ratio(jitter_ratio)

        if self._max_backoff_seconds < self._backoff_seconds:
            raise ConfigurationError(
                "max_backoff_seconds phải lớn hơn hoặc bằng backoff_seconds."
            )

        self._sleep = sleep or time.sleep
        self._random = random_source or random

    # ------------------------------------------------------------------
    # Thuộc tính công khai
    # ------------------------------------------------------------------

    @property
    def max_attempts(self):
        return self._max_attempts

    @property
    def backoff_seconds(self):
        return self._backoff_seconds

    @property
    def max_backoff_seconds(self):
        return self._max_backoff_seconds

    @property
    def jitter_ratio(self):
        return self._jitter_ratio

    # ------------------------------------------------------------------
    # Tạo từ cấu hình
    # ------------------------------------------------------------------

    @classmethod
    def from_config(cls, *configs, sleep=None, random_source=None):
        """Gộp nhiều dict cấu hình thành một policy.

        Dict đứng sau ghi đè dict đứng trước, nên thứ tự thường dùng là
        (mặc định chung, provider, model, ...). Mỗi dict có thể khai báo khoá
        phẳng (``max_attempts``) hoặc lồng trong object ``retry``.
        """
        values = {}

        for config in configs:
            merged = cls._flatten(config)

            for key in _CONFIG_KEYS:
                value = merged.get(key)

                if value is None and key == "max_attempts":
                    value = merged.get("max_retries")

                if value is not None:
                    values[key] = value

        if sleep is not None:
            values["sleep"] = sleep

        if random_source is not None:
            values["random_source"] = random_source

        return cls(**values)

    @staticmethod
    def _flatten(config):
        if not isinstance(config, dict):
            return {}

        merged = {}

        nested = config.get("retry")

        if isinstance(nested, dict):
            merged.update(nested)

        merged.update(config)
        return merged

    # ------------------------------------------------------------------
    # Tính thời gian chờ
    # ------------------------------------------------------------------

    def delay_for(self, attempt, retry_after=None):
        """Thời gian chờ trước lần thử tiếp theo (giây)."""
        if retry_after:
            return min(float(retry_after), self._max_backoff_seconds)

        base = min(
            self._backoff_seconds * (2 ** (attempt - 1)),
            self._max_backoff_seconds,
        )

        if base <= 0:
            return 0.0

        # Jitter để nhiều tiến trình không thử lại cùng thời điểm.
        return base * self._random.uniform(1.0 - self._jitter_ratio, 1.0)

    # ------------------------------------------------------------------
    # Thực thi
    # ------------------------------------------------------------------

    def run(self, operation, *, event_name="retry", description=None,
            service=None, fields=None, log=None, article_id=None):
        """Gọi ``operation`` và thử lại nếu lỗi tạm thời.

        Raises:
            ExternalServiceError: lỗi vĩnh viễn, hoặc hết số lần thử.
        """
        attempt = 1

        while True:
            try:
                return operation()
            except ExternalServiceError as error:
                if not error.retryable or attempt >= self._max_attempts:
                    raise

                delay = self.delay_for(attempt, error.retry_after)
                self._log_retry(
                    log, event_name, attempt, delay, error, description,
                    service, fields, article_id,
                )
                self._sleep(delay)
                attempt += 1

    def _log_retry(self, log, event_name, attempt, delay, error, description,
                   service, fields, article_id):
        if log is None:
            return

        payload = {
            "attempt": attempt,
            "max_attempts": self._max_attempts,
            "delay_seconds": round(delay, 2),
            "status": error.status,
            "service": service or error.service,
            "target": description,
            "article_id": article_id,
            "error": str(error),
        }

        if fields:
            payload.update(fields)

        payload = {
            key: value for key, value in payload.items() if value is not None
        }

        log.warning(
            event_name,
            f"Thử lại lần {attempt}/{self._max_attempts} "
            f"sau {round(delay, 2)} giây.",
            **payload,
        )

    # ------------------------------------------------------------------
    # Kiểm tra cấu hình
    # ------------------------------------------------------------------

    @staticmethod
    def _positive_int(value, name):
        if isinstance(value, bool):
            raise ConfigurationError(f"{name} phải là số nguyên >= 1.")

        try:
            number = int(value)
        except (TypeError, ValueError):
            raise ConfigurationError(f"{name} phải là số nguyên >= 1.") from None

        if number < 1:
            raise ConfigurationError(f"{name} phải lớn hơn hoặc bằng 1.")

        return number

    @staticmethod
    def _non_negative(value, name):
        if isinstance(value, bool):
            raise ConfigurationError(f"{name} không được âm.")

        try:
            number = float(value)
        except (TypeError, ValueError):
            raise ConfigurationError(f"{name} phải là một số.") from None

        if number < 0:
            raise ConfigurationError(f"{name} không được âm.")

        return number

    @staticmethod
    def _ratio(value):
        if isinstance(value, bool):
            raise ConfigurationError("jitter_ratio phải nằm trong [0, 1].")

        try:
            number = float(value)
        except (TypeError, ValueError):
            raise ConfigurationError(
                "jitter_ratio phải nằm trong [0, 1]."
            ) from None

        if not 0.0 <= number <= 1.0:
            raise ConfigurationError("jitter_ratio phải nằm trong [0, 1].")

        return number
