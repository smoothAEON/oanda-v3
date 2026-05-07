# MCP Guide

Gold Signal Bot V3 includes an embedded FastMCP server. It exposes the same live runtime used by the Telegram bot so an MCP client can inspect market data, account state, journal/history, alerts, and sanitized evidence snapshots.

The MCP surface is intentionally LLM-first. The bot serves fresh structured evidence; the LLM forms the market opinion.

## Quick Start

Set the MCP environment variables:

```text
MCP_HTTP_ENABLED=true
MCP_HTTP_API_KEY=your_secret_here
```

Then start the normal bot runtime:

```powershell
python bootstrap.py
```

Local health check:

```text
GET http://127.0.0.1:8080/healthz
```

Local MCP endpoint:

```text
http://127.0.0.1:8080/mcp?api_key=<MCP_HTTP_API_KEY>
```

On Railway, keep the service command as `python bootstrap.py`. Railway deployments bind to `0.0.0.0:${PORT}` when Railway environment variables are present, so use the public Railway URL for `/healthz` and `/mcp?api_key=...`.

## Transport And Auth

| Item | Value |
| --- | --- |
| Transport | FastMCP `streamable-http` |
| Default bind | `0.0.0.0:8080` |
| Default MCP path | `/mcp` |
| Health route | `GET /healthz` |
| Auth | `api_key` query parameter on every MCP request except `/healthz` |

Environment settings:

| Variable | Default | Notes |
| --- | --- | --- |
| `MCP_HTTP_ENABLED` | `false` | Starts the embedded MCP server next to Telegram polling. |
| `MCP_HTTP_HOST` | `0.0.0.0` | Use `127.0.0.1` for local-only access. |
| `MCP_HTTP_PORT` | `8080` | Also accepts `PORT`; Railway `PORT` wins. |
| `MCP_HTTP_PATH` | `/mcp` | Streamable HTTP path. |
| `MCP_HTTP_API_KEY` | none | Required when MCP is enabled. |
| `MCP_DEFAULT_CHAT_ID` | unset | Falls back to `TELEGRAM_CHAT_ID`. Alert tools use this chat scope. |

The auth model is simple query-parameter auth. Keep the endpoint private or behind a trusted gateway if it is exposed outside localhost or Railway's intended service boundary.

## Runtime Model

The MCP app shares the Telegram bot's live runtime. It reads and writes the same in-memory market state, alert repository, TinyDB store, scheduler, stream task, and OANDA-backed services.

Important boundaries:

- Broker operations are read-only. MCP does not place, modify, or close orders.
- Scan and alert tools mutate local bot state, not broker state.
- Alert tools are scoped to the MCP default chat.
- Snapshot tools read the scan-published state and may refresh it through the same scan path.
- Raw candle tools can read any live OANDA account instrument from the broker catalog.

## Resources

The server publishes four JSON resources:

| Resource | Purpose |
| --- | --- |
| `goldsignal://capabilities` | Transport, candle limits, published snapshot frames, and read/write surface summary. |
| `goldsignal://supported-instruments` | Aliases, scan instruments, registry metadata, and live OANDA catalog instruments. |
| `goldsignal://alert-defaults` | Indicator seed defaults and time-alert timezone. |
| `goldsignal://tool-surface` | Current tool names and descriptions from `mcp_server/server.py`. |

Use `goldsignal://tool-surface` as the source of truth when a client needs to confirm the live tool list.

## Recommended LLM Flow

Use small tools rather than asking the bot for a final opinion.

1. Call `get_runtime_status` and `get_market_status` for scheduler, stream, market-hours, macro, calendar, and freshness context.
2. Call `get_calendar` or `get_macro_context` when event risk or macro inputs matter.
3. Call `get_candles` or `get_ohlc` for the raw OANDA timeframe evidence requested by the user.
4. Call `get_price` and `get_spread_snapshot` when current bid/ask, spread, or live-stream state matters.
5. Call account, position, order, journal, trade-history, transfer, trade-stat, and alert-history tools for operational context.
6. Use published evidence tools only as supporting evidence on scan-published frames.
7. Separate supportive, conflicting, missing, and stale evidence before forming an opinion.

