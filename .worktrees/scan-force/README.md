# Gold Signal Bot V3

Gold Signal Bot V3 is a Python Telegram bot for **read-only** OANDA market analysis and account monitoring. It connects to an OANDA brokerage account, runs a scheduled multi-timeframe scan pipeline, and delivers analysis, alerts, trade journaling, and account visibility through a Telegram bot interface.

The bot does not place trades. `providers/oanda_execution.py` is a reserved stub only.

---

## Table Of Contents

- [Features](#features)
- [Supported Instruments](#supported-instruments)
- [Installation](#installation)
- [Configuration](#configuration)
- [Running The Bot](#running-the-bot)
- [Authentication](#authentication)
- [How To Use The Current Feature Set](#how-to-use-the-current-feature-set)
- [Commands Reference](#commands-reference)
  - [Session And Auth](#session-and-auth)
  - [Runtime Status](#runtime-status)
  - [Account And Pricing](#account-and-pricing)
  - [Analysis And Scanning](#analysis-and-scanning)
  - [Analysis Helpers](#analysis-helpers)
  - [Charting And Data Export](#charting-and-data-export)
  - [Trade Journal And Excursion Tracking](#trade-journal-and-excursion-tracking)
  - [Trade History](#trade-history)
  - [Price Alerts](#price-alerts)
  - [Indicator Alerts](#indicator-alerts)
  - [Time Alerts](#time-alerts)
  - [Runtime Config](#runtime-config)
  - [Admin Commands](#admin-commands)
- [Background Services](#background-services)
- [Symbol And Timeframe Input](#symbol-and-timeframe-input)
- [Storage](#storage)
- [Architecture](#architecture)
- [Testing](#testing)
- [Repository Layout](#repository-layout)

---

## Features

### Multi-Timeframe Market Analysis

- Automated scheduled scans across M15, H1, H4, and D timeframes for all 12 supported instruments
- Smart Money Concepts (SMC) structure analysis: Break of Structure (BOS), Change of Character (CHOCH), order blocks, and liquidity sweeps
- Higher-Timeframe (HTF) bias scoring with weighted D/H4/H1 votes, alignment percentage, and mixed-freshness detection
- Swing Failure Pattern (SFP) detection on closed bars
- Turtle Soup reversal pattern detection on closed bars
- Opening Range Breakout (ORB) detection is computed on `M15` scan snapshots; there is currently no dedicated `/orb` Telegram command
- Clustered support and resistance levels with configurable pip tolerance
- Fibonacci retracement ladder from published swing context
- TA-Lib and pandas-ta technical indicators: EMA, Bollinger Bands, VWAP, RSI, MACD, Stochastic, and tick-volume analysis
- Three-layer candle cache with in-memory rows, CSV persistence, and TinyDB freshness metadata
- Spread gate: every instrument has explicit registry metadata for typical and maximum spread; no generic fallback
- Chop gate: ADX-based chop detection to filter noise
- Closed-bar analysis only: no detector output is produced from forming candles
- Session context for Sydney, Tokyo, London, and New York sessions
- Previous day high/low tracking with sweep detection
- Daily range calculation in pips
- Read-only trade plan summaries derived from published state, never placing orders

### Live Pricing And Account Monitoring

- Real-time OANDA pricing stream with automatic reconnect and exponential backoff
- Live bid/ask/spread quotes via `/price --live` with stream-first, REST-fallback behavior
- OANDA account summary: balance, NAV, unrealized P/L, margin used, trade and order counts
- Open positions display with entry price, stop loss, take profit, guaranteed stop loss, and live pip-distance annotations
- Open orders grouped into entry orders and trade-attached risk orders

### Trade Journaling And Excursion Tracking

- Automatic trade-state journaling from OANDA account polling
- MAE (Maximum Adverse Excursion) and MFE (Maximum Favorable Excursion) sampling on open trades
- Configurable minimum pip move threshold for excursion recording
- Trade labeling: attach custom notes to any journaled trade
- Per-trade excursion detail and aggregate open-trade excursion summaries

### Transaction-Based Trade History

- Incremental OANDA transaction sync with watermark-based pagination
- Normalized trade lifecycle events: OPEN, CLOSE, PARTIAL_CLOSE
- Realized PnL summaries for day, week, month, today, thisweek, thismonth, or custom date ranges
- All date windows resolved in configurable local timezone (default: Asia/Singapore, UTC+8)
- Paginated output for large result sets
- Instrument filtering on history queries
- Historical backfill from OANDA transaction API with idempotent upsert (re-running is safe)
- Automatic journal-row projection from trade history events

### Alert System

**Price alerts:**

- Create alerts that fire when price crosses a target level
- `above` fires on ask crossing; `below` fires on bid crossing
- Alerts evaluate against the live pricing stream in real time
- Fired alerts push a Telegram notification immediately
- List and clear operations are scoped to the authenticated chat

**Indicator alerts:**

- Automatic evaluation on fresh M15, H1, H4, and D snapshots during open-market scans
- Supported indicators: RSI, Stochastic, MACD, SMA_CROSS
- Conditions: `above`, `below`, `cross_up`, `cross_down`
- Default seeding: RSI 70/30 on H1, Stochastic 80/20 on H1, SMA golden/death cross on M15/H1/H4/D
- Same-candle deduplication persisted across restarts
- Fired alerts push a Telegram notification immediately

**Time alerts:**

- Fixed-time reminders: input in SGT (Asia/Singapore), stored in UTC, supports `daily` or `once`
- Session reminders: `london`, `newyork`, or `market_open` recurring opens
- Evaluated every 60 seconds
- Time alerts are pure reminders; they do not depend on market-open state or run analysis inline

### Background Push Notifications

- Trade-open events: pushed when a new trade appears in the OANDA account
- Trade-close events: pushed with close-reason attribution from read-only transaction enrichment
- Trade-open and trade-close pushes go to `TELEGRAM_CHAT_ID`; user-created alert notifications go back to the chat that created them
- Fired price alerts, indicator alerts, and time-alert reminders all push through the live Telegram runtime
- Push delivery is toggleable via `/config trade_push on|off` and `/config session_alerts on|off`

### Charting

- mplfinance-rendered candlestick and line charts sent as Telegram document images
- Three presentation modes: `compact`, `balanced`, `full`
- Overlay selector families: `--overlays`, `--smc`, `--trade`, `--alert`, `--indicator`
- Configurable candle count (2 to 5000, default 500)
- SMC chart overlays can trigger a targeted single-snapshot refresh when published snapshot state is stale or missing
- Chart captions warn when stale snapshot overlays or cached fallback candles were used
- Default chart style and mode persist via `/config chart` and `/config chart_mode`

### Market Hours And Macro Context

- Category-aware market-hours detection using `pandas_market_calendars` for FX (CME_FX) and metals (CMEGlobex_PreciousMetals)
- Next open/close time reporting
- Closed-market scans use cached candles only and refuse to fabricate freshness provenance
- Bounded VIX and DXY macro status via `yfinance`, exposed through `/status` and `/marketstatus`
- Macro refresh on a configurable interval (default: 1 hour)
- Dynamic market-open warm scheduling

### Economic Calendar

- Forex Factory-style calendar view filtered to HIGH and MEDIUM impact events
- Filterable by currency (USD, EUR, GBP, etc.)
- Time display in SGT (Asia/Singapore)
- Today and week views
- Force-refresh option to bypass cache

### Data Export

- CSV export of bid and ask candle data for any supported instrument
- Configurable timeframes (default: M15, H1, H4, D)
- Single instrument or all instruments in one command

### Runtime Configuration

- Eight runtime knobs adjustable via `/config` without restarting the bot
- Persisted in TinyDB, survives restarts
- Immediate effect on scan scheduling when `scan_interval` is changed

### Security And Sessions

- Password-based authentication via `/start <password>`
- One active session per Telegram user; calling `/start` again replaces the stored session
- Session binding to specific Telegram chat
- Admin user enforcement via `TELEGRAM_ADMIN_IDS`
- All commands except `/start` require an active authenticated session
- Session logout with duration reporting

---

## Supported Instruments

Twelve instruments with explicit registry metadata (pip size, typical spread, max spread, spike multiplier, lot size, category):

| Metals | FX Majors | FX Crosses |
| --- | --- | --- |
| `XAU_USD` (gold) | `EUR_USD` | `EUR_GBP` |
| `XAG_USD` (silver) | `GBP_USD` | `EUR_JPY` |
| | `USD_JPY` | `GBP_JPY` |
| | `AUD_USD` | |
| | `USD_CAD` | |
| | `USD_CHF` | |
| | `NZD_USD` | |

Unknown instruments fail validation. There is no generic spread fallback.

---

## Installation

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

**Prerequisites:**

- Python 3.10 or later
- TA-Lib C library must be installed on the host before the Python `TA-Lib` wrapper will work
- `smartmoneyconcepts` is installed separately with `--no-deps` because its published dependency metadata conflicts with the approved pandas stack

---

## Configuration

Copy `.env.example` to `.env` and fill in the required values.

### Required Secrets

| Variable | Description |
| --- | --- |
| `OANDA_API_KEY` | OANDA v20 REST API key |
| `OANDA_ACCOUNT_ID` | OANDA account identifier |
| `OANDA_ENVIRONMENT` | `practice` or `live` |
| `TELEGRAM_BOT_TOKEN` | Telegram Bot API token from BotFather |
| `TELEGRAM_CHAT_ID` | Telegram chat ID for push notifications |
| `TELEGRAM_BOT_PASSWORD` | Password required for `/start` authentication |
| `TELEGRAM_ADMIN_IDS` | Comma-separated Telegram user IDs for admin commands |

### Runtime Settings

Set in `.env` or as environment variables. Code defaults < `.env` < explicit environment variables.

| Setting | Default | Description |
| --- | --- | --- |
| `LOG_LEVEL` | `INFO` | Logging verbosity: CRITICAL, ERROR, WARNING, INFO, DEBUG |
| `LOG_JSON` | `false` | Emit structured JSON logs when true |
| `DEFAULT_CANDLE_COUNT` | `500` | Default number of candles fetched per request |
| `DEFAULT_SWING_LENGTH` | `10` | Lookback period for swing detection |
| `RUPTURES_PENALTY` | `10.0` | Penalty parameter for ruptures change-point detection |
| `HTF_BIAS_WEIGHT_D` | `0.50` | Weight of the Daily timeframe in HTF bias scoring |
| `HTF_BIAS_WEIGHT_H4` | `0.30` | Weight of the H4 timeframe in HTF bias scoring |
| `HTF_BIAS_WEIGHT_H1` | `0.20` | Weight of the H1 timeframe in HTF bias scoring |
| `HTF_BIAS_NEUTRAL_BAND` | `0.15` | Threshold below which bias is considered neutral |
| `HTF_TRANSITION_WINDOW_D` | `3` | Number of candles for D transition detection |
| `HTF_TRANSITION_WINDOW_H4` | `4` | Number of candles for H4 transition detection |
| `HTF_TRANSITION_WINDOW_H1` | `6` | Number of candles for H1 transition detection |
| `SCAN_INTERVAL_MINUTES` | `5` | Minutes between scheduled scan cycles |
| `POLL_INTERVAL_SECONDS` | `30` | Seconds between OANDA account trade polls (minimum 10) |
| `JOURNAL_TIMEZONE` | `Asia/Singapore` | Timezone for trade history date windows and display |
| `STREAM_INSTRUMENTS` | all 12 instruments | Comma-separated instruments for the live pricing stream |
| `MAE_MFE_MIN_PIP_MOVE` | `0.5` | Minimum pip change to record an excursion sample |
| `ACCOUNT_CURRENCY` | `USD` | 3-letter account base currency |
| `CALENDAR_REFRESH_HOURS` | `1` | Hours between economic calendar cache refreshes |
| `MACRO_REFRESH_HOURS` | `1` | Hours between VIX/DXY macro data refreshes |
| `TINYDB_PATH` | `data/bot.json` | Path to the TinyDB database file |

### Persisted Runtime Overrides

These knobs are adjustable at runtime via `/config` without restarting. They persist in TinyDB:

| Key | Values | Description |
| --- | --- | --- |
| `tolerance` | pip value (float) | S/R clustering tolerance for `/sr` |
| `spread` | pip value (float) | Stricter global spread cap (cannot widen registry limits) |
| `chop` | ADX threshold (float) | Chop gate threshold for scans |
| `chart` | `line`, `candlestick` | Default chart rendering style |
| `chart_mode` | `compact`, `balanced`, `full` | Default chart presentation mode |
| `scan_interval` | minutes (int) | Auto-scan interval; reschedules APScheduler immediately |
| `trade_push` | `on`, `off` | Enable/disable trade-open and trade-close push notifications |
| `session_alerts` | `on`, `off` | Enable/disable session reminder push notifications |

---

## Running The Bot

```bash
python main.py
# or
python -m bot.main
```

**Startup sequence:**

1. Loads and validates all settings from `.env` and environment
2. Configures logging
3. Builds the Telegram application and full dependency graph
4. Bootstraps the transaction-history watermark on first run; performs best-effort incremental trade-history sync
5. Starts the live OANDA pricing stream task
6. Starts APScheduler jobs: scans, London/New York cache warmers, dynamic market-open warmers, calendar refresh, macro refresh, trade polling, transaction-history sync, and time-alert evaluation

---

## Authentication

The bot is fully locked behind password authentication.

1. Send `/start <password>` to the bot in Telegram
2. The bot creates or replaces a session bound to your Telegram chat
3. All other commands require this active session
4. `/help` is not public — it requires authentication
5. `/logout` ends the session and reports how long it was active

Admin commands (like `/tradehistory_backfill`) additionally require your Telegram user ID to be in `TELEGRAM_ADMIN_IDS`.

---

## How To Use The Current Feature Set

| Goal | Use This | Notes |
| --- | --- | --- |
| Authenticate | `/start <password>` | Required before every other command. One active session is stored per Telegram user. |
| Prime scan state | `/scan` or `/scan XAU_USD` | Most analysis commands read published scan state and will tell you to run `/scan` first if nothing has been published yet. |
| Read market structure and bias | `/smc`, `/bias`, `/structure`, `/ob`, `/sfp`, `/turtlesoup`, `/sr`, `/session`, `/fib`, `/dayrange`, `/pdh`, `/pdl`, `/tradeplan` | These are read-only views over published state. Timeframe usually defaults to `H1` when omitted. |
| Watch runtime and broker state | `/status`, `/marketstatus`, `/price XAU_USD --live`, `/account`, `/positions`, `/orders` | Use `/price --live` when you want stream-first pricing with explicit REST fallback. |
| Render charts | `/chart XAU_USD H1 --mode balanced --smc orderblocks,structure --trade positions,sl,tp` | `/chart` can refresh a single snapshot for SMC overlays and will warn if overlays are stale or candles came from cached fallback data. |
| Export raw candles | `/extractor XAU_USD 500 M15 H1 H4 D` | Use `all` instead of one symbol to export every supported instrument. |
| Review journaled trades | `/journal`, `/journal 12345`, `/label 12345 text`, `/maemfe`, `/maemfe 12345` | `/journal` lists the most recent matching trades; `/maemfe` adds live stream-aware excursion and P/L context for open trades. |
| Review transaction history | `/tradehistory day`, `/tradehistory week closed`, `/tradehistory custom:2026-03-01:2026-03-31 all XAU_USD 2` | Every read attempts an incremental transaction sync first, then falls back to stored data with a warning if sync fails. |
| Create alerts | `/pricealert`, `/indicatoralert`, `/indicatoralert defaults`, `/timealert` | List and clear commands are chat-scoped. Indicator alerts evaluate on fresh market-open snapshots only. |
| Change runtime behavior | `/config ...` | Use this for S/R tolerance, spread cap, chop threshold, chart defaults, scan interval, and push toggles without a restart. |

---

## Commands Reference

### Session And Auth

| Command | Usage |
| --- | --- |
| `/start <password>` | Authenticate and create a session. The only command available without a session. |
| `/help` | Show the command reference. Requires an active session. |
| `/logout` | End the current session. Reports session duration. |

### Runtime Status

| Command | Usage |
| --- | --- |
| `/status` | Runtime health: uptime, scheduler state, stream state, last scan kind, active session count, VIX/DXY macro status. |
| `/status help` | Explain the fields shown by `/status`. |
| `/marketstatus` | Market-hours view: current open/closed state per category (FX, metals), next open or close time, stream health, reconnect count, last tick time, VIX/DXY levels. |

### Account And Pricing

| Command | Usage |
| --- | --- |
| `/price <symbol>` | Current bid, ask, spread, and timestamp for one instrument. |
| `/price <symbol> --live` | Prefer the freshest streamed quote; falls back to REST if stream cache is stale or unavailable. Reports which source was used. |
| `/account` | OANDA account summary: balance, NAV, unrealized P/L, margin used/available, open trade count, pending order count. |
| `/positions` | Open trades with entry price, stop loss, take profit, guaranteed stop loss, and live pip-distance when pricing is available. |
| `/orders` | Open orders grouped into entry orders and trade-attached risk orders (SL/TP/GSLO). |

### Analysis And Scanning

Most analysis commands read previously published scan state. Run `/scan` or `/scan <symbol>` first when you need fresh snapshots or bundles.

| Command | Usage |
| --- | --- |
| `/scan` | Run a full scan of all 12 instruments across M15/H1/H4/D. Publishes snapshots and HTF bundles. |
| `/scan <symbol>` | Refresh one instrument through the full publish path. |
| `/smc <symbol> [timeframe]` | SMC summary: market structure (BOS/CHOCH), order-block count, liquidity-sweep count, chop status, spread acceptability. Default timeframe: H1. |
| `/bias <symbol>` | HTF bias: direction (bullish/bearish/neutral), alignment score, per-timeframe votes (D/H4/H1), mixed-freshness flag. |
| `/structure <symbol> [timeframe]` | Recent BOS and CHOCH events from the published snapshot. |
| `/indicators <symbol> [timeframe] [compact\|full]` | Indicator values from the published snapshot. `compact` shows key levels; `full` shows all computed indicators. Default: compact. |
| `/ob <symbol> [timeframe]` | Order-block summary: bullish and bearish OBs with price levels and status. |
| `/sfp <symbol> [timeframe]` | Swing Failure Pattern summary: detected SFPs with direction, level, and bar context. |
| `/turtlesoup <symbol> [timeframe]` | Turtle Soup reversal pattern summary. |
| `/sr <symbol> [timeframe]` | Clustered support and resistance levels. Uses the runtime S/R tolerance override when set via `/config tolerance`. |
| `/session <symbol> [timeframe]` | Trading session context: Sydney, Tokyo, London, New York session ranges and whether each is active. |

### Analysis Helpers

| Command | Usage |
| --- | --- |
| `/dayrange <symbol>` | Previous day high, low, full range in pips, and whether the high or low has been swept today. Sourced from the H1 snapshot. |
| `/pdh <symbol>` | Previous day high level and whether it has been swept. |
| `/pdl <symbol>` | Previous day low level and whether it has been swept. |
| `/fib <symbol> [timeframe]` | Fibonacci retracement ladder from the published swing and retracement context. Shows 0%, 23.6%, 38.2%, 50%, 61.8%, 78.6%, 100% levels. |
| `/tradeplan <symbol>` | Bounded read-only setup summary: bias direction, key levels, S/R, order blocks, and suggested context. Derived from the published bundle plus H1 and M15 snapshots. Never places trades. Never runs detectors inline. |
| `/calendar [today\|week] [USD EUR GBP ...] [force\|refresh]` | Economic calendar filtered to HIGH and MEDIUM impact events. Times shown in SGT. Filterable by one or more currency codes. `force` or `refresh` bypasses the cache. |

### Charting And Data Export

**`/chart <symbol> [timeframe] [options]`**

Renders an mplfinance chart and sends it as a Telegram document image.

Options:

| Flag | Values | Description |
| --- | --- | --- |
| `--count N`, `-n N` | 2 to 5000 (default 500) | Number of candles to render |
| `--mode` | `compact`, `balanced`, `full` | Presentation mode (overrides `/config chart_mode`) |
| `--overlays` | `clean`, `smc`, `indicators` | Overlay preset |
| `--smc` | `orderblocks`, `structure`, `liquidity` | Individual SMC overlays |
| `--trade` | `positions`, `orders`, `sl`, `tp`, `gslo` | Trade-related overlays |
| `--alert` | `pricealerts` | Price alert level lines |
| `--indicator` | `ema`, `bollinger`, `vwap`, `rsi`, `macd` | Indicator overlays |

Mode defaults when no selectors are specified:

- **compact**: orderblocks, positions
- **balanced**: orderblocks, positions, orders, sl, tp, gslo, pricealerts
- **full**: all supported smc, trade, alert, and indicator selectors

The caption includes a warning when stale snapshot overlays or cached fallback candles were used.

If you request SMC overlays and the published snapshot is stale or missing, `/chart` attempts a targeted single-snapshot refresh for that instrument/timeframe before rendering.

**`/extractor <symbol|all> [count] [timeframes...]`**

Export bid and ask candle CSV files. Default timeframes: M15, H1, H4, D. Specify `all` to export every supported instrument.

### Trade Journal And Excursion Tracking

| Command | Usage |
| --- | --- |
| `/journal` | List the 10 most recent matching journaled trades with summary information. |
| `/journal <trade_id>` | Show one trade in detail: entry, exit, P/L, note, timestamps, and MAE/MFE summary when samples exist. |
| `/journal --instrument <symbol>` | Filter journal list by instrument. |
| `/journal --from <YYYY-MM-DD> --to <YYYY-MM-DD>` | Filter journal list by date range. |
| `/label <trade_id> <text>` | Add or replace the stored note for one journaled trade. |
| `/maemfe` | Show excursion summaries for all open trades, including live P/L in pips when stream pricing is available. |
| `/maemfe <trade_id>` | Show full MAE/MFE detail for one trade: worst drawdown (MAE), best run-up (MFE), sample history. |

### Trade History

**`/tradehistory [period] [view] [instrument] [page]`**

Shows transaction-based trade lifecycle history and realized PnL.

| Argument | Values | Default |
| --- | --- | --- |
| `period` | `day`, `week`, `month`, `today`, `thisweek`, `thismonth`, `custom:YYYY-MM-DD:YYYY-MM-DD` | `day` |
| `view` | `all`, `opened`, `closed` | `all` |
| `instrument` | any supported instrument, e.g. `XAU_USD` | no filter |
| `page` | positive integer | `1` |

Only OPEN, CLOSE, and PARTIAL_CLOSE lifecycle events are shown. Pending orders, order create/cancel events, rejected orders, and account-admin transactions are excluded.

Date windows are resolved in `JOURNAL_TIMEZONE` (default: Asia/Singapore, UTC+8).
Page size is 20 rows.

Every `/tradehistory` read attempts an incremental OANDA transaction sync first. If that sync fails but stored history already exists, the response falls back to stored data and includes a warning.

Examples:

```text
/tradehistory day
/tradehistory week closed
/tradehistory month all XAU_USD
/tradehistory custom:2026-03-01:2026-03-31 closed XAU_USD
/tradehistory today opened
/tradehistory thismonth all EUR_USD 2
```

### Price Alerts

| Command | Usage |
| --- | --- |
| `/pricealert <symbol> <price> <above\|below> [note]` | Create a pending price alert. `above` fires when ask crosses above the level; `below` fires when bid crosses below. |
| `/listpricealerts` | List all pending price alerts for your chat. |
| `/clearpricealert <id>` | Cancel one pending price alert by its ID. |

Price alerts evaluate against the live pricing stream in real time. When an alert fires, a Telegram notification is pushed immediately.
Price alerts are fire-once. Each alert arms only after price is first seen on the non-trigger side, which prevents an already-crossed level from firing immediately on creation.

### Indicator Alerts

| Command | Usage |
| --- | --- |
| `/indicatoralert <symbol> <timeframe> <indicator> <condition> [threshold] [note]` | Create an indicator alert. |
| `/indicatoralert defaults` | Seed the default alert set for all supported instruments. |
| `/listindicators` | List all active indicator alerts for your chat. |
| `/clearindicator <id>` | Cancel one indicator alert by its ID. |

**Supported indicators:** `RSI`, `STOCH`, `MACD`, `SMA_CROSS`

**Supported timeframes:** `M15`, `H1`, `H4`, `D`

**Conditions:**

- `above <threshold>` — fires when the indicator value exceeds the threshold
- `below <threshold>` — fires when the indicator value drops below the threshold
- `cross_up` — fires on a bullish cross using the indicator's baseline logic
- `cross_down` — fires on a bearish cross using the indicator's baseline logic

**Default seeding** (`/indicatoralert defaults`) creates:

- RSI above 70 and below 30 on H1
- Stochastic above 80 and below 20 on H1
- SMA golden cross and death cross on M15, H1, H4, and D

Indicator alerts evaluate only on fresh snapshots while the market is open. Same-candle deduplication is persisted across restarts.

### Time Alerts

| Command | Usage |
| --- | --- |
| `/timealert at <HH:MM> [daily\|once] [note]` | Create a fixed-time reminder. Input time is interpreted in Asia/Singapore (SGT) and stored in UTC. |
| `/timealert session <london\|newyork\|market_open> [note]` | Create a recurring session-open reminder. |
| `/listtimealerts` | List active time alerts for your chat. |
| `/cleartimealert <id>` | Cancel one active time alert by its ID. |

Time alerts are pure reminders. They fire on schedule regardless of market state and do not run analysis inline.
Session reminders can be globally muted with `/config session_alerts off` without deleting the stored alerts.

### Runtime Config

| Command | Usage |
| --- | --- |
| `/config` | Show the current persisted runtime-config snapshot with all override values. |
| `/config tolerance <pips>` | Set support/resistance clustering tolerance. |
| `/config spread <pips>` | Set a stricter global spread cap (cannot widen instrument registry limits). |
| `/config chop <value>` | Override the chop threshold for future scans. |
| `/config chart <line\|candlestick>` | Set the default chart rendering style. |
| `/config chart_mode <compact\|balanced\|full>` | Set the default chart presentation mode. Explicit chart selectors still override the mode. |
| `/config scan_interval <minutes>` | Persist a new auto-scan interval and reschedule the APScheduler job immediately. |
| `/config trade_push <on\|off>` | Enable or disable trade-open and trade-close Telegram pushes. |
| `/config session_alerts <on\|off>` | Enable or disable session reminder pushes. |

### Admin Commands

| Command | Usage |
| --- | --- |
| `/tradehistory_backfill <YYYY-MM-DD> <YYYY-MM-DD>` | Admin-only. Pull OANDA transaction history for the inclusive local-date range, normalize lifecycle events, upsert raw and normalized rows, and project missing journal summaries. |

The backfill is idempotent — re-running the same range is safe and will not duplicate rows. Ranges are chunked internally to stay within OANDA transaction-list API limits.

CLI equivalent:

```bash
python -m journal.trade_history_service backfill-trades --start 2025-01-01 --end 2026-04-01 --tz Asia/Singapore
```

---

## Background Services

These run automatically after startup without user interaction:

| Service | Interval | Description |
| --- | --- | --- |
| Scan pipeline | `SCAN_INTERVAL_MINUTES` (default 5 min) | Fetches candles, runs all detectors, publishes M15/H1/H4/D snapshots and HTF bundles |
| London cache warmer | daily at 08:00 UTC | Pre-warms scan candles for supported instruments and timeframes when the relevant category is open |
| New York cache warmer | daily at 13:00 UTC | Pre-warms scan candles for supported instruments and timeframes when the relevant category is open |
| Market-open cache warmer | dynamic one-shot, rescheduled after each run | Warms scan candles at the next detected cross-category market open |
| Trade poller | `POLL_INTERVAL_SECONDS` (default 30 sec) | Polls OANDA account for open trades, updates TinyDB, samples MAE/MFE excursions |
| Price stream | continuous | Maintains a persistent OANDA pricing stream with automatic reconnect and exponential backoff |
| Price alert evaluation | on each stream tick | Evaluates pending price alerts against live bid/ask from the stream |
| Indicator alert evaluation | on each fresh snapshot | Evaluates indicator alerts against newly published M15/H1/H4/D snapshots when market is open |
| Time alert evaluation | every 60 seconds | Checks fixed-time and session-open reminders |
| Calendar refresh | `CALENDAR_REFRESH_HOURS` (default 1 hr) | Refreshes the economic calendar cache |
| Macro refresh | `MACRO_REFRESH_HOURS` (default 1 hr) | Refreshes VIX and DXY macro data via yfinance |
| Transaction-history sync | on startup + every `POLL_INTERVAL_SECONDS` | Incrementally syncs OANDA transactions and normalizes trade lifecycle events |
| Trade push delivery | on trade state change | Sends Telegram notifications for trade-open and trade-close events |

---

## Symbol And Timeframe Input

### Symbol Aliases

The parser is flexible with symbol input:

| You Type | Resolved To |
| --- | --- |
| `gold` | `XAU_USD` |
| `silver` | `XAG_USD` |
| `eurusd` | `EUR_USD` |
| `EUR/USD` | `EUR_USD` |
| `eur-usd` | `EUR_USD` |
| `EUR USD` | `EUR_USD` |
| `GBPJPY` | `GBP_JPY` |

Any 6-letter alphabetic input without separators is split after the first three characters.

### Timeframe Aliases

| You Type | Resolved To |
| --- | --- |
| `1m`, `m1` | `M1` |
| `5m`, `m5` | `M5` |
| `15m`, `m15` | `M15` |
| `30m`, `m30` | `M30` |
| `1h`, `h1` | `H1` |
| `4h`, `h4` | `H4` |
| `1d`, `d`, `day`, `daily` | `D` |
| `1w`, `w`, `weekly` | `W` (normalized then rejected as unsupported) |

### Timeframe Availability By Feature

| Feature | Accepted Timeframes |
| --- | --- |
| Command parsing | M1, M5, M15, M30, H1, H4, D |
| Scheduled scans | M15, H1, H4, D |
| HTF bundle assembly | D, H4, H1 |
| Chart rendering | M1, M5, M15, M30, H1, H4, D |
| Indicator alerts | M15, H1, H4, D |
| Extractor | M1, M5, M15, M30, H1, H4, D |

Default timeframe for most analysis commands: **H1** when you omit it. User-facing commands generally require you to provide a symbol explicitly.

---

## Storage

All runtime state is local:

| Location | Contents |
| --- | --- |
| `data/bot.json` | TinyDB database (configurable via `TINYDB_PATH`) |
| `data/bot.json.lock` | Single-writer runtime lock; prevents cross-process reuse |
| `data/cache/<instrument>/<timeframe>.csv` | CSV candle cache files |
| `<TINYDB parent>/chart_artifacts/*.png` | Temporary `/chart` render artifacts created beside the configured TinyDB file |

The candle cache itself is three-layered: in-memory rows, CSV files on disk, and TinyDB freshness metadata.

### TinyDB Tables

| Table | Purpose |
| --- | --- |
| `trades` | Journaled trade records from account polling |
| `signals` | Published scan signal snapshots |
| `spread_history` | Historical spread observations |
| `cache_metadata` | CSV candle cache provenance and freshness |
| `excursion_samples` | MAE/MFE pip excursion samples |
| `price_alerts` | User-created price alert definitions and fired state |
| `indicator_alerts` | User-created indicator alert definitions and fired state |
| `time_alerts` | User-created time alert and session reminder definitions |
| `indicator_alert_cursors` | Same-candle deduplication state for indicator alerts |
| `raw_transactions` | Raw OANDA transaction records from sync |
| `trade_history_events` | Normalized trade lifecycle events (OPEN/CLOSE/PARTIAL_CLOSE) |
| `trade_history_sync` | Watermark state for incremental transaction sync |
| `sessions` | Authenticated Telegram sessions |
| `runtime_config` | Persisted runtime override values |

---

## Architecture

### Analysis Pipeline

```text
OANDA REST API -> CandleCache (memory + CSV + TinyDB metadata) -> ScanOrchestrator -> MarketStateStore -> Command Handlers
```

### Trade Helper Pipeline

```text
OANDA Account REST + Pricing Stream -> TradePollerTask + PriceStreamTask -> TinyDB -> Command Handlers
```

### Trade History Pipeline

```text
OANDA Transactions -> OandaHistoryClient + TradeHistoryService -> TinyDB (raw/history/sync) -> /tradehistory + /journal
```

**Key design rules:**

- Most analysis commands read published state only and tell you to run `/scan` first when data is missing; `/chart` is the main exception and may perform a targeted snapshot refresh for SMC overlays
- Analysis modules never import account-state or execution paths
- No detector output on forming candles — closed-bar analysis only
- No inline detector execution inside command handlers
- Blocking provider calls from async handlers go through `asyncio.to_thread()`
- Closed-market scans are cache-only; no fabricated freshness provenance
- Trade-history reads attempt an incremental sync before query; backfill is a separate admin workflow
- APScheduler jobs run single-instance with coalescing and failure backoff rather than stacking overlapping runs
- All public models are frozen Pydantic `BaseModel` with `extra="forbid"`
- All datetimes are timezone-aware UTC
- OANDA volume is tick count; always `tick_volume`, never `volume`

---

## Testing

```bash
pytest                                    # unit + integration (default)
pytest tests/unit -v                      # unit only
pytest tests/integration -v              # integration only
pytest tests/live -v -m live             # live tests (real OANDA credentials required)
pytest tests/unit/test_foo.py -v         # single file
pytest tests/unit/test_foo.py::test_bar  # single test
```

`asyncio_mode = "auto"` is set in `pyproject.toml` — no `@pytest.mark.asyncio` needed.

**Test suites:**

| Suite | Location | Description |
| --- | --- | --- |
| Unit | `tests/unit/` | Deterministic component tests with hand-rolled fakes |
| Integration | `tests/integration/` | Mocked-provider tests over real persistence and orchestration |
| Live | `tests/live/` | Real OANDA API tests, excluded from default runs |

---

## Repository Layout

```text
alerts/          Alert repositories and evaluation engines (price, indicator, time)
background/      Stream task, poller task, and task supervisor
bot/             Telegram application, command handlers, parsing, formatting, runtime wiring
charting/        Chart request builder and mplfinance renderer
config/          Pydantic settings
core/            Models, enums, events, candle policy, instrument registry, state store, logging
data/            Calendar client, market hours, macro context, CSV persistence, TinyDB persistence
filters/         Spread and chop gate implementations
indicators/      TA-Lib, pandas-ta, and tick-volume indicator wrappers
journal/         Trade repository, excursion repository, trade history service, trade normalizer
notifications/   Notifier protocol, Telegram notifier, delivery service
orchestration/   Scan orchestrator, scheduler, cache warmer
providers/       OANDA market-data, account, stream, history, cache, and execution stub
smc/             SMC adapter, HTF bias, SFP, Turtle Soup, ORB detectors
tracking/        MAE/MFE excursion tracker
tests/           Unit, integration, and live test suites
docs/            Commands reference, glossary, tracker, and historical planning documents
```

---

## Not Yet Implemented

- Broker write paths or automated trade execution
- Admin commands: `/security`, `/sessions`, `/ban`, `/unban`, `/mute`, `/unmute`, `/override`
- CI/CD pipeline, deployment automation, or release workflow files
