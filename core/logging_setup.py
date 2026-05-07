"""Structured logging bootstrap for analysis-stage modules."""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

import structlog

from config.settings import Settings, get_settings

LOG_FILE_NAME = "bot.log"
ERROR_LOG_FILE_NAME = "bot.error.log"
LOG_FILE_MAX_BYTES = 5 * 1024 * 1024
LOG_FILE_BACKUP_COUNT = 5


def get_log_paths(settings: Settings | None = None) -> dict[str, Path]:
    """Return the canonical runtime log file locations."""

    resolved_settings = settings or get_settings()
    log_dir = resolved_settings.tinydb_path.resolve().parent / "logs"
    return {
        "directory": log_dir,
        "application": log_dir / LOG_FILE_NAME,
        "error": log_dir / ERROR_LOG_FILE_NAME,
    }


def configure_logging(settings: Settings | None = None) -> None:
    """Configure stdlib logging and structlog for the current process."""

    resolved_settings = settings or get_settings()
    log_level = getattr(logging, resolved_settings.log_level.upper(), logging.INFO)
    log_paths = get_log_paths(resolved_settings)
    log_paths["directory"].mkdir(parents=True, exist_ok=True)

    renderer: Any
    if resolved_settings.log_json:
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=False)

    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
    ]
    if resolved_settings.log_json:
        shared_processors.append(structlog.processors.format_exc_info)

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=False,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.setLevel(log_level)

    handlers: list[logging.Handler] = [
        logging.StreamHandler(sys.stdout),
        RotatingFileHandler(
            log_paths["application"],
            maxBytes=LOG_FILE_MAX_BYTES,
            backupCount=LOG_FILE_BACKUP_COUNT,
            encoding="utf-8",
        ),
        RotatingFileHandler(
            log_paths["error"],
            maxBytes=LOG_FILE_MAX_BYTES,
            backupCount=LOG_FILE_BACKUP_COUNT,
            encoding="utf-8",
        ),
    ]
    handlers[0].setLevel(log_level)
    handlers[1].setLevel(log_level)
    handlers[2].setLevel(logging.ERROR)

    for handler in handlers:
        handler.setFormatter(formatter)
        root_logger.addHandler(handler)


def get_logger(name: str | None = None) -> Any:
    """Return a structlog logger for a module or subsystem."""

    return structlog.get_logger(name)


def log_failure(
    logger: Any,
    event: str,
    exc: BaseException,
    *,
    level: str = "error",
    **fields: object,
) -> None:
    """Emit a structured failure log with traceback details."""

    log_method = getattr(logger, level)
    log_method(
        event,
        error=str(exc) or repr(exc),
        exception_type=type(exc).__name__,
        exc_info=exc,
        **fields,
    )


__all__ = ["configure_logging", "get_log_paths", "get_logger", "log_failure"]
