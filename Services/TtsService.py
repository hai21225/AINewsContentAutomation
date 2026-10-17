import asyncio
from pathlib import Path
from Console import Console
import edge_tts

try:
    from mutagen.mp3 import MP3
except ImportError:
    MP3 = None


class TtsService:
    def __init__(self, config, output_dir="outputmp3"):
        self._config = config
        self._output_dir = Path(output_dir)

        provider_name = config.get("provider", "edge_tts")
        providers = config.get("providers", {})
        self._provider_config = providers.get(provider_name, {})

        if provider_name != "edge_tts":
            raise ValueError(f"TTS provider chưa được hỗ trợ: {provider_name}")

    def _output_path(self, article_id):
        if not article_id or Path(article_id).name != article_id:
            raise ValueError("id không hợp lệ để tạo tên file audio.")
        return self._output_dir / f"{article_id}.mp3"

    async def _generate(self, text, output_path):
        communicate = edge_tts.Communicate(
            text,
            self._provider_config.get("voice", "vi-VN-HoaiMyNeural"),
            rate=self._provider_config.get("rate", "+0%"),
            pitch=self._provider_config.get("pitch", "+0Hz"),
            volume=self._provider_config.get("volume", "+0%"),
        )
        await communicate.save(str(output_path))

    def run(self, article_id, tts_text):
        """Sinh file MP3 từ nội dung TTS và trả về đường dẫn file."""
        if not tts_text or not tts_text.strip():
            raise ValueError("tts_text không được rỗng.")

        output_path = self._output_path(article_id)
        self._output_dir.mkdir(parents=True, exist_ok=True)
        asyncio.run(self._generate(tts_text.strip(), output_path))
        Console.loading(f"Đã tạo file âm thanh: {output_path}")
        return str(output_path)

    def duration(self, audio_path):
        """Trả về thời lượng MP3 theo giây nếu mutagen đã được cài."""
        if MP3 is None:
            return None
        return round(float(MP3(audio_path).info.length), 2)
