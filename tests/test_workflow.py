import pytest

from pathlib import Path

from Agents.Workflow import Workflow
from Errors import AgentOutputError, MediaError
from Services.StateStore import COMPLETED, FAILED, StateStore


class StubResearch:
    def __init__(self, articles):
        self.articles = articles
        self.calls = 0

    def search_all(self, rss_config):
        self.calls += 1
        return list(self.articles)


class StubSelector:
    def __init__(self, order=None):
        self.order = order

    def run(self, articles):
        if self.order is not None:
            articles = [
                article
                for article_id in self.order
                for article in articles
                if article["id"] == article_id
            ]

        return [{"id": article["id"], "reason": "ok"} for article in articles]


class StubSupervisor:
    def __init__(self, failing=()):
        self.failing = set(failing)
        self.calls = []

    def run(self, article):
        self.calls.append(article["id"])

        if article["id"] in self.failing:
            raise AgentOutputError("không viết được kịch bản")

        return {
            "tts_text": "Nội dung bản tin.",
            "hashtags": ["#a", "#b", "#c"],
            "title": article["title"],
        }


class StubTts:
    def __init__(self, failing=(), output_dir=None, fingerprint="tts-fingerprint"):
        self.failing = set(failing)
        self.output_dir = Path(output_dir) if output_dir else None
        self.fingerprint_value = fingerprint
        self.calls = []

    def run(self, article_id, tts_text):
        self.calls.append(article_id)

        if article_id in self.failing:
            raise MediaError("edge-tts lỗi")

        if self.output_dir is not None:
            target = self.output_dir / f"{article_id}.mp3"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"audio")
            return str(target)

        return f"outputmp3/{article_id}.mp3"

    def duration(self, audio_path):
        return 9.0

    def fingerprint(self, tts_text):
        return self.fingerprint_value


class StubPlanner:
    def __init__(self, failing=()):
        self.failing = set(failing)

    def run(self, article, tts_text, tts_duration):
        if article["id"] in self.failing:
            raise AgentOutputError("kế hoạch cảnh không hợp lệ")

        return {
            "total_duration_seconds": tts_duration,
            "image_count": 1,
            "scenes": [{"scene_id": 1}],
        }


class StubImageService:
    def __init__(self, failing=()):
        self.failing = set(failing)

    def run(self, article_id, image_plan):
        ok = article_id not in self.failing

        return [
            {
                "scene_id": 1,
                "path": "output/images/scene_001.png" if ok else None,
                "ok": ok,
                "error": None if ok else "pollinations lỗi",
            }
        ]


class StubRenderService:
    def __init__(self, failing=(), output_dir=None):
        self.failing = set(failing)
        self.output_dir = Path(output_dir) if output_dir else None
        self.calls = []

    def run(self, article_id, images, image_plan=None):
        self.calls.append(article_id)
        ok = article_id not in self.failing
        results = []

        for item in images:
            scene_id = item["scene_id"]
            path = None

            if ok:
                if self.output_dir is not None:
                    target = (
                        self.output_dir / article_id / f"scene_{scene_id:03d}.mp4"
                    )
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(b"clip")
                    path = str(target)
                else:
                    path = f"output/clips/{article_id}/scene_{scene_id:03d}.mp4"

            results.append(
                {
                    "scene_id": scene_id,
                    "path": path,
                    "ok": ok,
                    "error": None if ok else "engine lỗi",
                }
            )

        return results


class StubMediaService:
    def __init__(self, failing=(), output_dir=None):
        self.failing = set(failing)
        self.output_dir = Path(output_dir) if output_dir else None
        self.calls = []
        self.audios = []

    def run(self, article_id, clips, audio=None):
        self.calls.append(article_id)
        self.audios.append(audio)

        if article_id in self.failing:
            raise MediaError("ffmpeg lỗi")

        if self.output_dir is not None:
            target = self.output_dir / f"{article_id}.mp4"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"video")
            path = str(target)
        else:
            path = f"output/videos/{article_id}.mp4"

        return {
            "video_path": path,
            "duration_seconds": 9.0,
            "clip_count": len(clips),
        }


