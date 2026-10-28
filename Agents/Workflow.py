import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from Errors import AppError
from Services.LogService import NullLogService
from Services.StateStore import COMPLETED, FAILED, artifact_ok


def _digest(*parts):
    """Băm ổn định một tập giá trị (dùng làm fingerprint cho bước pipeline)."""
    payload = json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _file_fingerprint(path):
    """Định danh file theo (kích thước, mtime): file đổi thì chuỗi đổi."""
    if not path:
        return "missing"

    try:
        stat = Path(path).stat()
    except OSError:
        return "missing"

    return f"{stat.st_size}:{stat.st_mtime_ns}"


class Workflow:
    """Điều phối pipeline cho từng bài viết đã chọn.

    Nguyên tắc quan trọng: **một bài lỗi không làm chết các bài khác**. Mỗi
    bài được chạy trong vùng bảo vệ riêng và luôn trả về trạng thái
    ``completed``/``failed`` kèm bước thất bại.

    Phase 3: nếu có ``state_store``, trạng thái từng bước được ghi ra đĩa sau
    mỗi bước. Lần chạy sau sẽ **dùng lại** bước đã ``completed`` khi output và
    artifact còn hợp lệ, chỉ chạy lại phần còn thiếu. ``resume=False`` tương
    ứng cờ ``--fresh``: bỏ state cũ và chạy lại từ đầu.

    Phase 4: bước ``render`` đã được nối — mỗi cảnh có ảnh sẽ được dựng thành
    một clip whiteboard qua ``RenderService`` (engine ngoài, lệnh
    ``render-image``). Kết quả nằm ở ``result["clips"]``. Bước ``mux`` ghép
    các clip + audio lời đọc thành video cuối qua ``MediaService`` (ffmpeg);
    kết quả nằm ở ``result["video"]``.

    Fingerprint: mỗi bước từ ``tts`` trở đi lưu kèm một chuỗi định danh
    (fingerprint) của **đầu vào thật** (cấu hình TTS + lời đọc, file audio,
    thời lượng, các bước trước...). Khi đầu vào đổi — ví dụ đổi voice —
    fingerprint đổi nên bước đó và mọi bước sau không dùng lại kết quả cũ.
    State cũ chưa có fingerprint vẫn được dùng lại như trước (migration an
    toàn: chỉ áp dụng từ lần chạy ghi fingerprint trở đi).

    Phase 5: bước ``publish`` ghi bản kê video (tiêu đề/mô tả/hashtag) và — khi
    ``Config/publish.json`` đặt ``mode = "publish"`` — đăng thật lên Page
    Facebook qua ``PublishService``. Kết quả nằm ở ``result["publish"]``.
    Mặc định ``mode = "prepare"``: chỉ ghi bản kê, **không gọi mạng**.
    """

    def __init__(self, research_service, selector_agent, writing_supervisor,
                 tts_service=None, image_planner_agent=None,
                 image_service=None, render_service=None, media_service=None,
                 publish_service=None,
                 log_service=None, article_limit=None, fail_fast=False,
                 state_store=None, resume=True):
        self.research_service = research_service
        self.selector_agent = selector_agent
        self.writing_supervisor = writing_supervisor
        self.tts_service = tts_service
        self.image_planner_agent = image_planner_agent
        self.image_service = image_service
        self.render_service = render_service
        self.media_service = media_service
        self.publish_service = publish_service
        self.log_service = log_service or NullLogService()
        self.article_limit = int(article_limit) if article_limit else None
        self.fail_fast = bool(fail_fast)
        self.state_store = state_store
        self.resume = bool(resume)

    # ------------------------------------------------------------------
    # Điểm vào
    # ------------------------------------------------------------------

    def run(self, rss_config):
        """Chạy toàn bộ pipeline.

        Returns:
            dict: ``{"total", "completed", "failed", "results"}``.
        """
        self._prepare_state()

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

        self._remember_selection(articles)

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

        if self.state_store is not None:
            self.state_store.upsert_article(article, index=index, total=total)

        stage = "write"

        try:
            self.log_service.info(
                "article_started",
                f"Bắt đầu bài {index}/{total}.",
                article_id=article_id,
                index=index,
                total=total,
            )

            stage = "write"
            write_stage = self._run_or_reuse(
                article_id, "write",
                lambda: (self.writing_supervisor.run(article), [], {}),
                validate=_has_tts_text,
            )
            content = write_stage["output"]
            result["content"] = content

            tts_fingerprint = None
            plan_fingerprint = None
            images_fingerprint = None
            render_fingerprint = None

            if self.tts_service:
                stage = "tts"
                tts_fingerprint = self._tts_fingerprint(content["tts_text"])
                tts_stage = self._run_or_reuse(
                    article_id, "tts",
                    lambda: self._produce_audio(article_id, content),
                    fingerprint=tts_fingerprint,
                )
                audio = tts_stage["output"]["audio_path"]
                result["audio"] = audio

                stage = "audio_duration"
                duration_fingerprint = _digest(
                    tts_fingerprint, _file_fingerprint(audio)
                )
                duration_stage = self._run_or_reuse(
                    article_id, "audio_duration",
                    lambda: self._measure_audio(audio),
                    validate=_is_positive_number,
                    fingerprint=duration_fingerprint,
                )
                result["audio_duration_seconds"] = duration_stage["output"]

            if self.image_planner_agent:
                stage = "image_plan"
                plan_fingerprint = _digest(
                    tts_fingerprint, result.get("audio_duration_seconds")
                )
                plan_stage = self._run_or_reuse(
                    article_id, "image_plan",
                    lambda: (
                        self.image_planner_agent.run(
                            article,
                            content["tts_text"],
                            result.get("audio_duration_seconds"),
                        ),
                        [],
                        {},
                    ),
                    validate=_has_scenes,
                    fingerprint=plan_fingerprint,
                )
                result["image_plan"] = plan_stage["output"]

            if self.image_service and result.get("image_plan"):
                stage = "images"
                images_fingerprint = _digest(
                    plan_fingerprint, getattr(self.image_service, "seed", None)
                )
                images_stage = self._run_or_reuse(
                    article_id, "images",
                    lambda: self._produce_images(
                        article_id, result["image_plan"]
                    ),
                    fingerprint=images_fingerprint,
                )
                result["images"] = images_stage["output"]

            if self.render_service and result.get("images"):
                stage = "render"
                render_fingerprint = _digest(
                    images_fingerprint,
                    getattr(self.render_service, "width", None),
                    getattr(self.render_service, "height", None),
                    getattr(self.render_service, "hand_scale", None),
                )
                render_stage = self._run_or_reuse(
                    article_id, "render",
                    lambda: self._produce_clips(
                        article_id, result["images"], result.get("image_plan")
                    ),
                    validate=_has_clips,
                    fingerprint=render_fingerprint,
                )
                result["clips"] = render_stage["output"]

            if self.media_service and result.get("clips"):
                stage = "mux"
                mux_fingerprint = _digest(
                    render_fingerprint, _file_fingerprint(result.get("audio"))
                )
                mux_stage = self._run_or_reuse(
                    article_id, "mux",
                    lambda: self._produce_video(
                        article_id, result["clips"], result.get("audio")
                    ),
                    validate=_has_video,
                    fingerprint=mux_fingerprint,
                )
                result["video"] = mux_stage["output"]

            if self.publish_service and result.get("video"):
                stage = "publish"
                video = result["video"]
                publish_fingerprint = _digest(
                    self.publish_service.platform,
                    self.publish_service.mode,
                    _file_fingerprint(video.get("video_path")),
                    content.get("title"),
                    content.get("hashtags"),
                    article.get("des"),
                    article.get("source_url"),
                )
                publish_stage = self._run_or_reuse(
                    article_id, "publish",
                    lambda: self._publish_video(
                        article_id, video, content, article
                    ),
                    validate=_has_publish_result,
                    fingerprint=publish_fingerprint,
                )
                result["publish"] = publish_stage["output"]

        except Exception as error:
            self._mark_failed(result, stage, error)
            self._record_failure(article_id, stage, result["error"])

            if self.fail_fast:
                raise

        else:
            self._record_success(article_id)
            self.log_service.info(
                "article_completed",
                f"Hoàn tất bài {index}/{total}.",
                article_id=article_id,
                index=index,
            )

        return result

    # ------------------------------------------------------------------
    # Chạy hoặc dùng lại một bước
    # ------------------------------------------------------------------

    def _run_or_reuse(self, article_id, stage, runner, validate=None,
                      fingerprint=None):
        """Chạy một bước, hoặc dùng lại kết quả đã lưu nếu còn hợp lệ.

        ``runner()`` trả về ``(output, artifacts, metadata)``. Kết quả được ghi
        vào state (nếu có) sau khi chạy xong.

        ``fingerprint`` (nếu có) là chuỗi định danh đầu vào thật của bước; nó
        được lưu kèm và dùng để phát hiện đầu vào đã đổi (ví dụ đổi voice).
        """
        cached = None

        if self.state_store is not None:
            cached = self.state_store.get_stage(article_id, stage)

        if self._can_reuse(cached, validate, fingerprint):
            self.log_service.info(
                "stage_reused",
                f"Dùng lại kết quả bước '{stage}' đã có.",
                article_id=article_id,
                stage=stage,
            )
            return cached

        if cached is not None and cached.get("status") == COMPLETED:
            self.log_service.warning(
                "stage_reuse_invalid",
                f"Kết quả bước '{stage}' không còn hợp lệ, chạy lại.",
                article_id=article_id,
                stage=stage,
            )

        with self.log_service.stage(stage, article_id=article_id):
            output, artifacts, metadata = runner()

        metadata = metadata or {}

        if fingerprint is not None:
            metadata = {**metadata, "fingerprint": fingerprint}

        outcome = {
            "status": COMPLETED,
            "output": output,
            "artifacts": [str(item) for item in (artifacts or [])],
            "metadata": metadata,
        }

        if self.state_store is not None:
            self.state_store.mark_stage_completed(
                article_id, stage, output=output, artifacts=artifacts,
                metadata=metadata,
            )
            self.state_store.save()

        return outcome

    def _can_reuse(self, cached, validate, fingerprint=None):
        if self.state_store is None or not cached:
            return False

        if cached.get("status") != COMPLETED:
            return False

        artifacts = cached.get("artifacts") or []

        if not all(artifact_ok(path) for path in artifacts):
            return False

        if validate is not None and not validate(cached):
            return False

        # Fingerprint: chỉ so khi state đã có sẵn (state cũ -> bỏ qua để không
        # ép chạy lại LLM/TTS tốn kém; từ lần ghi fingerprint trở đi mới siết).
        if fingerprint is not None:
            stored = (cached.get("metadata") or {}).get("fingerprint")

            if stored is not None and stored != fingerprint:
                return False

        return True

    def _tts_fingerprint(self, tts_text):
        method = getattr(self.tts_service, "fingerprint", None)

        if method is None:
            return None

        return method(tts_text)

    # ------------------------------------------------------------------
    # Sản xuất từng bước
    # ------------------------------------------------------------------

    def _produce_audio(self, article_id, content):
        audio = self.tts_service.run(article_id, content["tts_text"])
        return {"audio_path": audio}, [audio], {}

    def _measure_audio(self, audio):
        return self.tts_service.duration(audio), [], {}

    def _produce_images(self, article_id, image_plan):
        results = self.image_service.run(article_id, image_plan)
        failed = [item for item in results if not item.get("ok")]

        if failed:
            raise AppError(
                f"{len(failed)}/{len(results)} ảnh không tạo được."
            )

        artifacts = [item["path"] for item in results if item.get("path")]
        return results, artifacts, {"ok": len(results), "failed": 0}

    def _produce_clips(self, article_id, images, image_plan):
        results = self.render_service.run(article_id, images, image_plan)
        failed = [item for item in results if not item.get("ok")]

        if failed:
            raise AppError(
                f"{len(failed)}/{len(results)} clip không render được."
            )

        artifacts = [item["path"] for item in results if item.get("path")]
        return results, artifacts, {"ok": len(results), "failed": 0}

    def _produce_video(self, article_id, clips, audio):
        video = self.media_service.run(article_id, clips, audio)
        metadata = {
            key: value for key, value in video.items() if key != "video_path"
        }
        return video, [video["video_path"]], metadata

    def _publish_video(self, article_id, video, content, article):
        result = self.publish_service.run(
            article_id,
            video["video_path"],
            title=content.get("title"),
            hashtags=content.get("hashtags"),
            description=article.get("des"),
            source_url=article.get("source_url"),
            duration_seconds=video.get("duration_seconds"),
        )
        metadata = {
            key: value for key, value in result.items() if key != "manifest_path"
        }
        return result, [result["manifest_path"]], metadata

    # ------------------------------------------------------------------
    # Trạng thái job
    # ------------------------------------------------------------------

    def _prepare_state(self):
        if self.state_store is None:
            return

        if self.resume:
            self.state_store.load()
        else:
            self.state_store.reset()

        self.state_store.start_job(job_id=self._job_id())
        self.state_store.save()

    def _job_id(self):
        log_path = getattr(self.log_service, "log_path", None)

        if log_path:
            return Path(log_path).stem

        return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")

    def _remember_selection(self, articles):
        if self.state_store is None:
            return

        total = len(articles)

        for index, article in enumerate(articles, start=1):
            self.state_store.upsert_article(article, index=index, total=total)

        self.state_store.set_selected_articles(
            [article["id"] for article in articles]
        )
        self.state_store.save()

    def _record_success(self, article_id):
        if self.state_store is None:
            return

        self.state_store.mark_article_status(article_id, COMPLETED)
        self.state_store.save()

    def _record_failure(self, article_id, stage, error):
        if self.state_store is None:
            return

        self.state_store.mark_stage_failed(article_id, stage, error)
        self.state_store.mark_article_status(
            article_id, FAILED, failed_stage=stage, error=error
        )
        self.state_store.save()

    # ------------------------------------------------------------------
    # Thu thập / chọn bài
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Báo cáo
    # ------------------------------------------------------------------

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