Do not expect MCP to return final trading conclusions. The removed pack and opinion tools are not part of the active surface:

- `get_market_context_pack`
- `get_instrument_context_pack`
- `get_historical_bars`
- `get_htf_bias`
- `get_trade_plan`
- `get_sfp`
- `get_turtle_soup`
- `get_support_resistance`
- `get_fibonacci`

MCP evidence tools strip final-decision fields such as `bias`, `valid`, `setup`, `entry`, `target`, `invalidation`, `reward_risk`, `score`, `confidence`, `recommendation`, and `trade_plan`.

Directional evidence is still allowed when it names the evidence source:

- structure breaks use `break_side`
- zones and order blocks use `zone_side`
- liquidity levels use `liquidity_side`
- positions use `position_side`
- orders use `order_side`
- retracements use `retracement_side`

## Tool Surface

### Runtime, Market, Macro, And Calendar

| Tool | Notes |
| --- | --- |
| `get_runtime_status()` | Scheduler, stream, task, and last-scan health. |
| `get_market_status()` | Market-hours, stream, macro, and calendar status. |
| `get_macro_context(force=false)` | Bounded macro snapshot. `force=true` refreshes immediately. |
| `get_calendar(scope='today', currencies=None, force=false)` | HIGH and MEDIUM impact events for `today` or `week`; currency filters are 3-letter codes. |

The bounded macro context covers `VIX`, `DXY`, `CL`, `SPX`, and `US10Y` through the runtime macro cache:

| Macro key | Source symbol |
| --- | --- |
| `vix` | `^VIX` |
| `dxy` | `DX-Y.NYB` |
| `cl` | `CL=F` |
| `spx` | `^GSPC` |
| `us10y` | `^TNX` |

### Yahoo Finance Research

| Tool | Notes |
| --- | --- |
| `search_yfinance_tickers(query, limit=8, news_count=0, enable_fuzzy=false)` | Symbol lookup plus optional related query news. |
| `get_yfinance_ticker(symbol, include_news=false, news_limit=5)` | Quote, profile, calendar, options, and optional news. |
| `get_yfinance_history(symbol, period='1mo', interval='1d', start=None, end=None, prepost=false, actions=false, auto_adjust=true, max_rows=250)` | Bounded OHLCV history. |
| `get_yfinance_news(symbol, limit=8)` | Recent symbol-specific news. |

These are research helpers. They do not replace OANDA pricing for trading-facing reads.

### Raw OANDA Market Data

| Tool | Notes |
| --- | --- |
| `get_candles(instrument, timeframe='H1', count=None, force=false)` | Direct, on-demand, no-cache closed mid-price OANDA OHLC bars. |
| `get_ohlc(instrument, timeframe='H1', count=None, price_component='mid', force=false)` | Direct, on-demand, no-cache mid or bid/ask OHLC bars. `price_component` is `mid` or `bid_ask`. |
| `get_price(instrument, prefer_live=false)` | Current bid/ask pricing. Default uses REST; `prefer_live=true` tries fresh stream data first. |
| `get_spread_snapshot(instrument, include_history=false, history_limit=20, prefer_live=true, require_live=true)` | Raw bid, ask, spread, source, and optional recent spread history. |

Raw candle rules:

- Instrument scope is the live OANDA account catalog.
- Supported granularities are `S5`, `S10`, `S15`, `S30`, `M1`, `M2`, `M4`, `M5`, `M10`, `M15`, `M30`, `H1`, `H2`, `H3`, `H4`, `H6`, `H8`, `H12`, `D`, and `W`.
- Monthly `M` is intentionally not exposed.
- `count` defaults to `DEFAULT_CANDLE_COUNT` and is capped at OANDA's 5000-candle maximum.
- `get_candles` and `get_ohlc` always read directly from OANDA when called; they do not use or update the analysis candle cache, CSV candles, or TinyDB freshness metadata.
- `force=true` is retained for compatibility and has no additional effect for raw MCP candle reads.
- Raw candle responses do not return candle-cache freshness metadata; use published snapshot tools when cache freshness matters.
- OANDA daily and weekly alignment defaults are used: `dailyAlignment=17`, `alignmentTimezone=America/New_York`, `weeklyAlignment=Friday`.