class StubPublishService:
    def __init__(self, failing=(), mode="prepare", output_dir=None):
        self.failing = set(failing)
        self.platform = "facebook"
        self.mode = mode
        self.output_dir = Path(output_dir) if output_dir else None
        self.calls = []

    def run(self, article_id, video_path, title=None, hashtags=None,
            description=None, duration_seconds=None, source_url=None):
        self.calls.append(article_id)
        self.last_call = {
            "article_id": article_id,
            "title": title,
            "hashtags": hashtags,
            "description": description,
            "source_url": source_url,
            "duration_seconds": duration_seconds,
        }

        if article_id in self.failing:
            raise MediaError("facebook lỗi")

        if self.output_dir is not None:
            target = self.output_dir / f"{article_id}.json"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("{}", encoding="utf-8")
            manifest = str(target)
        else:
            manifest = f"output/publish/{article_id}.json"

        published = self.mode == "publish"

        return {
            "article_id": article_id,
            "platform": self.platform,
            "mode": self.mode,
            "status": "published" if published else "prepared",
            "post_id": "999_1" if published else None,
            "manifest_path": manifest,
            "title": title,
            "hashtags": hashtags,
        }


def build_workflow(articles, **kwargs):
    return Workflow(
        StubResearch(articles),
        kwargs.pop("selector", StubSelector()),
        kwargs.pop("supervisor", StubSupervisor()),
        log_service=None,
        **kwargs,
    )


def test_all_articles_complete(articles):
    workflow = build_workflow(
        articles,
        tts_service=StubTts(),
        image_planner_agent=StubPlanner(),
        image_service=StubImageService(),
    )

    summary = workflow.run({"feed": "url"})

    assert summary["total"] == 3
    assert summary["completed"] == 3
    assert summary["failed"] == 0
    assert all(item["status"] == "completed" for item in summary["results"])
    assert summary["results"][0]["audio_duration_seconds"] == 9.0
    assert summary["results"][0]["images"][0]["ok"] is True


def test_one_failing_article_does_not_stop_others(articles):
    workflow = build_workflow(
        articles,
        supervisor=StubSupervisor(failing={"id-2"}),
        tts_service=StubTts(),
        image_planner_agent=StubPlanner(),
        image_service=StubImageService(),
    )

    summary = workflow.run({})

    assert summary["completed"] == 2
    assert summary["failed"] == 1

    failed = [item for item in summary["results"] if item["status"] == "failed"]
    assert failed[0]["article_id"] == "id-2"
    assert failed[0]["failed_stage"] == "write"
    assert "không viết được kịch bản" in failed[0]["error"]


def test_tts_failure_is_reported_with_stage(articles):
    workflow = build_workflow(
        articles,
        tts_service=StubTts(failing={"id-1"}),
        image_planner_agent=StubPlanner(),
        image_service=StubImageService(),
    )

    summary = workflow.run({})

    failed = [item for item in summary["results"] if item["status"] == "failed"]
    assert failed[0]["failed_stage"] == "tts"


def test_image_failure_is_reported_with_stage(articles):
    workflow = build_workflow(
        articles,
        tts_service=StubTts(),
        image_planner_agent=StubPlanner(),
        image_service=StubImageService(failing={"id-1"}),
    )

    summary = workflow.run({})

    failed = [item for item in summary["results"] if item["status"] == "failed"]
    assert failed[0]["failed_stage"] == "images"


def test_planner_failure_is_reported_with_stage(articles):
    workflow = build_workflow(
        articles,
        tts_service=StubTts(),
        image_planner_agent=StubPlanner(failing={"id-1"}),
        image_service=StubImageService(),
    )

    summary = workflow.run({})

    failed = [item for item in summary["results"] if item["status"] == "failed"]
    assert failed[0]["failed_stage"] == "image_plan"


