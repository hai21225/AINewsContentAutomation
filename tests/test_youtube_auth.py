import json
import threading
import urllib.request
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

import youtube_auth
from Errors import ConfigurationError, ExternalServiceError

from fakes import FakeHttpResponse, FakeSession


def ok_response(payload):
    return FakeHttpResponse(
        200, text=json.dumps(payload), headers={"Content-Type": "application/json"}
    )


def client_secret_file(tmp_path, kind="installed"):
    path = tmp_path / "client_secret.json"
    path.write_text(
        json.dumps({
            kind: {
                "client_id": "client-id.apps.googleusercontent.com",
                "client_secret": "client-secret-value",
                "redirect_uris": ["http://localhost"],
            }
        }),
        encoding="utf-8",
    )
    return path


# ----------------------------------------------------------------------
# Đọc client secret
# ----------------------------------------------------------------------


def test_load_client_config_reads_installed_client(tmp_path):
    path = client_secret_file(tmp_path)

    client_id, client_secret, source = youtube_auth.load_client_config(path)

    assert client_id == "client-id.apps.googleusercontent.com"
    assert client_secret == "client-secret-value"
    assert source == str(path)


def test_load_client_config_rejects_web_client(tmp_path):
    path = client_secret_file(tmp_path, kind="web")

    with pytest.raises(ConfigurationError) as excinfo:
        youtube_auth.load_client_config(path)

    assert "Desktop app" in str(excinfo.value)


def test_load_client_config_falls_back_to_env(tmp_path):
    client_id, client_secret, source = youtube_auth.load_client_config(
        None,
        environ={"YT_CLIENT_ID": "cid", "YT_CLIENT_SECRET": "secret"},
    )

    assert (client_id, client_secret) == ("cid", "secret")
    assert ".env" in source


def test_load_client_config_requires_something(tmp_path):
    with pytest.raises(ConfigurationError):
        youtube_auth.load_client_config(None, environ={})


# ----------------------------------------------------------------------
# URL + đổi code
# ----------------------------------------------------------------------


def test_build_auth_url_requests_offline_consent():
    url = youtube_auth.build_auth_url(
        "cid", "http://localhost:1234", state="st"
    )
    query = parse_qs(urlparse(url).query)

    assert query["access_type"] == ["offline"]
    assert query["prompt"] == ["consent"]
    assert query["response_type"] == ["code"]
    assert query["redirect_uri"] == ["http://localhost:1234"]
    assert query["state"] == ["st"]
    assert "youtube.upload" in query["scope"][0]


def test_exchange_code_returns_refresh_token():
    session = FakeSession([
        ok_response({
            "access_token": "at",
            "refresh_token": "1//refresh",
            "expires_in": 3600,
            "scope": youtube_auth.DEFAULT_SCOPES[0],
        })
    ])

    payload = youtube_auth.exchange_code(
        session, "cid", "secret", "the-code", "http://localhost:1234"
    )

    assert payload["refresh_token"] == "1//refresh"
    request = session.requests[0]
    assert request["method"] == "POST"
    assert request["url"] == youtube_auth.TOKEN_URL
    assert request["data"]["grant_type"] == "authorization_code"
    assert request["data"]["code"] == "the-code"
    assert request["data"]["redirect_uri"] == "http://localhost:1234"


def test_exchange_code_without_refresh_token_explains_how_to_fix():
    session = FakeSession([ok_response({"access_token": "at"})])

    with pytest.raises(ConfigurationError) as excinfo:
        youtube_auth.exchange_code(
            session, "cid", "secret", "the-code", "http://localhost:1234"
        )

    assert "myaccount.google.com/permissions" in str(excinfo.value)


def test_exchange_code_reports_google_error():
    session = FakeSession([
        FakeHttpResponse(
            400,
            text=json.dumps({
                "error": "invalid_grant",
                "error_description": "Bad Request",
            }),
            headers={"Content-Type": "application/json"},
        )
    ])

    with pytest.raises(ExternalServiceError) as excinfo:
        youtube_auth.exchange_code(
            session, "cid", "secret", "the-code", "http://localhost:1234"
        )

    assert "invalid_grant" in str(excinfo.value)
    assert excinfo.value.retryable is False


