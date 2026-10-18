import pytest
import requests

from Errors import ExternalServiceError
from LLM.OllamaClient import OllamaClient
from fakes import FakeHttpResponse, FakeSession


def build_client(results, max_retries=1, model="llama3.1:8b"):
    session = FakeSession(results)
    client = OllamaClient(
        {"type": "ollama", "base_url": "http://localhost:11434"},
        {"model": model, "max_retries": max_retries},
        session=session,
        sleep=lambda seconds: None,
    )
    return client, session


def test_returns_response_text():
    client, session = build_client(
        [FakeHttpResponse(200, '{"response": "xin chào"}')]
    )

    assert client.generate("prompt") == "xin chào"
    assert client.last_used_model == "llama3.1:8b"
    assert session.requests[0]["url"] == "http://localhost:11434/api/generate"
    assert session.requests[0]["json"]["model"] == "llama3.1:8b"


def test_json_schema_is_forwarded():
    client, session = build_client(
        [FakeHttpResponse(200, '{"response": "{}"}')]
    )

    schema = {"type": "object"}
    client.generate("prompt", schema)

    assert session.requests[0]["json"]["format"] == schema


def test_retries_transient_http_error_then_succeeds():
    client, session = build_client(
        [
            FakeHttpResponse(503, "server error"),
            FakeHttpResponse(200, '{"response": "ok"}'),
        ],
        max_retries=2,
    )

    assert client.generate("prompt") == "ok"
    assert len(session.requests) == 2


def test_permanent_http_error_is_not_retried():
    client, session = build_client(
        [FakeHttpResponse(404, "not found")], max_retries=3
    )

    with pytest.raises(ExternalServiceError) as excinfo:
        client.generate("prompt")

    assert excinfo.value.retryable is False
    assert len(session.requests) == 1


def test_connection_error_is_retryable():
    client, session = build_client(
        [requests.ConnectionError("boom")], max_retries=1
    )

    with pytest.raises(ExternalServiceError) as excinfo:
        client.generate("prompt")

    assert excinfo.value.retryable is True


def test_invalid_json_is_retryable():
    client, session = build_client(
        [FakeHttpResponse(200, "khong-phai-json")], max_retries=1
    )

    with pytest.raises(ExternalServiceError) as excinfo:
        client.generate("prompt")

    assert excinfo.value.retryable is True


def test_missing_response_field_is_retryable():
    client, session = build_client(
        [FakeHttpResponse(200, "{}")], max_retries=1
    )

    with pytest.raises(ExternalServiceError) as excinfo:
        client.generate("prompt")

    assert excinfo.value.retryable is True
