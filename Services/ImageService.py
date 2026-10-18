from io import BytesIO
from pathlib import Path

from Errors import ConfigurationError, MediaError, ValidationError

try:
    from PIL import Image, UnidentifiedImageError
except ImportError as error:  # pragma: no cover - Pillow là phụ thuộc bắt buộc
    Image = None
    UnidentifiedImageError = Exception
    _PIL_IMPORT_ERROR = error
else:
    _PIL_IMPORT_ERROR = None


DEFAULT_OUTPUT_DIR = "output/images"
DEFAULT_SEED = 42

#: Ánh xạ định dạng Pillow -> phần mở rộng file.
FORMAT_EXTENSIONS = {
    "JPEG": ".jpg",
    "PNG": ".png",
    "WEBP": ".webp",
    "GIF": ".gif",
    "BMP": ".bmp",
}


class ImageService:
    """Sinh và lưu ảnh cho từng cảnh của một bài.

    - Lưu vào ``<output_dir>/<article_id>/scene_<n>.<ext>`` với đuôi file
      suy ra từ định dạng thật của dữ liệu (Pollinations flux trả JPEG).
    - Kiểm tra dữ liệu giải mã được bằng Pillow trước khi ghi ra file chính.
    - Một cảnh lỗi không làm dừng các cảnh còn lại: kết quả trả về theo từng
      cảnh gồm ``ok``/``error`` để caller quyết định.
    """

    def __init__(self, image_client, config=None, log_service=None,
                 output_dir=None):
        config = config or {}

        if _PIL_IMPORT_ERROR is not None:
            raise ConfigurationError(
                "ImageService cần Pillow để kiểm tra ảnh. Hãy cài đặt Pillow."
            ) from _PIL_IMPORT_ERROR

        self._client = image_client
        self._output_dir = Path(
            output_dir or config.get("output_dir", DEFAULT_OUTPUT_DIR)
        )
        self._base_seed = int(config.get("seed", DEFAULT_SEED))
        self._log = log_service

    @property
    def output_dir(self):
        return self._output_dir

    def article_dir(self, article_id):
        """Thư mục ảnh của một bài (chống path traversal)."""
        if not article_id or Path(str(article_id)).name != str(article_id):
            raise ValidationError("id không hợp lệ để tạo thư mục ảnh.")

        return self._output_dir / str(article_id)

    def run(self, article_id, image_plan):
        """Sinh ảnh cho mọi cảnh trong kế hoạch.

        Returns:
            list[dict]: mỗi phần tử gồm ``scene_id``, ``path``, ``ok``, ``error``.
        """
        scenes = image_plan.get("scenes") if isinstance(image_plan, dict) else None

        if not isinstance(scenes, list) or not scenes:
            raise ValidationError("image_plan không có 'scenes' hợp lệ.")

        directory = self.article_dir(article_id)
        directory.mkdir(parents=True, exist_ok=True)

        results = []

        for scene in scenes:
            scene_id = scene.get("scene_id") if isinstance(scene, dict) else None

            try:
                path = self._generate_scene(scene, directory)
            except Exception as error:
                message = f"{type(error).__name__}: {error}"
                self._log_error(
                    "image_scene_failed",
                    f"Không tạo được ảnh cho cảnh {scene_id}: {message}",
                    article_id=article_id,
                    scene_id=scene_id,
                )
                results.append(
                    {
                        "scene_id": scene_id,
                        "path": None,
                        "ok": False,
                        "error": message,
                    }
                )
                continue

            self._log_info(
                "image_scene_created",
                f"Đã tạo ảnh cho cảnh {scene_id}.",
                article_id=article_id,
                scene_id=scene_id,
                path=str(path),
            )
            results.append(
                {
                    "scene_id": scene_id,
                    "path": str(path),
                    "ok": True,
                    "error": None,
                }
            )

        return results

    # ------------------------------------------------------------------
    # Nội bộ
    # ------------------------------------------------------------------

    def _generate_scene(self, scene, directory):
        if not isinstance(scene, dict):
            raise ValidationError("Cảnh phải là dictionary.")

        prompt = scene.get("image_prompt")

        if not isinstance(prompt, str) or not prompt.strip():
            raise ValidationError("Cảnh thiếu 'image_prompt'.")

        scene_id = scene.get("scene_id")

        if not isinstance(scene_id, int) or isinstance(scene_id, bool):
            raise ValidationError("Cảnh thiếu 'scene_id' hợp lệ.")

        # Seed theo cảnh để kết quả ổn định khi chạy lại.
        data = self._client.generate(
            prompt.strip(), seed=self._base_seed + scene_id
        )

        return self._save_image(data, directory, scene_id)

    def _save_image(self, data, directory, scene_id):
        extension = self._detect_extension(data)

        path = directory / f"scene_{scene_id:03d}{extension}"
        temp_path = directory / (path.name + ".part")

        temp_path.write_bytes(bytes(data))
        temp_path.replace(path)

        return path

    @staticmethod
    def _detect_extension(data):
        if not isinstance(data, (bytes, bytearray)) or not data:
            raise MediaError("Dữ liệu ảnh rỗng hoặc sai kiểu.")

        try:
            with Image.open(BytesIO(bytes(data))) as image:
                image_format = (image.format or "").upper()
                image.verify()
        except UnidentifiedImageError as error:
            raise MediaError(
                "Dữ liệu trả về không giải mã được thành ảnh."
            ) from error
        except Exception as error:
            raise MediaError(
                f"Không kiểm tra được dữ liệu ảnh: {type(error).__name__}."
            ) from error

        extension = FORMAT_EXTENSIONS.get(image_format)

        if extension is None:
            raise MediaError(
                f"Định dạng ảnh không được hỗ trợ: {image_format or '(trống)'}."
            )

        return extension

    def _log_info(self, event, message="", **fields):
        if self._log is not None:
            self._log.info(event, message, **fields)

    def _log_error(self, event, message="", **fields):
        if self._log is not None:
            self._log.error(event, message, **fields)