def test_unexpected_error_is_recorded_and_isolated(articles):
    class BrokenSupervisor:
        def run(self, article):
            raise ValueError("lỗi lập trình")

    workflow = build_workflow(articles, supervisor=BrokenSupervisor())

    summary = workflow.run({})

    assert summary["failed"] == 3
    assert "ValueError" in summary["results"][0]["error"]


def test_article_limit_is_applied(articles):
    workflow = build_workflow(
        articles,
        tts_service=StubTts(),
        image_planner_agent=StubPlanner(),
        image_service=StubImageService(),
        article_limit=2,
    )

    summary = workflow.run({})

    assert summary["total"] == 2


def test_selection_order_is_preserved(articles):
    workflow = build_workflow(
        articles,
        selector=StubSelector(order=["id-3", "id-1"]),
        tts_service=StubTts(),
        image_planner_agent=StubPlanner(),
        image_service=StubImageService(),
    )

    summary = workflow.run({})

    assert [item["article_id"] for item in summary["results"]] == ["id-3", "id-1"]


def test_fail_fast_reraises(articles):
    workflow = build_workflow(
        articles,
        supervisor=StubSupervisor(failing={"id-1"}),
        fail_fast=True,
    )

    with pytest.raises(AgentOutputError):
        workflow.run({})


def test_no_articles_returns_empty_summary():
    workflow = build_workflow([])

    summary = workflow.run({})

    assert summary == {"total": 0, "completed": 0, "failed": 0, "results": []}


def test_workflow_runs_without_optional_stages(articles):
    workflow = build_workflow(articles)

    summary = workflow.run({})

    assert summary["total"] == 3
    assert summary["completed"] == 3
    assert "audio" not in summary["results"][0]
    assert "image_plan" not in summary["results"][0]
    assert summary["results"][0]["content"]["tts_text"] == "Nội dung bản tin."


# ----------------------------------------------------------------------
# Phase 3: trạng thái job & resume
# ----------------------------------------------------------------------


def test_state_file_records_selection_and_completed_articles(articles, tmp_path):
    store = StateStore(tmp_path / "state.json")

    workflow = build_workflow(
        articles,
        tts_service=StubTts(),
        image_planner_agent=StubPlanner(),
        image_service=StubImageService(),
        state_store=store,
    )

    workflow.run({"feed": "url"})

    reloaded = StateStore(tmp_path / "state.json")
    reloaded.load()

    assert reloaded.load()["selected_articles"] == ["id-1", "id-2", "id-3"]
    assert all(a["status"] == COMPLETED for a in reloaded.iter_articles())
    assert reloaded.is_stage_completed("id-1", "write")
    assert reloaded.is_stage_completed("id-1", "image_plan")


def test_completed_write_stage_is_reused_on_second_run(articles, tmp_path):
    store = StateStore(tmp_path / "state.json")
    supervisor = StubSupervisor()

    workflow = build_workflow(
        articles,
        supervisor=supervisor,
        tts_service=StubTts(),
        image_planner_agent=StubPlanner(),
        image_service=StubImageService(),
        state_store=store,
    )

    workflow.run({})
    assert len(supervisor.calls) == 3

    summary = workflow.run({})

    # Bước "write" đã xong nên không gọi lại WriterSupervisor.
    assert supervisor.calls == ["id-1", "id-2", "id-3"]
    assert summary["completed"] == 3


def test_resume_reruns_only_missing_stage(articles, tmp_path):
    store = StateStore(tmp_path / "state.json")
    supervisor = StubSupervisor()
    tts = StubTts(failing={"id-1"})

    workflow = build_workflow(
        articles,
        supervisor=supervisor,
        tts_service=tts,
        image_planner_agent=StubPlanner(),
        image_service=StubImageService(),
        state_store=store,
    )

    first = workflow.run({})

    assert first["failed"] == 1
    failed = [item for item in first["results"] if item["status"] == "failed"]
    assert failed[0]["article_id"] == "id-1"
    assert failed[0]["failed_stage"] == "tts"
    assert supervisor.calls.count("id-1") == 1

    # Sửa lỗi rồi chạy lại: "write" của id-1 phải được dùng lại.
    tts.failing = set()
    second = workflow.run({})

    assert second["completed"] == 3
    assert supervisor.calls.count("id-1") == 1


