# Bot Commands Reference

This file describes the command surface that is implemented in the current bot runtime.

Authentication note:

- Only `/start <password>` is available without an active session.
- `/help` requires authentication.

Normalization note:

- Symbol and timeframe aliases are described in [GLOSSARY.md](./GLOSSARY.md).
- Weekly aliases normalize to `W` and are then rejected by command validation.

## Authentication And Session

| Command | Description |
| --- | --- |
| `/start <password>` | Authenticate the Telegram user and create or replace the active session. |
| `/help` | Show the authenticated command reference. |
| `/logout` | End the current session and report session duration. |

## Runtime Status

| Command | Description |
| --- | --- |
| `/status` | Runtime health summary: uptime, scheduler state, stream state, last scan kind, active session count. |
| `/status help` | Explain the fields shown by `/status`. |
| `/marketstatus` | Current market-hours view, next open or close, stream health, reconnect count, last tick time. |

## Account And Orders

| Command | Description |
| --- | --- |
| `/price <symbol> [--live]` | Current bid, ask, spread, and timestamp for one supported instrument. `--live` prefers a fresh streamed quote and reports the REST fallback when the stream cache is stale or unavailable. |
| `/account` | OANDA account summary: balance, NAV, margin, P/L, trade and order counts. |
| `/positions` | Open trades with entry, SL, TP, GSLO, and live pip-distance annotations when pricing is available. |
| `/orders` | Open orders grouped into entry orders and trade-attached risk orders. |

## Analysis And Scanning

| Command | Description |
| --- | --- |
| `/session <symbol> [timeframe]` | Trading session context from the published snapshot for Sydney, Tokyo, London, and New York. |
| `/dayrange <symbol>` | Previous day high, previous day low, full range in pips, and high or low sweep flags from the published `H1` snapshot context. |
| `/pdh <symbol>` | Previous day high and whether that level has already been swept. |
| `/pdl <symbol>` | Previous day low and whether that level has already been swept. |
| `/calendar [today|week] [USD EUR GBP...] [force]` | Calendar view in SGT, filtered to HIGH and MEDIUM events for the requested currencies. |
| `/scan [force]` | Run a full scan of all supported instruments. Pass `force` to bypass the closed-market gate and fetch from OANDA when no cache exists. |
| `/scan <symbol> [force]` | Refresh one instrument through the full publish path. Pass `force` to bypass the closed-market gate. |
| `/smc <symbol> [timeframe]` | Structure, order-block count, liquidity count, chop status, and spread acceptability from one snapshot. |
| `/bias <symbol>` | HTF bias direction, alignment score, timeframe votes, and mixed-freshness flag from one bundle. |
| `/tradeplan <symbol>` | Bounded read-only setup summary derived from the published bundle plus `H1` and `M15` snapshots. It never places trades and never runs detectors inline. |
| `/structure <symbol> [timeframe]` | Recent BOS and CHOCH entries from one snapshot. |
| `/indicators <symbol> [timeframe] [compact|full]` | Compact or full indicator list from the published indicator summary. |
| `/ob <symbol> [timeframe]` | Order-block summary for the requested snapshot. |
| `/sfp <symbol> [timeframe]` | Swing failure pattern summary for the requested snapshot. |
| `/turtlesoup <symbol> [timeframe]` | Turtle Soup summary for the requested snapshot. |
| `/sr <symbol> [timeframe]` | Clustered support and resistance levels using the runtime S/R tolerance override when present. |
| `/fib <symbol> [timeframe]` | Compact Fibonacci ladder built from the published swing and retracement context for the requested snapshot. |

## Charting And Data Export

| Command | Description |
| --- | --- |
| `/chart <symbol> [timeframe] [--count N] [--mode compact|balanced|full] [--overlays X] [--smc X] [--trade X] [--alert X] [--indicator X]` | Render an `mplfinance` chart in a worker process and send the image back as a document. |
| `/extractor <symbol|all> [count] [timeframes...]` | Export bid and ask candle CSV files. Default timeframes are `M15 H1 H4 D`. |

Chart selector families:

- `--overlays clean|smc|indicators`
- `--smc orderblocks|structure|liquidity`
- `--trade positions|orders|sl|tp|gslo`
- `--alert pricealerts`
- `--indicator ema|bollinger|vwap|rsi|macd`

Chart notes:

- default count is `500`
- valid `--count` range is `2..5000`
- default style comes from `/config chart`
- default mode comes from `/config chart_mode`
- if no selector is supplied, the renderer uses the mode-specific default bundle:
  - `compact`: `orderblocks`, `positions`
  - `balanced`: `orderblocks`, `positions`, `orders`, `sl`, `tp`, `gslo`, `pricealerts`
  - `full`: all supported `smc`, `trade`, `alert`, and `indicator` selectors
- the returned document caption includes a warning when the chart used cached fallback candles or stale snapshot overlays

## Journal And MAE/MFE

| Command | Description |
| --- | --- |
| `/journal [trade_id] [--instrument <symbol>] [--from <YYYY-MM-DD>] [--to <YYYY-MM-DD>]` | List journaled trades or show one trade in detail. |
| `/tradehistory [period] [view] [instrument] [page]` | Show transaction-based trade lifecycle history and realized PnL for the requested window. Rows only include `OPEN`, `CLOSE`, and `PARTIAL_CLOSE` events. |
| `/label <trade_id> <text>` | Add or replace the stored note for one journaled trade. |
| `/maemfe [trade_id]` | Show open-trade excursion summaries or full MAE/MFE detail for one trade. |

`/tradehistory` argument notes:

