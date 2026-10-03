import json


class ConfigLoader:

    def __init__(self, configPath):
        self._configPath = configPath
        self._config = None

        self.Load()

    def Load(self):
        with open(
            self._configPath,
            "r",
            encoding="utf-8"
        ) as file:

            self._config = json.load(file)

    def GetAgentConfig(self, agentName):
        return self._config["agents"][agentName]

    def GetRssConfig(self):
        return self._config["rss"]

    def GetLlmConfig(self):
        return self._config["llm"]