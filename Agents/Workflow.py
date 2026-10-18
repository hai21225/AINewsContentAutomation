from Errors import AppError
from Services.LogService import NullLogService


class Workflow:
    """Điều phối pipeline cho từng bài viết đã chọn.

    Nguyên tắc quan trọng: **một bài lỗi không làm chết các bài khác**. Mỗi
    bài được chạy trong vùng bảo vệ riêng và luôn trả về trạng thái
    ``completed``/``failed`` kèm bước thất bại.

    Ghi chú: ``video_service``/``social_service`` chưa được nối vào pipeline
    (sẽ bổ sung ở Phase 4/5 cùng hợp đồng rõ ràng), nên Phase 1 chưa nhận
    tham số này.
    """

    def __init__(self, research_service, selector_agent, writing_supervisor,
                 tts_service=None, image_planner_agent=None,
                 image_service=None, log_service=None, article_limit=None,
                 fail_fast=False):
        self.research_service = research_service
        self.selector_agent = selector_agent
        self.writing_supervisor = writing_supervisor
        self.tts_service = tts_service
        self.image_planner_agent = image_planner_agent
        self.image_service = image_service
        self.log_service = log_service or NullLogService()
        self.article_limit = int(article_limit) if article_limit else None
        self.fail_fast = bool(fail_fast)

    # ------------------------------------------------------------------
    # Điểm vào
    # ------------------------------------------------------------------

    def run(self, rss_config):
        """Chạy toàn bộ pipeline.

        Returns:
            dict: ``{"total", "completed", "failed", "results"}``.
        """
        self.log_service.info(
            "workflow_started", f"Bắt đầu pipeline cho {len(rss_config)} feed."
        )

        articles = self._collect_articles(rss_config)

        if not articles:
            self.log_service.error(
                "workflow_no_articles",
                "Không thu thập được bài viết nào từ RSS.",
            )
            return self._summary([])

        articles = self._select_articles(articles)

        if not articles:
            self.log_service.error(
                "workflow_no_selection",
                "Selector không chọn được bài nào.",
            )
            return self._summary([])

        results = []

        for index, article in enumerate(articles, start=1):
            results.append(self.process_article(article, index, len(articles)))

        summary = self._summary(results)

        self.log_service.info(
            "workflow_completed",
            f"Hoàn tất: {summary['completed']}/{summary['total']} bài thành công.",
            completed=summary["completed"],
            failed=summary["failed"],
        )

        return summary

    def process_article(self, article, index, total):
        """Xử lý một bài; luôn trả về dict có ``status``."""
        article_id = article.get("id")

        result = {
            "article_id": article_id,
            "title": article.get("title", ""),
            "index": index,
            "total": total,
            "status": "completed",
            "failed_stage": None,
            "error": None,
        }

        stage = "write"

        try:
            self.log_service.info(
                "article_started",
                f"Bắt đầu bài {index}/{total}.",
                article_id=article_id,
                index=index,
                total=total,
            )

            content = self.run_stage(
                "write", article_id,
                lambda: self.writing_supervisor.run(article),
            )
            result["content"] = content

            if self.tts_service:
                stage = "tts"
                audio = self.run_stage(
                    "tts", article_id,
                    lambda: self.tts_service.run(
                        article_id, content["tts_text"]
                    ),
                )
                result["audio"] = audio

                stage = "audio_duration"
                result["audio_duration_seconds"] = self.run_stage(
                    "audio_duration", article_id,
                    lambda: self.tts_service.duration(audio),
                )

            if self.image_planner_agent:
                stage = "image_plan"
                result["image_plan"] = self.run_stage(
                    "image_plan", article_id,
                    lambda: self.image_planner_agent.run(
                        article,
                        content["tts_text"],
                        result.get("audio_duration_seconds"),
                    ),
                )

            if self.image_service and result.get("image_plan"):
                stage = "images"
                images = self.run_stage(
                    "images", article_id,
                    lambda: self.image_service.run(
                        article_id, result["image_plan"]
                    ),
                )
                result["images"] = images

                failed_images = [item for item in images if not item.get("ok")]

                if failed_images:
                    raise AppError(
                        f"{len(failed_images)}/{len(images)} ảnh không tạo được."
                    )

        except Exception as error:
            self._mark_failed(result, stage, error)

            if self.fail_fast:
                raise

        else:
            self.log_service.info(
                "article_completed",
                f"Hoàn tất bài {index}/{total}.",
                article_id=article_id,
                index=index,
            )

        return result

    # ------------------------------------------------------------------
    # Nội bộ
    # ------------------------------------------------------------------

    def run_stage(self, stage, article_id, func):
        """Chạy một bước trong vùng có log đo thời gian."""
        with self.log_service.stage(stage, article_id=article_id):
            return func()

    def _collect_articles(self, rss_config):
        with self.log_service.stage("collect_articles", feed_count=len(rss_config)):
            return self.research_service.search_all(rss_config)

    def _select_articles(self, articles):
        with self.log_service.stage(
            "select_articles", candidate_count=len(articles)
        ):
            selected = self.selector_agent.run(articles)

        by_id = {article["id"]: article for article in articles}
        chosen = []

        # Giữ đúng thứ tự ưu tiên mà selector đã trả về.
        for item in selected:
            article = by_id.get(item.get("id"))

            if article is not None:
                chosen.append(article)

        if self.article_limit is not None:
            chosen = chosen[: self.article_limit]
            self.log_service.info(
                "article_limit_applied",
                f"Giới hạn xử lý {len(chosen)} bài.",
                limit=self.article_limit,
            )

        return chosen

    def _mark_failed(self, result, stage, error):
        result["status"] = "failed"
        result["failed_stage"] = stage
        result["error"] = f"{type(error).__name__}: {error}"

        log = self.log_service.error

        if isinstance(error, AppError):
            log(
                "article_failed",
                f"Bài {result['index']}/{result['total']} thất bại ở bước "
                f"'{stage}': {error}",
                article_id=result["article_id"],
                stage=stage,
                error_type=type(error).__name__,
            )
        else:
            log(
                "article_failed_unexpected",
                f"Bài {result['index']}/{result['total']} lỗi ngoài dự kiến ở "
                f"bước '{stage}': {type(error).__name__}: {error}",
                article_id=result["article_id"],
                stage=stage,
                error_type=type(error).__name__,
            )

    @staticmethod
    def _summary(results):
        completed = [item for item in results if item["status"] == "completed"]
        failed = [item for item in results if item["status"] != "completed"]

        return {
            "total": len(results),
            "completed": len(completed),
            "failed": len(failed),
            "results": results,
        }
