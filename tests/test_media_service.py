import json
import subprocess
from pathlib import Path

import pytest

from Errors import ConfigurationError, MediaError, ValidationError
from Services.MediaService import MediaService, _parse_frame_rate


class FakeCompleted:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class FakeProbe:
    """ffprobe giả: trả thông tin theo tên file, có giá trị mặc định."""

    def __init__(self, infos=None, default=None):
        self.infos = dict(infos or {})
        self.default = default
        self.calls = []

    def __call__(self, path):
        name = Path(path).name
        self.calls.append(name)

        if name in self.infos:
            return dict(self.infos[name])

        if self.default is not None:
            return dict(self.default)

        raise AssertionError(f"FakeProbe không có thông tin cho {name!r}.")


def video_info(duration=5.0, codec="h264", width=1280, height=720,
               pix_fmt="yuv420p", fps=24.0):
    return {
        "duration": duration,
        "has_video": True,
        "has_audio": False,
        "codec_name": codec,
        "width": width,
        "height": height,
        "pix_fmt": pix_fmt,
        "fps": fps,
    }


def audio_info(duration=8.0):
    return {
        "duration": duration,
        "has_video": False,
        "has_audio": True,
        "codec_name": None,
        "width": None,
        "height": None,
        "pix_fmt": None,
        "fps": None,
    }


def output_info(duration, has_audio=True):
    return {
        "duration": duration,
        "has_video": True,
        "has_audio": has_audio,
        "codec_name": "h264",
        "width": 1280,
        "height": 720,
        "pix_fmt": "yuv420p",
        "fps": 24.0,
    }


def make_service(tmp_path, infos=None, execute=None, default_probe=None,
                 **options):
    ffmpeg = tmp_path / "ffmpeg.exe"
    ffmpeg.write_bytes(b"stub")
    ffprobe = tmp_path / "ffprobe.exe"
    ffprobe.write_bytes(b"stub")

    log_service = options.pop("log_service", None)

    service = MediaService(
        {"duration_tolerance_seconds": 1.0, **options},
        output_dir=tmp_path / "videos",
        ffmpeg_path=ffmpeg,
        ffprobe_path=ffprobe,
        log_service=log_service,
    )

    calls = []

    def default_execute(command):
        calls.append(command)
        output = Path(command[-1])
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"video-bytes")
        return FakeCompleted()

    service._execute = execute or default_execute

    probe = FakeProbe(infos, default=default_probe)
    service._probe = probe

    return service, calls, probe


def standard(tmp_path, article_id="bai-1", clip_count=2, clip_duration=5.0,
             audio_duration=8.0, execute=None, **config):
    """Dựng service + clip + audio + thông tin ffprobe khớp thực tế."""
    clips = []
    infos = {}
    tolerance = config.get("duration_tolerance_seconds", 1.0)

    for index in range(1, clip_count + 1):
        name = f"scene_{index:03d}.mp4"
        path = tmp_path / "clips" / article_id / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"clip-bytes")
        clips.append({"scene_id": index, "path": str(path), "ok": True})
        infos[name] = video_info(duration=clip_duration)

    audio = None

    if audio_duration is not None:
        audio = tmp_path / "outputmp3" / f"{article_id}.mp3"
        audio.parent.mkdir(parents=True, exist_ok=True)
        audio.write_bytes(b"audio-bytes")
        infos[f"{article_id}.mp3"] = audio_info(duration=audio_duration)

    video_total = clip_count * clip_duration

    if audio_duration is None or audio_duration - video_total <= tolerance:
        target = video_total
    else:
        target = audio_duration

    infos[f"{article_id}.part.mp4"] = output_info(
        duration=target, has_audio=audio_duration is not None
    )

    service, calls, probe = make_service(
        tmp_path, infos=infos, execute=execute, **config
    )
    return service, calls, probe, clips, audio


# ----------------------------------------------------------------------
# Luồng ghép cơ bản
# ----------------------------------------------------------------------


def test_run_concatenates_clips_and_muxes_audio(tmp_path):
    service, calls, probe, clips, audio = standard(tmp_path)

    result = service.run("bai-1", clips, audio)

    output = Path(result["video_path"])
    assert output == tmp_path / "videos" / "bai-1.mp4"
    assert output.read_bytes() == b"video-bytes"
    assert not (tmp_path / "videos" / "bai-1.part.mp4").exists()
    assert result["clip_count"] == 2
    assert result["audio_duration_seconds"] == 8.0
    assert len(calls) == 2  # concat + mux


