import pytest

from Agents.ImagePlannerAgent import ImagePlannerAgent
from Errors import ConfigurationError, ValidationError
from fakes import FakeLlmClient

CONFIG = {
    "role": "visual planner",
    "prompt": "Lập kế hoạch cảnh.",
    "min_images": 2,
    "max_images": 5,
    "duration_tolerance_seconds": 0.05,
    "json_max_attempts": 1,
}


def scene(scene_id, start, end):
    return {
        "scene_id": scene_id,
        "start_seconds": start,
        "end_seconds": end,
        "duration_seconds": end - start,
        "narration_summary": "Tóm tắt lời đọc.",
        "visual_description": "Mô tả hình ảnh.",
        "image_prompt": "black marker line art drawing of a simple scene",
        "transition": "draw",
    }


def plan(duration=9.0, scene_count=3):
    step = duration / scene_count
    scenes = []

    for index in range(1, scene_count + 1):
        start = round(step * (index - 1), 3)
        end = duration if index == scene_count else round(step * index, 3)
        scenes.append(scene(index, start, end))

    return {
        "total_duration_seconds": duration,
        "image_count": scene_count,
        "scenes": scenes,
    }


def build_agent(responses, config=None):
    client = FakeLlmClient(responses)
    return ImagePlannerAgent(config or CONFIG, client, log_service=None), client


def test_run_accepts_valid_plan(article):
    agent, _ = build_agent([plan()])

    result = agent.run(article, "Kịch bản.", 9.0)

    assert result["image_count"] == 3
    assert len(result["scenes"]) == 3


def test_float_image_count_is_accepted(article):
    valid_plan = plan()
    valid_plan["image_count"] = 3.0

    agent, _ = build_agent([valid_plan])

    assert agent.run(article, "Kịch bản.", 9.0)["image_count"] == 3.0


def test_image_count_mismatch_is_normalized(article):
    """image_count là trường suy dẫn nên được suy ra từ số cảnh."""
    invalid_plan = plan()
    invalid_plan["image_count"] = 4

    agent, _ = build_agent([invalid_plan])

    result = agent.run(article, "Kịch bản.", 9.0)

    assert result["image_count"] == 3
    assert len(result["scenes"]) == 3


def test_too_few_scenes_is_rejected(article):
    agent, _ = build_agent([plan(scene_count=1)])

    with pytest.raises(ValidationError):
        agent.run(article, "Kịch bản.", 6.0)


def test_plan_ending_beyond_tolerance_is_rejected(article):
    invalid_plan = plan()
    invalid_plan["scenes"][-1]["end_seconds"] = 20.0
    invalid_plan["scenes"][-1]["duration_seconds"] = 14.0
    invalid_plan["total_duration_seconds"] = 20.0

    agent, _ = build_agent([invalid_plan])

    with pytest.raises(ValidationError):
        agent.run(article, "Kịch bản.", 9.0)


def test_gap_in_timeline_is_rejected(article):
    invalid_plan = plan()
    invalid_plan["scenes"][1]["start_seconds"] += 1.0

    agent, _ = build_agent([invalid_plan])

    with pytest.raises(ValidationError):
        agent.run(article, "Kịch bản.", 9.0)


def test_scene_ids_are_renumbered(article):
    """scene_id đánh số theo vị trí cảnh, không tin giá trị model trả về."""
    invalid_plan = plan()
    invalid_plan["scenes"][0]["scene_id"] = 7

    agent, _ = build_agent([invalid_plan])

    result = agent.run(article, "Kịch bản.", 9.0)

    assert [scene["scene_id"] for scene in result["scenes"]] == [1, 2, 3]


def test_missing_image_prompt_is_rejected(article):
    invalid_plan = plan()
    invalid_plan["scenes"][2]["image_prompt"] = "   "

    agent, _ = build_agent([invalid_plan])

    with pytest.raises(ValidationError):
        agent.run(article, "Kịch bản.", 9.0)


