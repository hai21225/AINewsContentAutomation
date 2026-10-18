import json

import pytest

from Errors import ConfigurationError
from Services.LogService import LogService


def read_events(log_service):
    with open(log_service.log_path, "r", encoding="utf-8") as file:
        return [json.loads(line) for line in file if line.strip()]


def build_log_service(tmp_path, **config):
    return LogService(
        {
            "level": "INFO",
            "console": False,
            "log_file": True,
            "log_dir": str(tmp_path),
            **config,
        }
    )


def test_writes_structured_jsonl(tmp_path):
    log = build_log_service(tmp_path)

    log.info("stage_started", "Bắt đầu.", stage="write", article_id="abc")
    log.close()

    events = read_events(log)

    assert events[0]["event"] == "stage_started"
    assert events[0]["stage"] == "write"
    assert events[0]["article_id"] == "abc"
    assert events[0]["level"] == "INFO"


def test_redacts_secret_fields(tmp_path):
    log = build_log_service(tmp_path)

    log.info("call", api_key="abc123", token="xyz")
    log.close()

    event = read_events(log)[0]

    assert event["api_key"] == "***"
    assert event["token"] == "***"


def test_redacts_secrets_inside_messages_and_nested_fields(tmp_path):
    log = build_log_service(tmp_path)

    log.info(
        "call",
        "https://example.com/img?key=abc123&model=flux",
        payload={"access_token": "xyz"},
    )
    log.close()

    event = read_events(log)[0]

    assert "abc123" not in event["message"]
    assert "key=***" in event["message"]
    assert event["payload"]["access_token"] == "***"


def test_stage_logs_success_with_elapsed_time(tmp_path):
    log = build_log_service(tmp_path)

    with log.stage("tts", article_id="abc"):
        pass

    log.close()

    events = read_events(log)

    assert events[0]["event"] == "stage_started"
    assert events[1]["event"] == "stage_completed"
    assert "elapsed_ms" in events[1]
    assert events[1]["stage"] == "tts"


def test_stage_logs_failure_and_reraises(tmp_path):
    log = build_log_service(tmp_path)

    with pytest.raises(ValueError):
        with log.stage("images", article_id="abc"):
            raise ValueError("hết quota")

    log.close()

    events = read_events(log)

    assert events[-1]["event"] == "stage_failed"
    assert events[-1]["error_type"] == "ValueError"
    assert "hết quota" in events[-1]["error"]


def test_level_filters_debug_messages(tmp_path):
    log = build_log_service(tmp_path, level="WARNING")

    log.debug("khong-duoc-ghi")
    log.warning("duoc-ghi")
    log.close()

    events = read_events(log)

    assert [event["event"] for event in events] == ["duoc-ghi"]


def test_invalid_level_raises_configuration_error(tmp_path):
    with pytest.raises(ConfigurationError):
        build_log_service(tmp_path, level="KHONG-CO")


def test_log_file_disabled_has_no_path(tmp_path):
    log = LogService(
        {"level": "INFO", "console": False, "log_file": False}, name="test_no_file"
    )

    log.info("x")
    log.close()

    assert log.log_path is None
