import requests

from LLM.AIClient import AiClient


class OllamaClient(AiClient):

    def __init__(self, config):
        self._baseUrl = config["base_url"]
        self._model = config["model"]
        self._options = config.get("options", {})

    def generate(self, prompt, outputFormat=None):

        url = f"{self._baseUrl}/api/generate"

        payload = {
            "model": self._model,
            "prompt": prompt,
            "stream": False,
            "options": self._options
        }

        if outputFormat is not None:
            payload["format"] = outputFormat

        response = requests.post(
            url,
            json=payload,
            timeout=300
        )

        response.raise_for_status()

        data = response.json()

        return data["response"]