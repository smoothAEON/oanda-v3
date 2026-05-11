from __future__ import annotations

from pathlib import Path

from agent.runtime import AgentRuntime, build_runtime
from config.settings import Settings, load_settings


def write_env_file(path: Path, **overrides: str) -> Path:
    values = {
        "OANDA_API_KEY": "api-key",
        "OANDA_ACCOUNT_ID": "account-id",
        "OANDA_ENVIRONMENT": "practice",
        "TINYDB_PATH": str(path.parent / "bot.json"),
    }
    values.update(overrides)
    path.write_text("\n".join(f"{key}={value}" for key, value in values.items()), encoding="utf-8")
    return path


def build_settings(tmp_path: Path) -> Settings:
    return load_settings(env_file=write_env_file(tmp_path / ".env"))


def test_agent_runtime_assembles_local_on_demand_graph_without_removed_services(tmp_path: Path) -> None:
    settings = build_settings(tmp_path)
    runtime = build_runtime(settings=settings)

    try:
        assert isinstance(runtime, AgentRuntime)
        assert runtime.settings is settings
        assert runtime.trade_poller is not None
        assert runtime.scan_orchestrator is not None
        assert runtime.chart_renderer is not None

        for removed_attr in (
            "scheduler",
            "scheduler_service",
            "stream_task",
            "task_supervisor",
            "telegram_notifier",
            "notifier",
            "price_alert_engine",
            "indicator_alert_engine",
            "time_alert_engine",
            "alert_repository",
        ):
            assert not hasattr(runtime, removed_attr)

        assert not hasattr(runtime.scan_orchestrator, "indicator_alert_engine")
        assert not hasattr(runtime.trade_poller, "notifier")
        assert not hasattr(runtime.trade_poller, "message_builder")
    finally:
        runtime.close()
