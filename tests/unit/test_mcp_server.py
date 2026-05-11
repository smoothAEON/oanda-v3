from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from config.settings import Settings, load_settings
from data.persistence.trade_store import TradeStore
from mcp_server.adapters import BotMcpService
from mcp_server.server import TOOL_SPECS, build_mcp_server

BASE_TIME = datetime(2026, 4, 4, 12, 0, tzinfo=timezone.utc)


def write_env_file(path: Path, **overrides: str) -> Path:
    values = {
        "OANDA_API_KEY": "api-key",
        "OANDA_ACCOUNT_ID": "account-id",
        "OANDA_ENVIRONMENT": "practice",
        "TINYDB_PATH": str(path.parent / "bot.json"),
        "LOG_LEVEL": "warning",
    }
    values.update(overrides)
    path.write_text("\n".join(f"{key}={value}" for key, value in values.items()), encoding="utf-8")
    return path


def build_settings(tmp_path: Path, **overrides: str) -> Settings:
    env_file = write_env_file(tmp_path / ".env", **overrides)
    return load_settings(env_file=env_file)


def test_build_mcp_server_constructs_stdio_server(tmp_path: Path) -> None:
    settings = build_settings(tmp_path)
    runtime = SimpleNamespace(settings=settings)

    server = build_mcp_server(runtime=runtime, settings=settings)

    assert server is not None


def test_mcp_tool_surface_matches_local_stdio_contract() -> None:
    tool_names = {spec["name"] for spec in TOOL_SPECS}
    capabilities = BotMcpService.capabilities_payload()

    assert {"render_chart", "export_candles", "get_price", "get_trade_history"}.issubset(tool_names)
    assert {
        "create_price_alert",
        "list_price_alerts",
        "clear_all_price_alerts",
        "create_time_alert",
        "list_alert_history",
    }.isdisjoint(tool_names)
    assert capabilities["transport"] == "stdio"
    assert "chart_rendering" in capabilities["surfaces"]["reads"]
    assert "csv_exports" in capabilities["surfaces"]["writes"]
    assert "alert_history" not in capabilities["surfaces"]["reads"]


def test_mcp_get_price_uses_rest_pricing_and_records_spread_history(tmp_path: Path) -> None:
    settings = build_settings(tmp_path)
    store = TradeStore(db_path=settings.tinydb_path, settings=settings)

    class FakeAccountClient:
        def __init__(self) -> None:
            self.calls: list[str] = []

        async def get_pricing(self, instrument: str):
            self.calls.append(instrument)
            return SimpleNamespace(
                bid=3049.90,
                ask=3050.20,
                spread_pips=30.0,
                fetched_at=BASE_TIME,
            )

    runtime = SimpleNamespace(
        settings=settings,
        account_client=FakeAccountClient(),
        trade_store=store,
    )
    service = BotMcpService(runtime=runtime, settings=settings)

    try:
        result = asyncio.run(service.get_price("spx500usd", prefer_live=True))
        history = store.get_recent_spreads("SPX500_USD", limit=5)

        assert result["source"] == "rest_pricing"
        assert result["fallback_note"] == "live stream is not available in the local MCP runtime; REST pricing used"
        assert result["bid"] == 3049.90
        assert runtime.account_client.calls == ["SPX500_USD"]
        assert history[0]["reason"] == "mcp_get_price"
        assert history[0]["source"] == "rest_pricing"
    finally:
        store.close()


def test_mcp_get_spread_snapshot_rejects_required_live_stream(tmp_path: Path) -> None:
    settings = build_settings(tmp_path)
    runtime = SimpleNamespace(settings=settings)
    service = BotMcpService(runtime=runtime, settings=settings)

    with pytest.raises(ValueError, match="Live stream pricing is not available"):
        asyncio.run(service.get_spread_snapshot("spx500usd", require_live=True))


def test_mcp_render_chart_syncs_account_state_and_runs_renderer(tmp_path: Path) -> None:
    settings = build_settings(tmp_path)
    calls: list[object] = []

    class FakeChartRenderer:
        def render(self, request):
            calls.append(("render", request.instrument, request.timeframe, request.count))
            return SimpleNamespace(
                artifact=SimpleNamespace(path=tmp_path / "chart.png"),
                warning_text=None,
                omitted_layers=(),
                overlay_selection=request.selection,
            )

    async def fake_sync_account_state() -> None:
        calls.append("sync")

    async def fake_run_blocking(fn, *args, write: bool = False, **kwargs):
        calls.append(("run_blocking", write))
        return fn(*args, **kwargs)

    runtime = SimpleNamespace(
        settings=settings,
        chart_renderer=FakeChartRenderer(),
        sync_account_state=fake_sync_account_state,
        run_blocking=fake_run_blocking,
    )
    service = BotMcpService(runtime=runtime, settings=settings)

    result = asyncio.run(
        service.render_chart("spx500usd", timeframe="H1", count=100, overlays=["clean"])
    )

    assert result["instrument"] == "SPX500_USD"
    assert result["timeframe"] == "H1"
    assert result["count"] == 100
    assert result["exists"] is False
    assert calls == [
        "sync",
        ("run_blocking", True),
        ("render", "SPX500_USD", "H1", 100),
    ]


def test_mcp_export_candles_writes_bid_ask_csv(tmp_path: Path) -> None:
    settings = build_settings(tmp_path)
    frame = pd.DataFrame(
        {
            "time": pd.to_datetime(["2026-04-04T11:00:00Z", "2026-04-04T12:00:00Z"], utc=True),
            "bid_open": [3300.0, 3301.0],
            "bid_high": [3301.0, 3302.0],
            "bid_low": [3299.0, 3300.0],
            "bid_close": [3300.5, 3301.5],
            "ask_open": [3300.2, 3301.2],
            "ask_high": [3301.2, 3302.2],
            "ask_low": [3299.2, 3300.2],
            "ask_close": [3300.7, 3301.7],
            "tick_volume": [100, 101],
        }
    )

    class FakeAccountClient:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str, int]] = []

        async def get_bid_ask_candles(self, instrument: str, granularity: str, count: int) -> pd.DataFrame:
            self.calls.append((instrument, granularity, count))
            return frame

    runtime = SimpleNamespace(settings=settings, account_client=FakeAccountClient())
    service = BotMcpService(runtime=runtime, settings=settings)

    result = asyncio.run(service.export_candles("spx500usd", timeframes=["H1"], count=2))

    exported = Path(result["files"][0]["path"])
    assert result["file_count"] == 1
    assert result["files"][0]["row_count"] == 2
    assert exported.exists()
    assert runtime.account_client.calls == [("SPX500_USD", "H1", 2)]
