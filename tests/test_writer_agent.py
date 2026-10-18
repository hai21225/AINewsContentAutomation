import pytest

from Agents.WriterAgent import WriterAgent
from Errors import AgentOutputError, ConfigurationError, ValidationError
from fakes import FakeLlmClient

CONFIG = {
    "role": "biên tập viên",
    "prompt": "Viết bản tin ngắn.",
    "min_word": 5,
    "max_word": 50,
    "minimum_accepted_word": 3,
    "maximum_accepted_word": 60,
    "candidate_count": 2,
    "max_retries": 2,
    "json_max_attempts": 2,
}

GOOD_TEXT = "Đây là bản tin thử nghiệm đủ dài để được chấp nhận."
SHORT_TEXT = "Ngắn."

#: Cấu hình chỉ sinh 1 ứng viên, dùng cho test kiểm tra một payload đơn lẻ.
SINGLE_CANDIDATE_CONFIG = {**CONFIG, "candidate_count": 1}


def payload(text=GOOD_TEXT, hashtags=None):
    if hashtags is None:
        hashtags = ["#tin", "#video", "#news"]

    return {"tts_text": text, "hashtags": hashtags}


def build_agent(responses, config=None):
    client = FakeLlmClient(responses)
    agent = WriterAgent(config or CONFIG, client, log_service=None)
    return agent, client


def test_run_returns_valid_candidates(article):
    agent, client = build_agent([payload(), payload()])

    result = agent.run(article)

    assert len(result["candidates"]) == 2
    assert result["writer_attempt_count"] == 2
    assert len(client.calls) == 2

    candidate = result["candidates"][0]
    assert candidate["title"] == article["title"]
    assert candidate["validation_errors"] == []
    assert candidate["word_count"] == len(GOOD_TEXT.split())


def test_run_rejects_candidates_that_are_too_short(article):
    agent, _ = build_agent([payload(SHORT_TEXT), payload(SHORT_TEXT)])

    result = agent.run(article)

    assert result["candidates"] == []
    assert len(result["rejected_candidates"]) == 2
    assert any(
        "quá ngắn" in error
        for error in result["rejected_candidates"][0]["validation_errors"]
    )


def test_run_repairs_malformed_json(article):
    agent, client = build_agent(
        ["khong-phai-json", payload()], SINGLE_CANDIDATE_CONFIG
    )

    result = agent.run(article)

    assert len(result["candidates"]) == 1
    # Prompt lần 2 phải kèm yêu cầu sửa JSON
    assert "JSON" in client.calls[1]["prompt"]


def test_run_raises_after_malformed_json_is_not_fixed(article):
    agent, _ = build_agent(
        ["khong-phai-json", "van-khong-phai-json"], SINGLE_CANDIDATE_CONFIG
    )

    with pytest.raises(AgentOutputError):
        agent.run(article)


def test_candidate_without_hashtags_is_rejected_not_crashed(article):
    agent, _ = build_agent(
        [{"tts_text": GOOD_TEXT, "hashtags": []}], SINGLE_CANDIDATE_CONFIG
    )

    result = agent.run(article)

    assert result["candidates"] == []
    assert any(
        "hashtag" in error
        for error in result["rejected_candidates"][0]["validation_errors"]
    )


def test_hashtags_are_normalized(article):
    agent, _ = build_agent(
        [payload(hashtags=["tin tuc", "#video", " news "])],
        SINGLE_CANDIDATE_CONFIG,
    )

    result = agent.run(article)

    assert result["candidates"][0]["hashtags"] == [
        "#tintuc",
        "#video",
        "#news",
    ]


def test_missing_tts_text_field_is_rejected(article):
    agent, _ = build_agent(
        [{"hashtags": ["#a", "#b", "#c"]}], SINGLE_CANDIDATE_CONFIG
    )

    result = agent.run(article)

    assert result["candidates"] == []
    assert "tts_text đang rỗng." in result["rejected_candidates"][0]["validation_errors"]


def test_article_without_content_is_rejected():
    agent, _ = build_agent([payload()])

    with pytest.raises(ValidationError):
        agent.run({"id": "x", "title": "Có tiêu đề", "content": "   "})


def test_revise_returns_prepared_candidate(article):
    agent, _ = build_agent([payload("Bản đã sửa và đủ dài để chấp nhận.")])

    revised = agent.revise(
        article,
        {"tts_text": GOOD_TEXT, "candidate_id": 7},
        {"revision_instruction": "Sửa lại câu mở đầu."},
    )

    assert revised["candidate_id"] == 7
    assert revised["title"] == article["title"]
    assert revised["validation_errors"] == []
    assert "word_count" in revised


def test_revise_without_feedback_is_rejected(article):
    agent, _ = build_agent([payload()])

    with pytest.raises(ValidationError):
        agent.revise(article, {"tts_text": GOOD_TEXT}, {})


def test_public_accessors_expose_config():
    agent, _ = build_agent([payload()])

    assert agent.max_retries == 2
    assert agent.candidate_count == 2
    assert agent.count_words("một hai ba") == 3


def test_invalid_candidate_count_raises_configuration_error():
    config = {**CONFIG, "candidate_count": 0}

    with pytest.raises(ConfigurationError):
        build_agent([], config)


def test_invalid_word_range_raises_configuration_error():
    config = {**CONFIG, "min_word": 100, "max_word": 10}

    with pytest.raises(ConfigurationError):
        build_agent([], config)
