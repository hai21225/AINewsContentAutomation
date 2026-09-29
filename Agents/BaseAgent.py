class BaseAgent:
    _name = "llama3.1:8b"
    _role = "default role"

    def __init__(self, name , role):
        self._name = name
        self._role = role

    def build_prompt(self, task):
        return f"""
Vai trò của bạn:
{self._role}

Nhiệm vụ hiện tại:
{task}

Hãy thực hiện nhiệm vụ.
"""

    def run(self, task):
        raise NotImplementedError("Subclass must implement run()")
    



