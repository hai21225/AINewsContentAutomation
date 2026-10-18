import pytest
import requests

from Errors import ConfigurationError, ExternalServiceError
from ImageGeneration.PollinationsImageClient import PollinationsImageClient
from fakes import FakeHttpResponse, FakeSession

API_KEY = "key-super-secret-123"


def build_client(results, monkeypatch, provider_config=None, model_config=None,
                 retry_config=None):
    monkeypatch.setenv("POLLINATIONS_API_KEY", API_KEY)

    session = FakeSession(results)
    client = PollinationsImageClient(
        provider_config
        or {"type": "pollinations", "api_key_env": "POLLINATIONS_API_KEY"},
        model_config
        or {"model": "flux", "options": {"width": 64, "height": 64}},
        session=session,
        sleep=lambda seconds: None,
        retry_config=retry_config,
    )
    return client, session


def test_generate_returns_image_bytes(monkeypatch):
    client, session = build_client(
        [FakeHttpResponse(200, "", {"Content-Type": "image/jpeg"}, b"\xff\xd8\xff")],
        monkeypatch,
    )

    data = client.generate("a cat drawing")

    assert data == b"\xff\xd8\xff"
    assert session.requests[0]["params"]["key"] == API_KEY
    assert session.requests[0]["params"]["model"] == "flux"


def test_missing_api_key_raises_configuration_error(monkeypatch):
    monkeypatch.delenv("POLLINATIONS_API_KEY", raising=False)

    with pytest.raises(ConfigurationError) as excinfo:
        PollinationsImageClient(
            {"type": "pollinations", "api_key_env": "POLLINATIONS_API_KEY"},
            {"model": "flux"},
            session=FakeSession([]),
        )

    assert "POLLINATIONS_API_KEY" in str(excinfo.value)


def test_transient_http_error_is_retryable(monkeypatch):
    client, _ = build_client(
        [FakeHttpResponse(503, "server busy")], monkeypatch
    )

    with pytest.raises(ExternalServiceError) as excinfo:
        client.generate("a cat")

    assert excinfo.value.retryable is True
    assert excinfo.value.status == 503


def test_retries_transient_error_when_configured(monkeypatch):
    client, session = build_client(
        [
            FakeHttpResponse(503, "server busy"),
            FakeHttpResponse(
                200, "", {"Content-Type": "image/jpeg"}, b"\xff\xd8\xff"
            ),
        ],
        monkeypatch,
        retry_config={"max_attempts": 2},
    )

    assert client.generate("a cat") == b"\xff\xd8\xff"
    assert len(session.requests) == 2


def test_permanent_http_error_is_not_retryable(monkeypatch):
    client, _ = build_client([FakeHttpResponse(400, "bad prompt")], monkeypatch)

    with pytest.raises(ExternalServiceError) as excinfo:
        client.generate("a cat")

    assert excinfo.value.retryable is False


def test_error_message_does_not_leak_api_key(monkeypatch):
    body = f"unauthorized: key={API_KEY} is invalid"
    client, _ = build_client([FakeHttpResponse(401, body)], monkeypatch)

    with pytest.raises(ExternalServiceError) as excinfo:
        client.generate("a cat")

    message = str(excinfo.value)
    assert API_KEY not in message
    assert "key=***" in message


def test_connection_error_is_retryable_and_safe(monkeypatch):
    client, _ = build_client([requests.ConnectionError("boom")], monkeypatch)

    with pytest.raises(ExternalServiceError) as excinfo:
        client.generate("a cat")

    message = str(excinfo.value)
    assert excinfo.value.retryable is True
    assert API_KEY not in message
    assert "gen.pollinations.ai" not in message


def test_non_image_content_type_is_retryable(monkeypatch):
    client, _ = build_client(
        [
            FakeHttpResponse(
                200, '{"error":"quota"}', {"Content-Type": "application/json"}
            )
        ],
        monkeypatch,
    )

    with pytest.raises(ExternalServiceError) as excinfo:
        client.generate("a cat")

    assert excinfo.value.retryable is True


def test_empty_image_body_is_retryable(monkeypatch):
    client, _ = build_client(
        [FakeHttpResponse(200, "", {"Content-Type": "image/png"}, b"")], monkeypatch
    )

    with pytest.raises(ExternalServiceError) as excinfo:
        client.generate("a cat")

    assert excinfo.value.retryable is True


def test_blank_prompt_is_rejected(monkeypatch):
    client, _ = build_client([], monkeypatch)

    with pytest.raises(ValueError):
        client.generate("   ")
