"""Dựng clip whiteboard cho từng cảnh (Phase 4).

Service này gọi CLI của engine đã vendor tại
``Lib/WhiteBoardEngine/whiteboard-video-engine``.

Vì sao dùng ``render-image`` mà KHÔNG dùng ``render-photo``::

    render-photo -> có ``--lineart-provider`` nên phải chạy thêm model học sâu
                    (informative-drawings / Anime2Sketch / ...). Trọng số model
                    (``*.pth``) và thư mục ``tools/informative-drawings/`` đều
                    bị gitignore và phải cài tay, nên trên máy sạch
                    ``render-photo`` sẽ lỗi.
    render-image -> trích stroke trực tiếp từ chính ảnh đầu vào, không cần
                    model nào.

Lệnh render bắt buộc có dạng::

    whiteboard render-image <anh> -o <clip.mp4> --duration <giây> --fps <fps> \\
        --width <px> --height <px> --hand-scale <tỉ lệ>

Engine luôn được chạy với ``cwd`` là ``engine_dir`` để nó tìm thấy tài nguyên
tương đối của mình.

Ghi chú vận hành: ``.venv`` của engine bị gitignore nên bản clone về chưa có
CLI. Thiếu CLI -> ``ConfigurationError`` nêu rõ cách khắc phục, không chạy mò.
"""

import os
import subprocess
from pathlib import Path

from Errors import ConfigurationError, MediaError, ValidationError

#: Gốc repo (file này nằm ở ``<repo>/Services/``).
PROJECT_ROOT = Path(__file__).resolve().parents[1]

DEFAULT_ENGINE_DIR = Path("Lib") / "WhiteBoardEngine" / "whiteboard-video-engine"
DEFAULT_OUTPUT_DIR = "output/clips"

#: Mặc định lấy theo giá trị đã thử ở prototype của dự án (5 giây, 24 fps).
DEFAULT_DURATION_SECONDS = 5.0
DEFAULT_FPS = 24

#: Khung hình mặc định: 16:9 ngang. Đặt 1080x1920 trong Config/render.json để
#: có video dọc (YouTube Shorts / Reels).
DEFAULT_WIDTH = 1920
DEFAULT_HEIGHT = 1080

#: Bàn tay cầm bút được engine scale theo cạnh nhỏ của khung, nên khung càng hẹp
#: thì tay càng chiếm nhiều chiều ngang. Lấy tỉ lệ theo chiều rộng chuẩn 1920 để
#: tay giữ đúng độ lớn tương đối khi đổi sang video dọc.
HAND_SCALE_BASE_WIDTH = 1920

#: Render một clip có thể mất vài phút; 0 = không giới hạn.
DEFAULT_TIMEOUT_SECONDS = 1800