def test_fresh_run_ignores_previous_state(articles, tmp_path):
    store = StateStore(tmp_path / "state.json")
    supervisor = StubSupervisor()

    build_workflow(
        articles,
        supervisor=supervisor,
        tts_service=StubTts(),
        image_planner_agent=StubPlanner(),
        image_service=StubImageService(),
        state_store=store,
    ).run({})

    fresh = build_workflow(
        articles,
        supervisor=supervisor,
        tts_service=StubTts(),
        image_planner_agent=StubPlanner(),
        image_service=StubImageService(),
        state_store=StateStore(tmp_path / "state.json"),
        resume=False,
    )
    fresh.run({})

    # resume=False xoá state cũ nên WriterSupervisor phải chạy lại cho cả 3 bài.
    assert len(supervisor.calls) == 6


def test_missing_artifact_makes_stage_run_again(articles, tmp_path):
    store = StateStore(tmp_path / "state.json")
    supervisor = StubSupervisor()

    build_workflow(
        articles,
        supervisor=supervisor,
        tts_service=StubTts(),
        image_planner_agent=StubPlanner(),
        image_service=StubImageService(),
        state_store=store,
    ).run({})

    # Giả lập file audio bị xoá: state nói xong nhưng artifact không còn.
    store.mark_stage_failed("id-1", "tts", "giả lập mất file")
    store.save()

    workflow = build_workflow(
        articles,
        supervisor=supervisor,
        tts_service=StubTts(),
        image_planner_agent=StubPlanner(),
        image_service=StubImageService(),
        state_store=store,
    )
    summary = workflow.run({})

    assert summary["completed"] == 3
    assert store.get_stage("id-1", "tts")["status"] == COMPLETED


def test_failed_article_is_recorded_in_state(articles, tmp_path):
    store = StateStore(tmp_path / "state.json")

    build_workflow(
        articles,
        supervisor=StubSupervisor(failing={"id-2"}),
        tts_service=StubTts(),
        image_planner_agent=StubPlanner(),
        image_service=StubImageService(),
        state_store=store,
    ).run({})

    entry = store.get_article("id-2")
    assert entry["status"] == FAILED
    assert entry["failed_stage"] == "write"
    assert "không viết được kịch bản" in entry["error"]


# ----------------------------------------------------------------------
# Phase 4: dựng clip whiteboard
# ----------------------------------------------------------------------


def test_render_stage_produces_clips(articles):
    workflow = build_workflow(
        articles,
        tts_service=StubTts(),
        image_planner_agent=StubPlanner(),
        image_service=StubImageService(),
        render_service=StubRenderService(),
    )

    summary = workflow.run({})

    assert summary["completed"] == 3
    clips = summary["results"][0]["clips"]
    assert clips[0]["ok"] is True
    assert clips[0]["path"].endswith("scene_001.mp4")


def test_render_stage_is_skipped_without_render_service(articles):
    workflow = build_workflow(
        articles,
        tts_service=StubTts(),
        image_planner_agent=StubPlanner(),
        image_service=StubImageService(),
    )

    summary = workflow.run({})

    assert summary["completed"] == 3
    assert "clips" not in summary["results"][0]


def test_render_failure_is_reported_with_stage(articles):
    workflow = build_workflow(
        articles,
        tts_service=StubTts(),
        image_planner_agent=StubPlanner(),
        image_service=StubImageService(),
        render_service=StubRenderService(failing={"id-1"}),
    )

    summary = workflow.run({})

    failed = [item for item in summary["results"] if item["status"] == "failed"]
    assert failed[0]["failed_stage"] == "render"


