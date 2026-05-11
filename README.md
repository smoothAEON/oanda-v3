# Market Signal MCP

Local, read-only OANDA market-analysis MCP server. The default runtime is now
agent-first stdio: clone the repo, create a venv, install dependencies, set
OANDA credentials, and point an MCP-capable agent at `market-signal-mcp` or
`python -m mcp_server.main`.

This repo no longer ships a Railway service, Telegram bot runtime, HTTP MCP
server, scheduler, stream loop, alert engine, reminders, or push notifications.
All broker reads happen on demand through OANDA REST. Local sync/cache/chart
and export writes use TinyDB/files under your configured local data directory.

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

Edit `.env` with:

```text
OANDA_API_KEY=...
OANDA_ACCOUNT_ID=...
OANDA_ENVIRONMENT=practice
```

`smartmoneyconcepts==0.0.26` is installed explicitly with `--no-deps` because
its published metadata pins a pandas version that conflicts with this stack.
TA-Lib may need a platform-specific wheel or system install before
`pip install -r requirements.txt` succeeds.

## Run

For MCP clients, prefer the console script created by the editable install:

```powershell
market-signal-mcp
```

Equivalent module entrypoint:

```powershell
python -m mcp_server.main
```

The server speaks MCP over stdio. Do not use this command as an interactive
CLI; stdout is reserved for MCP protocol frames. Logs go to stderr and local
log files.

For a human-facing environment check:

```powershell
python -c "from config.settings import get_settings; s=get_settings(); print(s.oanda_environment, s.tinydb_path)"
```

## MCP Client Config

Using the installed script:

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

Using the venv Python directly:

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

If your MCP client does not load `.env` from `cwd`, pass the three required
OANDA variables in the client config `env` block.

## Environment

Required:

| Variable | Notes |
| --- | --- |
| `OANDA_API_KEY` | OANDA REST API token. |
| `OANDA_ACCOUNT_ID` | OANDA account id. |
| `OANDA_ENVIRONMENT` | `practice` or `live`. |

Useful local settings:

| Variable | Default |
| --- | --- |
| `LOG_LEVEL` | `INFO` |
| `LOG_JSON` | `false` |
| `DEFAULT_CANDLE_COUNT` | `500` |
| `DEFAULT_SWING_LENGTH` | `10` |
| `ACCOUNT_CURRENCY` | `USD` |
| `JOURNAL_TIMEZONE` | `Asia/Singapore` |
| `CALENDAR_REFRESH_HOURS` | `1` |
| `MACRO_REFRESH_HOURS` | `1` |
| `TINYDB_PATH` | `data/bot.json` |

Removed settings include all `TELEGRAM_*`, all `MCP_HTTP_*`, Railway/`PORT`
settings, `SCAN_INTERVAL_MINUTES`, `POLL_INTERVAL_SECONDS`,
`STREAM_INSTRUMENTS`, and `MAE_MFE_MIN_PIP_MOVE`.

## Data

The default local store is `data/bot.json`. To preserve old hosted state, copy
the previous TinyDB file to the configured `TINYDB_PATH`. Obsolete Telegram and
alert tables are ignored by the local MCP runtime.

Artifacts are written beside the TinyDB path:

- `cache/` for candle CSV cache metadata/files
- `charts/` for rendered chart PNGs
- `exports/` for exported bid/ask candle CSVs
- `logs/` for runtime logs

Local writes are guarded at the runtime level so concurrent MCP calls do not
interleave TinyDB/cache/artifact mutations.

## Tool Surface

Kept MCP areas:

- runtime, market-hours, macro, and calendar status
- yfinance research helpers
- scan, refresh, snapshot, structure, order-block, indicator, VWAP, session,
  day-range, and previous-day evidence
- OANDA price, spread, candles, and OHLC reads
- account summary, positions, orders, transfers
- journal, trade history, trade stats, MAE/MFE replay
- correlation

Added tools:

- `render_chart(...)` renders the existing chart request shape to a persistent
  PNG artifact and returns path, metadata, warnings, and omitted layers.
- `export_candles(...)` writes bid/ask CSV files for one instrument or all scan
  instruments by count or explicit UTC start/end range.

Removed tools:

- price alert create/list/clear/grid/default/history tools
- indicator alert create/list/clear/default/history tools
- time alert create/list/clear/history tools

Broker operations remain read-only. There are no order placement,
modification, cancellation, or auto-trading paths.

## Tests

```powershell
pytest tests/unit -v
pytest tests/integration -v
pytest
pytest tests/live -v -m live
```

`tests/live` requires real OANDA credentials and is not part of the default
test path.
