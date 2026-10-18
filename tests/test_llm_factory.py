import pytest

from Errors import ConfigurationError
from LLM.GeminiClient import GeminiClient
from LLM.LlmClientFactory import LlmClientFactory
from LLM.OllamaClient import OllamaClient

PROVIDERS = {
    "gemini": {"type": "gemini", "api_key_env": "GEMINI_API_KEY"},
    "ollama": {"type": "ollama", "base_url": "http://localhost:11434"},
    "openai": {"type": "openai", "api_key_env": "OPENAI_API_KEY"},
}

MODELS = {
    "gemini_x": {
        "provider": "gemini",
        "model": "gemini-x",
        "fallback_model": "gemma-x",
        "options": {"temperature": 0.2},
    },
    "local": {"provider": "ollama", "model": "llama3.1:8b"},
}


def test_create_gemini_client_from_registry(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")

    client = LlmClientFactory.create_for_model("gemini_x", PROVIDERS, MODELS)

    assert isinstance(client, GeminiClient)
    assert client.model == "gemini-x"
    # fallback dạng chuỗi được chuẩn hoá thành list
    assert client.fallback_models == ["gemma-x"]


def test_create_ollama_client_uses_same_signature():
    client = LlmClientFactory.create_for_model("local", PROVIDERS, MODELS)

    assert isinstance(client, OllamaClient)
    assert client.model == "llama3.1:8b"


def test_unknown_provider_type_raises_configuration_error(monkeypatch):
    providers = {"moi": {"type": "provider-khong-ton-tai"}}
    models = {"m": {"provider": "moi", "model": "x"}}

    with pytest.raises(ConfigurationError) as excinfo:
        LlmClientFactory.create_for_model("m", providers, models)

    assert "chưa được hỗ trợ" in str(excinfo.value)


def test_declared_openai_provider_is_not_implemented():
    models = {"m": {"provider": "openai", "model": "gpt-x"}}

    with pytest.raises(ConfigurationError):
        LlmClientFactory.create_for_model("m", PROVIDERS, models)


def test_unknown_model_raises_configuration_error():
    with pytest.raises(ConfigurationError) as excinfo:
        LlmClientFactory.create_for_model("khong-co", PROVIDERS, MODELS)

    assert "gemini_x" in str(excinfo.value)


def test_model_pointing_to_unknown_provider_raises():
    models = {"m": {"provider": "khong-co", "model": "x"}}

    with pytest.raises(ConfigurationError) as excinfo:
        LlmClientFactory.create_for_model("m", PROVIDERS, models)

    assert "khong-co" in str(excinfo.value)


def test_gemini_requires_api_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    with pytest.raises(ConfigurationError) as excinfo:
        LlmClientFactory.create_for_model("gemini_x", PROVIDERS, MODELS)

    assert "GEMINI_API_KEY" in str(excinfo.value)