# ----------------------------------------------------------------------
# Ghi token + .env
# ----------------------------------------------------------------------


def test_save_token_omits_client_secret(tmp_path):
    path = youtube_auth.save_token(
        tmp_path / "yt-secret" / "token.json",
        {
            "refresh_token": "1//refresh",
            "access_token": "at",
            "expires_in": 3600,
            "scope": " ".join(youtube_auth.DEFAULT_SCOPES),
        },
        "cid",
    )

    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["refresh_token"] == "1//refresh"
    assert saved["client_id"] == "cid"
    assert "client_secret" not in saved
    assert set(saved["scopes"]) == set(youtube_auth.DEFAULT_SCOPES)
    assert not list(path.parent.glob("*.part"))


def test_update_env_file_replaces_keys_and_keeps_comments(tmp_path):
    path = tmp_path / ".env"
    path.write_text(
        "# comment\nGEMINI_API_KEY=abc\nYT_REFRESH_TOKEN=placeholder\nexport YT_CLIENT_ID=old\n",
        encoding="utf-8",
    )

    youtube_auth.update_env_file(
        path,
        {
            "YT_CLIENT_ID": "new-id",
            "YT_REFRESH_TOKEN": "1//new-token",
            "YT_CLIENT_SECRET": "new-secret",
        },
    )

    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "# comment"
    assert "GEMINI_API_KEY=abc" in lines
    assert "YT_REFRESH_TOKEN=1//new-token" in lines
    assert "export YT_CLIENT_ID=new-id" in lines
    assert "YT_CLIENT_SECRET=new-secret" in lines


def test_update_env_file_creates_missing_file(tmp_path):
    path = tmp_path / ".env"

    youtube_auth.update_env_file(path, {"YT_REFRESH_TOKEN": "1//x"})

    assert "YT_REFRESH_TOKEN=1//x" in path.read_text(encoding="utf-8")


def test_update_env_file_preserves_crlf(tmp_path):
    path = tmp_path / ".env"
    path.write_bytes(b"A=1\r\nYT_REFRESH_TOKEN=old\r\n")

    youtube_auth.update_env_file(path, {"YT_REFRESH_TOKEN": "1//new"})

    raw = path.read_bytes()
    assert b"YT_REFRESH_TOKEN=1//new\r\n" in raw
    assert raw.replace(b"\r\n", b"").find(b"\n") == -1


# ----------------------------------------------------------------------
# Luồng loopback đầy đủ (không gọi mạng thật)
# ----------------------------------------------------------------------


def test_run_oauth_flow_receives_callback_and_returns_token():
    session = FakeSession([
        ok_response({
            "access_token": "at",
            "refresh_token": "1//from-browser",
            "expires_in": 3600,
        })
    ])

    def fake_browser(url):
        query = parse_qs(urlparse(url).query)
        redirect_uri = query["redirect_uri"][0]
        state = query["state"][0]

        def hit():
            try:
                urllib.request.urlopen(
                    f"{redirect_uri}?code=the-code&state={state}", timeout=5
                ).read()
            except OSError:
                pass

        threading.Thread(target=hit, daemon=True).start()

    payload = youtube_auth.run_oauth_flow(
        "cid",
        "secret",
        open_url=fake_browser,
        session=session,
        timeout=10,
        output=lambda message: None,
    )

    assert payload["refresh_token"] == "1//from-browser"

    request = session.requests[0]
    assert request["data"]["code"] == "the-code"
    assert request["data"]["redirect_uri"].startswith("http://localhost:")


def test_run_oauth_flow_times_out(tmp_path):
    with pytest.raises(ConfigurationError) as excinfo:
        youtube_auth.run_oauth_flow(
            "cid",
            "secret",
            open_url=False,
            session=FakeSession([]),
            timeout=1,
            output=lambda message: None,
        )

    assert "callback" in str(excinfo.value)


def test_looks_like_refresh_token_rejects_placeholder():
    assert youtube_auth.looks_like_refresh_token("1//" + "a" * 60) is True
    assert youtube_auth.looks_like_refresh_token("YOUR_REFRESH_TOKEN") is False
    assert youtube_auth.looks_like_refresh_token(None) is False
