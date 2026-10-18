import asyncio
import os
import shutil
import subprocess
from pathlib import Path

import edge_tts

from Errors import ConfigurationError, MediaError, ValidationError

try:
    from mutagen.mp3 import MP3
except ImportError:  # pragma: no cover - phụ thuộc tuỳ chọn
    MP3 = None


DEFAULT_PROVIDER = "edge_tts"
DEFAULT_OUTPUT_DIR = "outputmp3"
DEFAULT_VOICE = "vi-VN-HoaiMyNeural"
SUPPORTED_PROVIDERS = ("edge_tts",)
FFPROBE_TIMEOUT_SECONDS = 30


class TtsService:
    """Sinh audio TTS và đo thời lượng thực tế của file.

    Thời lượng dùng để lập kế hoạch cảnh, nên được đo từ file thật:
    ưu tiên ``mutagen``, nếu thiếu/không đọc được thì fallback sang ``ffprobe``.
    Nếu không đo được, raise ``MediaError`` thay vì trả ``None`` (trước đây
    ``None`` lọt xuống planner và gây lỗi khó hiểu).
    """

    def __init__(self, config, output_dir=None, log_service=None,
                 ffprobe_path=None):
        config = config or {}

        self._config = config
        self._provider_name = config.get("provider", DEFAULT_PROVIDER)

        providers = config.get("providers", {}) or {}
        self._provider_config = providers.get(self._provider_name, {}) or {}

        if self._provider_name not in SUPPORTED_PROVIDERS:
            raise ConfigurationError(
                f"TTS provider chưa được hỗ trợ: {self._provider_name}. "
                f"Provider hiện có: {', '.join(SUPPORTED_PROVIDERS)}."
            )

        self._output_dir = Path(
            output_dir or config.get("output_dir", DEFAULT_OUTPUT_DIR)
        )
        self._log = log_service
        self._ffprobe_path = ffprobe_path

    @property
    def provider_name(self):
        return self._provider_name

    # ------------------------------------------------------------------
    # Sinh audio
    # ------------------------------------------------------------------

    def output_path(self, article_id):
        """Đường dẫn file audio cố định cho một bài (chống path traversal)."""
        if not article_id or Path(str(article_id)).name != str(article_id):
            raise ValidationError(
                "id không hợp lệ để tạo tên file audio."
            )

        return self._output_dir / f"{article_id}.mp3"

    def run(self, article_id, tts_text):
        """Sinh file MP3 và trả về đường dẫn file."""
        if not isinstance(tts_text, str) or not tts_text.strip():
            raise ValidationError("tts_text không được rỗng.")

        output_path = self.output_path(article_id)
        self._output_dir.mkdir(parents=True, exist_ok=True)

        # Ghi ra file tạm rồi mới đổi tên: tránh file dở dang mang tên chính thức.
        temp_path = output_path.parent / (output_path.name + ".part")

        try:
            asyncio.run(self._generate(tts_text.strip(), temp_path))
        except Exception as error:
            self._remove_quietly(temp_path)
            raise MediaError(
                f"Tạo audio thất bại cho bài {article_id}: "
                f"{type(error).__name__}: {error}"
            ) from error

        try:
            self._verify_audio_file(temp_path)
        except MediaError:
            self._remove_quietly(temp_path)
            raise

        temp_path.replace(output_path)

        self._log_info(
            "tts_created",
            f"Đã tạo file âm thanh: {output_path}",
            article_id=article_id,
            bytes=output_path.stat().st_size,
        )

        return str(output_path)

    async def _generate(self, text, output_path):
        communicate = self.build_communicate(text)
        await communicate.save(str(output_path))

    def build_communicate(self, text):
        """Tạo đối tượng edge_tts (tách riêng để test có thể thay thế)."""
        return edge_tts.Communicate(
            text,
            self._provider_config.get("voice", DEFAULT_VOICE),
            rate=self._provider_config.get("rate", "+0%"),
            pitch=self._provider_config.get("pitch", "+0Hz"),
            volume=self._provider_config.get("volume", "+0%"),
        )

    # ------------------------------------------------------------------
    # Đo thời lượng
    # ------------------------------------------------------------------

    def duration(self, audio_path):
        """Trả về thời lượng audio (giây), raise MediaError nếu không đo được."""
        path = Path(audio_path)

        if not path.is_file():
            raise MediaError(f"Không tìm thấy file audio: {path}")

        if path.stat().st_size == 0:
            raise MediaError(f"File audio rỗng: {path}")

        length = self._duration_from_mutagen(path)

        if length is None:
            self._log_warning(
                "tts_duration_fallback",
                "Không đọc được thời lượng bằng mutagen, thử ffprobe.",
                path=str(path),
            )
            length = self._duration_from_ffprobe(path)

        if length is None or length <= 0:
            raise MediaError(
                f"Không xác định được thời lượng audio: {path}"
            )

        return round(float(length), 2)

    def _duration_from_mutagen(self, path):
        if MP3 is None:
            return None

        try:
            return float(MP3(str(path)).info.length)
        except Exception as error:
            self._log_warning(
                "tts_mutagen_failed",
                f"mutagen không đọc được file: {type(error).__name__}.",
                path=str(path),
            )
            return None

    def _duration_from_ffprobe(self, path):
        executable = self.ffprobe_executable()

        if executable is None:
            self._log_warning(
                "tts_ffprobe_missing",
                "Không tìm thấy ffprobe trong PATH hay FFPROBE_PATH.",
            )
            return None

        command = [
            str(executable),
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(path),
        ]

        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=FFPROBE_TIMEOUT_SECONDS,
            )
        except (OSError, subprocess.SubprocessError) as error:
            self._log_warning(
                "tts_ffprobe_failed",
                f"Không chạy được ffprobe: {type(error).__name__}.",
            )
            return None

        if result.returncode != 0:
            self._log_warning(
                "tts_ffprobe_failed",
                f"ffprobe trả về mã lỗi {result.returncode}.",
            )
            return None

        try:
            return float(result.stdout.strip())
        except (TypeError, ValueError):
            return None

    def ffprobe_executable(self):
        if self._ffprobe_path:
            return self._ffprobe_path

        env_path = os.getenv("FFPROBE_PATH")

        if env_path:
            return env_path

        return shutil.which("ffprobe")

    # ------------------------------------------------------------------
    # Nội bộ
    # ------------------------------------------------------------------

    @staticmethod
    def _verify_audio_file(path):
        if not path.is_file():
            raise MediaError(f"File audio không được tạo: {path}")

        if path.stat().st_size == 0:
            raise MediaError(f"File audio rỗng: {path}")

    @staticmethod
    def _remove_quietly(path):
        try:
            path.unlink(missing_ok=True)
        except OSError:  # pragma: no cover - không xoá được thì bỏ qua
            pass

    def _log_info(self, event, message="", **fields):
        if self._log is not None:
            self._log.info(event, message, provider=self._provider_name, **fields)

    def _log_warning(self, event, message="", **fields):
        if self._log is not None:
            self._log.warning(
                event, message, provider=self._provider_name, **fields
            )
