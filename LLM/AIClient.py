from abc import ABC, abstractmethod


class AiClient(ABC):

    @abstractmethod
    def generate(self, prompt, outputFormat=None):
        pass