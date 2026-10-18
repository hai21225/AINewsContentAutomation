from abc import ABC, abstractmethod


class BaseImageClient(ABC):
    """Hợp đồng chung cho mọi image provider.

    ``generate`` trả về **bytes** của file ảnh; việc kiểm tra và lưu file
    thuộc về ``Services.ImageService``.
    """

    #: Tên provider, dùng cho log và thông báo lỗi.
    provider_name = "unknown"

    @abstractmethod
    def generate(self, prompt, **options):
        """Sinh ảnh từ prompt, trả về bytes."""
        raise NotImplementedError
