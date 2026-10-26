
from abc import ABC, abstractmethod


class ImageGenerationClient(ABC):

    @abstractmethod
    def generate(self, prompt: str, **options) -> bytes:
        """
        Tạo ảnh từ prompt.

        Args:
            prompt: Mô tả ảnh cần tạo.
            **options: Các tùy chọn bổ sung như seed,
                       width, height.

        Returns:
            Dữ liệu ảnh dạng bytes.
        """
        pass
