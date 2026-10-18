import pytest

from Errors import ExternalServiceError, MediaError, ValidationError
from Services.ImageService import ImageService
from fakes import FakeImageClient, image_bytes


def build_service(results, tmp_path, **config):
    client = FakeImageClient(results)
    service = ImageService(
        client, {"seed": 42, **config}, output_dir=tmp_path / "images"
    )
    return service, client


def plan(scene_count=2):
    return {
        "scenes": [
            {
                "scene_id": index,
                "image_prompt": f"line art scene {index}",
                "narration_summary": "Tóm tắt.",
                "visual_description": "Mô tả.",
                "start_seconds": index - 1,
                "end_seconds": index,
                "duration_seconds": 1,
                "transition": "draw",
            }
            for index in range(1, scene_count + 1)
        ]
    }


def test_saves_png_with_detected_extension(tmp_path):
    service, client = build_service([image_bytes("PNG")], tmp_path)

    results = service.run("bai-1", plan(scene_count=1))

    assert results[0]["ok"] is True
    path = tmp_path / "images" / "bai-1" / "scene_001.png"
    assert path.is_file()
    assert path.stat().st_size > 0


def test_saves_jpeg_with_jpg_extension(tmp_path):
    service, client = build_service([image_bytes("JPEG")], tmp_path)

    results = service.run("bai-1", plan(scene_count=1))

    assert results[0]["path"].endswith(".jpg")


def test_seed_is_stable_per_scene(tmp_path):
    service, client = build_service([image_bytes("PNG"), image_bytes("PNG")], tmp_path)

    service.run("bai-2", plan(scene_count=2))

    assert [call["seed"] for call in client.calls] == [43, 44]


def test_invalid_image_data_is_recorded_as_failure(tmp_path):
    service, _ = build_service([b"khong-phai-anh"], tmp_path)

    results = service.run("bai-3", plan(scene_count=1))

    assert results[0]["ok"] is False
    assert "MediaError" in results[0]["error"]
    assert not (tmp_path / "images" / "bai-3" / "scene_001.png").exists()


def test_provider_error_is_recorded_without_stopping_other_scenes(tmp_path):
    service, _ = build_service(
        [ExternalServiceError("pollinations lỗi"), image_bytes("PNG")], tmp_path
    )

    results = service.run("bai-4", plan(scene_count=2))

    assert [item["ok"] for item in results] == [False, True]
    assert results[0]["path"] is None
    assert (tmp_path / "images" / "bai-4" / "scene_002.png").is_file()


def test_invalid_article_id_is_rejected(tmp_path):
    service, _ = build_service([image_bytes("PNG")], tmp_path)

    with pytest.raises(ValidationError):
        service.run("../thoat-ra-ngoai", plan(scene_count=1))


def test_plan_without_scenes_is_rejected(tmp_path):
    service, _ = build_service([], tmp_path)

    with pytest.raises(ValidationError):
        service.run("bai-5", {"scenes": []})


def test_scene_without_prompt_is_recorded_as_failure(tmp_path):
    service, _ = build_service([image_bytes("PNG")], tmp_path)
    broken_plan = plan(scene_count=1)
    broken_plan["scenes"][0]["image_prompt"] = "  "

    results = service.run("bai-6", broken_plan)

    assert results[0]["ok"] is False


def test_media_error_is_raised_for_wrong_data_type(tmp_path):
    service, _ = build_service(["khong-phai-bytes"], tmp_path)

    results = service.run("bai-7", plan(scene_count=1))

    assert results[0]["ok"] is False
    assert "MediaError" in results[0]["error"]
