"""Smoke tests for the current local MCP package layout."""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

MODULES = (
    "agent",
    "agent.parsing",
    "agent.pricing",
    "agent.runtime",
    "agent.runtime_views",
    "background",
    "background.poller_task",
    "charting",
    "charting.renderer",
    "config",
    "config.settings",
    "core",
    "core.analysis_config",
    "core.candle_policy",
    "core.enums",
    "core.events",
    "core.instrument_registry",
    "core.logging_setup",
    "core.market_state",
    "core.models",
    "data",
    "data.correlation_service",
    "data.csv_persistence",
    "data.forex_calendar",
    "data.macro",
    "data.market_hours",
    "data.persistence",
    "data.persistence.trade_store",
    "data.yfinance_service",
    "filters",
    "indicators",
    "indicators.pandasta_wrappers",
    "indicators.talib_wrappers",
    "indicators.tick_volume",
    "indicators.vwap",
    "journal",
    "journal.close_reasons",
    "journal.excursion_repository",
    "journal.journal_service",
    "journal.mae_mfe_service",
    "journal.trade_history_service",
    "journal.trade_normalizer",
    "journal.trade_repository",
    "journal.trade_stats_service",
    "mcp_server",
    "mcp_server.adapters",
    "mcp_server.main",
    "mcp_server.server",
    "orchestration",
    "orchestration.cache_warmer",
    "orchestration.scan_orchestrator",
    "providers",
    "providers.account_client",
    "providers.base",
    "providers.cache",
    "providers.oanda",
    "providers.oanda_execution",
    "providers.oanda_history",
    "providers.stream_client",
    "smc",
    "smc.provider",
    "tests",
    "tests.integration",
    "tests.unit",
    "tracking",
    "tracking.excursion_tracker",
)

REMOVED_MODULES = (
    "alerts.alert_repository",
    "background.stream_task",
    "background.task_supervisor",
    "bot.bot",
    "notifications.message_builder",
    "orchestration.scheduler",
    "mcp_server.auth",
    "smc." + "htf_bias",
    "smc." + "sfp",
    "smc." + "turtle_soup",
    "smc." + "orb",
    "filters." + "spread",
    "filters." + "chop",
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
def test_modules_import(module_name: str) -> None:
    importlib.import_module(module_name)


@pytest.mark.parametrize("module_name", REMOVED_MODULES)
def test_removed_modules_are_absent(module_name: str) -> None:
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(module_name)


@pytest.mark.parametrize("relative_path", DISALLOWED_TOP_LEVEL_NAMESPACES)
def test_non_canonical_top_level_namespaces_are_absent(relative_path: str) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    assert not (repo_root / relative_path).exists()
