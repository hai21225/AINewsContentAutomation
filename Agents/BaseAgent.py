


class BaseAgent:

    def __init__(self, llmClient , role):
        self._llmClient=llmClient
        self._role = role

    def build_prompt(self, task):
        return f"""
Vai trò của bạn:
{self._role}

Nhiệm vụ hiện tại:
{task}

Hãy thực hiện nhiệm vụ.
"""

    def generate(self, task, outputFormat):
        prompt = self.build_prompt(task)
        return self._llmClient.generate(prompt, outputFormat)

    def run(self, task):
        raise NotImplementedError("Subclass must implement run()")
    



