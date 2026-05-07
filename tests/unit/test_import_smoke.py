"""Stage 01 smoke tests for package layout and imports."""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

MODULES = (
    "config",
    "config.settings",
    "core",
    "core.candle_policy",
    "core.instrument_registry",
    "core.enums",
    "core.events",
    "core.models",
    "core.market_state",
    "core.logging_setup",
    "providers",
    "providers.base",
    "providers.oanda",
    "providers.account_client",
    "providers.stream_client",
    "providers.oanda_execution",
    "providers.cache",
    "smc",
    "smc.provider",
    "indicators",
    "indicators.talib_wrappers",
    "indicators.pandasta_wrappers",
    "indicators.tick_volume",
    "filters",
    "data",
    "data.forex_calendar",
    "data.macro",
    "data.market_hours",
    "data.yfinance_service",
    "data.csv_persistence",
    "data.persistence",
    "data.persistence.trade_store",
    "journal",
    "journal.trade_repository",
    "journal.excursion_repository",
    "journal.journal_service",
    "tracking",
    "tracking.excursion_tracker",
    "alerts",
    "alerts.alert_repository",
    "alerts.price_alert_engine",
    "alerts.indicator_alert_engine",
    "alerts.defaults",
    "notifications",
    "notifications.message_builder",
    "notifications.notifier",
    "background",
    "background.poller_task",
    "background.stream_task",
    "background.task_supervisor",
    "charting",
    "charting.renderer",
    "orchestration",
    "orchestration.scan_orchestrator",
    "orchestration.scheduler",
    "orchestration.cache_warmer",
    "bot",
    "bot.bot",
    "bot.formatting",
    "bot.main",
    "bot.parsing",
    "bot.runtime",
    "bot.runtime_config",
    "bot.security_manager",
    "bot.message_queue",
    "bot.__main__",
    "tests",
    "tests.unit",
    "tests.integration",
)

REMOVED_MODULES = (
    "smc." + "htf_bias",
    "smc." + "sfp",
    "smc." + "turtle_soup",
    "smc." + "orb",
    "filters." + "spread",
    "filters." + "chop",
    "bot." + "tradeplan",
)

DISALLOWED_TOP_LEVEL_NAMESPACES = (
    "src",
    "analytics",
    "backtesting",
    "registry",
    "cache",
    "models",
    "state",
    "detectors",
    "htf",
    "calendar",
    "persistence",
    "scheduler",
    "charts",
    "macro",
)


@pytest.mark.parametrize("module_name", MODULES)
def test_stage_01_modules_import(module_name: str) -> None:
    """Every reserved Stage 01 module must import without side effects."""
    importlib.import_module(module_name)


@pytest.mark.parametrize("module_name", REMOVED_MODULES)
def test_deleted_opinion_modules_are_absent(module_name: str) -> None:
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(module_name)


@pytest.mark.parametrize("relative_path", DISALLOWED_TOP_LEVEL_NAMESPACES)
def test_non_canonical_top_level_namespaces_are_absent(relative_path: str) -> None:
    """The Stage 01 layout should match the V3 plan exactly."""
    repo_root = Path(__file__).resolve().parents[2]
    assert not (repo_root / relative_path).exists()