Available raw candle timeframes:

| Timeframe | Duration | Accepted input aliases | Notes |
| --- | --- | --- | --- |
| `S5` | 5 seconds | `S5`, `5s` | Smallest exposed OANDA granularity. |
| `S10` | 10 seconds | `S10`, `10s` | Raw candle reads only. |
| `S15` | 15 seconds | `S15`, `15s` | Raw candle reads only. |
| `S30` | 30 seconds | `S30`, `30s` | Raw candle reads only. |
| `M1` | 1 minute | `M1`, `1m` | Raw candle reads only. |
| `M2` | 2 minutes | `M2`, `2m` | Raw candle reads only. |
| `M4` | 4 minutes | `M4`, `4m` | Raw candle reads only. |
| `M5` | 5 minutes | `M5`, `5m` | Raw candle reads only. |
| `M10` | 10 minutes | `M10`, `10m` | Raw candle reads only. |
| `M15` | 15 minutes | `M15`, `15m` | Raw candles and scan-published snapshots. |
| `M30` | 30 minutes | `M30`, `30m` | Raw candle reads only. |
| `H1` | 1 hour | `H1`, `1h` | Raw candles and scan-published snapshots. |
| `H2` | 2 hours | `H2`, `2h` | Uses OANDA day alignment. |
| `H3` | 3 hours | `H3`, `3h` | Uses OANDA day alignment. |
| `H4` | 4 hours | `H4`, `4h` | Raw candles and scan-published snapshots. |
| `H6` | 6 hours | `H6`, `6h` | Uses OANDA day alignment. |
| `H8` | 8 hours | `H8`, `8h` | Uses OANDA day alignment. |
| `H12` | 12 hours | `H12`, `12h` | Uses OANDA day alignment. |
| `D` | 1 day | `D`, `1d`, `day`, `daily` | Raw candles and scan-published snapshots. |
| `W` | 1 week | `W`, `1w`, `week`, `weekly` | Raw candle reads only. |

`M` is not an alias for monthly candles in this MCP surface; it is rejected.

Raw candle instrument aliases:

| Alias | Instrument |
| --- | --- |
| `gold` | `XAU_USD` |
| `silver` | `XAG_USD` |
| `oil` | `WTICO_USD` |
| `btc` | `BTC_USD` |
| `eth` | `ETH_USD` |

Flexible instrument formatting is accepted before validation. For example, `EUR/USD`, `eur-usd`, `eur usd`, and `eurusd` normalize to `EUR_USD`.

Available raw candle instruments below are the configured OANDA account catalog captured on 2026-05-04. The runtime source of truth is `goldsignal://supported-instruments`, because OANDA account entitlements can differ by account and environment.

#### CURRENCY instruments (68)

