import pytest

from Agents.SelectorAgent import SelectorAgent, build_output_format
from Errors import ConfigurationError
from fakes import FakeLlmClient

CONFIG = {
    "role": "biên tập viên",
    "prompt": "Chọn tin phù hợp.",
    "article_count": 2,
    "json_max_attempts": 1,
}


def build_agent(responses, config=None):
    client = FakeLlmClient(responses)
    return SelectorAgent(config or CONFIG, client, log_service=None), client


def test_schema_matches_configured_article_count():
    schema = build_output_format(1)
    selected = schema["properties"]["selected"]

    assert selected["minItems"] == 1
    assert selected["maxItems"] == 1

    schema = build_output_format(5)
    assert schema["properties"]["selected"]["minItems"] == 5


def test_run_returns_valid_selection(articles):
    agent, client = build_agent(
        [
            {
                "selected": [
                    {"id": "id-2", "reason": "Tin nóng."},
                    {"id": "id-1", "reason": "Có số liệu cụ thể."},
                ]
            }
        ]
    )

    selected = agent.run(articles)

    assert selected == [
        {"id": "id-2", "reason": "Tin nóng."},
        {"id": "id-1", "reason": "Có số liệu cụ thể."},
    ]

    sent_schema = client.calls[0]["output_format"]
    assert sent_schema["properties"]["selected"]["maxItems"] == 2


def test_run_filters_unknown_ids_and_duplicates(articles):
    agent, _ = build_agent(
        [
            {
                "selected": [
                    {"id": "khong-ton-tai", "reason": "x"},
                    {"id": "id-1", "reason": "ok"},
                    {"id": "id-1", "reason": "trùng"},
                ]
            }
        ]
    )

    selected = agent.run(articles)

    assert [item["id"] for item in selected] == ["id-1"]


def test_run_ignores_items_that_are_not_objects(articles):
    agent, _ = build_agent(
        [{"selected": ["id-1", {"id": "id-3", "reason": "ok"}]}]
    )

    assert [item["id"] for item in agent.run(articles)] == ["id-3"]


def test_run_with_no_candidates_returns_empty():
    agent, client = build_agent([])

    assert agent.run([]) == []
    assert client.calls == []


def test_result_is_limited_to_configured_count(articles):
    agent, _ = build_agent(
        [
            {
                "selected": [
                    {"id": "id-1", "reason": "a"},
                    {"id": "id-2", "reason": "b"},
                    {"id": "id-3", "reason": "c"},
                ]
            }
        ]
    )

    assert len(agent.run(articles)) == 2


def test_invalid_article_count_raises_configuration_error():
    config = {**CONFIG, "article_count": 0}

    with pytest.raises(ConfigurationError):
        build_agent([], config)
