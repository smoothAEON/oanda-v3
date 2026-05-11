# AGENTS.md

This file gives coding agents the current repo contract.

## Repo Summary

Market Signal MCP is a local, read-only OANDA analysis server exposed through
MCP stdio. It does not run as a Telegram bot, Railway service, HTTP server,
scheduler, stream loop, alert engine, reminder process, or push notifier.

The default flow is:

```powershell
py -3.10 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
pip install smartmoneyconcepts==0.0.26 --no-deps
pip install -e . --no-deps
Copy-Item .env.example .env
market-signal-mcp
```

Equivalent entrypoint: `python -m mcp_server.main`.

`smartmoneyconcepts==0.0.26` must be installed explicitly with `--no-deps`;
the stdio server must not auto-install packages or print setup text to stdout.

## Required Environment

Required:

- `OANDA_API_KEY`
- `OANDA_ACCOUNT_ID`
- `OANDA_ENVIRONMENT`

Supported local settings:

- `LOG_LEVEL`
- `LOG_JSON`
- `DEFAULT_CANDLE_COUNT`
- `DEFAULT_SWING_LENGTH`
- `ACCOUNT_CURRENCY`
- `JOURNAL_TIMEZONE`
- `CALENDAR_REFRESH_HOURS`
- `MACRO_REFRESH_HOURS`
- `TINYDB_PATH`

Removed settings include all `TELEGRAM_*`, all `MCP_HTTP_*`, Railway/`PORT`
settings, `SCAN_INTERVAL_MINUTES`, `POLL_INTERVAL_SECONDS`,
`STREAM_INSTRUMENTS`, and `MAE_MFE_MIN_PIP_MOVE`.

## Runtime Architecture

```text
mcp_server.main -> AgentRuntime -> FastMCP stdio tools
```

`agent.runtime.AgentRuntime` wires the OANDA REST clients, candle cache, market
state, scan orchestrator, journal/history services, MAE/MFE replay, chart
renderer, yfinance/correlation helpers, and TinyDB storage.

There is no app-start background orchestration. Do not add
`asyncio.create_task()` startup loops, APScheduler jobs, Telegram polling,
stream fan-out, alert evaluators, or HTTP serving paths.

Local writes that touch TinyDB/cache/chart/export state should go through the
runtime lock path (`runtime.run_blocking(..., write=True)` or the existing
runtime sync helpers) so concurrent MCP calls do not interleave mutations.

## MCP Boundaries

Kept MCP surfaces:

- runtime, market-hours, macro, and calendar
- yfinance helpers
- scan/refresh/published evidence
- price, spread, candles, and OHLC
- account summary, positions, orders, transfers
- journal, trade history, trade stats, MAE/MFE
- correlation
- chart rendering and candle CSV export

Removed MCP surfaces:

- price alert tools
- indicator alert tools
- time alert tools
- alert defaults/history resources
- HTTP auth and health routes

Broker operations stay read-only. Do not add order placement, modification,
cancellation, or auto-trading paths.

## Testing

```powershell
pytest tests/unit -v
pytest tests/integration -v
pytest
pytest tests/live -v -m live
```

Default `pytest` runs unit and integration tests only. Live tests require real
OANDA credentials.

## Coding Guardrails

- Public Pydantic contracts inherit from `FrozenModel`; keep datetimes
  timezone-aware UTC.
- Use typed models and service/repository methods instead of raw TinyDB writes.
- Use OANDA tick count as `tick_volume`, not `volume`.
- Keep analysis code independent from broker execution writes.
- Closed-market scan refreshes use cached candles unless `force=True`.
- Use `mplfinance` for candlestick rendering.
- Logs for stdio startup must go to stderr/files only.
- Do not reintroduce Telegram, Railway, HTTP MCP, alerts, reminders,
  scheduler loops, or stream loops as optional legacy paths.