- `period`: `day`, `week`, `month`, `today`, `thisweek`, `thismonth`, or `custom:YYYY-MM-DD:YYYY-MM-DD`
- `view`: `all`, `opened`, or `closed`
- `instrument`: exact OANDA instrument such as `XAU_USD`
- `page`: positive integer, default `1`
- defaults are `day`, `all`, no instrument filter, and page `1`
- realized PnL windows are resolved in `JOURNAL_TIMEZONE` and queried against OANDA transaction history
- pending orders, order create or cancel events, rejected orders, and unrelated account-admin transactions are never shown as trade-history rows

## Alerts

### Price alerts

| Command | Description |
| --- | --- |
| `/pricealert <symbol> <price> <above|below> [note]` | Create one pending price alert. `above` fires on ask crossing, `below` fires on bid crossing. |
| `/listpricealerts` | List pending price alerts. |
| `/clearpricealert <id>` | Cancel one pending price alert. |

### Indicator alerts

| Command | Description |
| --- | --- |
| `/indicatoralert <symbol> <timeframe> <indicator> <condition> [threshold] [note]` | Create an automatically evaluated indicator alert. Supported indicators: `RSI`, `STOCH`, `MACD`, `SMA_CROSS`. Supported alert timeframes: `M15`, `H1`, `H4`, `D`. |
| `/indicatoralert defaults` | Seed the default alert set for all supported instruments. |
| `/listindicators` | List active indicator alerts. |
| `/clearindicator <id>` | Cancel one indicator alert. |

Indicator alert notes:

- `above` and `below` use the supplied threshold
- `cross_up` and `cross_down` use the indicator baseline logic in the alert engine
- default seeding currently creates:
  - `RSI` 70 and 30 on `H1`
  - `STOCH` 80 and 20 on `H1`
  - `SMA_CROSS` golden and death cross alerts on `M15`, `H1`, `H4`, and `D`
- list and clear operations are scoped to the authenticated chat that owns the alert

Current alert-runtime behavior:

- fired price alerts, fired indicator alerts, time-alert reminders, and trade-open and trade-close lifecycle events dispatch Telegram notifications through the live runtime
- indicator alerts only evaluate on fresh `M15`, `H1`, `H4`, and `D` snapshots while the market is open
- closed-market scan and refresh paths are cache-only by default; pass `force` to fetch from OANDA when no cache exists. Live prices and alerts are never fetched when the market is closed, even with `force`.

### Time alerts

| Command | Description |
| --- | --- |
| `/timealert at <HH:MM> [daily|once] [note]` | Create a fixed-time reminder. Input time is interpreted in `Asia/Singapore` and stored in UTC. |
| `/timealert session <london|newyork|market_open> [note]` | Create a recurring session-open reminder. Session opens are evaluated in UTC. |
| `/listtimealerts` | List active time alerts for the authenticated chat. |
| `/cleartimealert <id>` | Cancel one active time alert owned by the authenticated chat. |

Time-alert notes:

- fixed-time reminders support `daily` and `once`
- session reminders support `london`, `newyork`, and `market_open`
- time alerts are reminders only; they do not depend on market-open state and they do not run analysis inline
- list and clear operations are scoped to the authenticated chat that owns the alert

## Runtime Config

| Command | Description |
| --- | --- |
| `/config` | Show the persisted runtime-config snapshot. |
| `/config tolerance <pips>` | Set support/resistance clustering tolerance. |
| `/config spread <pips>` | Set a stricter global spread cap without widening instrument registry limits. |
| `/config chop <value>` | Override the chop threshold used by future scans and refreshes. |
| `/config chart <line|candlestick>` | Set the default chart style. |
| `/config chart_mode <compact|balanced|full>` | Set the default chart presentation mode. Explicit chart selectors still win over the mode defaults. |
| `/config scan_interval <minutes>` | Persist a new auto-scan interval and reschedule the APScheduler job immediately. |
| `/config trade_push <on|off>` | Enable or disable background trade-open and trade-close Telegram pushes. |
| `/config session_alerts <on|off>` | Enable or disable background session reminder pushes. |

## Admin Journal Maintenance

| Command | Description |
| --- | --- |
| `/tradehistory_backfill <YYYY-MM-DD> <YYYY-MM-DD>` | Admin-only historical trade backfill. Pulls OANDA transaction history for the inclusive local-date range, normalizes lifecycle events, upserts raw and normalized rows, and projects missing journal summaries. |

Backfill notes:

- the command is idempotent; re-running the same range updates existing records instead of duplicating them
- ranges are chunked internally to stay within OANDA transaction-list limits
- the same functionality is also available through the CLI: `python -m journal.trade_history_service backfill-trades --start 2025-01-01 --end 2026-04-01 --tz Asia/Singapore`

## Not Implemented In The Current Bot

These commands appear in older planning docs but are not registered in the current runtime:

- `/security`
- `/sessions`
- `/ban`
- `/unban`
- `/mute`
- `/unmute`
- `/override`

These aliases from older docs are also not current:

- `/listalerts` is not a command; use `/listpricealerts`
- `/clearalerts` is not a command; use `/clearpricealert <id>`

## General Notes

- Supported timeframes in command parsing are `M1`, `M5`, `M15`, `M30`, `H1`, `H4`, and `D`.
- Automatic indicator alerts are narrower than generic parsing: only `M15`, `H1`, `H4`, and `D` are accepted for `/indicatoralert`.
- Weekly aliases are normalized and then rejected.
- Scans publish `M15`, `H1`, `H4`, and `D` snapshots plus HTF bundles.
- Commands use published state first and only trigger targeted refresh when needed.
- The bot is read-only with respect to OANDA broker state.