def test_render_stage_receives_images_and_plan(articles):
    render_service = StubRenderService()
    workflow = build_workflow(
        articles,
        tts_service=StubTts(),
        image_planner_agent=StubPlanner(),
        image_service=StubImageService(),
        render_service=render_service,
        article_limit=1,
    )

    workflow.run({})

    assert render_service.calls == ["id-1"]


def test_render_stage_is_reused_on_second_run(articles, tmp_path):
    store = StateStore(tmp_path / "state.json")
    render_service = StubRenderService(output_dir=tmp_path / "clips")

    workflow = build_workflow(
        articles,
        tts_service=StubTts(),
        image_planner_agent=StubPlanner(),
        image_service=StubImageService(),
        render_service=render_service,
        state_store=store,
    )

    workflow.run({})
    assert len(render_service.calls) == 3

    summary = workflow.run({})

    # Clip còn trên đĩa nên bước "render" được dùng lại, không render lại.
    assert render_service.calls == ["id-1", "id-2", "id-3"]
    assert summary["completed"] == 3


def test_missing_clip_makes_render_run_again(articles, tmp_path):
    store = StateStore(tmp_path / "state.json")
    render_service = StubRenderService(output_dir=tmp_path / "clips")

    build_workflow(
        articles,
        tts_service=StubTts(),
        image_planner_agent=StubPlanner(),
        image_service=StubImageService(),
        render_service=render_service,
        state_store=store,
    ).run({})

    # Giả lập clip bị xoá: state nói xong nhưng artifact không còn.
    (tmp_path / "clips" / "id-1" / "scene_001.mp4").unlink()

    workflow = build_workflow(
        articles,
        tts_service=StubTts(),
        image_planner_agent=StubPlanner(),
        image_service=StubImageService(),
        render_service=render_service,
        state_store=store,
    )
    summary = workflow.run({})

    assert render_service.calls.count("id-1") == 2
    assert summary["completed"] == 3


# ----------------------------------------------------------------------
# Phase 4b: ghép video cuối
# ----------------------------------------------------------------------


def test_mux_stage_produces_final_video(articles):
    workflow = build_workflow(
        articles,
        tts_service=StubTts(),
        image_planner_agent=StubPlanner(),
        image_service=StubImageService(),
        render_service=StubRenderService(),
        media_service=StubMediaService(),
    )

    summary = workflow.run({})

    assert summary["completed"] == 3
    video = summary["results"][0]["video"]
    assert video["video_path"].endswith(".mp4")
    assert video["clip_count"] == 1


def test_mux_receives_narration_audio(articles):
    media_service = StubMediaService()
    workflow = build_workflow(
        articles,
        tts_service=StubTts(),
        image_planner_agent=StubPlanner(),
        image_service=StubImageService(),
        render_service=StubRenderService(),
        media_service=media_service,
        article_limit=1,
    )

    workflow.run({})

    assert media_service.audios == ["outputmp3/id-1.mp3"]


def test_mux_stage_is_skipped_without_media_service(articles):
    workflow = build_workflow(
        articles,
        tts_service=StubTts(),
        image_planner_agent=StubPlanner(),
        image_service=StubImageService(),
        render_service=StubRenderService(),
    )

    summary = workflow.run({})

    assert "video" not in summary["results"][0]


def test_mux_stage_is_skipped_without_clips(articles):
    workflow = build_workflow(
        articles,
        tts_service=StubTts(),
        image_planner_agent=StubPlanner(),
        image_service=StubImageService(),
        media_service=StubMediaService(),
    )

    summary = workflow.run({})

    assert summary["completed"] == 3
    assert "video" not in summary["results"][0]


def test_mux_failure_is_reported_with_stage(articles):
    workflow = build_workflow(
        articles,
        tts_service=StubTts(),
        image_planner_agent=StubPlanner(),
        image_service=StubImageService(),
        render_service=StubRenderService(),
        media_service=StubMediaService(failing={"id-1"}),
    )

    summary = workflow.run({})

    failed = [item for item in summary["results"] if item["status"] == "failed"]
    assert failed[0]["failed_stage"] == "mux"


