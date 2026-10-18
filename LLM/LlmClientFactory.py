from Errors import ConfigurationError
from LLM.GeminiClient import GeminiClient
from LLM.OllamaClient import OllamaClient


class LlmClientFactory:
    """Tạo LLM client từ (provider_config, model_config)."""

    _PROVIDERS = {
        "gemini": GeminiClient,
        "ollama": OllamaClient,
    }

    @classmethod
    def available_providers(cls):
        return sorted(cls._PROVIDERS)

    @classmethod
    def create(cls, provider_config, model_config, log_service=None,
               retry_config=None):
        provider_type = str(provider_config.get("type", "")).lower()

        client_class = cls._PROVIDERS.get(provider_type)

        if client_class is None:
            raise ConfigurationError(
                f"Provider '{provider_type}' chưa được hỗ trợ. "
                f"Provider hiện có: {', '.join(cls.available_providers())}."
            )

        return client_class(
            provider_config,
            model_config,
            log_service=log_service,
            retry_config=retry_config,
        )

    @classmethod
    def create_for_model(cls, model_name, providers, models, log_service=None,
                         retry_config=None):
        """Tra cứu model -> provider rồi tạo client.

        Dùng cho main.py để tránh lặp lại logic tra cứu ở 4 nơi.
        """
        model_config = models.get(model_name)

        if model_config is None:
            raise ConfigurationError(
                f"Model '{model_name}' chưa được khai báo. "
                f"Model hiện có: {', '.join(sorted(models)) or '(không có)'}."
            )

        provider_name = model_config.get("provider")
        provider_config = providers.get(provider_name)

        if provider_config is None:
            raise ConfigurationError(
                f"Model '{model_name}' trỏ tới provider '{provider_name}' "
                f"nhưng provider này chưa được khai báo."
            )

        return cls.create(
            provider_config,
            model_config,
            log_service=log_service,
            retry_config=retry_config,
        )
