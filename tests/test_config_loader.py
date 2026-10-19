import json

import pytest

from Config.ConfigLoader import ConfigLoader
from Errors import ConfigurationError


def test_loads_real_project_config():
    loader = ConfigLoader()

    assert loader.config_dir.name == "Config"
    assert len(loader.get_rss()) == 10
    assert loader.get_tts()["provider"] == "edge_tts"
    assert "gemini_3.5_flash_lite" in loader.get_models()
    assert "gemini" in loader.get_providers()

    assert loader.get_agent("writer")["min_word"] > 0
    assert loader.get_agent("image_planner")["min_images"] >= 1
    assert set(loader.list_agents()) == {
        "checker",
        "image_planner",
        "selector",
        "writer",
    }

    assert loader.get_logging()["level"]
    assert loader.get_pipeline()["research"]["max_attempts"] >= 1
    assert loader.get_pipeline()["state"]["path"]
    assert "flux" in loader.get_image_models()
    assert loader.get_image_service()["output_dir"]

    render_config = loader.get_render()
    assert render_config["engine_dir"] == (
        "Lib/WhiteBoardEngine/whiteboard-video-engine"
    )
    assert render_config["fps"] >= 1
    assert render_config["output_dir"]

    media_config = loader.get_media()
    assert media_config["output_dir"]
    assert media_config["duration_tolerance_seconds"] > 0

    publish_config = loader.get_publish()
    assert publish_config["platform"] == "facebook"
    assert publish_config["mode"] in ("prepare", "publish", "off")
    assert publish_config["facebook"]["api_version"]
    assert publish_config["facebook"]["page_id_env"]


def test_default_config_dir_is_independent_of_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    loader = ConfigLoader()

    assert len(loader.get_rss()) == 10


def test_unknown_agent_lists_available_agents():
    loader = ConfigLoader()

    with pytest.raises(ConfigurationError) as excinfo:
        loader.get_agent("khong_ton_tai")

    message = str(excinfo.value)
    assert "writer" in message
    assert "selector" in message


def test_missing_file_raises_configuration_error(tmp_path):
    loader = ConfigLoader(tmp_path)

    with pytest.raises(ConfigurationError) as excinfo:
        loader.get_tts()

    assert "tts.json" in str(excinfo.value)


def test_missing_publish_config_raises(tmp_path):
    loader = ConfigLoader(tmp_path)

    with pytest.raises(ConfigurationError) as excinfo:
        loader.get_publish()

    assert "publish.json" in str(excinfo.value)


def test_invalid_json_raises_configuration_error(tmp_path):
    (tmp_path / "rss.json").write_text("{ khong phai json", encoding="utf-8")

    loader = ConfigLoader(tmp_path)

    with pytest.raises(ConfigurationError) as excinfo:
        loader.get_rss()

    assert "không hợp lệ" in str(excinfo.value).lower()


def test_missing_config_directory_raises(tmp_path):
    with pytest.raises(ConfigurationError):
        ConfigLoader(tmp_path / "khong_ton_tai")


def test_config_is_cached(tmp_path):
    (tmp_path / "rss.json").write_text(
        json.dumps({"a": "http://example.com/rss"}), encoding="utf-8"
    )

    loader = ConfigLoader(tmp_path)

    assert loader.get_rss() is loader.get_rss()
