"""FastMCP server factory for Market Signal Bot V3."""

from __future__ import annotations

import json
from typing import Any

from mcp.server.fastmcp import FastMCP
from starlette.responses import JSONResponse

from bot.runtime import BotRuntime
from config.settings import Settings, get_settings
from mcp_server.adapters import BotMcpService
from mcp_server.auth import QueryParamAPIKeyMiddleware

TOOL_SPECS: list[dict[str, str]] = [
    {"name": "get_runtime_status", "description": "Return scheduler, stream, task, and last-scan health."},
    {"name": "get_market_status", "description": "Return market-hours, stream, macro, and calendar status."},
    {"name": "get_macro_context", "description": "Return the bounded macro context snapshot, optionally forcing a refresh."},
    {"name": "search_yfinance_tickers", "description": "Search Yahoo Finance symbols and related query news."},
    {"name": "get_yfinance_ticker", "description": "Return quote, profile, calendar, options, and optional news for one Yahoo Finance symbol."},
    {"name": "get_yfinance_history", "description": "Return bounded historical OHLCV rows for one Yahoo Finance symbol."},
    {"name": "get_yfinance_news", "description": "Return recent Yahoo Finance news items for one symbol."},
    {"name": "get_calendar", "description": "Return HIGH and MEDIUM impact calendar events for today or week."},
    {"name": "scan_all", "description": "Run the full instrument scan cycle and return scan status."},
    {"name": "scan_instrument", "description": "Refresh one instrument through the full publish path."},
    {"name": "refresh_snapshot", "description": "Refresh one published timeframe snapshot."},
    {"name": "get_price", "description": "Return current bid/ask pricing, optionally preferring live stream data."},
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
    {"name": "get_spread_snapshot", "description": "Return raw current spread plus optional recent spread history."},
    {"name": "get_correlation", "description": "Return daily close-return correlation for two instruments or symbols."},
    {"name": "create_price_alert", "description": "Create a price alert for the default MCP chat."},
    {"name": "list_price_alerts", "description": "List pending price alerts for the default MCP chat."},
    {"name": "clear_price_alert", "description": "Clear one price alert for the default MCP chat."},
    {"name": "clear_all_price_alerts", "description": "Clear all pending price alerts for the default MCP chat, optionally filtered by instrument."},
    {"name": "replace_alert_grid", "description": "Atomically replace the pending price-alert grid for one instrument in the default MCP chat."},
    {"name": "create_indicator_alert", "description": "Create an indicator alert for the default MCP chat."},
    {"name": "seed_default_indicator_alerts", "description": "Seed the default indicator alert set for the default MCP chat."},
    {"name": "list_indicator_alerts", "description": "List active indicator alerts for the default MCP chat."},
    {"name": "clear_indicator_alert", "description": "Clear one indicator alert for the default MCP chat."},
    {"name": "clear_all_indicator_alerts", "description": "Clear matching indicator alerts for the default MCP chat."},
    {"name": "create_time_alert", "description": "Create a fixed-time or session reminder for the default MCP chat. Fixed-time alerts accept HH:MM or exact YYYY-MM-DD HH:MM in Asia/Singapore."},
    {"name": "list_time_alerts", "description": "List active time alerts for the default MCP chat."},
    {"name": "clear_time_alert", "description": "Clear one time alert for the default MCP chat."},
    {"name": "list_alert_history", "description": "Return alert trigger history for the default MCP chat."},
]


def build_mcp_server(
    *,
    runtime: BotRuntime,
    settings: Settings | None = None,
) -> FastMCP:
    """Build the FastMCP server backed by the live bot runtime."""

    resolved_settings = settings or runtime.settings
    service = BotMcpService(runtime=runtime, settings=resolved_settings)
    server = FastMCP(
        name="Market Signal Bot V3 MCP",
        instructions=(
            "Use these tools to inspect Market Signal Bot V3 raw market data, sanitized evidence, "
            "account state, trade journal/history, and alert surfaces. Responses are structured JSON."
        ),
        streamable_http_path=resolved_settings.mcp_http_path,
        host=resolved_settings.mcp_http_host,
        port=resolved_settings.mcp_http_port,
        log_level=resolved_settings.log_level,
        json_response=False,
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

    @server.resource("marketsignal://alert-defaults", name="Alert Defaults", mime_type="application/json")
    def alert_defaults_resource() -> str:
        return _resource_json(BotMcpService.alert_defaults_payload())

    @server.resource("marketsignal://tool-surface", name="Tool Surface", mime_type="application/json")
    def tool_surface_resource() -> str:
        return _resource_json(BotMcpService.tool_surface_payload(TOOL_SPECS))

    @server.custom_route("/healthz", methods=["GET"], include_in_schema=False)
    async def healthz_route(_request) -> JSONResponse:
        return JSONResponse({"ok": True, "service": "market-signal-bot-v3-mcp"})

    return server


def build_mcp_http_app(
    *,
    runtime: BotRuntime,
    settings: Settings | None = None,
) -> Any:
    """Build the authenticated ASGI app for the embedded HTTP MCP surface."""

    resolved_settings = settings or runtime.settings or get_settings()
    if resolved_settings.mcp_http_api_key is None:
        raise ValueError("MCP_HTTP_API_KEY is required to build the MCP HTTP app.")

    fastmcp = build_mcp_server(runtime=runtime, settings=resolved_settings)
    mcp_app = fastmcp.streamable_http_app()
    mcp_app.add_middleware(
        QueryParamAPIKeyMiddleware,
        api_key=resolved_settings.mcp_http_api_key.get_secret_value(),
    )
    return mcp_app


def _resource_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, indent=2, sort_keys=True)


__all__ = ["TOOL_SPECS", "build_mcp_http_app", "build_mcp_server"]
