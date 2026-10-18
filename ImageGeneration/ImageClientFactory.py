from Errors import ConfigurationError
from ImageGeneration.PollinationsImageClient import PollinationsImageClient


class ImageClientFactory:
    """Tạo image client từ (provider_config, model_config)."""

    _PROVIDERS = {
        "pollinations": PollinationsImageClient,
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
                f"Image provider '{provider_type}' chưa được hỗ trợ. "
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
        model_config = models.get(model_name)

        if model_config is None:
            raise ConfigurationError(
                f"Image model '{model_name}' chưa được khai báo. "
                f"Model hiện có: {', '.join(sorted(models)) or '(không có)'}."
            )

        provider_name = model_config.get("provider")
        provider_config = providers.get(provider_name)

        if provider_config is None:
            raise ConfigurationError(
                f"Image model '{model_name}' trỏ tới provider "
                f"'{provider_name}' nhưng provider này chưa được khai báo."
            )

        return cls.create(
            provider_config,
            model_config,
            log_service=log_service,
            retry_config=retry_config,
        )
