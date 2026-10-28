import argparse
import sys

from dotenv import load_dotenv

from Agents.CheckerAgent import CheckerAgent
from Agents.ImagePlannerAgent import ImagePlannerAgent
from Agents.SelectorAgent import SelectorAgent
from Agents.Workflow import Workflow
from Agents.WriterAgent import WriterAgent
from Agents.WriterSupervisor import WriterSupervisor
from Config.ConfigLoader import ConfigLoader
from Console import Console
from Errors import AppError
from ImageGeneration.ImageClientFactory import ImageClientFactory
from LLM.LlmClientFactory import LlmClientFactory
from Services.ImageService import ImageService
from Services.LogService import LogService
from Services.MediaService import MediaService
from Services.PublishService import PublishService
from Services.RenderService import RenderService
from Services.ResearchService import ResearchService
from Services.StateStore import StateStore
from Services.TtsService import TtsService

EXIT_OK = 0
EXIT_ARTICLE_FAILED = 1
EXIT_WORKFLOW_FAILED = 2


def configure_console_encoding():
    """Buộc stdout/stderr dùng UTF-8.

    Trên Windows, console mặc định dùng code page cp1252 nên tiếng Việt trong
    báo cáo/log sẽ gây ``UnicodeEncodeError``.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)

        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description=(
            "Tạo video tin tức ngắn từ RSS: chọn bài, viết kịch bản, "
            "kiểm tra, TTS, lập kế hoạch ảnh."
        )
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Chỉ nạp cấu hình, kiểm tra client và in kế hoạch; không gọi mạng.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Chỉ xử lý tối đa N bài sau khi chọn (dùng để thử nhanh).",
    )
    parser.add_argument(
        "--no-images",
        action="store_true",
        help="Bỏ qua bước sinh ảnh.",
    )
    parser.add_argument(
        "--no-render",
        action="store_true",
        help="Bỏ qua bước dựng clip whiteboard từ ảnh.",
    )
    parser.add_argument(
        "--no-mux",
        action="store_true",
        help="Bỏ qua bước ghép clip + audio thành video cuối.",
    )
    parser.add_argument(
        "--no-publish",
        action="store_true",
        help="Bỏ qua bước chuẩn bị/đăng video lên mạng xã hội.",
    )
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="Bỏ qua trạng thái đã lưu và chạy lại từ đầu (không resume).",
    )
    parser.add_argument(
        "--fail-fast",
        action="store_true",
        help="Dừng toàn bộ pipeline ngay khi một bài thất bại.",
    )
    parser.add_argument(
        "--log-level",
        default=None,
        help="Ghi đè mức log (DEBUG, INFO, WARNING, ERROR).",
    )

    args = parser.parse_args(argv)

    if args.limit is not None and args.limit < 1:
        parser.error("--limit phải lớn hơn hoặc bằng 1.")

    return args


def create_log_service(loader, args):
    config = loader.get_logging()

    if args.log_level:
        config = {**config, "level": args.log_level}

    return LogService(config)


def create_llm_client(agent_config, providers, models, log_service,
                      retry_config=None):
    """Tạo LLM client cho một agent từ tên model trong cấu hình agent."""
    return LlmClientFactory.create_for_model(
        agent_config["model"], providers, models, log_service=log_service,
        retry_config=retry_config,
    )


def _with_canvas(agent_config, render_config):
    """Gắn khung hình render vào cấu hình agent để prompt bố cục ảnh khớp."""
    try:
        canvas = f"{int(render_config['width'])}x{int(render_config['height'])}"
    except (KeyError, TypeError, ValueError):
        return agent_config

    return {**agent_config, "canvas": canvas}


def build_components(loader, log_service, args):
    """Tạo toàn bộ thành phần pipeline từ cấu hình."""
    models = loader.get_models()
    providers = loader.get_providers()
    pipeline_config = loader.get_pipeline()
    workflow_config = pipeline_config.get("workflow", {}) or {}
    retry_config = pipeline_config.get("retry", {}) or {}
    state_config = pipeline_config.get("state", {}) or {}

    writer_config = loader.get_agent("writer")
    checker_config = loader.get_agent("checker")
    selector_config = loader.get_agent("selector")
    render_config = loader.get_render()
    image_planner_config = _with_canvas(
        loader.get_agent("image_planner"), render_config
    )
    tts_config = loader.get_tts()

    writer_agent = WriterAgent(
        writer_config,
        create_llm_client(
            writer_config, providers, models, log_service, retry_config
        ),
        log_service=log_service,
    )
    checker_agent = CheckerAgent(
        checker_config,
        create_llm_client(
            checker_config, providers, models, log_service, retry_config
        ),
        log_service=log_service,
    )
    selector_agent = SelectorAgent(
        selector_config,
        create_llm_client(
            selector_config, providers, models, log_service, retry_config
        ),
        log_service=log_service,
    )
    image_planner_agent = ImagePlannerAgent(
        image_planner_config,
        create_llm_client(
            image_planner_config, providers, models, log_service, retry_config
        ),
        log_service=log_service,
    )

    writing_supervisor = WriterSupervisor(
        writer_agent,
        checker_agent,
        max_retries=int(workflow_config.get("supervisor_max_retries", 3)),
        log_service=log_service,
    )

    tts_service = TtsService(tts_config, log_service=log_service)

    research_service = ResearchService(
        pipeline_config.get("research", {}),
        log_service=log_service,
        retry_config=retry_config,
    )

    image_service = None

    if not args.no_images:
        image_config = loader.get_image_service()
        image_client = ImageClientFactory.create_for_model(
            image_config["model"],
            loader.get_image_providers(),
            loader.get_image_models(),
            log_service=log_service,
            retry_config=retry_config,
        )
        image_service = ImageService(
            image_client, image_config, log_service=log_service
        )

    render_service = None

    if not args.no_render:
        render_service = RenderService(
            render_config, log_service=log_service
        )

    media_service = None

    if not args.no_mux and render_service is not None:
        media_service = MediaService(
            loader.get_media(), log_service=log_service
        )

    publish_service = None

    if not args.no_publish:
        publish_config = loader.get_publish()

        if publish_config.get("mode") != "off":
            publish_service = PublishService(
                publish_config,
                log_service=log_service,
                retry_config=retry_config,
            )

    workflow = Workflow(
        research_service,
        selector_agent,
        writing_supervisor,
        tts_service=tts_service,
        image_planner_agent=image_planner_agent,
        image_service=image_service,
        render_service=render_service,
        media_service=media_service,
        publish_service=publish_service,
        log_service=log_service,
        article_limit=args.limit,
        fail_fast=args.fail_fast or bool(workflow_config.get("fail_fast", False)),
        state_store=create_state_store(state_config, log_service),
        resume=not args.fresh and bool(state_config.get("resume", True)),
    )

    return {
        "workflow": workflow,
        "rss_config": loader.get_rss(),
        "models": models,
        "providers": providers,
        "state_config": state_config,
        "render_service": render_service,
        "media_service": media_service,
        "publish_service": publish_service,
    }


def create_state_store(state_config, log_service):
    """Tạo StateStore từ khối ``state`` trong pipeline.json (None nếu tắt)."""
    if not state_config.get("enabled", True):
        return None

    return StateStore(state_config.get("path") or None, log_service=log_service)


def describe_plan(components, loader, args):
    """In kế hoạch chạy ở chế độ dry-run."""
    Console.print("=== KẾ HOẠCH CHẠY (dry-run) ===")
    Console.print(f"Thư mục cấu hình : {loader.config_dir}")
    Console.print(f"Log              : {loader.get_logging().get('level')}")
    Console.print(f"Feed RSS         : {len(components['rss_config'])}")
    Console.print(f"Model có sẵn     : {', '.join(sorted(components['models']))}")
    Console.print(f"Provider có sẵn  : {', '.join(sorted(components['providers']))}")

    for agent_name in loader.list_agents():
        agent_config = loader.get_agent(agent_name)
        Console.print(
            f"Agent {agent_name:<14}: model={agent_config.get('model')}"
        )

    Console.print(f"Giới hạn bài     : {args.limit if args.limit else 'không'}")
    Console.print(f"Sinh ảnh         : {'tắt' if args.no_images else 'bật'}")

    render_service = components.get("render_service")

    if render_service is None:
        Console.print("Render whiteboard: tắt")
    else:
        cli_state = "có" if render_service.is_available() else "THIẾU"
        Console.print(
            f"Render whiteboard: bật (engine CLI: {cli_state} - "
            f"{render_service.cli_path})"
        )

    media_service = components.get("media_service")

    if media_service is None:
        Console.print("Ghép video cuối  : tắt")
    else:
        tool_state = "có" if media_service.is_available() else "THIẾU"
        Console.print(
            f"Ghép video cuối  : bật (ffmpeg: {tool_state} - "
            f"{media_service.ffmpeg_path})"
        )

    publish_service = components.get("publish_service")

    if publish_service is None:
        Console.print("Đăng bài         : tắt")
    else:
        if publish_service.mode == "publish":
            credential_state = (
                "có" if publish_service.is_configured() else "THIẾU"
            )
        else:   
            credential_state = "không cần (prepare)"

        Console.print(
            f"Đăng bài         : {', '.join(publish_service.platforms)} "
            f"(chế độ: {publish_service.mode}, "
            f"credential: {credential_state})"
        )

    state_store = components["workflow"].state_store

    if state_store is None:
        Console.print("Trạng thái       : tắt")
    else:
        if args.fresh:
            mode = "chạy lại từ đầu (--fresh)"
        elif components.get("state_config", {}).get("resume", True):
            mode = "resume"
        else:
            mode = "không resume"

        Console.print(f"Trạng thái       : {state_store.path} ({mode})")


def print_report(summary):
    Console.print("=== KẾT QUẢ ===")
    Console.print(
        f"Tổng: {summary['total']} | Thành công: {summary['completed']} | "
        f"Thất bại: {summary['failed']}"
    )

    for result in summary["results"]:
        if result["status"] == "completed":
            Console.success(
                f"{result['index']}/{result['total']} "
                f"{result['title'][:60]!r}"
            )
            continue

        Console.error(
            f"{result['index']}/{result['total']} "
            f"{result['title'][:60]!r} "
            f"| bước lỗi: {result['failed_stage']} | {result['error']}"
        )


def main(argv=None):
    load_dotenv()
    configure_console_encoding()

    args = parse_args(argv)

    loader = ConfigLoader()
    log_service = create_log_service(loader, args)

    try:
        components = build_components(loader, log_service, args)
    except AppError as error:
        Console.error(f"Không khởi tạo được pipeline: {error}")
        log_service.error(
            "startup_failed", str(error), error_type=type(error).__name__
        )
        log_service.close()
        return EXIT_WORKFLOW_FAILED

    if args.dry_run:
        describe_plan(components, loader, args)
        Console.success("Cấu hình hợp lệ. Không gọi mạng ở chế độ dry-run.")
        log_service.close()
        return EXIT_OK

    try:
        summary = components["workflow"].run(components["rss_config"])
    except AppError as error:
        Console.error(f"Pipeline thất bại: {error}")
        log_service.error(
            "workflow_failed", str(error), error_type=type(error).__name__
        )
        log_service.close()
        return EXIT_WORKFLOW_FAILED

    print_report(summary)

    if log_service.log_path:
        Console.print(f"Log chi tiết: {log_service.log_path}")

    state_store = components["workflow"].state_store

    if state_store is not None:
        Console.print(f"Trạng thái job: {state_store.path}")

    log_service.close()

    if summary["total"] == 0:
        return EXIT_WORKFLOW_FAILED

    if summary["failed"] > 0:
        return EXIT_ARTICLE_FAILED

    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
