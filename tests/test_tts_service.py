import subprocess
from pathlib import Path

import pytest

from Errors import ConfigurationError, MediaError, ValidationError
from Services.TtsService import TtsService


class FakeCommunicate:
    """Đối tượng giả thay cho edge_tts.Communicate (không gọi mạng)."""

    def __init__(self, data=b"audio-bytes"):
        self._data = data

    async def save(self, path):
        Path(path).write_bytes(self._data)


def build_service(tmp_path, data=b"audio-bytes", **config):
    service = TtsService(
        {
            "provider": "edge_tts",
            "output_dir": str(tmp_path / "mp3"),
            "providers": {"edge_tts": {"voice": "vi-VN-HoaiMyNeural"}},
            **config,
        }
    )
    service.build_communicate = lambda text: FakeCommunicate(data)
    return service


def test_run_creates_file_and_returns_path(tmp_path):
    service = build_service(tmp_path)

    path = service.run("bai-1", "Nội dung đọc.")

    assert Path(path).is_file()
    assert Path(path).name == "bai-1.mp3"
    assert Path(path).stat().st_size > 0


def test_run_leaves_no_partial_file_on_failure(tmp_path):
    class BrokenCommunicate:
        async def save(self, path):
            Path(path).write_bytes(b"partial")
            raise RuntimeError("đứt kết nối")

    service = build_service(tmp_path)
    service.build_communicate = lambda text: BrokenCommunicate()

    with pytest.raises(MediaError):
        service.run("bai-2", "Nội dung đọc.")

    assert not (tmp_path / "mp3" / "bai-2.mp3").exists()
    assert not (tmp_path / "mp3" / "bai-2.mp3.part").exists()


def test_empty_audio_file_is_rejected(tmp_path):
    service = build_service(tmp_path, data=b"")

    with pytest.raises(MediaError):
        service.run("bai-3", "Nội dung đọc.")

    assert not (tmp_path / "mp3" / "bai-3.mp3").exists()


def test_empty_text_is_rejected(tmp_path):
    service = build_service(tmp_path)

    with pytest.raises(ValidationError):
        service.run("bai-4", "   ")


def test_invalid_article_id_is_rejected(tmp_path):
    service = build_service(tmp_path)

    with pytest.raises(ValidationError):
        service.run("../thoat-ra-ngoai", "Nội dung.")


def test_unsupported_provider_raises_configuration_error(tmp_path):
    with pytest.raises(ConfigurationError):
        TtsService({"provider": "google", "providers": {}})


def test_duration_uses_mutagen_when_available(tmp_path, monkeypatch):
    audio = tmp_path / "a.mp3"
    audio.write_bytes(b"data")

    class FakeInfo:
        length = 12.345

    class FakeMp3:
        def __init__(self, path):
            self.info = FakeInfo()

    monkeypatch.setattr("Services.TtsService.MP3", FakeMp3)

    assert build_service(tmp_path).duration(audio) == 12.35


def test_duration_falls_back_to_ffprobe(tmp_path, monkeypatch):
    audio = tmp_path / "a.mp3"
    audio.write_bytes(b"data")

    monkeypatch.setattr("Services.TtsService.MP3", None)

    completed = subprocess.CompletedProcess(
        args=["ffprobe"], returncode=0, stdout="7.5\n", stderr=""
    )
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: completed)

    service = TtsService(
        {"provider": "edge_tts"},
        output_dir=tmp_path,
        ffprobe_path="ffprobe",
    )

    assert service.duration(audio) == 7.5


def test_duration_raises_when_nothing_can_probe(tmp_path, monkeypatch):
    audio = tmp_path / "a.mp3"
    audio.write_bytes(b"data")

    monkeypatch.setattr("Services.TtsService.MP3", None)

    def raise_os_error(*args, **kwargs):
        raise OSError("không chạy được ffprobe")

    monkeypatch.setattr(subprocess, "run", raise_os_error)

    service = TtsService(
        {"provider": "edge_tts"}, output_dir=tmp_path, ffprobe_path="ffprobe"
    )

    with pytest.raises(MediaError):
        service.duration(audio)


def test_duration_rejects_missing_file(tmp_path):
    service = build_service(tmp_path)

    with pytest.raises(MediaError):
        service.duration(tmp_path / "khong-ton-tai.mp3")


def test_duration_rejects_empty_file(tmp_path):
    audio = tmp_path / "empty.mp3"
    audio.write_bytes(b"")

    with pytest.raises(MediaError):
        build_service(tmp_path).duration(audio)


def test_ffprobe_from_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("FFPROBE_PATH", "D:/tools/ffprobe.exe")

    assert build_service(tmp_path).ffprobe_executable() == "D:/tools/ffprobe.exe"


# ----------------------------------------------------------------------
# Fingerprint (Phase 4b: đổi voice phải làm lại audio)
# ----------------------------------------------------------------------


def test_fingerprint_is_stable_for_same_input(tmp_path):
    first = build_service(tmp_path).fingerprint("Nội dung đọc.")
    second = build_service(tmp_path).fingerprint("Nội dung đọc.")

    assert first == second


def test_fingerprint_changes_when_voice_changes(tmp_path):
    base = build_service(tmp_path).fingerprint("Nội dung đọc.")

    other = build_service(
        tmp_path, providers={"edge_tts": {"voice": "vi-VN-NamMinhNeural"}}
    ).fingerprint("Nội dung đọc.")

    assert base != other


def test_fingerprint_changes_when_text_changes(tmp_path):
    service = build_service(tmp_path)

    assert service.fingerprint("Câu một.") != service.fingerprint("Câu hai.")
