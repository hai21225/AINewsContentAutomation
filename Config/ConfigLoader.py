import json
from pathlib import Path

from Errors import ConfigurationError


class ConfigLoader:
    """Đọc và kiểm tra cấu hình JSON của dự án.

    Mặc định đọc thư mục ``Config/`` nằm cạnh file này, nên không phụ thuộc
    vào thư mục làm việc hiện tại (khác với việc dùng đường dẫn tương đối).
    """

    def __init__(self, config_dir=None):
        if config_dir is None:
            config_dir = Path(__file__).resolve().parent
        self._config_dir = Path(config_dir)
        self._cache = {}

        if not self._config_dir.is_dir():
            raise ConfigurationError(
                f"Không tìm thấy thư mục cấu hình: {self._config_dir}"
            )

    @property
    def config_dir(self):
        return self._config_dir

    def _load_json(self, relative_path):
        path = self._config_dir / relative_path

        if path in self._cache:
            return self._cache[path]

        if not path.is_file():
            raise ConfigurationError(
                f"Thiếu file cấu hình: {path}"
            )

        try:
            with open(path, "r", encoding="utf-8") as file:
                data = json.load(file)
        except json.JSONDecodeError as error:
            raise ConfigurationError(
                f"Cấu hình JSON không hợp lệ tại {path}: {error}"
            ) from error

        self._cache[path] = data
        return data

    # ------------------------------------------------------------------
    # LLM
    # ------------------------------------------------------------------

    def get_providers(self):
        return self._load_json(Path("LLM") / "providers.json")

    def get_models(self):
        return self._load_json(Path("LLM") / "models.json")

    # ------------------------------------------------------------------
    # Agent
    # ------------------------------------------------------------------

    def get_agent(self, agent_name):
        path = Path("Agents") / f"{agent_name}.json"

        if not (self._config_dir / path).is_file():
            available = ", ".join(self.list_agents()) or "(không có)"
            raise ConfigurationError(
                f"Không có cấu hình agent '{agent_name}'. "
                f"Các agent hiện có: {available}"
            )

        return self._load_json(path)

    def list_agents(self):
        agents_dir = self._config_dir / "Agents"

        if not agents_dir.is_dir():
            return []

        return sorted(path.stem for path in agents_dir.glob("*.json"))

    # ------------------------------------------------------------------
    # Nguồn tin, TTS, pipeline, logging
    # ------------------------------------------------------------------

    def get_rss(self):
        return self._load_json(Path("rss.json"))

    def get_tts(self):
        return self._load_json(Path("tts.json"))

    def get_pipeline(self):
        return self._load_json(Path("pipeline.json"))

    def get_logging(self):
        return self._load_json(Path("logging.json"))

    # ------------------------------------------------------------------
    # Sinh ảnh
    # ------------------------------------------------------------------

    def get_image_providers(self):
        return self._load_json(Path("Image") / "providers.json")

    def get_image_models(self):
        return self._load_json(Path("Image") / "models.json")

    def get_image_service(self):
        return self._load_json(Path("Image") / "service.json")

    # ------------------------------------------------------------------
    # Render (engine whiteboard)
    # ------------------------------------------------------------------

    def get_render(self):
        return self._load_json(Path("render.json"))

    # ------------------------------------------------------------------
    # Ghép video cuối (ffmpeg)
    # ------------------------------------------------------------------

    def get_media(self):
        return self._load_json(Path("media.json"))

    # ------------------------------------------------------------------
    # Đăng bài lên mạng xã hội
    # ------------------------------------------------------------------

    def get_publish(self):
        return self._load_json(Path("publish.json"))