class RenderService:
    """Dựng clip whiteboard cho mọi cảnh của một bài.

    - Mỗi cảnh -> một clip ``<output_dir>/<article_id>/scene_<n>.mp4``.
    - Một cảnh lỗi không làm dừng các cảnh còn lại: kết quả trả về theo từng
      cảnh gồm ``ok``/``error`` để caller quyết định (giống ``ImageService``).
    - Ghi ra file tạm cùng đuôi ``.mp4`` rồi ``replace``: ffmpeg chọn muxer
      theo đuôi file, và không bao giờ để lại clip dở dang mang tên chính thức.
    """

    def __init__(self, config=None, output_dir=None, log_service=None,
                 cli_path=None):
        config = config or {}

        engine_dir = Path(config.get("engine_dir") or DEFAULT_ENGINE_DIR)

        if not engine_dir.is_absolute():
            engine_dir = PROJECT_ROOT / engine_dir

        self._config = config
        self._engine_dir = engine_dir
        self._cli_path = Path(cli_path) if cli_path else self._resolve_cli(config)
        self._output_dir = Path(
            output_dir or config.get("output_dir", DEFAULT_OUTPUT_DIR)
        )
        self._default_duration = float(
            config.get("duration_seconds", DEFAULT_DURATION_SECONDS)
        )
        self._fps = int(config.get("fps", DEFAULT_FPS))
        self._width = self._even_dimension(
            config.get("width", DEFAULT_WIDTH), "width"
        )
        self._height = self._even_dimension(
            config.get("height", DEFAULT_HEIGHT), "height"
        )
        self._hand_scale = self._resolve_hand_scale(config, self._width)
        self._timeout = float(
            config.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS)
        )
        self._log = log_service

    # ------------------------------------------------------------------
    # Thuộc tính / khả dụng
    # ------------------------------------------------------------------

    @property
    def engine_dir(self):
        return self._engine_dir

    @property
    def cli_path(self):
        return self._cli_path

    @property
    def output_dir(self):
        return self._output_dir

    @property
    def width(self):
        return self._width

    @property
    def height(self):
        return self._height

    @property
    def hand_scale(self):
        return self._hand_scale

    def is_available(self):
        """True nếu CLI của engine đã sẵn sàng để gọi."""
        return self._cli_path.is_file()

    def ensure_available(self):
        """Raise ``ConfigurationError`` nếu chưa có CLI của engine."""
        if self.is_available():
            return

        raise ConfigurationError(
            f"Không tìm thấy WhiteBoardEngine CLI: {self._cli_path}. "
            "Thư mục .venv của engine không được lưu trong repo, hãy tạo lại "
            f"tại {self._engine_dir} (ví dụ: `python -m venv .venv` rồi "
            "`pip install -e .`) hoặc trỏ biến môi trường WHITEBOARD_CLI / "
            "khóa `cli_path` trong Config/render.json tới CLI có sẵn."
        )

    # ------------------------------------------------------------------
    # Đường dẫn
    # ------------------------------------------------------------------

    def article_dir(self, article_id):
        """Thư mục clip của một bài (chống path traversal)."""
        if not article_id or Path(str(article_id)).name != str(article_id):
            raise ValidationError("id không hợp lệ để tạo thư mục clip.")

        return self._output_dir / str(article_id)

    def clip_path(self, article_id, scene_id):
        """Đường dẫn clip cố định cho một cảnh."""
        return self.article_dir(article_id) / f"scene_{scene_id:03d}.mp4"

    # ------------------------------------------------------------------
    # Dựng clip
    # ------------------------------------------------------------------

    def render_clip(self, image_path, output_path, duration=None, fps=None,
                    width=None, height=None, hand_scale=None):
        """Dựng một clip từ một ảnh, trả về đường dẫn clip.

        Lệnh gọi engine là cố định: ``render-image`` (xem docstring module),
        kèm ``--duration``/``--fps``/``--width``/``--height``/``--hand-scale``.

        Engine được chạy với ``cwd = engine_dir`` nên MỌI đường dẫn truyền cho
        nó phải là tuyệt đối; nếu để tương đối, engine sẽ resolve theo thư mục
        của chính nó và báo "No such file or directory" (hoặc ghi clip nhầm
        chỗ). Vì vậy input/output được ``resolve()`` ngay tại đây.
        """
        image_path = Path(image_path).resolve()
        output_path = Path(output_path).resolve()

        self._require_image(image_path)
        self.ensure_available()

        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Cùng đuôi .mp4 để ffmpeg chọn đúng muxer, nhưng vẫn là file tạm.
        temp_path = output_path.parent / (
            f"{output_path.stem}.part{output_path.suffix}"
        )
        self._remove_quietly(temp_path)

        command = [
            str(self._cli_path),
            "render-image",
            str(image_path),
            "-o",
            str(temp_path),
            "--duration",
            str(duration if duration is not None else self._default_duration),
            "--fps",
            str(fps if fps is not None else self._fps),
            "--width",
            str(width if width is not None else self._width),
            "--height",
            str(height if height is not None else self._height),
            "--hand-scale",
            str(hand_scale if hand_scale is not None else self._hand_scale),
        ]

        self._log_info(
            "render_started",
            f"Bắt đầu render clip từ {image_path.name}.",
            image=str(image_path),
            output=str(output_path),
        )

        try:
            result = self._execute(command)
        except subprocess.TimeoutExpired as error:
            self._remove_quietly(temp_path)
            raise MediaError(
                f"Render quá thời gian cho phép ({self._timeout:g} giây): "
                f"{image_path}"
            ) from error
        except OSError as error:
            self._remove_quietly(temp_path)
            raise MediaError(
                f"Không chạy được engine render: {type(error).__name__}: {error}"
            ) from error

        if result.returncode != 0:
            self._remove_quietly(temp_path)
            raise MediaError(
                f"render-image thất bại cho {image_path.name} "
                f"(mã thoát {result.returncode}).\n"
                f"STDERR:\n{result.stderr}\nSTDOUT:\n{result.stdout}"
            )

        if not temp_path.is_file() or temp_path.stat().st_size == 0:
            self._remove_quietly(temp_path)
            raise MediaError(
                f"Engine không tạo được clip hợp lệ: {output_path}"
            )

        temp_path.replace(output_path)

        self._log_info(
            "render_created",
            f"Đã tạo clip: {output_path}",
            output=str(output_path),
            bytes=output_path.stat().st_size,
        )

        return str(output_path)

    def run(self, article_id, images, image_plan=None):
        """Dựng clip cho mọi cảnh đã có ảnh.

        Args:
            article_id: id bài viết (dùng làm tên thư mục).
            images: kết quả của ``ImageService.run`` — list các dict có
                ``scene_id``, ``path``, ``ok``.
            image_plan: kế hoạch cảnh; nếu một cảnh có ``duration_seconds``
                hợp lệ thì clip dùng đúng thời lượng đó để khớp với lời đọc.

        Returns:
            list[dict]: mỗi phần tử gồm ``scene_id``, ``path``, ``ok``, ``error``.
        """
        if not isinstance(images, list) or not images:
            raise ValidationError("Không có ảnh nào để render.")

        # Kiểm tra id trước khi vào vòng lặp: id sai phải raise ngay, không
        # được biến thành "cảnh lỗi" để không âm thầm bỏ qua.
        self.article_dir(article_id)

        durations = self._scene_durations(image_plan)
        results = []

        for item in images:
            scene_id = item.get("scene_id") if isinstance(item, dict) else None

            if not isinstance(item, dict) or not item.get("ok") or not item.get("path"):
                message = "Ảnh của cảnh chưa sẵn sàng để render."
                self._record_failure(article_id, scene_id, message, results)
                continue

            try:
                path = self.render_clip(
                    item["path"],
                    self.clip_path(article_id, scene_id),
                    duration=durations.get(scene_id),
                )
            except Exception as error:
                message = f"{type(error).__name__}: {error}"
                self._record_failure(article_id, scene_id, message, results)
                continue

            results.append(
                {
                    "scene_id": scene_id,
                    "path": path,
                    "ok": True,
                    "error": None,
                }
            )

        return results

    # ------------------------------------------------------------------
    # Nội bộ
    # ------------------------------------------------------------------

    def _execute(self, command):
        """Chạy engine. Tách riêng để test thay thế mà không cần CLI thật."""
        return subprocess.run(
            command,
            cwd=str(self._engine_dir),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=self._timeout or None,
        )

    def _resolve_cli(self, config):
        override = config.get("cli_path") or os.getenv("WHITEBOARD_CLI")

        if override:
            return Path(override)

        scripts_dir = "Scripts" if os.name == "nt" else "bin"
        executable = "whiteboard.exe" if os.name == "nt" else "whiteboard"

        return self._engine_dir / ".venv" / scripts_dir / executable

    @staticmethod
    def _even_dimension(value, name):
        """Kích thước video phải là số chẵn (H.264 yêu cầu); làm tròn xuống."""
        if isinstance(value, bool):
            raise ConfigurationError(f"render.{name} phải là số nguyên dương.")

        try:
            number = int(value)
        except (TypeError, ValueError):
            raise ConfigurationError(
                f"render.{name} phải là số nguyên dương."
            ) from None

        if number < 2:
            raise ConfigurationError(
                f"render.{name} phải lớn hơn hoặc bằng 2."
            )

        return number - number % 2

    @staticmethod
    def _resolve_hand_scale(config, width):
        """Tỉ lệ bàn tay: mặc định theo chiều rộng, có thể ghi đè trong config."""
        value = config.get("hand_scale")

        if value is None:
            return max(0.1, width / HAND_SCALE_BASE_WIDTH)

        if isinstance(value, bool):
            raise ConfigurationError(
                "render.hand_scale phải là số dương."
            )

        try:
            scale = float(value)
        except (TypeError, ValueError):
            raise ConfigurationError(
                "render.hand_scale phải là số dương."
            ) from None

        if scale <= 0:
            raise ConfigurationError("render.hand_scale phải là số dương.")

        return max(0.1, scale)

    def _record_failure(self, article_id, scene_id, message, results):
        self._log_error(
            "render_scene_failed",
            f"Không render được cảnh {scene_id}: {message}",
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

    @staticmethod
    def _scene_durations(image_plan):
        """Bảng ``scene_id -> duration_seconds`` suy ra từ kế hoạch cảnh."""
        scenes = image_plan.get("scenes") if isinstance(image_plan, dict) else None
        durations = {}

        if not isinstance(scenes, list):
            return durations

        for scene in scenes:
            if not isinstance(scene, dict):
                continue

            scene_id = scene.get("scene_id")
            value = scene.get("duration_seconds")

            if isinstance(scene_id, bool) or not isinstance(scene_id, int):
                continue

            if isinstance(value, bool) or not isinstance(value, (int, float)):
                continue

            if value > 0:
                durations[scene_id] = float(value)

        return durations

    @staticmethod
    def _require_image(image_path):
        if not image_path.is_file():
            raise MediaError(f"Không tìm thấy ảnh đầu vào: {image_path}")

        if image_path.stat().st_size == 0:
            raise MediaError(f"Ảnh đầu vào rỗng: {image_path}")

    @staticmethod
    def _remove_quietly(path):
        try:
            path.unlink(missing_ok=True)
        except OSError:  # pragma: no cover - không xoá được thì bỏ qua
            pass

    def _log_info(self, event, message="", **fields):
        if self._log is not None:
            self._log.info(event, message, **fields)

    def _log_error(self, event, message="", **fields):
        if self._log is not None:
            self._log.error(event, message, **fields)
