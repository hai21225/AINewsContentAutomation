from abc import ABC, abstractmethod


class BaseLlmClient(ABC):
    """Hợp đồng chung cho mọi LLM client.

    Mọi client phải:
    - nhận ``generate(prompt, output_format)`` với ``output_format`` là JSON
      schema (dict) hoặc ``None``;
    - trả về **chuỗi văn bản** thô của model (việc parse JSON thuộc về agent);
    - ném ``ExternalServiceError`` khi lỗi dịch vụ ngoài, có cờ ``retryable``;
    - ghi lại model đã dùng thành công vào ``last_used_model``.
    """

    #: Tên provider, dùng cho log và thông báo lỗi.
    provider_name = "unknown"

    #: Model đã trả về kết quả thành công gần nhất (None nếu chưa gọi).
    last_used_model = None

    @abstractmethod
    def generate(self, prompt, output_format=None):
        """Sinh nội dung từ prompt, trả về chuỗi thô."""
        raise NotImplementedError
