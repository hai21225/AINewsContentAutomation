"""Ghép các clip whiteboard + audio lời đọc thành video cuối (bước ``mux``).

Vì sao dùng ffmpeg trực tiếp thay vì ``whiteboard compose`` của engine::

    whiteboard compose -o <out> <clips...>  -> chỉ concat video, KHÔNG nhận
    audio (CLI bỏ hẳn tham số audio của hàm nội bộ ``compose_project``).
    Nên dù có gọi engine hay không, việc gắn audio vẫn phải do ffmpeg làm.
    Dùng thẳng ffmpeg cho cả hai việc giữ đúng một đường ống, một chỗ báo lỗi.

Quy ước (bám theo ``TtsService``/``ImageService``/``RenderService``)::

- Video cuối: ``<output_dir>/<article_id>.mp4`` — một file cho mỗi bài, giống
  file audio.
- Ghi ra ``<tên>.part.mp4`` rồi ``replace``: không bao giờ để lại video dở
  dang mang tên chính thức.
- Clip cùng codec/kích thước -> concat ``-c copy`` (nhanh, không mất chất
  lượng). Khác nhau -> re-encode và chuẩn hoá về kích thước clip đầu tiên,
  vì ``-c copy`` không nối được luồng khác tham số.
- Audio ngắn hơn hình -> đệm im lặng (``apad``) cho khớp độ dài hình. Audio
  dài hơn hình -> kéo dài khung hình cuối (``tpad=stop_mode=clone``) và ghi
  cảnh báo; **không bao giờ cắt mất lời đọc**.
- Audio là **bắt buộc**: thiếu audio -> ``ValidationError``, không âm thầm
  xuất video câm (video câm phải là quyết định rõ ràng, không phải tai nạn).
- Kiểm tra lại video cuối bằng ffprobe: phải có **cả** luồng hình và luồng
  tiếng, thời lượng khớp dự kiến trong sai số cho phép.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

from Errors import ConfigurationError, MediaError, ValidationError

#: Kiểm tra độ dài video cuối lệch bao nhiêu giây thì coi là hỏng.
DEFAULT_DURATION_TOLERANCE_SECONDS = 1.0

DEFAULT_OUTPUT_DIR = "output/videos"
DEFAULT_FPS = 24

#: Ghép video có thể lâu hơn render; 0 = không giới hạn.
DEFAULT_TIMEOUT_SECONDS = 1800

#: Tham số dùng khi phải chuẩn hoá clip về cùng kích thước/fps.
FALLBACK_WIDTH = 1280
FALLBACK_HEIGHT = 720

AUDIO_SAMPLE_RATE = 48000
AUDIO_CHANNELS = 2


class MediaService:
    """Ghép clip + audio thành video cuối cho một bài."""

    def __init__(self, config=None, output_dir=None, log_service=None,
                 ffmpeg_path=None, ffprobe_path=None):
        config = config or {}

        self._config = config
        self._output_dir = Path(
            output_dir or config.get("output_dir", DEFAULT_OUTPUT_DIR)
        )
        self._fallback_fps = int(config.get("fps", DEFAULT_FPS))
        self._tolerance = float(
            config.get(
                "duration_tolerance_seconds", DEFAULT_DURATION_TOLERANCE_SECONDS
            )
        )
        self._timeout = float(
            config.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS)
        )
        self._log = log_service

        self._ffmpeg_path = self._resolve_tool(
            ffmpeg_path or config.get("ffmpeg_path"), "FFMPEG_PATH", "ffmpeg"
        )
        self._ffprobe_path = self._resolve_tool(
            ffprobe_path or config.get("ffprobe_path"), "FFPROBE_PATH", "ffprobe"
        )

    # ------------------------------------------------------------------
    # Thuộc tính / khả dụng
    # ------------------------------------------------------------------

    @property
    def output_dir(self):
        return self._output_dir

    @property
    def ffmpeg_path(self):
        return self._ffmpeg_path

    @property
    def ffprobe_path(self):
        return self._ffprobe_path

    def is_available(self):
        """True nếu đã tìm thấy cả ``ffmpeg`` và ``ffprobe``."""
        return (
            self._ffmpeg_path is not None
            and Path(self._ffmpeg_path).is_file()
            and self._ffprobe_path is not None
            and Path(self._ffprobe_path).is_file()
        )

    def ensure_available(self):
        """Raise ``ConfigurationError`` nếu thiếu ffmpeg/ffprobe."""
        if self._ffmpeg_path is None or self._ffprobe_path is None:
            raise ConfigurationError(
                "Không tìm thấy ffmpeg/ffprobe để ghép video. Hãy cài ffmpeg "
                "và thêm vào PATH, hoặc đặt biến môi trường FFMPEG_PATH / "
                "FFPROBE_PATH, hoặc khóa `ffmpeg_path`/`ffprobe_path` trong "
                "Config/media.json."
            )

        if not self.is_available():
            raise ConfigurationError(
                f"Đường dẫn ffmpeg/ffprobe không tồn tại: "
                f"{self._ffmpeg_path} | {self._ffprobe_path}"
            )

    # ------------------------------------------------------------------
    # Đường dẫn
    # ------------------------------------------------------------------

    def output_path(self, article_id):
        """Đường dẫn video cuối của một bài (chống path traversal)."""
        self._require_article_id(article_id)
        return self._output_dir / f"{article_id}.mp4"

    # ------------------------------------------------------------------
    # Ghép video
    # ------------------------------------------------------------------

    def run(self, article_id, clips, audio_path=None):
        """Ghép clip + audio lời đọc thành video cuối.

        Args:
            article_id: id bài viết (dùng làm tên file).
            clips: kết quả của ``RenderService.run`` — list dict có
                ``scene_id``/``path``/``ok``, theo đúng thứ tự cảnh.
            audio_path: file audio lời đọc. **Bắt buộc**: thiếu audio là lỗi
                (raise ``ValidationError``) chứ không âm thầm xuất video câm.

        Returns:
            dict: thông tin video cuối (``video_path``, các mốc thời lượng,
            số clip, có/không chuẩn hoá, có/không kéo dài hình).
        """
        if audio_path is None:
            raise ValidationError(
                "Thiếu audio lời đọc để ghép video; không xuất video câm."
            )

        self.ensure_available()
        clip_paths = self._validate_clips(clips)
        audio = self._validate_audio(audio_path)
        output_path = self.output_path(article_id)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        temp_path = output_path.parent / (
            f"{output_path.stem}.part{output_path.suffix}"
        )
        concat_path = output_path.parent / (
            f"{output_path.stem}.concat{output_path.suffix}"
        )
        self._remove_quietly(temp_path)
        self._remove_quietly(concat_path)

        clip_infos = [self._probe(path) for path in clip_paths]

        for path, info in zip(clip_paths, clip_infos):
            if not info.get("has_video") or info.get("duration", 0) <= 0:
                raise MediaError(f"Clip không có luồng hình hợp lệ: {path}")

        video_duration = sum(float(info["duration"]) for info in clip_infos)

        audio_info = self._probe(audio)

        if not audio_info.get("has_audio") or audio_info.get("duration", 0) <= 0:
            raise MediaError(f"File audio không có luồng tiếng hợp lệ: {audio}")

        audio_duration = float(audio_info["duration"])

        normalized = not self._compatible(clip_infos)
        extend_video = audio_duration - video_duration > self._tolerance

        if extend_video:
            self._log_warning(
                "media_audio_longer_than_video",
                "Audio dài hơn hình; kéo dài khung hình cuối để giữ đủ lời đọc.",
                article_id=article_id,
                audio_seconds=round(audio_duration, 3),
                video_seconds=round(video_duration, 3),
            )

        try:
            self._concat(
                clip_paths,
                concat_path,
                normalized=normalized,
                reference=clip_infos[0],
            )

            target_duration = audio_duration if extend_video else video_duration

            self._mux(
                concat_path,
                audio,
                temp_path,
                video_duration=video_duration,
                target_duration=target_duration,
                extend_video=extend_video,
            )

            info = self._verify_output(temp_path, target_duration)
        except BaseException:
            self._remove_quietly(temp_path)
            raise
        finally:
            self._remove_quietly(concat_path)

        temp_path.replace(output_path)

        self._log_info(
            "media_created",
            f"Đã ghép video cuối: {output_path}",
            article_id=article_id,
            clips=len(clip_paths),
            seconds=round(info["duration"], 3),
            bytes=output_path.stat().st_size,
        )

        return {
            "video_path": str(output_path),
            "duration_seconds": round(float(info["duration"]), 3),
            "target_duration_seconds": round(float(target_duration), 3),
            "video_duration_seconds": round(float(video_duration), 3),
            "audio_duration_seconds": round(float(audio_duration), 3),
            "clip_count": len(clip_paths),
            "normalized": normalized,
            "extended": bool(extend_video),
        }

    # ------------------------------------------------------------------
    # Concat / mux (tách riêng để test thay thế không cần ffmpeg thật)
    # ------------------------------------------------------------------

    def _concat(self, clip_paths, dest, normalized=False, reference=None):
        """Nối các clip thành một file video tại ``dest``."""
        if not normalized and len(clip_paths) == 1:
            shutil.copyfile(clip_paths[0], dest)
            return

        if not normalized:
            list_file = dest.with_suffix(".txt")
            list_file.write_text(
                "".join(f"file '{path.resolve()}'\n" for path in clip_paths),
                encoding="utf-8",
            )

            try:
                self._run_ffmpeg(
                    [
                        str(self._ffmpeg_path),
                        "-y",
                        "-f", "concat",
                        "-safe", "0",
                        "-i", str(list_file),
                        "-c", "copy",
                        str(dest),
                    ]
                )
            finally:
                self._remove_quietly(list_file)

            return

        width, height, fps = self._normalize_target(reference)
        parts = []
        inputs = []

        for index, path in enumerate(clip_paths):
            inputs.extend(["-i", str(path)])
            parts.append(
                f"[{index}:v:0]scale={width}:{height}:"
                "force_original_aspect_ratio=decrease,"
                f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,"
                f"setsar=1,fps={fps}[v{index}]"
            )

        joined = "".join(f"[v{index}]" for index in range(len(clip_paths)))
        filter_complex = (
            ";".join(parts)
            + f";{joined}concat=n={len(clip_paths)}:v=1:a=0[outv]"
        )

        self._run_ffmpeg(
            [
                str(self._ffmpeg_path),
                "-y",
                *inputs,
                "-filter_complex", filter_complex,
                "-map", "[outv]",
                "-c:v", "libx264",
                "-pix_fmt", "yuv420p",
                "-movflags", "+faststart",
                str(dest),
            ]
        )

    def _mux(self, video_path, audio_path, dest, video_duration,
             target_duration, extend_video):
        """Gắn audio vào video; đệm im lặng hoặc kéo dài hình khi cần."""
        target = f"{target_duration:.6f}"

        if extend_video:
            pad = f"{max(0.0, target_duration - video_duration):.6f}"
            filter_complex = (
                f"[0:v]tpad=stop_mode=clone:stop_duration={pad},"
                "format=yuv420p[v];"
                f"[1:a]apad=whole_dur={target}[a]"
            )

            self._run_ffmpeg(
                [
                    str(self._ffmpeg_path),
                    "-y",
                    "-i", str(video_path),
                    "-i", str(audio_path),
                    "-filter_complex", filter_complex,
                    "-map", "[v]",
                    "-map", "[a]",
                    "-c:v", "libx264",
                    "-pix_fmt", "yuv420p",
                    "-c:a", "aac",
                    "-ar", str(AUDIO_SAMPLE_RATE),
                    "-ac", str(AUDIO_CHANNELS),
                    "-t", target,
                    "-movflags", "+faststart",
                    str(dest),
                ]
            )
            return

        # Hình đủ dài: giữ nguyên video, chỉ đệm im lặng cho audio.
        self._run_ffmpeg(
            [
                str(self._ffmpeg_path),
                "-y",
                "-i", str(video_path),
                "-i", str(audio_path),
                "-filter_complex", f"[1:a]apad=whole_dur={target}[a]",
                "-map", "0:v:0",
                "-map", "[a]",
                "-c:v", "copy",
                "-c:a", "aac",
                "-ar", str(AUDIO_SAMPLE_RATE),
                "-ac", str(AUDIO_CHANNELS),
                "-t", target,
                "-movflags", "+faststart",
                str(dest),
            ]
        )

    # ------------------------------------------------------------------
    # Nội bộ
    # ------------------------------------------------------------------

    def _execute(self, command):
        """Chạy tiến trình con. Tách riêng để test thay thế mà không cần thật."""
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=self._timeout or None,
        )

    def _run_ffmpeg(self, command):
        """Chạy ffmpeg và chuyển mọi lỗi hạ tầng thành ``MediaError``."""
        try:
            result = self._execute(command)
        except subprocess.TimeoutExpired as error:
            raise MediaError(
                f"ffmpeg quá thời gian cho phép ({self._timeout:g} giây)."
            ) from error
        except OSError as error:
            raise MediaError(
                f"Không chạy được ffmpeg: {type(error).__name__}: {error}"
            ) from error

        if result.returncode != 0:
            raise MediaError(
                f"ffmpeg thất bại (mã thoát {result.returncode}).\n"
                f"STDERR:\n{result.stderr}\nSTDOUT:\n{result.stdout}"
            )

        return result

    def _probe(self, path):
        """Đọc thông tin luồng/thời lượng của một file media bằng ffprobe."""
        command = [
            str(self._ffprobe_path),
            "-v", "error",
            "-print_format", "json",
            "-show_format",
            "-show_streams",
            str(path),
        ]

        try:
            result = self._execute(command)
        except subprocess.TimeoutExpired as error:
            raise MediaError(
                f"ffprobe quá thời gian cho phép ({self._timeout:g} giây): {path}"
            ) from error
        except OSError as error:
            raise MediaError(
                f"Không chạy được ffprobe: {type(error).__name__}: {error}"
            ) from error

        if result.returncode != 0:
            raise MediaError(
                f"ffprobe không đọc được file {path} (mã thoát "
                f"{result.returncode}): {result.stderr}"
            )

        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError as error:
            raise MediaError(
                f"ffprobe trả về JSON không hợp lệ cho {path}: {error}"
            ) from error

        return self._summarize(data)

    def _verify_output(self, path, target_duration):
        """Kiểm tra video cuối tồn tại, có hình + tiếng và đúng thời lượng."""
        if not path.is_file() or path.stat().st_size == 0:
            raise MediaError(f"ffmpeg không tạo được video hợp lệ: {path}")

        info = self._probe(path)

        if not info.get("has_video"):
            raise MediaError(f"Video cuối thiếu luồng hình: {path}")

        if not info.get("has_audio"):
            raise MediaError(f"Video cuối thiếu luồng tiếng: {path}")

        duration = float(info.get("duration") or 0)

        if duration <= 0:
            raise MediaError(f"Không đọc được thời lượng video cuối: {path}")

        if abs(duration - target_duration) > self._tolerance:
            raise MediaError(
                f"Thời lượng video cuối lệch quá cho phép: được {duration:.2f}s, "
                f"dự kiến {target_duration:.2f}s (sai số {self._tolerance:g}s) "
                f"tại {path}"
            )

        return info

    @staticmethod
    def _summarize(data):
        """Chuẩn hoá output ffprobe thành dict gọn dùng cho kiểm tra."""
        streams = data.get("streams") or []
        video = next(
            (item for item in streams if item.get("codec_type") == "video"), None
        )
        has_audio = any(
            item.get("codec_type") == "audio" for item in streams
        )

        duration = _to_positive_float((data.get("format") or {}).get("duration"))

        if duration is None:
            durations = [
                _to_positive_float(item.get("duration")) for item in streams
            ]
            durations = [value for value in durations if value]
            duration = max(durations) if durations else 0.0

        return {
            "duration": duration,
            "has_video": video is not None,
            "has_audio": has_audio,
            "codec_name": (video or {}).get("codec_name"),
            "width": (video or {}).get("width"),
            "height": (video or {}).get("height"),
            "pix_fmt": (video or {}).get("pix_fmt"),
            "fps": _parse_frame_rate((video or {}).get("r_frame_rate")),
        }

    def _normalize_target(self, reference):
        """Kích thước/fps đích khi phải chuẩn hoá clip khác tham số."""
        width = _to_even(int(reference.get("width") or 0), FALLBACK_WIDTH)
        height = _to_even(int(reference.get("height") or 0), FALLBACK_HEIGHT)
        fps = reference.get("fps")
        fps = float(fps) if fps else float(self._fallback_fps)

        return width, height, fps

    @staticmethod
    def _compatible(clip_infos):
        """True nếu mọi clip cùng codec/kích thước/pix_fmt -> concat copy được."""
        first = clip_infos[0]
        signature = (first.get("codec_name"), first.get("width"),
                     first.get("height"), first.get("pix_fmt"))

        return all(
            (info.get("codec_name"), info.get("width"),
             info.get("height"), info.get("pix_fmt")) == signature
            for info in clip_infos
        )

    @staticmethod
    def _validate_clips(clips):
        if not isinstance(clips, list) or not clips:
            raise ValidationError("Không có clip nào để ghép video.")

        paths = []

        for item in clips:
            if not isinstance(item, dict) or not item.get("ok") or not item.get("path"):
                raise ValidationError("Có clip chưa sẵn sàng để ghép video.")

            path = Path(item["path"])

            if not path.is_file():
                raise MediaError(f"Không tìm thấy clip: {path}")

            if path.stat().st_size == 0:
                raise MediaError(f"Clip rỗng: {path}")

            paths.append(path)

        return paths

    @staticmethod
    def _validate_audio(audio_path):
        if audio_path is None:
            raise ValidationError(
                "Thiếu audio lời đọc để ghép video; không xuất video câm."
            )

        path = Path(audio_path)

        if not path.is_file():
            raise MediaError(f"Không tìm thấy file audio: {path}")

        if path.stat().st_size == 0:
            raise MediaError(f"File audio rỗng: {path}")

        return path

    @staticmethod
    def _require_article_id(article_id):
        if not article_id or Path(str(article_id)).name != str(article_id):
            raise ValidationError("id không hợp lệ để tạo tên video.")

    @staticmethod
    def _resolve_tool(explicit, env_name, executable):
        candidate = explicit or os.getenv(env_name)

        if candidate:
            return str(candidate)

        return shutil.which(executable)

    @staticmethod
    def _remove_quietly(path):
        try:
            Path(path).unlink(missing_ok=True)
        except OSError:  # pragma: no cover - không xoá được thì bỏ qua
            pass

    def _log_info(self, event, message="", **fields):
        if self._log is not None:
            self._log.info(event, message, **fields)

    def _log_warning(self, event, message="", **fields):
        if self._log is not None:
            self._log.warning(event, message, **fields)


def _to_positive_float(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None

    if number <= 0:
        return None

    return number


def _parse_frame_rate(value):
    """Đọc ``r_frame_rate`` của ffprobe (dạng ``"24/1"`` hoặc ``"30000/1001"``)."""
    if not value:
        return None

    text = str(value)

    if "/" in text:
        numerator, _, denominator = text.partition("/")

        try:
            denominator = float(denominator)
            if denominator == 0:
                return None
            return _to_positive_float(float(numerator) / denominator)
        except (TypeError, ValueError):
            return None

    return _to_positive_float(text)


def _to_even(value, fallback):
    if value <= 0:
        value = fallback

    return value - (value % 2)
