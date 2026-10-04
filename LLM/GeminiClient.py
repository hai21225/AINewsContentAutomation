import os

from google import genai
from google.genai import types

from LLM.AIClient import AiClient


class GeminiClient(AiClient):

    def __init__(self, provider_config, model_config):

        api_key_env = provider_config["api_key_env"]
        api_key = os.getenv(api_key_env)

        if not api_key:
            raise ValueError(
                f"Không tìm thấy API key trong biến môi trường '{api_key_env}'."
            )

        self._client = genai.Client(
            api_key=api_key
        )

        self._model = model_config["model"]

        self._options = model_config.get(
            "options",
            {}
        )

    def generate(self, prompt, outputFormat=None):

        config_args = {
            "temperature": self._options.get(
                "temperature",
                0.2
            )
        }

        if outputFormat is not None:
            config_args["response_mime_type"] = "application/json"
            config_args["response_schema"] = outputFormat

        response = self._client.models.generate_content(
            model=self._model,
            contents=prompt,
            config=types.GenerateContentConfig(
                **config_args
            )
        )

        return response.text