| Instrument | Display precision | Pip location | Pip size | Trade unit precision | Minimum trade size | Margin rate |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `AUD_CAD` | 5 | -4 | 0.0001 | 0 | 1 | 0.05 |
| `AUD_CHF` | 5 | -4 | 0.0001 | 0 | 1 | 0.05 |
| `AUD_HKD` | 5 | -4 | 0.0001 | 0 | 1 | 0.1 |
| `AUD_JPY` | 3 | -2 | 0.01 | 0 | 1 | 0.05 |
| `AUD_NZD` | 5 | -4 | 0.0001 | 0 | 1 | 0.05 |
| `AUD_SGD` | 5 | -4 | 0.0001 | 0 | 1 | 0.05 |
| `AUD_USD` | 5 | -4 | 0.0001 | 0 | 1 | 0.05 |
| `CAD_CHF` | 5 | -4 | 0.0001 | 0 | 1 | 0.05 |
| `CAD_HKD` | 5 | -4 | 0.0001 | 0 | 1 | 0.1 |
| `CAD_JPY` | 3 | -2 | 0.01 | 0 | 1 | 0.05 |
| `CAD_SGD` | 5 | -4 | 0.0001 | 0 | 1 | 0.05 |
| `CHF_HKD` | 5 | -4 | 0.0001 | 0 | 1 | 0.1 |
| `CHF_JPY` | 3 | -2 | 0.01 | 0 | 1 | 0.05 |
| `CHF_ZAR` | 5 | -4 | 0.0001 | 0 | 1 | 0.05 |
| `EUR_AUD` | 5 | -4 | 0.0001 | 0 | 1 | 0.05 |
| `EUR_CAD` | 5 | -4 | 0.0001 | 0 | 1 | 0.05 |
| `EUR_CHF` | 5 | -4 | 0.0001 | 0 | 1 | 0.05 |
| `EUR_CZK` | 5 | -4 | 0.0001 | 0 | 1 | 0.05 |
| `EUR_DKK` | 5 | -4 | 0.0001 | 0 | 1 | 0.1 |
| `EUR_GBP` | 5 | -4 | 0.0001 | 0 | 1 | 0.05 |
| `EUR_HKD` | 5 | -4 | 0.0001 | 0 | 1 | 0.1 |
| `EUR_HUF` | 3 | -2 | 0.01 | 0 | 1 | 0.05 |
| `EUR_JPY` | 3 | -2 | 0.01 | 0 | 1 | 0.05 |
| `EUR_NOK` | 5 | -4 | 0.0001 | 0 | 1 | 0.05 |
| `EUR_NZD` | 5 | -4 | 0.0001 | 0 | 1 | 0.05 |
| `EUR_PLN` | 5 | -4 | 0.0001 | 0 | 1 | 0.05 |
| `EUR_SEK` | 5 | -4 | 0.0001 | 0 | 1 | 0.05 |
| `EUR_SGD` | 5 | -4 | 0.0001 | 0 | 1 | 0.05 |
| `EUR_TRY` | 5 | -4 | 0.0001 | 0 | 1 | 0.25 |
| `EUR_USD` | 5 | -4 | 0.0001 | 0 | 1 | 0.05 |
| `EUR_ZAR` | 5 | -4 | 0.0001 | 0 | 1 | 0.05 |
| `GBP_AUD` | 5 | -4 | 0.0001 | 0 | 1 | 0.05 |
| `GBP_CAD` | 5 | -4 | 0.0001 | 0 | 1 | 0.05 |
| `GBP_CHF` | 5 | -4 | 0.0001 | 0 | 1 | 0.05 |
| `GBP_HKD` | 5 | -4 | 0.0001 | 0 | 1 | 0.1 |
| `GBP_JPY` | 3 | -2 | 0.01 | 0 | 1 | 0.05 |
| `GBP_NZD` | 5 | -4 | 0.0001 | 0 | 1 | 0.05 |
| `GBP_PLN` | 5 | -4 | 0.0001 | 0 | 1 | 0.05 |
| `GBP_SGD` | 5 | -4 | 0.0001 | 0 | 1 | 0.05 |
| `GBP_USD` | 5 | -4 | 0.0001 | 0 | 1 | 0.05 |
| `GBP_ZAR` | 5 | -4 | 0.0001 | 0 | 1 | 0.05 |
| `HKD_JPY` | 5 | -4 | 0.0001 | 0 | 1 | 0.1 |
| `NZD_CAD` | 5 | -4 | 0.0001 | 0 | 1 | 0.05 |
| `NZD_CHF` | 5 | -4 | 0.0001 | 0 | 1 | 0.05 |
| `NZD_HKD` | 5 | -4 | 0.0001 | 0 | 1 | 0.1 |
| `NZD_JPY` | 3 | -2 | 0.01 | 0 | 1 | 0.05 |
| `NZD_SGD` | 5 | -4 | 0.0001 | 0 | 1 | 0.05 |
| `NZD_USD` | 5 | -4 | 0.0001 | 0 | 1 | 0.05 |
| `SGD_CHF` | 5 | -4 | 0.0001 | 0 | 1 | 0.05 |
| `SGD_JPY` | 3 | -2 | 0.01 | 0 | 1 | 0.05 |
| `TRY_JPY` | 3 | -2 | 0.01 | 0 | 1 | 0.25 |
| `USD_CAD` | 5 | -4 | 0.0001 | 0 | 1 | 0.05 |
| `USD_CHF` | 5 | -4 | 0.0001 | 0 | 1 | 0.05 |
| `USD_CNH` | 5 | -4 | 0.0001 | 0 | 1 | 0.05 |
| `USD_CZK` | 5 | -4 | 0.0001 | 0 | 1 | 0.05 |
| `USD_DKK` | 5 | -4 | 0.0001 | 0 | 1 | 0.05 |
| `USD_HKD` | 5 | -4 | 0.0001 | 0 | 1 | 0.1 |
| `USD_HUF` | 3 | -2 | 0.01 | 0 | 1 | 0.05 |
| `USD_JPY` | 3 | -2 | 0.01 | 0 | 1 | 0.05 |
| `USD_MXN` | 5 | -4 | 0.0001 | 0 | 1 | 0.05 |
| `USD_NOK` | 5 | -4 | 0.0001 | 0 | 1 | 0.05 |
| `USD_PLN` | 5 | -4 | 0.0001 | 0 | 1 | 0.05 |
| `USD_SEK` | 5 | -4 | 0.0001 | 0 | 1 | 0.05 |
| `USD_SGD` | 5 | -4 | 0.0001 | 0 | 1 | 0.05 |
| `USD_THB` | 3 | -2 | 0.01 | 0 | 1 | 0.05 |
| `USD_TRY` | 5 | -4 | 0.0001 | 0 | 1 | 0.25 |
| `USD_ZAR` | 5 | -4 | 0.0001 | 0 | 1 | 0.05 |
| `ZAR_JPY` | 3 | -2 | 0.01 | 0 | 1 | 0.05 |

