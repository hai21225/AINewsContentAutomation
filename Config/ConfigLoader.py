import json
from pathlib import Path


class ConfigLoader:

    def __init__(self, config_dir="config"):
        self._config_dir = Path(config_dir)

    def _load_json(self, path):
        with open(path, "r", encoding="utf-8") as file:
            return json.load(file)

    def get_providers(self):
        return self._load_json(
            self._config_dir / "llm" / "providers.json"
        )

    def get_models(self):
        return self._load_json(
            self._config_dir / "llm" / "models.json"
        )

    def get_agent(self, agent_name):
        return self._load_json(
            self._config_dir / "agents" / f"{agent_name}.json"
        )

    def get_rss(self):
        return self._load_json(
            self._config_dir / "rss.json"
        )

    def get_tts(self):
        return self._load_json(
            self._config_dir / "tts.json"
        )