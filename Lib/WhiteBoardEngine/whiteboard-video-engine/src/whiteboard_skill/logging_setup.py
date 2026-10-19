"""Logging setup with a loguru-compatible fallback."""

from __future__ import annotations

import logging
import sys
from typing import Any


class _StdLogger:
    def __init__(self) -> None:
        self._logger = logging.getLogger("whiteboard_skill")

    def configure(self, level: str) -> None:
        logging.basicConfig(level=getattr(logging, level.upper(), logging.INFO), format="%(levelname)s: %(message)s")

    def info(self, message: str, *args: Any) -> None:
        self._logger.info(message.format(*args))

    def warning(self, message: str, *args: Any) -> None:
        self._logger.warning(message.format(*args))

    def error(self, message: str, *args: Any) -> None:
        self._logger.error(message.format(*args))


try:  # pragma: no cover - optional dependency branch
    from loguru import logger as _logger

    logger = _logger
except Exception:  # pragma: no cover - fallback branch
    logger = _StdLogger()


def setup_logging(level: str = "INFO") -> None:
    """Configure logging.

    Example:
        >>> setup_logging("INFO")
    """

    if hasattr(logger, "remove") and hasattr(logger, "add"):
        logger.remove()
        logger.add(sys.stderr, level=level)
    elif hasattr(logger, "configure"):
        logger.configure(level)