#### METAL instruments (21)

| Instrument | Display precision | Pip location | Pip size | Trade unit precision | Minimum trade size | Margin rate |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `XAG_AUD` | 5 | -4 | 0.0001 | 0 | 1 | 0.2 |
| `XAG_CAD` | 5 | -4 | 0.0001 | 0 | 1 | 0.2 |
| `XAG_CHF` | 5 | -4 | 0.0001 | 0 | 1 | 0.2 |
| `XAG_EUR` | 5 | -4 | 0.0001 | 0 | 1 | 0.2 |
| `XAG_GBP` | 5 | -4 | 0.0001 | 0 | 1 | 0.2 |
| `XAG_HKD` | 5 | -4 | 0.0001 | 0 | 1 | 0.2 |
| `XAG_JPY` | 1 | 0 | 1 | 0 | 1 | 0.2 |
| `XAG_NZD` | 5 | -4 | 0.0001 | 0 | 1 | 0.2 |
| `XAG_SGD` | 5 | -4 | 0.0001 | 0 | 1 | 0.2 |
| `XAG_USD` | 5 | -4 | 0.0001 | 0 | 1 | 0.2 |
| `XAU_AUD` | 3 | -2 | 0.01 | 1 | 0.1 | 0.2 |
| `XAU_CAD` | 3 | -2 | 0.01 | 1 | 0.1 | 0.2 |
| `XAU_CHF` | 3 | -2 | 0.01 | 1 | 0.1 | 0.2 |
| `XAU_EUR` | 3 | -2 | 0.01 | 1 | 0.1 | 0.2 |
| `XAU_GBP` | 3 | -2 | 0.01 | 1 | 0.1 | 0.2 |
| `XAU_HKD` | 3 | -2 | 0.01 | 1 | 0.1 | 0.2 |
| `XAU_JPY` | 0 | 1 | 10 | 1 | 0.1 | 0.2 |
| `XAU_NZD` | 3 | -2 | 0.01 | 1 | 0.1 | 0.2 |
| `XAU_SGD` | 3 | -2 | 0.01 | 1 | 0.1 | 0.2 |
| `XAU_USD` | 3 | -2 | 0.01 | 1 | 0.1 | 0.2 |
| `XAU_XAG` | 3 | -2 | 0.01 | 1 | 0.1 | 0.2 |