def test_concat_uses_copy_when_clips_match(tmp_path):
    service, calls, _, clips, audio = standard(tmp_path)

    service.run("bai-1", clips, audio)

    concat = calls[0]
    assert concat[concat.index("-f") + 1] == "concat"
    assert concat[concat.index("-c") + 1] == "copy"


def test_mux_copies_video_when_audio_is_shorter(tmp_path):
    service, calls, _, clips, audio = standard(tmp_path, audio_duration=6.0)

    service.run("bai-1", clips, audio)

    mux = calls[1]
    assert mux[mux.index("-c:v") + 1] == "copy"
    assert "apad" in mux[mux.index("-filter_complex") + 1]


def test_single_clip_skips_concat(tmp_path):
    service, calls, _, clips, audio = standard(
        tmp_path, clip_count=1, clip_duration=5.0, audio_duration=3.0
    )

    result = service.run("bai-1", clips, audio)

    # Một clip: chỉ copy file, không cần ffmpeg concat -> chỉ còn lệnh mux.
    assert len(calls) == 1
    assert result["clip_count"] == 1


def test_audio_longer_than_video_extends_last_frame(tmp_path, null_log):
    service, calls, _, clips, audio = standard(
        tmp_path, audio_duration=14.0, log_service=null_log
    )

    result = service.run("bai-1", clips, audio)

    mux = calls[1]
    assert "tpad" in mux[mux.index("-filter_complex") + 1]
    assert result["extended"] is True
    assert result["target_duration_seconds"] == 14.0


def test_incompatible_clips_are_normalized(tmp_path):
    service, calls, probe, clips, audio = standard(tmp_path)

    # Clip thứ hai khác kích thước -> phải re-encode, không copy được.
    probe.infos["scene_002.mp4"] = video_info(width=640, height=360)

    result = service.run("bai-1", clips, audio)

    assert result["normalized"] is True
    concat = calls[0]
    filter_complex = concat[concat.index("-filter_complex") + 1]
    assert "scale=" in filter_complex and "concat=n=2" in filter_complex


def test_audio_duration_is_recorded(tmp_path):
    service, _, _, clips, audio = standard(tmp_path, audio_duration=6.0)

    result = service.run("bai-1", clips, audio)

    assert result["audio_duration_seconds"] == 6.0
    assert Path(result["video_path"]).is_file()


# ----------------------------------------------------------------------
# Kiểm tra đầu vào
# ----------------------------------------------------------------------


def test_empty_clip_list_is_rejected(tmp_path):
    service, _, _, _, _ = standard(tmp_path)

    with pytest.raises(ValidationError):
        service.run("bai-1", [], None)


def test_failed_clip_is_rejected(tmp_path):
    service, _, _, clips, audio = standard(tmp_path)
    clips[0]["ok"] = False
    clips[0]["path"] = None

    with pytest.raises(ValidationError):
        service.run("bai-1", clips, audio)


def test_missing_clip_file_is_rejected(tmp_path):
    service, _, _, clips, audio = standard(tmp_path)
    Path(clips[0]["path"]).unlink()

    with pytest.raises(MediaError):
        service.run("bai-1", clips, audio)


def test_missing_audio_is_rejected(tmp_path):
    service, _, _, clips, _ = standard(tmp_path, audio_duration=None)

    with pytest.raises(MediaError):
        service.run("bai-1", clips, tmp_path / "khong-co.mp3")


def test_no_audio_argument_is_rejected(tmp_path):
    service, _, _, clips, _ = standard(tmp_path, audio_duration=None)

    with pytest.raises(ValidationError):
        service.run("bai-1", clips, None)


def test_invalid_article_id_is_rejected(tmp_path):
    service, _, _, clips, audio = standard(tmp_path)

    with pytest.raises(ValidationError):
        service.run("../thoat-ra-ngoai", clips, audio)


# ----------------------------------------------------------------------
# ffmpeg/ffprobe
# ----------------------------------------------------------------------


def test_missing_ffmpeg_raises_configuration_error(tmp_path):
    service = MediaService(
        {}, output_dir=tmp_path / "videos",
        ffmpeg_path=tmp_path / "khong-co.exe",
        ffprobe_path=tmp_path / "khong-co-2.exe",
    )
    clips = [{"scene_id": 1, "path": str(tmp_path / "a.mp4"), "ok": True}]

    with pytest.raises(ConfigurationError) as excinfo:
        service.run("bai-1", clips, tmp_path / "a.mp3")

    assert "ffmpeg" in str(excinfo.value).lower()


