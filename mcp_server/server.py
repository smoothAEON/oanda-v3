"""FastMCP stdio server factory for Market Signal MCP."""

from __future__ import annotations

import json
from typing import Any

from mcp.server.fastmcp import FastMCP

from agent.runtime import AgentRuntime
from config.settings import Settings
from mcp_server.adapters import BotMcpService

TOOL_SPECS: list[dict[str, str]] = [
    {"name": "get_runtime_status", "description": "Return local runtime, storage, market, macro, and last-scan status."},
    {"name": "get_market_status", "description": "Return market-hours, macro, and calendar status."},
    {"name": "get_macro_context", "description": "Return the bounded macro context snapshot, optionally forcing a refresh."},
    {"name": "search_yfinance_tickers", "description": "Search Yahoo Finance symbols and related query news."},
    {"name": "get_yfinance_ticker", "description": "Return quote, profile, calendar, options, and optional news for one Yahoo Finance symbol."},
    {"name": "get_yfinance_history", "description": "Return bounded historical OHLCV rows for one Yahoo Finance symbol."},
    {"name": "get_yfinance_news", "description": "Return recent Yahoo Finance news items for one symbol."},
    {"name": "get_calendar", "description": "Return HIGH and MEDIUM impact calendar events for today or week."},
    {"name": "scan_all", "description": "Run the full instrument scan cycle and return scan status."},
    {"name": "scan_instrument", "description": "Refresh one instrument through the full publish path."},
    {"name": "refresh_snapshot", "description": "Refresh one published timeframe snapshot."},
    {"name": "get_price", "description": "Return current bid/ask pricing from OANDA REST."},
    {"name": "get_candles", "description": "Fetch direct, on-demand, no-cache closed OANDA mid OHLC candles for any live-account instrument using S5 through W granularity."},
    {"name": "get_ohlc", "description": "Fetch direct, on-demand, no-cache mid or bid/ask OANDA OHLC bars for any live-account instrument using S5 through W granularity."},
    {"name": "get_account_summary", "description": "Return the current OANDA account summary."},
    {"name": "list_transfers", "description": "Return raw TRANSFER_FUNDS history for a bounded date window."},
    {"name": "list_open_positions", "description": "Return open trades with current pip-distance annotations."},
    {"name": "list_open_orders", "description": "Return open orders with current pip-distance annotations."},
    {"name": "get_session_context", "description": "Return published Sydney/Tokyo/London/New York session context."},
    {"name": "get_day_range", "description": "Return previous-day range and sweep status from H1 context."},
    {"name": "get_previous_day_levels", "description": "Return previous-day high/low and sweep flags."},
    {"name": "get_smc_snapshot", "description": "Return the full sanitized published timeframe evidence snapshot."},
    {"name": "get_structure", "description": "Return recent BOS/CHOCH structure state."},
    {"name": "get_indicators", "description": "Return compact or full published indicator metrics."},
    {"name": "get_vwap", "description": "Return anchor-based VWAP with optional bands using OANDA tick-count proxy volume."},
    {"name": "get_order_blocks", "description": "Return published order blocks filtered by mitigation status."},
    {"name": "list_journal_trades", "description": "Return filtered journal trades from persisted state."},
    {"name": "get_journal_trade", "description": "Return one journal trade with MAE/MFE data and samples."},
    {"name": "get_mae_mfe", "description": "Return MAE/MFE summaries for open trades or one specific trade."},
    {"name": "get_trade_history", "description": "Return transaction-backed trade history and realized PnL pages."},
    {"name": "get_trade_stats", "description": "Return aggregated realized trade statistics and per-instrument attribution."},
    {"name": "get_spread_snapshot", "description": "Return current REST spread plus optional recent spread history."},
    {"name": "get_correlation", "description": "Return daily close-return correlation for two instruments or symbols."},
    {"name": "render_chart", "description": "Render a local PNG chart artifact and return its path and overlay metadata."},
    {"name": "export_candles", "description": "Export bid/ask candle CSV files for one instrument or the scan universe."},
]


def build_mcp_server(
    *,
    runtime: AgentRuntime,
    settings: Settings | None = None,
) -> FastMCP:
    """Build the FastMCP server backed by the local agent runtime."""

    resolved_settings = settings or runtime.settings
    service = BotMcpService(runtime=runtime, settings=resolved_settings)
    server = FastMCP(
        name="Market Signal MCP",
        instructions=(
            "Use these tools to inspect OANDA raw market data, sanitized evidence, "
            "account state, trade journal/history, rendered charts, and CSV exports. Responses are structured JSON."
        ),
        log_level=resolved_settings.log_level,
    )

    for spec in TOOL_SPECS:
        server.tool(
            name=spec["name"],
            description=spec["description"],
            structured_output=True,
        )(getattr(service, spec["name"]))

    @server.resource("marketsignal://capabilities", name="Capabilities", mime_type="application/json")
    def capabilities_resource() -> str:
        return _resource_json(BotMcpService.capabilities_payload())

    @server.resource("marketsignal://supported-instruments", name="Supported Instruments", mime_type="application/json")
    def supported_instruments_resource() -> str:
        return _resource_json(BotMcpService.supported_instruments_payload())

    @server.resource("marketsignal://tool-surface", name="Tool Surface", mime_type="application/json")
    def tool_surface_resource() -> str:
        return _resource_json(BotMcpService.tool_surface_payload(TOOL_SPECS))

    return server


def _resource_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, indent=2, sort_keys=True)


__all__ = ["TOOL_SPECS", "build_mcp_server"]