#### CFD instruments (38)

| Instrument | Display precision | Pip location | Pip size | Trade unit precision | Minimum trade size | Margin rate |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `AU200_AUD` | 1 | 0 | 1 | 1 | 0.1 | 0.05 |
| `BCH_USD` | 2 | -1 | 0.1 | 2 | 0.01 | 0.5 |
| `BCO_USD` | 3 | -2 | 0.01 | 0 | 1 | 0.2 |
| `BTC_USD` | 1 | 0 | 1 | 3 | 0.001 | 0.5 |
| `CH20_CHF` | 1 | 0 | 1 | 1 | 0.1 | 0.2 |
| `CHINAH_HKD` | 1 | 0 | 1 | 1 | 0.1 | 0.05 |
| `CN50_USD` | 1 | 0 | 1 | 1 | 0.1 | 0.2 |
| `CORN_USD` | 3 | -2 | 0.01 | 0 | 1 | 0.2 |
| `DE10YB_EUR` | 3 | -2 | 0.01 | 0 | 1 | 0.2 |
| `DE30_EUR` | 1 | 0 | 1 | 2 | 0.01 | 0.05 |
| `ESPIX_EUR` | 1 | 0 | 1 | 1 | 0.1 | 0.2 |
| `ETH_USD` | 2 | -1 | 0.1 | 2 | 0.01 | 0.5 |
| `EU50_EUR` | 1 | 0 | 1 | 1 | 0.1 | 0.05 |
| `FR40_EUR` | 1 | 0 | 1 | 1 | 0.1 | 0.05 |
| `HK33_HKD` | 1 | 0 | 1 | 1 | 0.1 | 0.05 |
| `JP225Y_JPY` | 1 | 0 | 1 | 0 | 1 | 0.05 |
| `JP225_USD` | 1 | 0 | 1 | 2 | 0.01 | 0.05 |
| `LTC_USD` | 2 | -1 | 0.1 | 2 | 0.01 | 0.5 |
| `NAS100_USD` | 1 | 0 | 1 | 2 | 0.01 | 0.05 |
| `NATGAS_USD` | 3 | -2 | 0.01 | 0 | 1 | 0.2 |
| `NL25_EUR` | 3 | -2 | 0.01 | 1 | 0.1 | 0.2 |
| `SG30_SGD` | 2 | -1 | 0.1 | 1 | 0.1 | 0.05 |
| `SOYBN_USD` | 3 | -2 | 0.01 | 0 | 1 | 0.2 |
| `SPX500_USD` | 1 | 0 | 1 | 2 | 0.01 | 0.05 |
| `SUGAR_USD` | 5 | -4 | 0.0001 | 0 | 1 | 0.2 |
| `UK100_GBP` | 1 | 0 | 1 | 1 | 0.1 | 0.05 |
| `UK10YB_GBP` | 3 | -2 | 0.01 | 0 | 1 | 0.2 |
| `US2000_USD` | 3 | -2 | 0.01 | 2 | 0.01 | 0.05 |
| `US30_USD` | 1 | 0 | 1 | 2 | 0.01 | 0.05 |
| `USB02Y_USD` | 3 | -2 | 0.01 | 0 | 1 | 0.2 |
| `USB05Y_USD` | 3 | -2 | 0.01 | 0 | 1 | 0.2 |
| `USB10Y_USD` | 3 | -2 | 0.01 | 0 | 1 | 0.2 |
| `USB30Y_USD` | 3 | -2 | 0.01 | 0 | 1 | 0.2 |
| `WHEAT_USD` | 3 | -2 | 0.01 | 0 | 1 | 0.2 |
| `WTICO_USD` | 3 | -2 | 0.01 | 0 | 1 | 0.2 |
| `XCU_USD` | 5 | -4 | 0.0001 | 0 | 1 | 0.2 |
| `XPD_USD` | 3 | -2 | 0.01 | 0 | 1 | 0.2 |
| `XPT_USD` | 3 | -2 | 0.01 | 0 | 1 | 0.2 |

Price and spread rules:

- `get_price(prefer_live=false)` follows Telegram `/price` and uses REST pricing by default.
- `get_price(prefer_live=true)` uses the live stream when fresh, then falls back to REST and includes `fallback_note`.
- Every explicit MCP `get_price` read records a spread-history observation for tracked instruments.
- `get_spread_snapshot()` defaults to `require_live=true`; stale or missing stream data raises instead of silently falling back.
- Use `get_spread_snapshot(require_live=false)` when a REST fallback is acceptable.

### Scan And Published Evidence

| Tool | Notes |
| --- | --- |
| `scan_all(force=false)` | Runs the full instrument scan cycle and returns scan status. |
| `scan_instrument(instrument, force=false)` | Refreshes one instrument through the full publish path. |
| `refresh_snapshot(instrument, timeframe='H1', force=false)` | Refreshes one published timeframe snapshot. |
| `get_smc_snapshot(instrument, timeframe='H1', refresh_policy='if_missing')` | Full sanitized published evidence snapshot. |
| `get_structure(instrument, timeframe='H1', refresh_policy='if_missing')` | Recent BOS/CHOCH structure evidence. |
| `get_order_blocks(instrument, timeframe='H1', refresh_policy='if_missing', mitigation_status='all')` | Published order-block or zone evidence filtered by `all`, `mitigated`, or `unmitigated`. |
| `get_indicators(instrument, timeframe='H1', mode='compact', refresh_policy='if_missing')` | Compact or full indicator metrics plus tick-volume metrics. |
| `get_vwap(instrument, timeframe='H1', anchor='D', bands=None)` | Anchor-based VWAP with optional bands using OANDA tick-count proxy volume. |
| `get_session_context(instrument, timeframe='H1', refresh_policy='if_missing')` | Sydney/Tokyo/London/New York session context. |
| `get_day_range(instrument, refresh_policy='if_missing')` | Previous-day range and sweep status from H1 context. |
| `get_previous_day_levels(instrument, refresh_policy='if_missing')` | Previous-day high/low and break flags. |

Published evidence rules:

- Published snapshot tools are limited to `D`, `H4`, `H1`, and `M15`.
- Use raw candle tools for seconds-level frames or minor OANDA frames.
- `refresh_policy` accepts `never`, `if_missing`, or `always`.
- Snapshot responses include freshness metadata and a warning when stale.
- Sanitized evidence is context, not a generated trade plan.

### Account, Positions, Orders, And Transfers

| Tool | Notes |
| --- | --- |
| `get_account_summary()` | Current OANDA account summary. |
| `list_open_positions()` | Open trades with current mid-price and pip-distance annotations. |
| `list_open_orders()` | Open orders with current mid-price and pip-distance annotations. |
| `list_transfers(start_date=None, end_date=None, limit=100)` | Raw normalized `TRANSFER_FUNDS` history. |

`list_transfers` defaults to a trailing 365-day UTC window when dates are omitted. Dates use `YYYY-MM-DD`, results are newest-first, and the response includes the resolved UTC window.

### Journal, History, Stats, And Correlation

| Tool | Notes |
| --- | --- |
| `list_journal_trades(instrument=None, start_date=None, end_date=None, limit=10)` | Persisted journal trades, newest first. |
| `get_journal_trade(trade_id)` | One trade with excursion samples, MAE/MFE, and current price context. |
| `get_mae_mfe(trade_id=None)` | One trade when `trade_id` is supplied; otherwise summaries for open trades. |
| `get_trade_history(period='day', view='all', instrument=None, page=1, start_date=None, end_date=None)` | Transaction-backed trade history and realized PnL pages. |
| `get_trade_stats(period='day', start_date=None, end_date=None, instrument=None)` | Realized trade rollups and per-instrument attribution. |
| `get_correlation(primary, secondary, timeframe='D', lookback=60, secondary_transform='raw')` | Daily close-return correlation across two aligned series. |

History window rules:

- `get_trade_history` supports symbolic periods: `day`, `week`, `month`, `today`, `thisweek`, and `thismonth`.
- `start_date` and `end_date` are MCP-only explicit `YYYY-MM-DD` overrides.
- Explicit dates must be supplied together and override `period`.
- `view` accepts `all`, `opened`, or `closed`.

