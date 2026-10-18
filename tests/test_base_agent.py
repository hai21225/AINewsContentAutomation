import pytest

from Agents.BaseAgent import BaseAgent
from Errors import AgentOutputError, ValidationError
from fakes import FakeLlmClient


class SampleAgent(BaseAgent):
    """Agent tối thiểu để kiểm thử hợp đồng generate_json."""

    def run(self, validator=None, max_attempts=None):
        return self.generate_json(
            "Hãy trả về JSON.",
            {"type": "object"},
            max_attempts=max_attempts,
            validator=validator,
        )


def build(responses):
    client = FakeLlmClient(responses)
    agent = SampleAgent(client, "vai trò kiểm thử", log_service=None)
    return agent, client


def test_returns_parsed_object_without_validator():
    agent, _ = build([{"a": 1}])

    assert agent.run() == {"a": 1}


def test_validator_error_triggers_repair_then_succeeds():
    calls = []

    def validator(data):
        calls.append(data)

        if len(calls) == 1:
            raise ValidationError("thiếu trường bắt buộc")

    agent, client = build([{"a": 1}, {"a": 2}])

    assert agent.run(validator=validator, max_attempts=2) == {"a": 2}
    assert len(client.calls) == 2
    # Prompt lần 2 phải kèm mô tả lỗi ngữ nghĩa để model sửa
    assert "thiếu trường bắt buộc" in client.calls[1]["prompt"]


def test_validator_exhaustion_raises_validation_error():
    def validator(data):
        raise ValidationError("luôn sai")

    agent, client = build([{"a": 1}, {"a": 2}])

    with pytest.raises(ValidationError):
        agent.run(validator=validator, max_attempts=2)

    assert len(client.calls) == 2


def test_validator_may_mutate_output_in_place():
    def validator(data):
        data["b"] = 2

    agent, _ = build([{"a": 1}])

    assert agent.run(validator=validator) == {"a": 1, "b": 2}


def test_validator_is_not_called_when_json_is_invalid():
    called = []

    def validator(data):
        called.append(data)

    agent, _ = build(["khong-phai-json"])

    with pytest.raises(AgentOutputError):
        agent.run(validator=validator, max_attempts=1)

    assert called == []
