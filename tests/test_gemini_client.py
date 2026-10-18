import pytest
from google.genai import errors

from Errors import ExternalServiceError
from LLM.GeminiClient import GeminiClient
from fakes import FakeGeminiClient

TRANSIENT_ERROR = errors.APIError(503, {"error": {"message": "boom"}})
PERMANENT_ERROR = errors.ClientError(400, {"error": {"message": "bad request"}})


class FakeResponseWithHeaders:
    def __init__(self, headers):
        self.headers = headers


def build_client(behaviours, model="main-model", fallback=None, max_retries=2):
    model_config = {
        "model": model,
        "options": {"temperature": 0.2},
        "max_retries": max_retries,
    }

    if fallback is not None:
        model_config["fallback_model"] = fallback

    fake = FakeGeminiClient(behaviours)
    slept = []

    client = GeminiClient(
        {"type": "gemini"},
        model_config,
        client=fake,
        sleep=slept.append,
    )

    return client, fake, slept


def called_models(fake):
    return [call["model"] for call in fake.models.calls]


def test_returns_text_from_primary_model():
    client, fake, _ = build_client({"main-model": "xin chào"})

    assert client.generate("prompt") == "xin chào"
    assert client.last_used_model == "main-model"
    assert called_models(fake) == ["main-model"]


def test_retries_transient_error_then_succeeds():
    from types import SimpleNamespace

    state = {"count": 0}

    def behaviour():
        state["count"] += 1
        if state["count"] == 1:
            raise TRANSIENT_ERROR
        return SimpleNamespace(text="thành công")

    client, fake, slept = build_client({"main-model": behaviour})

    assert client.generate("prompt") == "thành công"
    assert called_models(fake) == ["main-model", "main-model"]
    assert len(slept) == 1
    assert slept[0] > 0


def test_does_not_retry_permanent_error_but_tries_fallback():
    behaviours = {
        "main-model": PERMANENT_ERROR,
        "gemma-x": "kết quả dự phòng",
    }
    client, fake, slept = build_client(behaviours, fallback="gemma-x")

    assert client.generate("prompt") == "kết quả dự phòng"
    assert called_models(fake) == ["main-model", "gemma-x"]
    assert slept == []
    assert client.last_used_model == "gemma-x"


def test_string_fallback_is_single_model_name_not_characters():
    """Chống hồi quy: trước đây code lặp qua chuỗi theo từng ký tự."""
    behaviours = {
        "main-model": TRANSIENT_ERROR,
        "gemma-x": "ok",
    }
    client, fake, _ = build_client(behaviours, fallback="gemma-x")

    assert client.generate("prompt") == "ok"
    assert called_models(fake) == ["main-model"] * 2 + ["gemma-x"]


def test_fallback_list_and_primary_deduplication():
    behaviours = {
        "main-model": PERMANENT_ERROR,
        "fb-1": PERMANENT_ERROR,
        "fb-2": "ok",
    }
    model_config = {
        "model": "main-model",
        "fallback_models": ["fb-1", "main-model", "fb-2"],
    }
    fake = FakeGeminiClient(behaviours)
    client = GeminiClient(
        {"type": "gemini"}, model_config, client=fake, sleep=lambda _: None
    )

    assert client.fallback_models == ["fb-1", "fb-2"]
    assert client.generate("prompt") == "ok"
    assert called_models(fake) == ["main-model", "fb-1", "fb-2"]


def test_honours_retry_after_header():
    error = errors.APIError(
        429,
        {"error": {"message": "quota"}},
        response=FakeResponseWithHeaders({"retry-after": "3"}),
    )

    client, _, slept = build_client({"main-model": error}, fallback=None,
                                    max_retries=2)

    with pytest.raises(ExternalServiceError):
        client.generate("prompt")

    assert slept == [3.0]


def test_retry_exhaustion_raises_external_service_error():
    client, fake, _ = build_client({"main-model": TRANSIENT_ERROR},
                                   max_retries=3)

    with pytest.raises(ExternalServiceError) as excinfo:
        client.generate("prompt")

    assert excinfo.value.retryable is True
    assert excinfo.value.status == 503
    assert called_models(fake) == ["main-model"] * 3


def test_empty_response_is_treated_as_transient_then_falls_back():
    behaviours = {"main-model": "", "gemma-x": "ok"}
    client, fake, _ = build_client(behaviours, fallback="gemma-x")

    assert client.generate("prompt") == "ok"
    assert called_models(fake) == ["main-model"] * 2 + ["gemma-x"]


def test_all_models_failing_raises_last_error():
    behaviours = {"main-model": PERMANENT_ERROR, "gemma-x": PERMANENT_ERROR}
    client, _, _ = build_client(behaviours, fallback="gemma-x")

    with pytest.raises(ExternalServiceError) as excinfo:
        client.generate("prompt")

    assert excinfo.value.retryable is False
    assert excinfo.value.status == 400


def test_unexpected_exception_is_not_swallowed():
    def behaviour():
        raise ValueError("lỗi lập trình")

    client, _, _ = build_client({"main-model": behaviour})

    with pytest.raises(ValueError):
        client.generate("prompt")


def test_schema_is_sent_without_unsupported_keys():
    client, fake, _ = build_client({"main-model": "{}"})

    schema = {
        "type": "object",
        "properties": {"a": {"type": "string", "additionalProperties": False}},
        "additionalProperties": False,
    }

    client.generate("prompt", schema)

    sent = fake.models.calls[0]["config"].response_schema
    assert "additionalProperties" not in sent
    assert "additionalProperties" not in sent["properties"]["a"]
    assert fake.models.calls[0]["config"].response_mime_type == "application/json"
