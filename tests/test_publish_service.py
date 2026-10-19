import json
from pathlib import Path

import pytest

from Errors import (
    ConfigurationError,
    ExternalServiceError,
    MediaError,
    ValidationError,
)
from Services.PublishService import PublishService

from fakes import FakeHttpResponse, FakeSession


def make_video(tmp_path, name="video.mp4", data=b"video-bytes"):
    path = tmp_path / name
    path.write_bytes(data)
    return path


def build_service(tmp_path, session=None, mode="prepare", page_id="1234567890",
                  access_token="token-abc", **facebook):
    config = {
        "platform": "facebook",
        "mode": mode,
        "output_dir": str(tmp_path / "publish"),
        "facebook": {"api_version": "v26.0", **facebook},
    }

    return PublishService(
        config,
        session=session,
        page_id=page_id,
        access_token=access_token,
        environ={},
        retry_config={"max_attempts": 1, "backoff_seconds": 0},
    )


def ok_response(payload):
    return FakeHttpResponse(
        200, text=json.dumps(payload), headers={"Content-Type": "application/json"}
    )


def error_response(status, message, code=100):
    return FakeHttpResponse(
        status,
        text=json.dumps({"error": {"message": message, "code": code}}),
        headers={"Content-Type": "application/json"},
    )


def empty_lookup():
    return ok_response({"data": []})


# ----------------------------------------------------------------------
# Chế độ prepare (mặc định): không gọi mạng
# ----------------------------------------------------------------------


def test_prepare_writes_manifest_without_network(tmp_path):
    session = FakeSession([])
    service = build_service(tmp_path, session=session, mode="prepare")
    video = make_video(tmp_path)

    result = service.run(
        "bai-1", video, title="Tiêu đề", hashtags=["#a", "b"], duration_seconds=9.5
    )

    assert result["status"] == "prepared"
    assert result["post_id"] is None
    assert result["platform"] == "facebook"
    assert result["mode"] == "prepare"
    assert session.requests == []

    manifest = Path(result["manifest_path"])
    assert manifest.is_file()
    assert manifest.name == "bai-1.json"

    saved = json.loads(manifest.read_text(encoding="utf-8"))
    assert saved["title"] == "Tiêu đề"
    assert saved["hashtags"] == ["#a", "#b"]
    assert saved["video_bytes"] == len(b"video-bytes")
    assert saved["video_duration_seconds"] == 9.5
    assert saved["status"] == "prepared"


def test_prepare_needs_no_credentials(tmp_path):
    service = PublishService(
        {"platform": "facebook", "mode": "prepare", "output_dir": str(tmp_path)},
        environ={},
    )

    assert service.is_configured() is True
    service.ensure_configured()


def test_prepare_uses_video_name_and_hashtags_as_description(tmp_path):
    service = build_service(tmp_path)
    video = make_video(tmp_path, name="bai-9.mp4")

    result = service.run("bai-9", video, hashtags=["#tin", "#congnghe"])

    assert result["title"] == "bai-9"
    assert result["description"] == "#tin #congnghe"


def test_prepare_does_not_leave_part_file(tmp_path):
    service = build_service(tmp_path)
    video = make_video(tmp_path)

    service.run("bai-1", video)

    leftovers = list(Path(tmp_path / "publish").glob("*.part"))
    assert leftovers == []


# ----------------------------------------------------------------------
# Kiểm tra dữ liệu vào
# ----------------------------------------------------------------------


def test_missing_video_is_rejected(tmp_path):
    service = build_service(tmp_path)

    with pytest.raises(MediaError):
        service.run("bai-1", tmp_path / "khong-co.mp4")


def test_empty_video_is_rejected(tmp_path):
    service = build_service(tmp_path)
    video = make_video(tmp_path, data=b"")

    with pytest.raises(MediaError):
        service.run("bai-1", video)


def test_non_mp4_is_rejected(tmp_path):
    service = build_service(tmp_path)
    video = make_video(tmp_path, name="video.mov")

    with pytest.raises(ValidationError) as excinfo:
        service.run("bai-1", video)

    assert ".mp4" in str(excinfo.value)


def test_video_over_size_limit_is_rejected(tmp_path):
    service = build_service(tmp_path, max_file_bytes=4)
    video = make_video(tmp_path, data=b"0123456789")

    with pytest.raises(ValidationError) as excinfo:
        service.run("bai-1", video)

    assert "Resumable Upload" in str(excinfo.value)


def test_invalid_article_id_is_rejected(tmp_path):
    service = build_service(tmp_path)
    video = make_video(tmp_path)

    with pytest.raises(ValidationError):
        service.run("../thoat", video)


def test_unsupported_platform_is_rejected(tmp_path):
    with pytest.raises(ConfigurationError):
        PublishService({"platform": "tiktok"}, environ={})


def test_invalid_mode_is_rejected(tmp_path):
    with pytest.raises(ConfigurationError):
        PublishService({"mode": "lung-tung"}, environ={})


# ----------------------------------------------------------------------
# Chế độ publish: đăng thật
# ----------------------------------------------------------------------


def test_publish_requires_credentials(tmp_path):
    service = PublishService(
        {
            "platform": "facebook",
            "mode": "publish",
            "output_dir": str(tmp_path),
            "facebook": {"check_existing": False},
        },
        environ={},
    )
    video = make_video(tmp_path)

    assert service.is_configured() is False

    with pytest.raises(ConfigurationError) as excinfo:
        service.run("bai-1", video)

    assert "FB_PAGE_ID" in str(excinfo.value)
    assert "FB_PAGE_ACCESS_TOKEN" in str(excinfo.value)


