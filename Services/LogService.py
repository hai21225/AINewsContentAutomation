"""Log có cấu trúc cho toàn pipeline.

Mục tiêu (theo yêu cầu của skill):
- Mỗi dòng log mang đủ ngữ cảnh: ``stage``, ``article_id``, ``attempt``,
  ``provider``, ``model``, ``elapsed_ms``, ``outcome``.
- Ghi ra console (dễ đọc) và ra file JSONL (dễ truy vết/parse).
- Không bao giờ ghi API key/token ra log.

Thiết kế: bọc ``logging`` chuẩn của Python (thread-safe) với formatter riêng.
Mọi field tuỳ biến được truyền qua ``extra={"fields": {...}}``.
"""

import json
import logging
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from Errors import ConfigurationError
from Redaction import redact, redact_fields

DEFAULT_LEVEL = "INFO"
DEFAULT_LOG_DIR = "logs"


class _JsonFormatter(logging.Formatter):
    """Ghi mỗi bản ghi thành một dòng JSON."""

    def format(self, record):
        payload = {
            "timestamp": datetime.fromtimestamp(
                record.created, tz=timezone.utc
            ).isoformat(),
            "level": record.levelname,
            "event": getattr(record, "event", record.getMessage()),
            "message": redact(record.getMessage()),
            "logger": record.name,
        }
        payload.update(getattr(record, "fields", {}))
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


class _ConsoleFormatter(logging.Formatter):
    """Ghi log dễ đọc cho người vận hành."""

    _ICONS = {
        "DEBUG": "·",
        "INFO": "→",
        "WARNING": "!",
        "ERROR": "✗",
        "CRITICAL": "✗",
    }

    def format(self, record):
        event = getattr(record, "event", "")
        icon = self._ICONS.get(record.levelname, "→")
        parts = [f"{icon} [{record.levelname}]", f"{event}"]
        message = redact(record.getMessage())
        if message:
            parts.append(message)

        fields = getattr(record, "fields", {})
        details = " ".join(
            f"{key}={value}"
            for key, value in fields.items()
            if value is not None and value != ""
        )
        line = " ".join(part for part in parts if part)
        if details:
            line = f"{line} | {details}"
        return line


class LogService:
    """Logger có cấu trúc, dùng chung cho pipeline và cho agent/service."""

    def __init__(self, config=None, name="ai_news", console=None):
        config = config or {}

        self._level_name = str(config.get("level", DEFAULT_LEVEL)).upper()
        self._level = logging.getLevelName(self._level_name)

        if not isinstance(self._level, int):
            raise ConfigurationError(
                f"Log level không hợp lệ: {self._level_name!r}"
            )

        self._log_dir = Path(config.get("log_dir", DEFAULT_LOG_DIR))
        self._write_file = bool(config.get("log_file", True))
        self._write_console = (
            bool(config.get("console", True)) if console is None else bool(console)
        )

        self._logger = logging.getLogger(name)
        self._logger.setLevel(self._level)
        self._logger.propagate = False

        # Tránh nhân đôi handler khi tạo nhiều LogService cùng tên.
        for handler in list(self._logger.handlers):
            self._logger.removeHandler(handler)
            handler.close()

        self._log_path = None

        if self._write_console:
            stream_handler = logging.StreamHandler()
            stream_handler.setFormatter(_ConsoleFormatter())
            stream_handler.setLevel(self._level)
            self._logger.addHandler(stream_handler)

        if self._write_file:
            self._log_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            self._log_path = self._log_dir / f"run-{stamp}.jsonl"
            file_handler = logging.FileHandler(
                self._log_path, encoding="utf-8"
            )
            file_handler.setFormatter(_JsonFormatter())
            file_handler.setLevel(self._level)
            self._logger.addHandler(file_handler)

    @property
    def log_path(self):
        """Đường dẫn file log JSONL (None nếu không ghi file)."""
        return self._log_path

    def _emit(self, level, event, message="", **fields):
        if level < self._level:
            return
        self._logger.log(
            level,
            redact(message),
            extra={"event": event, "fields": redact_fields(fields)},
        )

    def debug(self, event, message="", **fields):
        self._emit(logging.DEBUG, event, message, **fields)

    def info(self, event, message="", **fields):
        self._emit(logging.INFO, event, message, **fields)

    def warning(self, event, message="", **fields):
        self._emit(logging.WARNING, event, message, **fields)

    def error(self, event, message="", **fields):
        self._emit(logging.ERROR, event, message, **fields)

    @contextmanager
    def stage(self, stage, article_id=None, attempt=None, provider=None,
              model=None, **fields):
        """Đo thời gian và log bắt đầu/kết thúc/thất bại của một bước.

        Không nuốt exception: lỗi vẫn được ném lại cho caller xử lý.
        """
        context = {
            "stage": stage,
            "article_id": article_id,
            "attempt": attempt,
            "provider": provider,
            "model": model,
        }
        context.update(fields)
        context = {key: value for key, value in context.items() if value is not None}

        started = time.perf_counter()
        self.info("stage_started", **context)
        try:
            yield context
        except Exception as error:
            self.error(
                "stage_failed",
                error_type=type(error).__name__,
                error=str(error),
                elapsed_ms=self._elapsed_ms(started),
                **context,
            )
            raise
        else:
            self.info(
                "stage_completed",
                elapsed_ms=self._elapsed_ms(started),
                **context,
            )

    def close(self):
        """Đóng toàn bộ handler (giải phóng file log)."""
        for handler in list(self._logger.handlers):
            self._logger.removeHandler(handler)
            handler.close()

    @staticmethod
    def _elapsed_ms(started):
        return round((time.perf_counter() - started) * 1000, 1)


class NullLogService(LogService):
    """Log im lặng, dùng trong test hoặc khi tắt logging."""

    def __init__(self):
        super().__init__(
            {"level": "CRITICAL", "log_file": False, "console": False},
            name="ai_news_null",
        )
