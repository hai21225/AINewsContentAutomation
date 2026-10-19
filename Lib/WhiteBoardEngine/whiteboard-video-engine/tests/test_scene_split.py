from whiteboard_skill.providers.llm_mock import MockLLMProvider
from whiteboard_skill.scene_split import split_script


def test_mock_scene_split_returns_scenes():
    scenes = split_script("第一句。第二句。", MockLLMProvider(), scene_count=2)
    assert len(scenes) == 2
    assert scenes[0].image_prompt
