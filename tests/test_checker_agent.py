import pytest

from Agents.CheckerAgent import CheckerAgent
from Errors import AgentOutputError
from fakes import FakeLlmClient

CONFIG = {
    "role": "kiểm tra viên",
    "prompt": "Kiểm tra nội dung.",
    "minimum_score": 8.0,
    "json_max_attempts": 1,
}

WRITER_OUTPUT = {"tts_text": "Nội dung bản tin.", "hashtags": ["#a", "#b", "#c"]}


def build_agent(responses):
    client = FakeLlmClient(responses)
    return CheckerAgent(CONFIG, client, log_service=None), client


def result(passed=True, score=9.0, issues=None, instruction="Không cần sửa."):
    return {
        "passed": passed,
        "score": score,
        "issues": issues or [],
        "revision_instruction": instruction,
    }


def test_valid_result_is_normalized(article):
    agent, _ = build_agent([result()])

    checked = agent.run(article, WRITER_OUTPUT)

    assert checked == {
        "passed": True,
        "score": 9.0,
        "issues": [],
        "revision_instruction": "Không cần sửa.",
    }


def test_score_is_clamped_to_valid_range(article):
    agent, _ = build_agent([result(score=99)])

    assert agent.run(article, WRITER_OUTPUT)["score"] == 10.0


def test_missing_score_raises_agent_output_error(article):
    agent, _ = build_agent([{"passed": True, "issues": []}])

    with pytest.raises(AgentOutputError):
        agent.run(article, WRITER_OUTPUT)


def test_non_numeric_score_raises_agent_output_error(article):
    agent, _ = build_agent([result(score="rất tốt")])

    with pytest.raises(AgentOutputError):
        agent.run(article, WRITER_OUTPUT)


def test_passed_string_is_coerced(article):
    agent, _ = build_agent([result(passed="true")])

    assert agent.run(article, WRITER_OUTPUT)["passed"] is True


def test_invalid_passed_becomes_false(article):
    agent, _ = build_agent([result(passed="khong-ro")])

    assert agent.run(article, WRITER_OUTPUT)["passed"] is False


def test_missing_issues_and_instruction_are_tolerated(article):
    agent, _ = build_agent([{"passed": False, "score": 4}])

    checked = agent.run(article, WRITER_OUTPUT)

    assert checked["issues"] == []
    assert checked["revision_instruction"] == ""


def test_malformed_json_raises_agent_output_error(article):
    agent, _ = build_agent(["khong-phai-json"])

    with pytest.raises(AgentOutputError):
        agent.run(article, WRITER_OUTPUT)


def test_run_for_candidates_attaches_candidate(article):
    candidates = [
        {"candidate_id": 0, "tts_text": "A", "hashtags": []},
        {"candidate_id": 1, "tts_text": "B", "hashtags": []},
    ]
    agent, _ = build_agent([result(score=5), result(score=9)])

    checked = agent.run_for_candidates(article, candidates)

    assert [item["candidate_id"] for item in checked] == [0, 1]
    assert [item["score"] for item in checked] == [5.0, 9.0]
    assert checked[1]["candidate"] is candidates[1]


def test_minimum_score_is_exposed():
    agent, _ = build_agent([result()])

    assert agent.minimum_score == 8.0