def test_mux_stage_is_reused_on_second_run(articles, tmp_path):
    store = StateStore(tmp_path / "state.json")
    media_service = StubMediaService(output_dir=tmp_path / "videos")

    workflow = build_workflow(
        articles,
        tts_service=StubTts(),
        image_planner_agent=StubPlanner(),
        image_service=StubImageService(),
        render_service=StubRenderService(),
        media_service=media_service,
        state_store=store,
    )

    workflow.run({})
    assert len(media_service.calls) == 3

    summary = workflow.run({})

    # Video cuối còn trên đĩa nên bước "mux" được dùng lại.
    assert media_service.calls == ["id-1", "id-2", "id-3"]
    assert summary["completed"] == 3


def test_missing_video_makes_mux_run_again(articles, tmp_path):
    store = StateStore(tmp_path / "state.json")
    media_service = StubMediaService(output_dir=tmp_path / "videos")

    build_workflow(
        articles,
        tts_service=StubTts(),
        image_planner_agent=StubPlanner(),
        image_service=StubImageService(),
        render_service=StubRenderService(),
        media_service=media_service,
        state_store=store,
    ).run({})

    (tmp_path / "videos" / "id-1.mp4").unlink()

    workflow = build_workflow(
        articles,
        tts_service=StubTts(),
        image_planner_agent=StubPlanner(),
        image_service=StubImageService(),
        render_service=StubRenderService(),
        media_service=media_service,
        state_store=store,
    )
    summary = workflow.run({})

    assert media_service.calls.count("id-1") == 2
    assert summary["completed"] == 3


# ----------------------------------------------------------------------
# Fingerprint: đầu vào đổi thì bước và các bước sau phải chạy lại
# ----------------------------------------------------------------------


def test_changed_tts_fingerprint_invalidates_audio_and_downstream(
    articles, tmp_path
):
    store = StateStore(tmp_path / "state.json")
    tts = StubTts(output_dir=tmp_path / "mp3", fingerprint="v1")
    render = StubRenderService(output_dir=tmp_path / "clips")
    media = StubMediaService(output_dir=tmp_path / "videos")

    def build():
        return build_workflow(
            articles,
            tts_service=tts,
            image_planner_agent=StubPlanner(),
            image_service=StubImageService(),
            render_service=render,
            media_service=media,
            state_store=store,
            article_limit=1,
        )

    build().run({})
    build().run({})

    # Chưa đổi gì: audio và clip được dùng lại.
    assert tts.calls == ["id-1"]
    assert render.calls == ["id-1"]

    tts.fingerprint_value = "v2"

    build().run({})

    # Đổi voice -> audio làm lại, kéo theo ảnh/clip/video cũng làm lại.
    assert tts.calls == ["id-1", "id-1"]
    assert render.calls == ["id-1", "id-1"]
    assert media.calls == ["id-1", "id-1"]


def test_can_reuse_rejects_changed_fingerprint(tmp_path):
    store = StateStore(tmp_path / "state.json")
    workflow = build_workflow([], state_store=store)

    store.upsert_article({"id": "x"})
    store.mark_stage_completed(
        "x", "tts", output={"audio_path": "a.mp3"}, artifacts=[],
        metadata={"fingerprint": "a"},
    )
    cached = store.get_stage("x", "tts")

    assert workflow._can_reuse(cached, None, "a") is True
    assert workflow._can_reuse(cached, None, "b") is False

    # State cũ không có fingerprint: vẫn dùng lại (migration an toàn).
    legacy = {"status": "completed", "artifacts": [], "metadata": {}}
    assert workflow._can_reuse(legacy, None, "b") is True


# ----------------------------------------------------------------------
# Publish (Phase 5)
# ----------------------------------------------------------------------


