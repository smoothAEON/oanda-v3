# Market Signal Bot V3

![Python](https://img.shields.io/badge/python-3.10+-blue.svg) ![OANDA](https://img.shields.io/badge/OANDA-read--only-green.svg) ![Telegram](https://img.shields.io/badge/Telegram-bot-blue.svg) ![FastMCP](https://img.shields.io/badge/MCP-streamable--http-orange.svg) ![Pytest](https://img.shields.io/badge/tests-unit%2Fintegration%2Flive-lightgrey.svg)

Market Signal Bot V3 is a Python 3.10+ read-only market analysis and trading operations platform built around an OANDA account. It runs as a Telegram bot, can optionally expose the same live runtime over an embedded FastMCP HTTP server, and combines scheduled multi-timeframe analysis, live pricing, account visibility, trade journaling, alerting, chart rendering, and bounded research helpers.

This repository does **not** place trades. `providers/oanda_execution.py` is an explicit stub reserved for a future execution layer and is intentionally kept out of the production runtime.

## What This Repo Actually Does

At a high level, the runtime does five things in parallel:

1. Maintains a live read-only view of OANDA account state, prices, open trades, orders, and transactions.
2. Runs scheduled scans over a fixed instrument universe and publishes immutable timeframe snapshots.
3. Tracks open-trade lifecycle and excursion data into a local journal backed by TinyDB.
4. Delivers operator workflows through Telegram commands and push notifications.
5. Exposes the same state and services through an optional MCP server.

## At A Glance

| Surface | Entry point | Purpose |
| --- | --- | --- |
| Telegram runtime | `python bootstrap.py` | Main operator interface. Auth, scans, charts, alerts, journal, account reads. |
| Telegram runtime | `python main.py` or `python -m bot.main` | Same runtime without the dependency bootstrap wrapper. |
| Embedded MCP server | `python bootstrap.py` with `MCP_HTTP_ENABLED=true` | FastMCP `streamable-http` endpoint over the live bot runtime. |
| Trade-history maintenance CLI | `python -m journal.trade_history_service backfill-trades --start YYYY-MM-DD --end YYYY-MM-DD` | Historical backfill into normalized local journal tables. |

## System Boundary

### Included

- Read-only OANDA market data and pricing
- Read-only OANDA account, trade, order, and transaction inspection
- Scheduled market scans over a fixed analysis universe
- Multi-timeframe SMC and indicator analysis
- Live price alerts, scheduled indicator alerts, and time reminders
- Telegram push notifications for trade opens, trade closes, and alerts
- Trade journal, MAE/MFE sampling, and transaction-based realized PnL history
- Chart rendering with SMC, trade, alert, and indicator overlays
- Embedded MCP tools for runtime inspection, analysis reads, alert mutation, trade stats, correlation, and bounded `yfinance`

### Explicitly Not Included

- Trade execution
- Order placement, modification, or cancellation
- Auto-trading or strategy execution against broker state
- Hidden fallback behavior for unsupported scan instruments
- Weekly timeframe support in the command parser

## Core Capabilities

### Telegram Operator Runtime

- Password-gated Telegram bot with chat-bound sessions
- One active session per Telegram user
- Admin-only maintenance command for trade-history backfills
- `/help` generated from the live registered command surface, not static docs
- Shared runtime state across command handlers, scheduler jobs, and notifications

### Scheduled Analysis Engine

- Auto-scan cycle over 16 scan-universe instruments
- Snapshot publication on `M15`, `H1`, `H4`, and `D`
- Smart Money Concepts analysis through a wrapped `smartmoneyconcepts` integration
- Raw bid/ask spread evidence recorded without pass/fail gate language
- Closed-bar-only analysis: forming candles are trimmed before publication
- Versioned in-memory market state with pinned snapshot history

### Live Pricing And Account Visibility

- OANDA live pricing stream with reconnect and exponential backoff
- REST pricing fallback when live quotes are unavailable or stale
- OANDA account summary reads
- Open trades with SL, TP, GSLO, and live pip-distance annotation
- Pending order inspection grouped into entry orders and trade-attached risk orders
- Broker-catalog validation for live-price and export style commands

### Journal, Trade History, And Performance

- Local trade journal backed by TinyDB
- Incremental OANDA transaction sync using a stored watermark
- Historical backfill CLI and admin command
- Trade lifecycle normalization into `OPEN`, `CLOSE`, and `PARTIAL_CLOSE`
- Realized PnL summaries resolved in `JOURNAL_TIMEZONE`
- MAE/MFE excursion tracking for open trades from live streamed ticks
- Aggregate trade stats service for realized PnL, win rate, expectancy, profit factor, realized R, MAE coverage, and max drawdown

### Alerts And Notifications

- Live price alerts evaluated on the streaming quote path
- Scheduled indicator alerts evaluated on newly built snapshots
- Time alerts for fixed clock times or named session opens
- Trade-open and trade-close push notifications from the trade poller
- Alert trigger history persisted after successful notification/state transition
- Batch alert helpers on the MCP side for clear-all and grid replacement workflows

### Charting And Export

- `mplfinance` rendering in a worker process
- `compact`, `balanced`, and `full` presentation modes
- Selectable SMC, trade, alert, and indicator overlay families
- Overlay warnings when stale snapshots or cached candle fallbacks were used
- Bid/ask candle CSV export for one live OANDA instrument or all scan instruments

#### MCP And Research Surface

- Embedded FastMCP `streamable-http` app with API-key query param auth
- Runtime and market health reads
- LLM-first small tools for raw OANDA candles, prices, spreads, and sanitized snapshot reads
- Journal, MAE/MFE, trade-history, trade-stats, and spread inspection
- Alert creation and lifecycle reads/mutations
- Bounded `yfinance` search, quote, history, and news tools
- Daily return-correlation analysis across OANDA instruments and Yahoo Finance symbols

## Runtime Architecture

### Process Model

`bootstrap.py` is the recommended entrypoint. It verifies that `smartmoneyconcepts==0.0.26` is available and installs it with `--no-deps` when needed, then hands off to `bot.bot.main`.

`bot.bot.main` does the following:

1. Loads and validates settings from code defaults, `.env`, and process environment.
2. Configures structured logging.
3. Builds the runtime graph in `bot/runtime.py`.
4. Starts Telegram polling.
5. Optionally starts the embedded MCP HTTP server in the same event loop when `MCP_HTTP_ENABLED=true`.

### Dependency Graph

The runtime assembled in [`bot/runtime.py`](./bot/runtime.py) contains:

- `TradeStore`: TinyDB persistence and cache metadata
- `SecurityManager`: Telegram auth/session persistence
- `RuntimeConfigManager`: persisted runtime overrides
- `MarketStateStore`: in-memory published snapshot state
- `OandaMarketDataProvider`: scan-path candles and current prices
- `OandaAccountClient`: account, trade, order, transaction, and export reads
- `OandaStreamClient` + `PriceStreamTask`: live quote stream
- `ScanOrchestrator`: analysis pipeline and snapshot publication
- `SchedulerService`: APScheduler job lifecycle
- `TradePollerTask`: open-trade diffing and notification
- `TradeHistoryService`: transaction sync and realized-history queries
- `ChartRenderer`: worker-process chart rendering
- `AlertRepository` + alert engines: price, indicator, and time alerts

### Runtime Data Flow

```text
OANDA REST -----> CandleCache -----> ScanOrchestrator -----> MarketStateStore
     |                 |                    |                        |
     |                 |                    |                        +--> Telegram read commands
     |                 |                    |                        +--> MCP read tools
     |                 |                    +--> IndicatorAlertEngine
     |                 |
     |                 +--> CSV candle cache + TinyDB freshness metadata
     |
     +--> AccountClient ---> TradePollerTask ---> JournalService ---> TradeRepository
     |                           |                    |
     |                           |                    +--> Telegram push notifications
     |                           +--> open-trade watchlist updates
     |
OANDA stream ---> PriceStreamTask ---> PriceAlertEngine
                   |                  ---> ExcursionTracker ---> ExcursionRepository
                   |
                   +--> latest streamed quotes for /price --live and MCP get_price
```

## Analysis Pipeline

### Scan Scope//

Scheduled scans operate on `SCAN_INSTRUMENTS` from [`core/instrument_registry.py`](./core/instrument_registry.py) and publish `M15`, `H1`, `H4`, and `D` snapshots.

### Snapshot Build Steps

For each instrument/timeframe, the scan orchestrator:

1. Resolves market-hours state for the instrument category.
2. Reads candles through `OandaMarketDataProvider`, which goes through the three-layer cache unless explicitly forced elsewhere.
3. Normalizes candles to the canonical schema: `time`, `open`, `high`, `low`, `close`, `tick_volume`.
4. Trims any forming bar so detectors only see completed candles.
5. Builds SMC structure, zones, liquidity, and context through `SmcAdapter`.
6. Builds indicator summaries.
7. Computes freshness metadata from cache state.
8. Evaluates indicator alerts if the market is open and the snapshot is fresh.
9. Attaches raw bid/ask spread evidence.
10. Publishes immutable `TimeframeSnapshot` objects into `MarketStateStore`.

### SMC And Detector Surface

Published snapshots drive Telegram and MCP analysis reads for:

- structure: BOS / CHOCH and recent breaks
- order blocks
- liquidity levels and sweep state
- session context
- previous day high / low and daily range

## Market Data, Cache, And Freshness Model

### Candle Cache

`providers/cache.py` implements a three-level candle cache:

1. in-memory `CacheEntry`
2. CSV candle store under `data/cache/<INSTRUMENT>/<TIMEFRAME>.csv`
3. OANDA API fetch

Freshness metadata is stored separately in TinyDB so the system knows:

- `last_completed_candle`
- `fetched_at`
- `source`
- `candle_count`
- computed staleness in seconds

### Freshness Policy

- Scans and snapshot publication refuse to fabricate freshness provenance.
- Closed-market scans are cache-only unless a caller explicitly forces a fetch path.
- `CandleCache` will append-refresh from the last completed candle when cached data is stale.
- Weekend and holiday gaps return the best available cache instead of manufacturing candles.

### Chart Fetch Behavior

The chart renderer:

- resolves overlays from published snapshot state
- fetches render candles separately
- falls back to cached candles if a live fetch fails
- warns when stale overlay state or candle fallback data was used
- renders in a worker process, not inside the Telegram handler thread

## Live Streaming, Polling, And Background Jobs

### Price Stream

`background/stream_task.py` manages:

- one producer task for the OANDA price stream
- one consumer queue for MAE/MFE excursion tracking
- one consumer queue for live price alert evaluation
- latest streamed quote cache for `--live` pricing reads

The subscription universe is dynamic:

- base instruments from `STREAM_INSTRUMENTS`
- plus instruments from currently open trades
- plus instruments with active pending price alerts

### Trade Poller

`background/poller_task.py` runs on the scheduler cadence and:

- fetches current open trades
- diffs them against locally persisted open trades
- emits `TradeOpenedEvent`, `TradeModifiedEvent`, and `TradeClosedEvent`
- updates the journal repository
- enriches close reason using trade detail and closing transactions when available
- pushes trade-open and trade-close notifications when enabled
- updates the stream watchlist with currently open trade instruments

### Scheduler Jobs

`orchestration/scheduler.py` registers APScheduler jobs for:

- automatic scan cycle
- London cache warm
- New York cache warm
- next market-open cache warm
- calendar refresh
- macro refresh
- trade poller
- trade-history incremental sync
- time-alert evaluation

Each job is wrapped in a managed job runner that records status, queues reruns when overlapping triggers occur, and applies failure backoff.

## Trade Journal And History Model

There are two related but different data paths:

### Journal Path

The local journal is optimized for current operator workflows:

- open trades
- closed trades
- SL / TP / GSLO
- notes / labels
- excursion summaries
- recent journal list/detail views

### Transaction History Path

`journal/trade_history_service.py` is the canonical realized-history path:

- incremental sync from OANDA `lastTransactionID`
- raw transaction persistence
- normalized trade-history event persistence
- projection of trade-history events back into local `TradeRecord` rows
- realized PnL summaries by symbolic or explicit date window
- safe backfill chunking in 30-day windows

Realized history includes financing and commission handling and is resolved in the configured local timezone, which defaults to `Asia/Singapore`.

## Alerts And Notification Semantics

### Price Alerts

- Evaluated on every streamed tick.
- `above` triggers on ask crossing the target price.
- `below` triggers on bid crossing the target price.
- Alerts arm only after the market is first observed on the non-trigger side, which prevents immediate firing from a pre-crossed condition.
- Fired alerts are one-shot in the Telegram path.

### Indicator Alerts

- Evaluated only on fresh closed snapshots for `M15`, `H1`, `H4`, and `D`.
- Supported indicators: `RSI`, `STOCH`, `MACD`, `SMA_CROSS`.
- Supported conditions: `above`, `below`, `cross_up`, `cross_down`.
- Cross baseline rules:
  - `MACD` and `SMA_CROSS` cross around `0`
  - `RSI` and `STOCH` cross around `50`
- Same-candle evaluation dedupe is persisted through `indicator_alert_cursors`.
- Telegram creates one-shot alerts.
- MCP additionally exposes `repeat` and `cooloff_minutes` on indicator alert creation.

### Time Alerts

- Fixed times are entered in `Asia/Singapore` and stored in UTC.
- Dated one-time reminders use `YYYY-MM-DD HH:MM`.
- Session reminders support `london`, `newyork`, and `market_open`.
- Session reminder schedule in code:
  - London: `08:00 UTC`
  - New York: `13:00 UTC`
  - Weekly market open: Sunday `22:00 UTC`
- Time alerts are reminders only; they do not run analysis inline.

### Push Delivery

Push-capable events include:

- trade opened
- trade closed
- fired price alerts
- fired indicator alerts
- due time alerts

Push behavior is split:

- trade lifecycle pushes go to `TELEGRAM_CHAT_ID`
- user-created alerts go back to the chat that owns the alert

## Supported Instruments

### Scan Universe

The scheduled analysis universe currently contains 16 instruments:

| Metals | FX Majors | FX Crosses | Energy CFDs | Index CFDs |
| --- | --- | --- | --- | --- |
| `XAU_USD` | `EUR_USD` | `EUR_GBP` | `BCO_USD` | `SPX500_USD` |
| `XAG_USD` | `GBP_USD` | `EUR_JPY` | `WTICO_USD` | `JP225_USD` |
|  | `USD_JPY` | `GBP_JPY` |  |  |
|  | `AUD_USD` |  |  |  |
|  | `USD_CAD` |  |  |  |
|  | `USD_CHF` |  |  |  |
|  | `NZD_USD` |  |  |  |

Each scan-universe instrument has explicit metadata for:

- pip size
- pip value per lot
- lot size
- instrument category

Spread reads are recorded from bid/ask evidence, not registry thresholds.

### Broker-Catalog Commands vs Scan-Universe Commands

This distinction matters:

- Scan and snapshot commands validate against the 16-instrument scan universe.
- Live-price, account, alert, and export style commands often validate against the live OANDA account catalog instead.

That means commands like `/price`, `/pricealert`, and `/extractor` can operate on live OANDA instruments outside the scheduled scan universe if the connected account exposes them.

### Input Normalization

Common aliases and flexible formats are normalized before validation:

- `spx500usd` -> `SPX500_USD`
- `silver` -> `XAG_USD`
- `oil` -> `WTICO_USD`
- `EUR/USD`, `eur-usd`, `eur usd`, `eurusd` -> `EUR_USD`

## Supported Timeframes

### Command Parser

Accepted parser timeframes:

- `M1`
- `M5`
- `M15`
- `M30`
- `H1`
- `H4`
- `D`

Weekly aliases normalize to `W` and are then rejected.

### By Subsystem

| Subsystem | Timeframes |
| --- | --- |
| scheduled scan snapshots | `M15`, `H1`, `H4`, `D` |
| chart rendering | `M1`, `M5`, `M15`, `M30`, `H1`, `H4`, `D` |
| extractor | `M1`, `M5`, `M15`, `M30`, `H1`, `H4`, `D` |
| automatic indicator alerts | `M15`, `H1`, `H4`, `D` |
| `/vwap` | `M30`, `H1`, `H4`, `D` |

## Installation

### Requirements

Core dependencies are declared in [`requirements.txt`](./requirements.txt) and include:

- `oandapyV20`
- `pandas`, `numpy`
- `TA-Lib`
- `pandas-ta`
- `smartmoneyconcepts==0.0.26` installed separately with `--no-deps`
- `mplfinance`
- `tinydb`, `portalocker`
- `apscheduler`
- `python-telegram-bot`
- `mcp`, `starlette`, `uvicorn`
- `yfinance`
- `pandas_market_calendars`

`smartmoneyconcepts` is intentionally installed outside the main requirements file because its published dependency metadata conflicts with the approved pandas stack used in this repo.

### Windows (PowerShell)

```powershell
py -3.10 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
pip install smartmoneyconcepts==0.0.26 --no-deps
pip install -e . --no-deps
Copy-Item .env.example .env
```

### macOS / Linux

```bash
python3.10 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
pip install smartmoneyconcepts==0.0.26 --no-deps
pip install -e . --no-deps
cp .env.example .env
```

## Configuration

### Settings Precedence

Configuration precedence is:

```text
code defaults < repo-root .env < explicit process environment variables
```

### Required Environment Variables

| Variable | Meaning |
| --- | --- |
| `OANDA_API_KEY` | OANDA API access token |
| `OANDA_ACCOUNT_ID` | target OANDA account |
| `OANDA_ENVIRONMENT` | `practice` or `live` |
| `TELEGRAM_BOT_TOKEN` | Telegram bot token |
| `TELEGRAM_CHAT_ID` | default Telegram chat for push notifications |
| `TELEGRAM_BOT_PASSWORD` | password accepted by `/start <password>` |
| `TELEGRAM_ADMIN_IDS` | comma-separated Telegram user IDs with admin rights |

### Key Runtime Settings

| Variable | Default | Notes |
| --- | --- | --- |
| `LOG_LEVEL` | `INFO` | Mutable through runtime config only for the in-process runtime. |
| `LOG_JSON` | `false` | Structured logging toggle. |
| `DEFAULT_CANDLE_COUNT` | `500` | Default candle request size for scans and charting. |
| `DEFAULT_SWING_LENGTH` | `10` | SMC swing-length input. |
| `SCAN_INTERVAL_MINUTES` | `5` | APScheduler auto-scan cadence. |
| `POLL_INTERVAL_SECONDS` | `30` | Trade poller and trade-history sync cadence. |
| `STREAM_INSTRUMENTS` | all scan instruments | Base stream subscription set. |
| `JOURNAL_TIMEZONE` | `Asia/Singapore` | Trade-history local timezone. |
| `MAE_MFE_MIN_PIP_MOVE` | `0.5` | Minimum move before excursion samples are stored. |
| `CALENDAR_REFRESH_HOURS` | `1` | Economic-calendar refresh cadence. |
| `MACRO_REFRESH_HOURS` | `1` | Macro-context refresh cadence. |
| `TINYDB_PATH` | `data/bot.json` | Local default persistence file; candle CSV cache lives beside it under `cache/`. On Railway with a mounted volume and no explicit `TINYDB_PATH`, or with the local template value `data/bot.json`, the runtime falls back to `${RAILWAY_VOLUME_MOUNT_PATH}/bot.json`. |

### Railway Volume Deployment

For Railway, attach one volume to the single service that runs `python bootstrap.py`. The preferred deployment is to leave `TINYDB_PATH` unset and let Railway's `RAILWAY_VOLUME_MOUNT_PATH` choose the store automatically. If you mount the volume at `/data`, you can also set:

```text
TINYDB_PATH=/data/bot.json
```

Do not mount the volume at `/app/data`. Railway volumes are not overlays, and this repo already uses `data/` as a Python package. When `RAILWAY_VOLUME_MOUNT_PATH` is present and `TINYDB_PATH` is unset or left at the local template value `data/bot.json`, the runtime automatically uses `${RAILWAY_VOLUME_MOUNT_PATH}/bot.json`. Custom paths under the repo `data/` package are rejected on Railway.

Keep the Railway service at one replica only. The TinyDB lock model is single-writer and is not intended for multiple replicas writing to the same runtime store.

### Embedded MCP Settings

| Variable | Default | Notes |
| --- | --- | --- |
| `MCP_HTTP_ENABLED` | `false` | Enables embedded MCP server alongside Telegram polling. |
| `MCP_HTTP_HOST` | `0.0.0.0` | Bind host. Use `127.0.0.1` only for local-only MCP access. |
| `MCP_HTTP_PORT` | `8080` | Also accepts `PORT`; Railway `PORT` wins over `MCP_HTTP_PORT`. |
| `MCP_HTTP_PATH` | `/mcp` | FastMCP streamable HTTP path. |
| `MCP_HTTP_API_KEY` | none | Required when MCP is enabled. |
| `MCP_DEFAULT_CHAT_ID` | unset | Falls back to `TELEGRAM_CHAT_ID`. |

### Persisted Runtime Overrides

`/config` persists runtime overrides in TinyDB for:

- `chart`
- `chart_mode`
- `scan_interval`
- `trade_push`
- `session_alerts`

These survive restarts. `scan_interval` is immediately applied to APScheduler.

## Running The System

### Recommended Startup

```powershell
python bootstrap.py
```

This is the safest local entrypoint because it verifies the `smartmoneyconcepts` dependency before the bot starts.

### Alternate Startup

```powershell
python main.py
python -m bot.main
```

### Telegram + Embedded MCP

Set the MCP environment variables, especially:

```text
MCP_HTTP_ENABLED=true
MCP_HTTP_API_KEY=your_secret_here
```

On Railway, keep the service running `python bootstrap.py`. The runtime binds the MCP server to `0.0.0.0:${PORT}` when Railway environment variables are present, so the public Railway URL can reach `/healthz` and `/mcp`.

Then start the same runtime:

```powershell
python bootstrap.py
```

The MCP endpoint will then be available at:

```text
http://127.0.0.1:8080/mcp?api_key=<MCP_HTTP_API_KEY>
```

Health check:

```text
GET /healthz
```

### Trade-History Backfill CLI

```powershell
python -m journal.trade_history_service backfill-trades --start 2025-01-01 --end 2026-04-01 --tz Asia/Singapore
```

## Telegram Usage

### Authentication Model

- Only `/start <password>` is available before authentication.
- All other commands require an active session.
- The session is bound to the Telegram chat used at login.
- Calling `/start` again replaces the stored session for that user.
- `/help` is intentionally gated behind authentication.

### Command Surface

#### Session And Auth

- `/start <password>`: authenticate and bind the session to the current chat.
- `/help`: show the authenticated command reference.
- `/logout`: clear the session and report session duration.

#### Runtime Status

- `/status`: runtime health summary including uptime, scheduler, stream, last scan, macro state, and active session count.
- `/status help`: explain status fields.
- `/marketstatus`: market-hours status, stream health, reconnect count, last tick, and macro source.

#### Account And Pricing

- `/price <symbol> [--live]`: current bid, ask, spread, and source.
- `/account`: account balance, NAV, margin, and counts.
- `/positions`: open trades with live pip-distance annotations.
- `/orders`: open orders grouped into entries and trade-attached risk orders.

#### Analysis And Scan Control

- `/calendar [today|week] [USD EUR GBP...] [force]`: filtered economic calendar.
- `/scan [force]`: full scan of the entire scan universe.
- `/scan <symbol> [force]`: refresh one scan-universe instrument through the full publish path.
- `/session <symbol> [timeframe]`: published Sydney / Tokyo / London / New York session context.
- `/dayrange <symbol>`: previous-day range and sweep state from `H1`.
- `/pdh <symbol>`: previous day high and sweep flag.
- `/pdl <symbol>`: previous day low and sweep flag.
- `/smc <symbol> [timeframe]`: structure, order blocks, liquidity, and raw spread.
- `/structure <symbol> [timeframe]`: recent BOS / CHOCH.
- `/indicators <symbol> [timeframe] [compact|full]`: published indicator summary.
- `/vwap <symbol> [timeframe] [--anchor D|W|M] [--bands 1,2]`: on-demand anchored VWAP readout.
- `/ob <symbol> [timeframe] [all|mitigated|unmitigated]`: order blocks filtered by mitigation status.

#### Charting And Export

- `/chart <symbol> [timeframe] [--count N] [--mode compact|balanced|full] [--overlays X] [--smc X] [--trade X] [--alert X] [--indicator X]`: render a chart image and send it as a document.
- `/extractor <symbol|all> [count] [timeframes...]`: export bid and ask candle CSV files.

Chart selector families:

- `--overlays clean|smc|indicators`
- `--smc orderblocks|structure|liquidity`
- `--trade positions|orders|sl|tp|gslo`
- `--alert pricealerts`
- `--indicator ema|bollinger|vwap|rsi|macd`

#### Journal, Notes, And History

- `/journal [trade_id] [--instrument <symbol>] [--from <YYYY-MM-DD>] [--to <YYYY-MM-DD>]`: list journal rows or show one trade in detail.
- `/label <trade_id> <text>`: attach or replace a note on a journaled trade.
- `/maemfe [trade_id]`: show open-trade excursion summary or one trade's MAE/MFE detail.
- `/tradehistory [period] [view] [instrument] [page]`: show normalized transaction-backed history and realized PnL.

Supported `/tradehistory` period selectors:

- `day`
- `week`
- `month`
- `today`
- `thisweek`
- `thismonth`
- `custom:YYYY-MM-DD:YYYY-MM-DD`

#### Price Alerts

- `/pricealert <symbol> <price> <above|below> [note]`
- `/listpricealerts`
- `/clearpricealert <id>`

#### Indicator Alerts

- `/indicatoralert <symbol> <timeframe> <indicator> <condition> [threshold] [note]`
- `/indicatoralert defaults`
- `/listindicators`
- `/clearindicator <id>`

Default indicator seeding creates:

- `RSI` 70 and 30 on `H1`
- `STOCH` 80 and 20 on `H1`
- `SMA_CROSS` bullish and bearish cross alerts on `M15`, `H1`, `H4`, and `D`

#### Time Alerts

- `/timealert at <HH:MM> [daily|once] [note]`
- `/timealert at <YYYY-MM-DD> <HH:MM> [once] [note]`
- `/timealert session <london|newyork|market_open> [note]`
- `/listtimealerts`
- `/cleartimealert <id>`
- `/exporttimealerts`
- `/importtimealerts` as a reply to an exported JSON file

#### Runtime Config And Admin

- `/config`: show the persisted runtime-config snapshot.
- `/config chart <line|candlestick>`
- `/config chart_mode <compact|balanced|full>`
- `/config scan_interval <minutes>`
- `/config trade_push <on|off>`
- `/config session_alerts <on|off>`
- `/tradehistory_backfill <YYYY-MM-DD> <YYYY-MM-DD>`: admin-only historical backfill.

## Embedded MCP Surface

### Transport And Auth

- Transport: FastMCP `streamable-http`
- Local URL: `http://127.0.0.1:8080/mcp?api_key=<MCP_HTTP_API_KEY>`
- Bind default: `0.0.0.0:8080`
- Health route: `GET /healthz`
- Auth model: query-parameter API key on every MCP request except `/healthz`

The MCP app shares the same live runtime as Telegram. It reads and writes the same in-memory market state, alert repository, TinyDB store, scheduler, stream task, and OANDA-backed services.

### Resources

The MCP server publishes four JSON resources:

- `marketsignal://capabilities`
- `marketsignal://supported-instruments`
- `marketsignal://alert-defaults`
- `marketsignal://tool-surface`

### Tool Families

#### Runtime And Market

- `get_runtime_status`
- `get_market_status`
- `get_macro_context`
- `get_calendar`

#### Yahoo Finance Research

- `search_yfinance_tickers`
- `get_yfinance_ticker`
- `get_yfinance_history`
- `get_yfinance_news`

These are bounded research helpers and do not replace OANDA as the trading-data source.

#### Scan And Published Analysis State

- `scan_all`
- `scan_instrument`
- `refresh_snapshot`
- `get_session_context`
- `get_day_range`
- `get_previous_day_levels`
- `get_smc_snapshot`
- `get_structure`
- `get_indicators`
- `get_vwap`
- `get_order_blocks`

#### Pricing, Candles, Account, And Orders

- `get_price`
- `get_candles`
- `get_ohlc`
- `get_account_summary`
- `list_transfers`
- `list_open_positions`
- `list_open_orders`

#### Journal, History, And Analytics

- `list_journal_trades`
- `get_journal_trade`
- `get_mae_mfe`
- `get_trade_history`
- `get_trade_stats`
- `get_spread_snapshot`
- `get_correlation`

#### Alerts

- `create_price_alert`
- `list_price_alerts`
- `clear_price_alert`
- `clear_all_price_alerts`
- `replace_alert_grid`
- `create_indicator_alert`
- `seed_default_indicator_alerts`
- `list_indicator_alerts`
- `clear_indicator_alert`
- `clear_all_indicator_alerts`
- `create_time_alert`
- `list_time_alerts`
- `clear_time_alert`
- `list_alert_history`

Behavior notes:

- MCP destructive batch tools require explicit confirmation booleans.
- MCP indicator alerts support `repeat` and `cooloff_minutes`.
- MCP date-window APIs support explicit `start_date` / `end_date` in addition to symbolic periods.
- `get_correlation` is `D` timeframe only in the current implementation.
- `get_candles` and `get_ohlc` accept live OANDA catalog instruments and raw OANDA
  granularities from `S5` through `W`; monthly `M` is intentionally not exposed.
- MCP raw candle reads are direct, on-demand OANDA REST calls and do not use or update
  the analysis candle cache, CSV candles, or TinyDB freshness metadata.
- `force` remains accepted on raw MCP candle tools for compatibility, but direct fetch
  is already the default.
- Published analysis snapshot tools remain limited to `D`, `H4`, `H1`, and `M15`.

### MCP Limitations

The current MCP server does **not** expose:

- chart rendering
- CSV export
- runtime-config mutation
- trade-history backfill
- trade label writes
- Telegram time-alert import / export
- Telegram session authentication

## Persistence And Storage

### TinyDB

By default the runtime uses `data/bot.json` with:

- atomic JSON writes through a temp-file replacement strategy
- a process-level lock file (`data/bot.json.lock`)
- graceful degraded reads when non-critical persistence operations fail

On Railway, attach a volume and either leave `TINYDB_PATH` unset or set it to the mounted volume, such as `TINYDB_PATH=/data/bot.json`. That moves the main store, lock file, candle cache, logs, and chart artifacts under the mounted volume root.

### TinyDB Tables

| Table | Purpose |
| --- | --- |
| `trades` | journaled open and closed trade records |
| `signals` | generic signal records |
| `spread_history` | spread observations and context |
| `cache_metadata` | candle freshness metadata |
| `excursion_samples` | MAE/MFE sample points |
| `price_alerts` | price alerts |
| `indicator_alerts` | indicator alerts |
| `time_alerts` | time alerts |
| `alert_history` | fired alert history |
| `indicator_alert_cursors` | same-candle dedupe for indicator alerts |
| `sessions` | Telegram auth sessions |
| `runtime_config` | persisted runtime overrides |
| `raw_transactions` | raw OANDA transactions |
| `trade_history_events` | normalized trade-history events |
| `trade_history_sync` | incremental sync watermark state |

### CSV Candle Cache

Canonical candles are stored under:

```text
data/cache/<INSTRUMENT>/<TIMEFRAME>.csv
```

This cache is a runtime accelerator, not the source of truth for trade history or journal state.

When `TINYDB_PATH=/data/bot.json`, related runtime paths become:

- `/data/bot.json`
- `/data/bot.json.lock`
- `/data/cache/<INSTRUMENT>/<TIMEFRAME>.csv`
- `/data/logs/bot.log` and `/data/logs/bot.error.log`
- `/data/chart_artifacts/*.png`

### In-Memory State

`MarketStateStore` holds:

- latest published snapshots by `(instrument, timeframe)`
- snapshot history versions (retention default: 5 prior versions plus current)
- order-block tracker state

This state is process-local and rebuilt after restart from fresh scans rather than persisted wholesale.

## Testing

The repository currently contains:

- 55 unit test files
- 13 integration test files
- 6 live test files

### Default Test Targets

`pyproject.toml` configures `pytest` to run unit and integration tests by default:

```powershell
pytest
```

### Explicit Test Commands

```powershell
pytest tests/unit
pytest tests/integration
pytest tests/live -m live
```

Live tests require valid OANDA credentials and hit real external services.

## Repository Layout

| Path | Responsibility |
| --- | --- |
| [`bot/`](./bot) | Telegram runtime, command handlers, parsing, formatting, runtime assembly |
| [`orchestration/`](./orchestration) | scan orchestration, scheduler, cache warming |
| [`providers/`](./providers) | OANDA REST, streaming, candle cache, execution stub |
| [`alerts/`](./alerts) | alert engines, defaults, repository wrappers |
| [`journal/`](./journal) | journal service, trade history sync, stats, MAE/MFE |
| [`charting/`](./charting) | chart request model, overlay assembly, worker-process renderer |
| [`data/`](./data) | calendar, market-hours, macro, CSV persistence, correlation, `yfinance` |
| [`core/`](./core) | shared models, enums, logging, candle policy, instrument registry, market state |
| [`background/`](./background) | stream task, poller task, task supervision |
| [`mcp_server/`](./mcp_server) | FastMCP server factory, auth, adapters |
| [`tests/`](./tests) | unit, integration, and live coverage |
| [`docs/`](./docs) | operator docs, MCP guide, command reference, historical stage plans |

## Operational Notes And Limitations

- The system is intentionally read-only with respect to broker state.
- `providers/oanda_execution.py` is a placeholder and must not be wired into the live runtime without a separate design pass.
- Weekly timeframe aliases are normalized but rejected.
- Scan publication is closed-bar only.
- Closed-market scans are cache-first and may skip publication when no valid cache exists.
- Some docs under `docs/v3_stages/` are historical delivery notes rather than the current runtime contract.
- The authoritative runtime surfaces are the code in `bot/`, `orchestration/`, `providers/`, `journal/`, and `mcp_server/`.

## Useful Companion Docs

- [`docs/COMMANDS.md`](./docs/COMMANDS.md): command-level details and usage notes
- [`docs/MCP_GUIDE.md`](./docs/MCP_GUIDE.md): MCP endpoint details and tool behavior
