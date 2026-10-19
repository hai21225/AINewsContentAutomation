import subprocess
from pathlib import Path

import pytest

from Errors import ConfigurationError, MediaError, ValidationError
from Services.RenderService import PROJECT_ROOT, RenderService


class FakeCompleted:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def write_clip(command, returncode=0):
    """Giả lập engine: tạo file tại đúng đường dẫn sau ``-o``."""
    output = Path(command[command.index("-o") + 1])
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(b"video-bytes")
    return FakeCompleted(returncode=returncode)


def build_service(tmp_path, execute=None, **config):
    cli_path = tmp_path / "whiteboard.exe"
    cli_path.write_bytes(b"stub")

    service = RenderService(
        {"duration_seconds": 5, "fps": 24, **config},
        output_dir=tmp_path / "clips",
        cli_path=cli_path,
    )

    calls = []

    def runner(command):
        calls.append(command)

        if execute is not None:
            return execute(command)

        return write_clip(command)

    service._execute = runner
    return service, calls


def make_image(tmp_path, name="scene_001.png"):
    path = tmp_path / name
    path.write_bytes(b"png-bytes")
    return path


# ----------------------------------------------------------------------
# Lệnh gọi engine (phần bắt buộc của Phase 4)
# ----------------------------------------------------------------------


def test_command_is_the_mandated_render_image_command(tmp_path):
    service, calls = build_service(tmp_path)
    image = make_image(tmp_path)
    output = tmp_path / "clips" / "bai-1" / "scene_001.mp4"

    service.render_clip(image, output)

    command = calls[0]

    assert command[0] == str(service.cli_path)
    assert command[1] == "render-image"
    assert command[2] == str(image)
    assert command[3] == "-o"
    assert command[5] == "--duration"
    assert command[6] == "5.0"
    assert command[7] == "--fps"
    assert command[8] == "24"


def test_render_photo_is_never_used(tmp_path):
    service, calls = build_service(tmp_path)

    service.render_clip(make_image(tmp_path), tmp_path / "out.mp4")

    assert "render-photo" not in calls[0]
    assert "--lineart-provider" not in calls[0]


def test_duration_and_fps_can_be_overridden_per_call(tmp_path):
    service, calls = build_service(tmp_path)

    service.render_clip(
        make_image(tmp_path), tmp_path / "out.mp4", duration=2.5, fps=12
    )

    command = calls[0]
    assert command[command.index("--duration") + 1] == "2.5"
    assert command[command.index("--fps") + 1] == "12"


def test_relative_paths_are_made_absolute_for_engine(tmp_path, monkeypatch):
    """Regression: engine chạy với cwd=engine_dir nên cần đường dẫn tuyệt đối."""
    monkeypatch.chdir(tmp_path)
    service, calls = build_service(tmp_path)
    (tmp_path / "scene_001.jpg").write_bytes(b"jpg-bytes")

    service.render_clip("scene_001.jpg", "clips/bai-1/scene_001.mp4")

    command = calls[0]
    assert Path(command[2]) == (tmp_path / "scene_001.jpg").resolve()
    assert Path(command[command.index("-o") + 1]).is_absolute()
    assert Path(command[command.index("-o") + 1]).name == "scene_001.part.mp4"


def test_engine_runs_inside_engine_dir(tmp_path, monkeypatch):
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return write_clip(command)

    monkeypatch.setattr("Services.RenderService.subprocess.run", fake_run)

    cli_path = tmp_path / "whiteboard.exe"
    cli_path.write_bytes(b"stub")
    service = RenderService(
        {}, output_dir=tmp_path / "clips", cli_path=cli_path
    )

    service.render_clip(make_image(tmp_path), tmp_path / "out.mp4")

    assert captured["kwargs"]["cwd"] == str(service.engine_dir)
    assert captured["kwargs"]["encoding"] == "utf-8"


# ----------------------------------------------------------------------
# Đường dẫn CLI / engine
# ----------------------------------------------------------------------


def test_default_engine_dir_is_the_vendored_engine():
    service = RenderService({})

    assert service.engine_dir == PROJECT_ROOT / (
        "Lib/WhiteBoardEngine/whiteboard-video-engine"
    )
    assert service.cli_path.name in {"whiteboard.exe", "whiteboard"}
    assert service.cli_path.parent.name in {"Scripts", "bin"}


def test_cli_path_override_from_config(tmp_path):
    override = tmp_path / "engine.exe"

    service = RenderService({"cli_path": str(override)})

    assert service.cli_path == override


def test_cli_path_override_from_environment(tmp_path, monkeypatch):
    override = tmp_path / "engine.exe"
    monkeypatch.setenv("WHITEBOARD_CLI", str(override))

    service = RenderService({})

    assert service.cli_path == override


def test_is_available_follows_cli_presence(tmp_path):
    service, _ = build_service(tmp_path)
    assert service.is_available() is True

    missing = RenderService({}, cli_path=tmp_path / "khong-co.exe")
    assert missing.is_available() is False


def test_missing_cli_raises_configuration_error(tmp_path):
    service = RenderService({}, cli_path=tmp_path / "khong-co.exe")

    with pytest.raises(ConfigurationError) as excinfo:
        service.render_clip(make_image(tmp_path), tmp_path / "out.mp4")

    assert "whiteboard" in str(excinfo.value).lower()