def publish_workflow(articles, tmp_path, publish, **kwargs):
    return build_workflow(
        articles,
        tts_service=StubTts(output_dir=tmp_path / "mp3"),
        image_planner_agent=StubPlanner(),
        image_service=StubImageService(),
        render_service=StubRenderService(output_dir=tmp_path / "clips"),
        media_service=StubMediaService(output_dir=tmp_path / "videos"),
        publish_service=publish,
        **kwargs,
    )


def test_publish_stage_records_manifest(articles, tmp_path):
    publish = StubPublishService(output_dir=tmp_path / "publish")
    workflow = publish_workflow(articles, tmp_path, publish, article_limit=1)

    summary = workflow.run({})

    result = summary["results"][0]
    assert result["status"] == "completed"
    assert result["publish"]["status"] == "prepared"
    assert result["publish"]["post_id"] is None
    assert Path(result["publish"]["manifest_path"]).is_file()
    assert result["publish"]["title"] == "Bài 1"
    assert result["publish"]["hashtags"] == ["#a", "#b", "#c"]
    assert publish.calls == ["id-1"]


def test_publish_receives_article_description_and_source(articles, tmp_path):
    publish = StubPublishService(output_dir=tmp_path / "publish")
    workflow = publish_workflow(articles, tmp_path, publish, article_limit=1)

    workflow.run({})

    assert publish.last_call["description"] == "Mô tả ngắn của bài kiểm thử."
    assert publish.last_call["source_url"] == "https://example.com/bai-viet"
    assert publish.last_call["title"] == "Bài 1"


def test_publish_stage_skipped_without_service(articles):
    workflow = build_workflow(
        articles,
        tts_service=StubTts(),
        image_planner_agent=StubPlanner(),
        image_service=StubImageService(),
    )

    summary = workflow.run({})

    assert "publish" not in summary["results"][0]


def test_publish_stage_skipped_without_video(articles, tmp_path):
    publish = StubPublishService(output_dir=tmp_path / "publish")
    workflow = build_workflow(
        articles,
        tts_service=StubTts(),
        image_planner_agent=StubPlanner(),
        image_service=StubImageService(),
        publish_service=publish,
    )

    summary = workflow.run({})

    assert "publish" not in summary["results"][0]
    assert publish.calls == []


def test_publish_failure_is_reported_with_stage(articles, tmp_path):
    publish = StubPublishService(
        failing={"id-1"}, output_dir=tmp_path / "publish"
    )
    workflow = publish_workflow(articles, tmp_path, publish, article_limit=1)

    summary = workflow.run({})

    assert summary["results"][0]["status"] == "failed"
    assert summary["results"][0]["failed_stage"] == "publish"


def test_publish_stage_is_reused_until_mode_changes(articles, tmp_path):
    store = StateStore(tmp_path / "state.json")
    publish = StubPublishService(output_dir=tmp_path / "publish")

    def build():
        return publish_workflow(
            articles, tmp_path, publish, state_store=store, article_limit=1
        )

    build().run({})
    build().run({})

    # Vẫn chế độ prepare: bản kê được dùng lại, không ghi lại.
    assert publish.calls == ["id-1"]

    # Đổi sang đăng thật -> đầu vào publish đổi -> phải chạy lại.
    publish.mode = "publish"

    summary = build().run({})

    assert publish.calls == ["id-1", "id-1"]
    assert summary["results"][0]["publish"]["status"] == "published"
    assert summary["results"][0]["publish"]["post_id"] == "999_1"


def test_publish_result_requires_every_platform_to_succeed():
    from Agents.Workflow import _has_publish_result

    stage = {
        "output": {
            "status": "partial",
            "results": {
                "facebook": {"status": "published", "post_id": "fb-1"},
                "youtube": {"status": "failed", "post_id": None},
            },
        }
    }

    # Một nền tảng lỗi -> chưa xong, lần chạy sau phải đăng tiếp.
    assert _has_publish_result(stage) is False

    stage["output"]["status"] = "published"
    stage["output"]["results"]["youtube"] = {
        "status": "published",
        "post_id": "yt-1",
    }

    assert _has_publish_result(stage) is True