def test_publish_posts_video_and_records_id(tmp_path):
    session = FakeSession([empty_lookup(), ok_response({"id": "999_888"})])
    service = build_service(tmp_path, session=session, mode="publish")
    video = make_video(tmp_path)

    result = service.run(
        "bai-1", video, title="Tiêu đề tin", hashtags=["#a", "#b", "#c"]
    )

    assert result["status"] == "published"
    assert result["post_id"] == "999_888"
    assert result["post_url"] == "https://www.facebook.com/999_888"
    assert result["deduplicated"] is False

    lookup, upload = session.requests
    assert lookup["method"] == "GET"
    assert "/1234567890/videos" in lookup["url"]
    assert lookup["params"]["fields"] == "id,title,created_time"

    assert upload["method"] == "POST"
    assert "graph-video.facebook.com" in upload["url"]
    assert upload["data"]["title"] == "Tiêu đề tin"
    assert upload["data"]["description"] == "#a #b #c"
    assert upload["data"]["published"] == "true"
    assert upload["files"]["source"][2] == "video/mp4"


def test_publish_skips_when_page_already_has_same_title(tmp_path):
    existing = ok_response(
        {"data": [{"id": "111_222", "title": "Tiêu đề tin"}]}
    )
    session = FakeSession([existing])
    service = build_service(tmp_path, session=session, mode="publish")
    video = make_video(tmp_path)

    result = service.run("bai-1", video, title="Tiêu đề tin")

    assert result["status"] == "published"
    assert result["post_id"] == "111_222"
    assert result["deduplicated"] is True
    assert len(session.requests) == 1  # chỉ có GET tra cứu, không POST


def test_publish_ignores_lookup_entries_with_other_titles(tmp_path):
    other = ok_response({"data": [{"id": "1", "title": "Bài khác"}]})
    session = FakeSession([other, ok_response({"id": "5"})])
    service = build_service(tmp_path, session=session, mode="publish")
    video = make_video(tmp_path)

    result = service.run("bai-1", video, title="Tiêu đề tin")

    assert result["deduplicated"] is False
    assert result["post_id"] == "5"
    assert len(session.requests) == 2


def test_publish_http_error_is_reported(tmp_path):
    session = FakeSession(
        [empty_lookup(), error_response(400, "Invalid parameter", code=100)]
    )
    service = build_service(tmp_path, session=session, mode="publish")
    video = make_video(tmp_path)

    with pytest.raises(ExternalServiceError) as excinfo:
        service.run("bai-1", video, title="T")

    error = excinfo.value
    assert error.status == 400
    assert error.retryable is False
    assert "Invalid parameter" in str(error)


def test_publish_missing_id_in_response_is_an_error(tmp_path):
    session = FakeSession([empty_lookup(), ok_response({})])
    service = build_service(tmp_path, session=session, mode="publish")
    video = make_video(tmp_path)

    with pytest.raises(ExternalServiceError):
        service.run("bai-1", video, title="T")


def test_publish_network_error_is_retryable_and_no_post_retry(tmp_path):
    import requests

    session = FakeSession([requests.ConnectionError("boom")])
    service = build_service(
        tmp_path, session=session, mode="publish", check_existing=False
    )
    video = make_video(tmp_path)

    with pytest.raises(ExternalServiceError) as excinfo:
        service.run("bai-1", video, title="T")

    assert excinfo.value.retryable is True
    # POST là tác dụng phụ: không thử lại -> chỉ đúng một lần gọi.
    assert len(session.requests) == 1


def test_publish_writes_manifest_even_when_published(tmp_path):
    session = FakeSession([empty_lookup(), ok_response({"id": "7"})])
    service = build_service(tmp_path, session=session, mode="publish")
    video = make_video(tmp_path)

    result = service.run("bai-1", video, title="T")

    saved = json.loads(
        Path(result["manifest_path"]).read_text(encoding="utf-8")
    )
    assert saved["status"] == "published"
    assert saved["post_id"] == "7"
    assert saved["published_at"]


def test_published_false_uploads_as_unpublished(tmp_path):
    session = FakeSession([empty_lookup(), ok_response({"id": "7"})])
    service = build_service(
        tmp_path, session=session, mode="publish", published=False
    )
    video = make_video(tmp_path)

    service.run("bai-1", video, title="T")

    assert session.requests[1]["data"]["published"] == "false"


def test_check_existing_can_be_disabled(tmp_path):
    session = FakeSession([ok_response({"id": "7"})])
    service = build_service(
        tmp_path, session=session, mode="publish", check_existing=False
    )
    video = make_video(tmp_path)

    service.run("bai-1", video, title="T")

    assert len(session.requests) == 1
    assert session.requests[0]["method"] == "POST"


def test_environ_supplies_credentials(tmp_path):
    service = PublishService(
        {
            "platform": "facebook",
            "mode": "publish",
            "output_dir": str(tmp_path),
        },
        environ={
            "FB_PAGE_ID": "42",
            "FB_PAGE_ACCESS_TOKEN": "token-from-env",
        },
    )

    assert service.is_configured() is True


def test_custom_env_names_are_respected(tmp_path):
    service = PublishService(
        {
            "platform": "facebook",
            "mode": "publish",
            "output_dir": str(tmp_path),
            "facebook": {
                "page_id_env": "MY_PAGE",
                "access_token_env": "MY_TOKEN",
            },
        },
        environ={"MY_PAGE": "42", "MY_TOKEN": "secret"},
    )

    assert service.is_configured() is True
