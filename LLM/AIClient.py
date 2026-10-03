import requests

class AiClient:

    def __init__(self, config):
        self._baseUrl = config["base_url"]
        self._model = config["model"]
        self._options= config.get("options",{})

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

        print("done:", data.get("done"))
        print("done_reason:", data.get("done_reason"))
        print("eval_count:", data.get("eval_count"))
        print("response:", repr(data["response"]))

        return data["response"]