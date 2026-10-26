
from ImageGeneration.PollinationsImageClient import (
    PollinationsImageClient,
)


class ImageClientFactory:

    _providers = {
        "pollinations": PollinationsImageClient,
    }

    @classmethod
    def create(cls, provider_config, model_config):
        provider_type = provider_config["type"].lower()

        client_class = cls._providers.get(provider_type)

        if client_class is None:
            raise ValueError(
                f"Image provider '{provider_type}' "
                "chưa được hỗ trợ."
            )

        return client_class(
            provider_config,
            model_config,
        )
