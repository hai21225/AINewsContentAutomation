from LLM.OllamaClient import OllamaClient
from LLM.GeminiClient import GeminiClient

# from LLM.OpenAiClient import OpenAiClient


class AiClientFactory:

    _providers = {
        "ollama": OllamaClient,
        "gemini": GeminiClient,
    }

    @classmethod
    def create(cls, provider_config, model_config):

        provider_type = provider_config["type"].lower()

        client_class = cls._providers.get(provider_type)

        if client_class is None:
            raise ValueError(
                f"Provider '{provider_type}' chưa được hỗ trợ."
            )

        return client_class(
            provider_config,
            model_config
        )