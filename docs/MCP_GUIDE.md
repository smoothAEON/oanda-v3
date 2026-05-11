# MCP Guide

Market Signal MCP is a local stdio MCP server for read-only OANDA analysis and
account context. It is meant to be started by an MCP-capable agent on demand,
not hosted as an always-on service.

## Transport

| Item | Value |
| --- | --- |
| Transport | MCP stdio |
| Entrypoint | `market-signal-mcp` |
| Module | `python -m mcp_server.main` |
| Auth | Local process environment only |
| Logs | stderr and local log files |

There is no HTTP endpoint, API-key middleware, `/healthz` route, Uvicorn app,
Railway deployment, Telegram runtime, scheduler, stream loop, alert engine, or
push-notification surface.

## Setup

```powershell
py -3.10 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
pip install smartmoneyconcepts==0.0.26 --no-deps
pip install -e . --no-deps
Copy-Item .env.example .env
```

Required `.env` keys:

```text
OANDA_API_KEY=...
OANDA_ACCOUNT_ID=...
OANDA_ENVIRONMENT=practice
```

`smartmoneyconcepts==0.0.26` must stay as an explicit `--no-deps` install step.
The stdio server does not auto-install dependencies or print installer messages
to stdout.

## Client Config Examples

Installed script:

```json
{
  "mcpServers": {
    "market-signal": {
      "command": "market-signal-mcp",
      "cwd": "C:\\Users\\you\\path\\to\\oanda v3"
    }
  }
}
```

Venv Python:

```json
{
  "mcpServers": {
    "market-signal": {
      "command": "C:\\Users\\you\\path\\to\\oanda v3\\.venv\\Scripts\\python.exe",
      "args": ["-m", "mcp_server.main"],
      "cwd": "C:\\Users\\you\\path\\to\\oanda v3"
    }
  }
}
```

Explicit environment block, useful for clients that do not load `.env`:

```json
{
  "mcpServers": {
    "market-signal": {
      "command": "market-signal-mcp",
      "cwd": "C:\\Users\\you\\path\\to\\oanda v3",
      "env": {
        "OANDA_API_KEY": "your-token",
        "OANDA_ACCOUNT_ID": "your-account-id",
        "OANDA_ENVIRONMENT": "practice",
        "TINYDB_PATH": "data/bot.json"
      }
    }
  }
}
```

## Resources

| Resource | Purpose |
| --- | --- |
| `marketsignal://capabilities` | Transport, candle limits, published snapshot frames, and read/write summary. |
| `marketsignal://supported-instruments` | Registry metadata, aliases, scan instruments, and live OANDA catalog instruments. |
| `marketsignal://tool-surface` | Current tool names and descriptions. |

## Recommended Agent Flow

1. Call `get_runtime_status` and `get_market_status` for local runtime,
   market-hours, macro, calendar, and freshness context.
2. Call `get_calendar` or `get_macro_context` when event risk or macro inputs
   matter.
3. Use `get_candles`, `get_ohlc`, `get_price`, and `get_spread_snapshot` for
   direct OANDA REST evidence.
4. Use `scan_all`, `scan_instrument`, `refresh_snapshot`, and published
   snapshot tools when cached multi-timeframe evidence is useful.
5. Use journal, trade-history, trade-stat, MAE/MFE, transfer, account, order,
   and position tools for operational account context.
6. Use `render_chart` when the agent needs a local PNG artifact.
7. Use `export_candles` when the agent needs bid/ask CSV files for inspection
   or downstream tooling.

The tools return evidence and state, not final trading instructions.

## Tool Groups

Runtime and context:

- `get_runtime_status`
- `get_market_status`
- `get_macro_context`
- `get_calendar`

Yahoo Finance research:

- `search_yfinance_tickers`
- `get_yfinance_ticker`
- `get_yfinance_history`
- `get_yfinance_news`

OANDA market data:

- `get_candles`
- `get_ohlc`
- `get_price`
- `get_spread_snapshot`
- `export_candles`

Scan and published evidence:

- `scan_all`
- `scan_instrument`
- `refresh_snapshot`
- `get_smc_snapshot`
- `get_structure`
- `get_order_blocks`
- `get_indicators`
- `get_vwap`
- `get_session_context`
- `get_day_range`
- `get_previous_day_levels`
- `render_chart`

Account and operations:

- `get_account_summary`
- `list_open_positions`
- `list_open_orders`
- `list_transfers`
- `list_journal_trades`
- `get_journal_trade`
- `get_mae_mfe`
- `get_trade_history`
- `get_trade_stats`
- `get_correlation`

## Freshness Model

Broker-backed reads are fresh by default:

- direct price, spread, account, order, transfer, and candle tools call OANDA
  REST on demand
- journal, history, stats, MAE/MFE, and chart reads run the needed local sync
  before reading
- trade-history tools call incremental sync before returning results
- scan/cache/chart/export writes are serialized by the local runtime lock

Published snapshot frames are `M15`, `H1`, `H4`, and `D`. Raw candle tools can
read the broader OANDA granularity set directly.

## Removed Surfaces

The MCP server no longer exposes:

- price, indicator, or time alert tools
- alert defaults or alert history resources
- Telegram chat/session/auth helpers
- runtime config mutation
- HTTP API-key auth
- scheduler, stream, or poller health endpoints
- broker execution writes