def test_invalid_transition_is_rejected(article):
    invalid_plan = plan()
    invalid_plan["scenes"][0]["transition"] = "spin"

    agent, _ = build_agent([invalid_plan])

    with pytest.raises(ValidationError):
        agent.run(article, "Kịch bản.", 9.0)


def test_invalid_audio_duration_is_rejected(article):
    agent, _ = build_agent([plan()])

    with pytest.raises(ValidationError):
        agent.run(article, "Kịch bản.", None)


def test_empty_narration_is_rejected(article):
    agent, _ = build_agent([plan()])

    with pytest.raises(ValidationError):
        agent.run(article, "   ", 9.0)


def test_tolerance_is_configurable(article):
    """Lệch nhỏ trong sai số được snap về đúng thời lượng audio."""
    config = {**CONFIG, "duration_tolerance_seconds": 0.5}
    near_plan = plan()
    near_plan["scenes"][-1]["end_seconds"] = 9.3
    near_plan["scenes"][-1]["duration_seconds"] = 3.3
    near_plan["total_duration_seconds"] = 9.3

    agent, _ = build_agent([near_plan], config)

    result = agent.run(article, "Kịch bản.", 9.0)

    assert agent.duration_tolerance == 0.5
    assert result["scenes"][-1]["end_seconds"] == 9.0
    assert result["total_duration_seconds"] == 9.0


def test_derived_fields_are_normalized(article):
    broken = plan()
    broken["image_count"] = 9
    broken["total_duration_seconds"] = 42.0
    broken["scenes"][0]["scene_id"] = 5
    broken["scenes"][1]["duration_seconds"] = 99.0

    agent, _ = build_agent([broken])

    result = agent.run(article, "Kịch bản.", 9.0)

    assert result["image_count"] == 3
    assert result["total_duration_seconds"] == 9.0
    assert [scene["scene_id"] for scene in result["scenes"]] == [1, 2, 3]
    assert result["scenes"][1]["duration_seconds"] == 3.0


def test_semantic_error_triggers_repair(article):
    broken = plan()
    broken["scenes"][1]["start_seconds"] += 1.0  # tạo khoảng hở > sai số

    config = {**CONFIG, "json_max_attempts": 2}
    agent, client = build_agent([broken, plan()], config)

    result = agent.run(article, "Kịch bản.", 9.0)

    assert result["image_count"] == 3
    assert len(client.calls) == 2
    # Prompt sửa lỗi phải kèm mô tả lỗi ngữ nghĩa cụ thể
    assert "hở hoặc chồng lấn" in client.calls[1]["prompt"]


def test_semantic_repair_exhaustion_raises(article):
    broken = plan()
    broken["scenes"][1]["start_seconds"] += 1.0

    config = {**CONFIG, "json_max_attempts": 2}
    agent, client = build_agent([broken, broken], config)

    with pytest.raises(ValidationError):
        agent.run(article, "Kịch bản.", 9.0)

    assert len(client.calls) == 2


def test_normalization_is_logged(article):
    events = []

    class RecordingLog:
        def info(self, event, message="", **fields):
            events.append({"event": event, "fields": fields})

        def warning(self, event, message="", **fields):
            events.append({"event": event, "fields": fields})

    broken = plan()
    broken["image_count"] = 9

    client = FakeLlmClient([broken])
    agent = ImagePlannerAgent(CONFIG, client, log_service=RecordingLog())

    agent.run(article, "Kịch bản.", 9.0)

    assert any(
        item["event"] == "image_plan_normalized" for item in events
    )


def test_invalid_tolerance_raises_configuration_error():
    config = {**CONFIG, "duration_tolerance_seconds": -1}

    with pytest.raises(ConfigurationError):
        build_agent([], config)


def test_invalid_image_limits_raise_configuration_error():
    config = {**CONFIG, "min_images": 5, "max_images": 2}

    with pytest.raises(ConfigurationError):
        build_agent([], config)
