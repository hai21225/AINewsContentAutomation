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
                  access_token="token-abc", page_token="page-token", **facebook):
    config = {
        "platform": "facebook",
        "mode": mode,
        "output_dir": str(tmp_path / "publish"),
        "facebook": {
            "api_version": "v26.0",
            "page_token_env": "PAGE_TOKEN",
            **facebook,
        },
    }

    return PublishService(
        config,
        session=session,
        page_id=page_id,
        access_token=access_token,
        environ={"PAGE_TOKEN": page_token} if page_token else {},
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


def test_caption_combines_description_hashtags_and_source(tmp_path):
    session = FakeSession([empty_lookup(), ok_response({"id": "7"})])
    service = build_service(tmp_path, session=session, mode="publish")
    video = make_video(tmp_path)

    result = service.run(
        "bai-1",
        video,
        title="Tiêu đề",
        hashtags=["#a", "#b"],
        description="Mô tả ngắn của bài.",
        source_url="https://example.com/bai-viet",
    )

    caption = "Mô tả ngắn của bài.\n\n#a #b\n\nNguồn: https://example.com/bai-viet"
    assert session.requests[1]["data"]["description"] == caption
    assert result["description"] == caption


def test_caption_does_not_repeat_hashtags_already_in_description(tmp_path):
    service = build_service(tmp_path)

    result = service.run(
        "bai-1",
        make_video(tmp_path),
        hashtags=["#a", "#b"],
        description="Mô tả đã có #a #b",
    )

    assert result["description"] == "Mô tả đã có #a #b"


def test_manifest_records_source_url(tmp_path):
    service = build_service(tmp_path)

    result = service.run(
        "bai-1", make_video(tmp_path), source_url="https://example.com/x"
    )

    saved = json.loads(
        Path(result["manifest_path"]).read_text(encoding="utf-8")
    )
    assert result["source_url"] == "https://example.com/x"
    assert saved["source_url"] == "https://example.com/x"
    assert "Nguồn: https://example.com/x" in saved["description"]


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


def test_upload_uses_page_token_resolved_from_user_token(tmp_path):
    session = FakeSession(
        [
            empty_lookup(),
            ok_response({"access_token": "resolved-page-token"}),
            ok_response({"id": "42"}),
        ]
    )
    service = build_service(
        tmp_path, session=session, mode="publish", page_token=None
    )

    result = service.run("bai-1", make_video(tmp_path), title="T")

    assert result["post_id"] == "42"

    lookup, exchange, upload = session.requests
    assert lookup["params"]["access_token"] == "token-abc"
    assert exchange["method"] == "GET"
    assert exchange["params"]["fields"] == "access_token"
    assert upload["data"]["access_token"] == "resolved-page-token"


def test_upload_falls_back_to_configured_token_when_page_token_fails(tmp_path):
    session = FakeSession(
        [
            empty_lookup(),
            error_response(400, "Invalid OAuth access token.", code=190),
            ok_response({"id": "7"}),
        ]
    )
    service = build_service(
        tmp_path, session=session, mode="publish", page_token=None
    )

    result = service.run("bai-1", make_video(tmp_path), title="T")

    assert result["post_id"] == "7"
    assert session.requests[-1]["data"]["access_token"] == "token-abc"


def test_missing_publish_permission_gets_actionable_hint(tmp_path):
    session = FakeSession(
        [
            empty_lookup(),
            error_response(400, "No permission to publish the video", code=100),
        ]
    )
    service = build_service(tmp_path, session=session, mode="publish")

    with pytest.raises(ExternalServiceError) as excinfo:
        service.run("bai-1", make_video(tmp_path), title="T")

    assert "pages_manage_posts" in str(excinfo.value)


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


# ----------------------------------------------------------------------
# YouTube
# ----------------------------------------------------------------------


def yt_token(token="yt-access-token", expires_in=3600):
    return ok_response({"access_token": token, "expires_in": expires_in})


def yt_init(location="https://upload.example/session/abc"):
    return FakeHttpResponse(
        200, text="", headers={"Location": location, "Content-Type": "application/json"}
    )


def yt_search(items=None):
    return ok_response({"items": items or []})


def yt_matches(video_id="yt-video", title="Tiêu đề tin"):
    return yt_search([
        {
            "id": {"videoId": video_id},
            "snippet": {"title": title},
        }
    ])


def build_youtube_service(tmp_path, session=None, mode="publish", platforms=None,
                          client_id="yt-client", refresh_token="yt-refresh",
                          **youtube):
    config = {
        "mode": mode,
        "output_dir": str(tmp_path / "publish"),
        "youtube": {"privacy_status": "unlisted", **youtube},
    }

    if platforms is None:
        config["platform"] = "youtube"
    else:
        config["platforms"] = platforms

    return PublishService(
        config,
        session=session,
        client_id=client_id,
        client_secret="yt-secret",
        refresh_token=refresh_token,
        environ={},
        retry_config={"max_attempts": 1, "backoff_seconds": 0},
    )


def test_youtube_platform_is_supported(tmp_path):
    service = build_youtube_service(tmp_path, mode="prepare")

    assert service.platforms == ("youtube",)
    assert service.platform == "youtube"
    assert service.is_configured() is True


def test_youtube_requires_credentials(tmp_path):
    service = PublishService(
        {"platform": "youtube", "mode": "publish", "output_dir": str(tmp_path)},
        environ={},
    )

    assert service.is_configured() is False

    with pytest.raises(ConfigurationError) as excinfo:
        service.run("bai-1", make_video(tmp_path))

    message = str(excinfo.value)
    assert "YT_CLIENT_ID" in message
    assert "YT_CLIENT_SECRET" in message
    assert "YT_REFRESH_TOKEN" in message


def test_youtube_prepare_writes_manifest_without_network(tmp_path):
    session = FakeSession([])
    service = build_youtube_service(tmp_path, session=session, mode="prepare")

    result = service.run(
        "bai-1",
        make_video(tmp_path),
        title="Tiêu đề",
        hashtags=["#a", "b"],
        description="Mô tả",
    )

    assert result["status"] == "prepared"
    assert result["platform"] == "youtube"
    assert result["platforms"] == ["youtube"]
    assert result["results"] == {}
    assert session.requests == []


def test_youtube_publish_uploads_video_and_records_id(tmp_path):
    session = FakeSession([
        yt_token(),
        yt_search(),
        yt_init(),
        ok_response({"id": "abc123"}),
    ])
    service = build_youtube_service(tmp_path, session=session)
    video = make_video(tmp_path, data=b"video-bytes")

    result = service.run(
        "bai-1",
        video,
        title="Tiêu đề tin",
        hashtags=["#a", "#b"],
        description="Mô tả ngắn.",
        source_url="https://example.com/x",
    )

    assert result["status"] == "published"
    assert result["post_id"] == "abc123"
    assert result["post_url"] == "https://youtu.be/abc123"
    assert result["deduplicated"] is False
    assert result["results"]["youtube"]["status"] == "published"

    token_request, lookup, init, upload = session.requests

    assert token_request["method"] == "POST"
    assert token_request["url"] == "https://oauth2.googleapis.com/token"
    assert token_request["data"]["grant_type"] == "refresh_token"
    assert token_request["data"]["client_id"] == "yt-client"
    assert token_request["data"]["refresh_token"] == "yt-refresh"

    assert lookup["method"] == "GET"
    assert lookup["url"].endswith("/search")
    assert lookup["params"]["forMine"] == "true"
    assert lookup["params"]["q"] == "Tiêu đề tin"
    assert lookup["headers"]["Authorization"] == "Bearer yt-access-token"

    assert init["method"] == "POST"
    assert init["params"] == {"uploadType": "resumable", "part": "snippet,status"}
    assert init["headers"]["X-Upload-Content-Length"] == "11"

    body = json.loads(init["data"].decode("utf-8"))
    assert body["snippet"]["title"] == "Tiêu đề tin"
    assert body["snippet"]["tags"] == ["a", "b"]
    assert body["snippet"]["categoryId"] == "25"
    assert body["status"]["privacyStatus"] == "unlisted"
    assert body["status"]["selfDeclaredMadeForKids"] is False
    assert "Mô tả ngắn." in body["snippet"]["description"]
    assert "Nguồn: https://example.com/x" in body["snippet"]["description"]

    assert upload["method"] == "PUT"
    assert upload["url"] == "https://upload.example/session/abc"
    assert upload["headers"]["Content-Type"] == "video/mp4"
    assert upload["headers"]["Content-Length"] == "11"
    assert upload["data"].name.endswith("video.mp4")

    saved = json.loads(
        Path(result["manifest_path"]).read_text(encoding="utf-8")
    )
    assert saved["platforms"] == ["youtube"]
    assert saved["results"]["youtube"]["post_id"] == "abc123"


def test_youtube_title_and_description_are_truncated(tmp_path):
    session = FakeSession([
        yt_token(),
        yt_search(),
        yt_init(),
        ok_response({"id": "abc123"}),
    ])
    service = build_youtube_service(tmp_path, session=session)

    service.run(
        "bai-1",
        make_video(tmp_path),
        title="T" * 150,
        description="M" * 6000,
    )

    init = session.requests[2]
    body = json.loads(init["data"].decode("utf-8"))
    assert len(body["snippet"]["title"]) == 100
    assert len(body["snippet"]["description"]) == 5000


def test_youtube_skips_upload_when_channel_has_same_title(tmp_path):
    session = FakeSession([yt_token(), yt_matches()])
    service = build_youtube_service(tmp_path, session=session)

    result = service.run("bai-1", make_video(tmp_path), title="Tiêu đề tin")

    assert result["status"] == "published"
    assert result["post_id"] == "yt-video"
    assert result["deduplicated"] is True
    assert len(session.requests) == 2  # token + tìm kiếm, không upload


def test_youtube_refresh_token_can_come_from_token_file(tmp_path):
    token_file = tmp_path / "token.json"
    token_file.write_text(
        json.dumps({"refresh_token": "1//from-token-file"}), encoding="utf-8"
    )
    session = FakeSession([
        yt_token(),
        yt_search(),
        yt_init(),
        ok_response({"id": "v1"}),
    ])
    service = build_youtube_service(
        tmp_path, session=session, refresh_token=None,
        token_file=str(token_file),
    )

    service.run("bai-1", make_video(tmp_path), title="T")

    assert session.requests[0]["data"]["refresh_token"] == "1//from-token-file"


def test_youtube_token_file_wins_over_placeholder_env_token(tmp_path):
    token_file = tmp_path / "token.json"
    token_file.write_text(
        json.dumps({"refresh_token": "1//real-token"}), encoding="utf-8"
    )
    session = FakeSession([
        yt_token(),
        yt_search(),
        yt_init(),
        ok_response({"id": "v1"}),
    ])
    service = build_youtube_service(
        tmp_path,
        session=session,
        refresh_token="YOUR_REFRESH_TOKEN_HERE",
        token_file=str(token_file),
    )

    service.run("bai-1", make_video(tmp_path), title="T")

    assert session.requests[0]["data"]["refresh_token"] == "1//real-token"


def test_youtube_access_token_is_cached_between_articles(tmp_path):
    session = FakeSession([
        yt_token(),
        yt_search(),
        yt_init(),
        ok_response({"id": "v1"}),
        yt_search(),
        yt_init(),
        ok_response({"id": "v2"}),
    ])
    service = build_youtube_service(tmp_path, session=session)
    video = make_video(tmp_path)

    service.run("bai-1", video, title="Một")
    service.run("bai-2", video, title="Hai")

    token_calls = [
        request for request in session.requests
        if request["url"] == "https://oauth2.googleapis.com/token"
    ]
    assert len(token_calls) == 1


def test_youtube_missing_upload_location_is_an_error(tmp_path):
    session = FakeSession([
        yt_token(),
        yt_search(),
        FakeHttpResponse(200, text="", headers={}),
    ])
    service = build_youtube_service(tmp_path, session=session)

    with pytest.raises(ExternalServiceError) as excinfo:
        service.run("bai-1", make_video(tmp_path), title="T")

    assert "Location" in str(excinfo.value)

    saved = json.loads(
        Path(service.manifest_path("bai-1")).read_text(encoding="utf-8")
    )
    assert saved["status"] == "failed"
    assert saved["results"]["youtube"]["status"] == "failed"


def test_youtube_permission_error_gets_actionable_hint(tmp_path):
    session = FakeSession([
        yt_token(),
        error_response(403, "Request had insufficient authentication scopes."),
    ])
    service = build_youtube_service(tmp_path, session=session)

    with pytest.raises(ExternalServiceError) as excinfo:
        service.run("bai-1", make_video(tmp_path), title="T")

    assert "youtube.upload" in str(excinfo.value)


# ----------------------------------------------------------------------
# Nhiều nền tảng cùng lúc
# ----------------------------------------------------------------------


def test_empty_platform_list_is_rejected(tmp_path):
    with pytest.raises(ConfigurationError):
        PublishService({"platforms": [], "mode": "prepare"}, environ={})


def test_platforms_list_can_enable_facebook_and_youtube(tmp_path):
    session = FakeSession([
        empty_lookup(),
        ok_response({"id": "fb-1"}),
        yt_token(),
        yt_search(),
        yt_init(),
        ok_response({"id": "yt-1"}),
    ])
    service = PublishService(
        {
            "platforms": ["facebook", "youtube"],
            "mode": "publish",
            "output_dir": str(tmp_path / "publish"),
            "facebook": {"page_token_env": "PAGE_TOKEN"},
            "youtube": {"privacy_status": "unlisted"},
        },
        session=session,
        page_id="1234567890",
        access_token="fb-token",
        client_id="yt-client",
        client_secret="yt-secret",
        refresh_token="yt-refresh",
        environ={"PAGE_TOKEN": "page-token"},
        retry_config={"max_attempts": 1, "backoff_seconds": 0},
    )

    result = service.run("bai-1", make_video(tmp_path), title="Tiêu đề tin")

    assert service.platforms == ("facebook", "youtube")
    assert service.platform == "facebook,youtube"
    assert result["status"] == "published"
    # Trường kiểu cũ phản ánh nền tảng đầu tiên.
    assert result["post_id"] == "fb-1"
    assert result["post_url"] == "https://www.facebook.com/fb-1"
    assert result["results"]["facebook"]["post_id"] == "fb-1"
    assert result["results"]["youtube"]["post_id"] == "yt-1"

    saved = json.loads(
        Path(result["manifest_path"]).read_text(encoding="utf-8")
    )
    assert saved["status"] == "published"
    assert set(saved["results"]) == {"facebook", "youtube"}


def test_one_platform_failure_keeps_other_result_and_marks_partial(tmp_path):
    session = FakeSession([
        empty_lookup(),
        ok_response({"id": "fb-1"}),
        error_response(
            400, "invalid_grant: Token has been expired or revoked."
        ),
    ])
    service = PublishService(
        {
            "platforms": ["facebook", "youtube"],
            "mode": "publish",
            "output_dir": str(tmp_path / "publish"),
            "facebook": {"page_token_env": "PAGE_TOKEN"},
        },
        session=session,
        page_id="1234567890",
        access_token="fb-token",
        client_id="yt-client",
        client_secret="yt-secret",
        refresh_token="yt-refresh",
        environ={"PAGE_TOKEN": "page-token"},
        retry_config={"max_attempts": 1, "backoff_seconds": 0},
    )

    with pytest.raises(ExternalServiceError) as excinfo:
        service.run("bai-1", make_video(tmp_path), title="Tiêu đề tin")

    assert "youtube" in str(excinfo.value)

    saved = json.loads(
        Path(service.manifest_path("bai-1")).read_text(encoding="utf-8")
    )
    assert saved["status"] == "partial"
    assert saved["results"]["facebook"]["status"] == "published"
    assert saved["results"]["facebook"]["post_id"] == "fb-1"
    assert saved["results"]["youtube"]["status"] == "failed"
    assert saved["results"]["youtube"]["post_id"] is None

