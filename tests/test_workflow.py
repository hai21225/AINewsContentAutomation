import pytest

from Agents.Workflow import Workflow
from Errors import AgentOutputError, MediaError


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

    def run(self, article):
        if article["id"] in self.failing:
            raise AgentOutputError("không viết được kịch bản")

        return {
            "tts_text": "Nội dung bản tin.",
            "hashtags": ["#a", "#b", "#c"],
            "title": article["title"],
        }


class StubTts:
    def __init__(self, failing=()):
        self.failing = set(failing)

    def run(self, article_id, tts_text):
        if article_id in self.failing:
            raise MediaError("edge-tts lỗi")

        return f"outputmp3/{article_id}.mp3"

    def duration(self, audio_path):
        return 9.0


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
