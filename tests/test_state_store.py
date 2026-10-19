import json

import pytest

from Errors import StateError
from Services.StateStore import (
    COMPLETED,
    FAILED,
    PENDING,
    STAGES,
    StateStore,
    artifact_ok,
)

ARTICLE = {
    "id": "abc123",
    "title": "Bài 1",
    "source_url": "https://example.com/bai-1",
    "category": "cong_nghe",
}


def make_store(tmp_path):
    return StateStore(tmp_path / "state" / "pipeline_state.json")


def test_new_store_has_empty_state(tmp_path):
    store = make_store(tmp_path)

    assert store.load()["articles"] == {}
    assert store.get_article("abc123") is None


def test_save_and_reload_roundtrip(tmp_path):
    path = tmp_path / "state" / "pipeline_state.json"
    store = StateStore(path)

    store.upsert_article(ARTICLE, index=1, total=5)
    store.mark_stage_completed("abc123", "write", output={"tts_text": "xin chào"})
    store.mark_article_status("abc123", COMPLETED)
    store.save()

    assert path.is_file()
    assert not path.with_name(path.name + ".part").exists()

    reloaded = StateStore(path)
    reloaded.load()

    assert reloaded.get_article("abc123")["title"] == "Bài 1"
    assert reloaded.is_article_completed("abc123") is True
    assert reloaded.is_stage_completed("abc123", "write") is True
    assert reloaded.get_stage("abc123", "write")["output"] == {"tts_text": "xin chào"}
    assert reloaded.get_stage("abc123", "tts")["status"] == PENDING


def test_declares_future_stages_as_pending(tmp_path):
    store = make_store(tmp_path)
    store.upsert_article(ARTICLE)

    stages = store.get_article("abc123")["stages"]

    for name in STAGES:
        assert stages[name]["status"] == PENDING

    for name in ("render", "mux", "publish"):
        assert name in stages


def test_upsert_article_is_idempotent_and_updates_metadata(tmp_path):
    store = make_store(tmp_path)
    store.upsert_article(ARTICLE, index=1, total=1)
    store.mark_stage_completed("abc123", "write", output={"tts_text": "x"})

    store.upsert_article({"id": "abc123", "title": "Đổi tên"})

    entry = store.get_article("abc123")
    assert entry["title"] == "Đổi tên"
    assert entry["stages"]["write"]["status"] == COMPLETED
    assert set(entry["stages"]) >= set(STAGES)


def test_corrupt_json_raises_state_error(tmp_path):
    path = tmp_path / "pipeline_state.json"
    path.write_text("{ khong phai json", encoding="utf-8")

    with pytest.raises(StateError):
        StateStore(path).load()


def test_schema_mismatch_raises_state_error(tmp_path):
    path = tmp_path / "pipeline_state.json"
    path.write_text(
        json.dumps({"schema_version": 999, "articles": {}}), encoding="utf-8"
    )

    with pytest.raises(StateError):
        StateStore(path).load()


def test_reset_discards_previous_state(tmp_path):
    store = make_store(tmp_path)
    store.upsert_article(ARTICLE)
    store.mark_stage_completed("abc123", "write", output={"tts_text": "x"})

    store.reset()

    assert store.get_article("abc123") is None
    assert store.load()["selected_articles"] == []


def test_mark_stage_failed_clears_output_and_artifacts(tmp_path):
    store = make_store(tmp_path)
    store.upsert_article(ARTICLE)
    store.mark_stage_completed(
        "abc123", "images", output=[{"ok": True}], artifacts=["a.png"]
    )

    store.mark_stage_failed("abc123", "images", "boom")

    stage = store.get_stage("abc123", "images")
    assert stage["status"] == FAILED
    assert stage["output"] is None
    assert stage["artifacts"] == []
    assert stage["error"] == "boom"


def test_artifact_ok_checks_existence_and_size(tmp_path):
    assert artifact_ok(tmp_path / "missing.mp3") is False
    assert artifact_ok(None) is False

    empty = tmp_path / "empty.mp3"
    empty.write_bytes(b"")
    assert artifact_ok(empty) is False

    good = tmp_path / "good.mp3"
    good.write_bytes(b"data")
    assert artifact_ok(good) is True


def test_stage_artifacts_returns_empty_when_files_missing(tmp_path):
    store = make_store(tmp_path)
    store.upsert_article(ARTICLE)
    store.mark_stage_completed(
        "abc123", "tts", output={"audio_path": "nope.mp3"}, artifacts=["nope.mp3"]
    )

    assert store.stage_artifacts("abc123", "tts") == []

    real = tmp_path / "audio.mp3"
    real.write_bytes(b"data")
    store.mark_stage_completed(
        "abc123", "tts",
        output={"audio_path": str(real)}, artifacts=[str(real)],
    )

    assert store.stage_artifacts("abc123", "tts") == [str(real)]


def test_marking_unknown_article_raises(tmp_path):
    store = make_store(tmp_path)

    with pytest.raises(StateError):
        store.mark_stage_completed("khong-ton-tai", "write", output={})
