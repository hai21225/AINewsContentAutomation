"""Lỗi ứng dụng dùng chung cho toàn pipeline.

Phân loại lỗi theo cách xử lý thay vì theo nơi phát sinh:

- ``ConfigurationError``: cấu hình sai/thiếu. Không bao giờ retry.
- ``ValidationError``: dữ liệu vào/ra không hợp lệ (kể cả output của LLM).
- ``AgentOutputError``: LLM trả về JSON sai/thiếu trường bắt buộc.
- ``MediaError``: file media hỏng/thiếu (audio, ảnh, video).
- ``StateError``: file trạng thái job (state JSON) hỏng/sai schema/không đọc
  được. Không tự "sửa" bằng cách ghi đè để tránh mất trạng thái thật.
- ``ExternalServiceError``: lỗi từ dịch vụ ngoài (HTTP/API). Có cờ
  ``retryable`` để tầng retry quyết định thử lại hay không.

Tầng retry tập trung (``Retry.RetryPolicy``) chỉ dựa vào
``ExternalServiceError.retryable`` và ``retry_after`` nên không cần đoán loại
lỗi từ chuỗi thông báo.
"""


class AppError(Exception):
    """Lớp cơ sở cho mọi lỗi có thể dự đoán của ứng dụng."""

    retryable = False

    def __init__(self, message):
        super().__init__(message)
        self.message = message


class ConfigurationError(AppError):
    """Cấu hình thiếu, sai kiểu hoặc không nhất quán."""


class ValidationError(AppError):
    """Dữ liệu đầu vào/đầu ra không thoả hợp đồng."""


class AgentOutputError(ValidationError):
    """Agent/LLM trả về output không dùng được sau khi đã thử sửa."""


class MediaError(AppError):
    """File media bị thiếu, rỗng hoặc không giải mã/đọc được."""


class StateError(AppError):
    """File trạng thái (state JSON) hỏng, sai schema hoặc không đọc được."""


class ExternalServiceError(AppError):
    """Lỗi khi gọi dịch vụ ngoài (LLM, RSS, image, ...).

    Attributes:
        service: tên dịch vụ (ví dụ ``"gemini"``, ``"rss"``).
        model: model/đối tượng cụ thể nếu có.
        status: HTTP status code nếu có.
        retryable: True khi lỗi có khả năng tự hết (timeout, 429, 5xx).
        retry_after: số giây server gợi ý chờ (từ header ``Retry-After``).
    """

    def __init__(
        self,
        message,
        *,
        service=None,
        model=None,
        status=None,
        retryable=False,
        retry_after=None,
    ):
        super().__init__(message)
        self.service = service
        self.model = model
        self.status = status
        self.retryable = retryable
        self.retry_after = retry_after


#: Các HTTP status được coi là tạm thời, có thể thử lại.
TRANSIENT_STATUS_CODES = frozenset({408, 409, 425, 429, 500, 502, 503, 504})


def is_transient_status(status):
    """Trả True nếu HTTP status thuộc nhóm có thể thử lại."""
    return status in TRANSIENT_STATUS_CODES
