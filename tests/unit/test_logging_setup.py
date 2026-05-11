"""Unit tests for logging_setup module."""

from __future__ import annotations

import logging
from pathlib import Path

import structlog
import structlog.testing

from config.settings import load_settings
from core.logging_setup import configure_logging, get_log_paths, get_logger, log_failure


def write_env_file(path: Path, **overrides: str) -> Path:
    values = {
        "OANDA_API_KEY": "k",
        "OANDA_ACCOUNT_ID": "a",
        "OANDA_ENVIRONMENT": "practice",
        "TINYDB_PATH": str(path.parent / "bot.json"),
    }
    values.update(overrides)
    path.write_text("\n".join(f"{k}={v}" for k, v in values.items()), encoding="utf-8")
    return path


class TestConfigureLogging:
    def test_sets_root_log_level_from_settings(self, tmp_path: Path) -> None:
        settings = load_settings(env_file=write_env_file(tmp_path / ".env", LOG_LEVEL="DEBUG"))
        configure_logging(settings)
        assert logging.getLogger().level == logging.DEBUG

    def test_warning_level(self, tmp_path: Path) -> None:
        settings = load_settings(env_file=write_env_file(tmp_path / ".env", LOG_LEVEL="WARNING"))
        configure_logging(settings)
        assert logging.getLogger().level == logging.WARNING

    def test_json_renderer_when_log_json_true(self, tmp_path: Path) -> None:
        settings = load_settings(
            env_file=write_env_file(tmp_path / ".env", LOG_JSON="true", LOG_LEVEL="INFO")
        )
        configure_logging(settings)
        # Verify structlog is configured (no exception)
        logger = get_logger("test")
        assert logger is not None

    def test_console_renderer_when_log_json_false(self, tmp_path: Path) -> None:
        settings = load_settings(
            env_file=write_env_file(tmp_path / ".env", LOG_JSON="false", LOG_LEVEL="INFO")
        )
        configure_logging(settings)
        logger = get_logger("test")
        assert logger is not None


class TestGetLogger:
    def test_returns_structlog_logger(self, tmp_path: Path) -> None:
        settings = load_settings(env_file=write_env_file(tmp_path / ".env"))
        configure_logging(settings)
        logger = get_logger("mymodule")
        assert logger is not None

    def test_logger_without_name(self, tmp_path: Path) -> None:
        settings = load_settings(env_file=write_env_file(tmp_path / ".env"))
        configure_logging(settings)
        logger = get_logger()
        assert logger is not None

    def test_captured_log_contains_event(self, tmp_path: Path) -> None:
        settings = load_settings(
            env_file=write_env_file(tmp_path / ".env", LOG_LEVEL="DEBUG")
        )
        configure_logging(settings)

        with structlog.testing.capture_logs() as logs:
            logger = get_logger("test")
            logger.info("test_event", key="value")

        assert any(entry["event"] == "test_event" for entry in logs)
        matching = [e for e in logs if e["event"] == "test_event"]
        assert matching[0]["key"] == "value"

    def test_log_failure_captures_exception_fields(self, tmp_path: Path) -> None:
        settings = load_settings(env_file=write_env_file(tmp_path / ".env", LOG_LEVEL="DEBUG"))
        configure_logging(settings)

        with structlog.testing.capture_logs() as logs:
            try:
                raise RuntimeError("boom")
            except RuntimeError as exc:
                log_failure(get_logger("test"), "failure_event", exc, level="warning", scope="unit")

        matching = [entry for entry in logs if entry["event"] == "failure_event"]
        assert matching[0]["error"] == "boom"
        assert matching[0]["exception_type"] == "RuntimeError"
        assert matching[0]["scope"] == "unit"


class TestFileLogging:
    def test_configure_logging_writes_application_and_error_files(self, tmp_path: Path) -> None:
        settings = load_settings(env_file=write_env_file(tmp_path / ".env", LOG_LEVEL="INFO"))
        configure_logging(settings)
        logger = get_logger("test.file")

        logger.info("info_event", key="value")
        try:
            raise ValueError("bad news")
        except ValueError as exc:
            log_failure(logger, "error_event", exc)

        for handler in logging.getLogger().handlers:
            handler.flush()

        paths = get_log_paths(settings)
        application_log = paths["application"].read_text(encoding="utf-8")
        error_log = paths["error"].read_text(encoding="utf-8")

        assert "info_event" in application_log
        assert "error_event" in application_log
        assert "error_event" in error_log
        assert "ValueError" in error_log