Journal quote rules:

- Open-trade journal reads resolve current price live-first and include `current_price_source`.
- If the live stream falls back to REST, `current_price_fallback_note` is included.
- Closed trades use stored close price when available, otherwise stored entry price.

Trade stats include realized PnL totals, win/loss/breakeven counts, win rate, expectancy, profit factor, average win/loss, largest win/loss, average realized R for RR-eligible trades, max drawdown, per-instrument attribution, and MAE coverage.

Correlation rules:

- `timeframe` is `D` only in the current implementation.
- Series can mix OANDA instruments and Yahoo Finance symbols.
- `secondary_transform='inverse'` is supported for inverse framing, such as interpreting `USD_JPY` as JPY strength.

### Alerts

| Tool | Notes |
| --- | --- |
| `create_price_alert(instrument, target_price, direction, note=None)` | Creates a pending price alert. `direction` is `above` or `below`. |
| `list_price_alerts()` | Lists pending price alerts for the MCP default chat. |
| `clear_price_alert(alert_id)` | Cancels one price alert for the MCP default chat. |
| `clear_all_price_alerts(confirm, instrument=None)` | Cancels matching price alerts; requires `confirm=true`. |
| `replace_alert_grid(instrument, alerts, confirm)` | Atomically replaces one instrument's pending price-alert grid; requires `confirm=true`. |
| `create_indicator_alert(instrument, timeframe, indicator, condition, threshold=None, note=None, repeat=false, cooloff_minutes=None)` | Creates an indicator alert. |
| `seed_default_indicator_alerts()` | Seeds default RSI/STOCH H1 alerts and SMA cross alerts on published frames. |
| `list_indicator_alerts()` | Lists active indicator alerts for the MCP default chat. |
| `clear_indicator_alert(alert_id)` | Cancels one indicator alert for the MCP default chat. |
| `clear_all_indicator_alerts(confirm, instrument=None, timeframe=None, indicator=None)` | Cancels matching indicator alerts; requires `confirm=true`. |
| `create_time_alert(kind, local_time=None, schedule=None, session_name=None, note=None)` | Creates a fixed-time or session reminder. |
| `list_time_alerts()` | Lists active time alerts for the MCP default chat. |
| `clear_time_alert(alert_id)` | Cancels one time alert for the MCP default chat. |
| `list_alert_history(alert_type='all', instrument=None, start_date=None, end_date=None, limit=50)` | Fired price, indicator, and time alert history. |

Alert behavior:

- Destructive batch tools require `confirm=true`.
- Price-alert grid replacement is price-alert-only.
- Batch alert tools use the same MCP default chat as single-alert tools.
- `list_alert_history` returns fired alerts only after successful notification and state transition.
- Indicator `indicator` accepts `RSI`, `STOCH`, `MACD`, or `SMA_CROSS`.
- Indicator `condition` accepts `above`, `below`, `cross_up`, or `cross_down`.
- Indicator alerts support `repeat` and `cooloff_minutes`.

Time-alert behavior:

- `create_time_alert(kind='at', local_time='HH:MM')` creates a fixed-time reminder in `Asia/Singapore`.
- Fixed-time schedules support `daily` and `once`; omitted `schedule` defaults to `daily`.
- `create_time_alert(kind='at', local_time='YYYY-MM-DD HH:MM')` creates an exact one-time reminder and forces `schedule='once'`.
- `create_time_alert(kind='session', session_name='london')` creates a session reminder.
- Session names are `london`, `newyork`, and `market_open`.
- Telegram-only time-alert import/export commands are not MCP tools.

## Current Limitations

The current MCP server does not expose:

- chart rendering
- CSV export
- runtime config mutation
- trade-history backfill
- trade label writes
- broker execution writes
- Telegram time-alert import/export
- Telegram session auth

The active baseline also intentionally excludes Fair Value Gap support, forming-candle detector output, raw DataFrame exposure, and deterministic trade-plan helpers.