def _has_tts_text(stage):
    output = stage.get("output")
    return bool(isinstance(output, dict) and output.get("tts_text"))


def _has_scenes(stage):
    output = stage.get("output")
    return bool(isinstance(output, dict) and output.get("scenes"))


def _has_clips(stage):
    output = stage.get("output")

    return bool(
        isinstance(output, list)
        and output
        and all(isinstance(item, dict) and item.get("ok") for item in output)
    )


def _has_video(stage):
    output = stage.get("output")
    return bool(isinstance(output, dict) and output.get("video_path"))


def _has_publish_result(stage):
    """Chỉ coi là xong khi có bản kê; 'published' thì BẮT BUỘC có post_id.

    Khi đăng nhiều nền tảng, mọi nền tảng trong ``results`` đều phải có
    ``post_id``; trạng thái ``partial`` (một nền tảng lỗi) không được coi là
    xong để lần chạy sau đăng tiếp nền tảng còn thiếu.
    """
    output = stage.get("output")

    if not isinstance(output, dict):
        return False

    status = output.get("status")

    if status == "prepared":
        return True

    if status != "published":
        return False

    results = output.get("results")

    if isinstance(results, dict) and results:
        return all(
            isinstance(result, dict)
            and result.get("status") == "published"
            and result.get("post_id")
            for result in results.values()
        )

    return bool(output.get("post_id"))


def _is_positive_number(stage):
    value = stage.get("output")
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and value > 0
    )
