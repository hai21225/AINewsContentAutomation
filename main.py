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
from Services.ResearchService import ResearchService
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


def build_components(loader, log_service, args):
    """Tạo toàn bộ thành phần pipeline từ cấu hình."""
    models = loader.get_models()
    providers = loader.get_providers()
    pipeline_config = loader.get_pipeline()
    workflow_config = pipeline_config.get("workflow", {}) or {}
    retry_config = pipeline_config.get("retry", {}) or {}

    writer_config = loader.get_agent("writer")
    checker_config = loader.get_agent("checker")
    selector_config = loader.get_agent("selector")
    image_planner_config = loader.get_agent("image_planner")
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

    workflow = Workflow(
        research_service,
        selector_agent,
        writing_supervisor,
        tts_service=tts_service,
        image_planner_agent=image_planner_agent,
        image_service=image_service,
        log_service=log_service,
        article_limit=args.limit,
        fail_fast=args.fail_fast or bool(workflow_config.get("fail_fast", False)),
    )

    return {
        "workflow": workflow,
        "rss_config": loader.get_rss(),
        "models": models,
        "providers": providers,
    }


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

    log_service.close()

    if summary["total"] == 0:
        return EXIT_WORKFLOW_FAILED

    if summary["failed"] > 0:
        return EXIT_ARTICLE_FAILED

    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