def test_ffmpeg_failure_raises_media_error_without_partial(tmp_path):
    def boom(command):
        calls.append(command)
        return FakeCompleted(returncode=1, stderr="lỗi")

    calls = []
    service, _, _, clips, audio = standard(tmp_path, execute=boom)

    with pytest.raises(MediaError) as excinfo:
        service.run("bai-1", clips, audio)

    assert "mã thoát 1" in str(excinfo.value)
    assert not (tmp_path / "videos" / "bai-1.mp4").exists()
    assert not (tmp_path / "videos" / "bai-1.part.mp4").exists()


def test_timeout_raises_media_error(tmp_path):
    def boom(command):
        raise subprocess.TimeoutExpired(cmd=command, timeout=1)

    service, _, _, clips, audio = standard(tmp_path, execute=boom)

    with pytest.raises(MediaError) as excinfo:
        service.run("bai-1", clips, audio)

    assert "thời gian" in str(excinfo.value)


# ----------------------------------------------------------------------
# Kiểm tra video cuối
# ----------------------------------------------------------------------


def test_output_without_audio_stream_is_rejected(tmp_path):
    service, _, probe, clips, audio = standard(tmp_path)
    probe.infos["bai-1.part.mp4"] = output_info(duration=10.0, has_audio=False)

    with pytest.raises(MediaError) as excinfo:
        service.run("bai-1", clips, audio)

    assert "tiếng" in str(excinfo.value)


def test_duration_mismatch_is_rejected(tmp_path):
    service, _, probe, clips, audio = standard(tmp_path)
    probe.infos["bai-1.part.mp4"] = output_info(duration=30.0)

    with pytest.raises(MediaError) as excinfo:
        service.run("bai-1", clips, audio)

    assert "thời lượng" in str(excinfo.value).lower()


def test_clip_without_video_stream_is_rejected(tmp_path):
    service, _, probe, clips, audio = standard(tmp_path)
    probe.infos["scene_001.mp4"] = audio_info(duration=5.0)

    with pytest.raises(MediaError):
        service.run("bai-1", clips, audio)


# ----------------------------------------------------------------------
# Hàm tiện ích
# ----------------------------------------------------------------------


def test_parse_frame_rate_handles_fractions_and_integers():
    assert _parse_frame_rate("24/1") == 24.0
    assert _parse_frame_rate("30000/1001") == pytest.approx(29.97, abs=0.01)
    assert _parse_frame_rate("25") == 25.0
    assert _parse_frame_rate("0/0") is None
    assert _parse_frame_rate(None) is None


def test_summarize_reads_format_duration_and_streams():
    data = {
        "format": {"duration": "12.5"},
        "streams": [
            {"codec_type": "video", "codec_name": "h264", "width": 1920,
             "height": 1080, "pix_fmt": "yuv420p", "r_frame_rate": "24/1"},
            {"codec_type": "audio", "codec_name": "aac"},
        ],
    }

    info = MediaService._summarize(data)

    assert info["duration"] == 12.5
    assert info["has_video"] is True
    assert info["has_audio"] is True
    assert info["width"] == 1920
    assert info["fps"] == 24.0


def test_summarize_falls_back_to_stream_duration():
    data = {
        "format": {},
        "streams": [{"codec_type": "video", "duration": "7.5",
                     "r_frame_rate": "24/1"}],
    }

    info = MediaService._summarize(data)

    assert info["duration"] == 7.5
    assert info["has_audio"] is False


def test_probe_parses_ffprobe_json(tmp_path, monkeypatch):
    service = MediaService({}, output_dir=tmp_path, ffprobe_path=tmp_path / "ffprobe")

    payload = {
        "format": {"duration": "3.0"},
        "streams": [{"codec_type": "video", "codec_name": "h264",
                     "width": 1280, "height": 720, "r_frame_rate": "24/1"}],
    }

    monkeypatch.setattr(
        "Services.MediaService.subprocess.run",
        lambda *a, **k: FakeCompleted(stdout=json.dumps(payload)),
    )

    info = service._probe(tmp_path / "a.mp4")

    assert info["duration"] == 3.0
    assert info["has_video"] is True