# ----------------------------------------------------------------------
# Kiểm tra ảnh đầu vào & output
# ----------------------------------------------------------------------


def test_missing_input_image_raises_media_error(tmp_path):
    service, _ = build_service(tmp_path)

    with pytest.raises(MediaError):
        service.render_clip(tmp_path / "khong-co.png", tmp_path / "out.mp4")


def test_empty_input_image_raises_media_error(tmp_path):
    service, _ = build_service(tmp_path)
    empty = tmp_path / "rong.png"
    empty.write_bytes(b"")

    with pytest.raises(MediaError):
        service.render_clip(empty, tmp_path / "out.mp4")


def test_clip_is_written_atomically(tmp_path):
    service, _ = build_service(tmp_path)
    output = tmp_path / "clips" / "bai-1" / "scene_001.mp4"

    result = service.render_clip(make_image(tmp_path), output)

    assert result == str(output)
    assert output.read_bytes() == b"video-bytes"
    assert not (output.parent / "scene_001.part.mp4").exists()


def test_engine_failure_raises_media_error_without_partial_file(tmp_path):
    service, _ = build_service(
        tmp_path,
        execute=lambda command: write_clip(command, returncode=1),
    )
    output = tmp_path / "clips" / "bai-1" / "scene_001.mp4"

    with pytest.raises(MediaError) as excinfo:
        service.render_clip(make_image(tmp_path), output)

    assert "mã thoát 1" in str(excinfo.value)
    assert not output.exists()
    assert not (output.parent / "scene_001.part.mp4").exists()


def test_no_output_file_is_rejected(tmp_path):
    service, _ = build_service(
        tmp_path, execute=lambda command: FakeCompleted(returncode=0)
    )

    with pytest.raises(MediaError):
        service.render_clip(make_image(tmp_path), tmp_path / "out.mp4")


def test_timeout_raises_media_error(tmp_path):
    def boom(command):
        raise subprocess.TimeoutExpired(cmd=command, timeout=1)

    service, _ = build_service(tmp_path, execute=boom)

    with pytest.raises(MediaError) as excinfo:
        service.render_clip(make_image(tmp_path), tmp_path / "out.mp4")

    assert "thời gian" in str(excinfo.value)


# ----------------------------------------------------------------------
# Chạy theo bài
# ----------------------------------------------------------------------


def test_run_creates_one_clip_per_scene(tmp_path):
    service, calls = build_service(tmp_path)
    images = [
        {"scene_id": 1, "path": str(make_image(tmp_path, "a.png")), "ok": True},
        {"scene_id": 2, "path": str(make_image(tmp_path, "b.png")), "ok": True},
    ]

    results = service.run("bai-1", images)

    assert [item["ok"] for item in results] == [True, True]
    assert [Path(item["path"]).name for item in results] == [
        "scene_001.mp4",
        "scene_002.mp4",
    ]
    assert (tmp_path / "clips" / "bai-1" / "scene_002.mp4").is_file()
    assert len(calls) == 2


def test_run_uses_scene_duration_from_plan(tmp_path):
    service, calls = build_service(tmp_path)
    images = [
        {"scene_id": 7, "path": str(make_image(tmp_path)), "ok": True},
    ]
    plan = {"scenes": [{"scene_id": 7, "duration_seconds": 3.5}]}

    service.run("bai-2", images, plan)

    assert calls[0][calls[0].index("--duration") + 1] == "3.5"


def test_run_ignores_invalid_scene_durations(tmp_path):
    service, calls = build_service(tmp_path)
    images = [
        {"scene_id": 1, "path": str(make_image(tmp_path)), "ok": True},
    ]
    plan = {"scenes": [{"scene_id": 1, "duration_seconds": 0}]}

    service.run("bai-3", images, plan)

    # Thời lượng không hợp lệ -> quay về mặc định 5 giây.
    assert calls[0][calls[0].index("--duration") + 1] == "5.0"


def test_run_records_failed_image_without_stopping_others(tmp_path):
    service, calls = build_service(tmp_path)
    images = [
        {"scene_id": 1, "path": None, "ok": False, "error": "pollinations lỗi"},
        {"scene_id": 2, "path": str(make_image(tmp_path)), "ok": True},
    ]

    results = service.run("bai-4", images)

    assert [item["ok"] for item in results] == [False, True]
    assert results[0]["path"] is None
    assert len(calls) == 1


def test_run_records_engine_failure_per_scene(tmp_path):
    service, _ = build_service(
        tmp_path, execute=lambda command: write_clip(command, returncode=2)
    )
    images = [
        {"scene_id": 1, "path": str(make_image(tmp_path)), "ok": True},
    ]

    results = service.run("bai-5", images)

    assert results[0]["ok"] is False
    assert "MediaError" in results[0]["error"]


def test_run_without_images_is_rejected(tmp_path):
    service, _ = build_service(tmp_path)

    with pytest.raises(ValidationError):
        service.run("bai-6", [])


def test_invalid_article_id_is_rejected(tmp_path):
    service, _ = build_service(tmp_path)
    images = [
        {"scene_id": 1, "path": str(make_image(tmp_path)), "ok": True},
    ]

    with pytest.raises(ValidationError):
        service.run("../thoat-ra-ngoai", images)
