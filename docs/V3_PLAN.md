# Gold Signal Bot V3 — Fresh Build Plan

> Historical design plan written before implementation. Keep this document for lineage and guardrails, but use [README](../README.md), [tracker.md](./tracker.md), and [COMMANDS.md](./COMMANDS.md) for the current repo state.

> **What this is**: A from-scratch build plan for an OANDA and Telegram bot stack with two bounded runtimes: the analysis runtime and a read-only trade-helper runtime. Written as if no codebase exists. Every design decision below was learned from two prior iterations (V1, V2) that got things wrong — the "What NOT to Do" section documents each mistake so they are never repeated.
>
> **Instruments**: **Commodities (XAU_USD, XAG_USD) and major/minor FX pairs** on **OANDA**. All instrument metadata, spread thresholds, pip conventions, and volume semantics are designed for OANDA's OTC market data — not exchange-traded instruments.
>
> **Scope**: Market analysis, feature extraction, read-only account-state monitoring, trade journaling, MAE/MFE tracking, trade alerts, price alerts, and indicator alerts. No signal scoring, trade planning, grading, confidence scoring, or trade execution.
>
> **FVG (Fair Value Gaps) is not a consideration in V3 — at all.** No FVG detection, no FVG commands, no FVG zones in snapshots, no FVG in signal evaluation. `smc.fvg()` exists in the `smartmoneyconcepts` package but is deliberately unused. This is a conscious design decision, not an oversight.

---

## Table of Contents

1. [What NOT to Do — Lessons from V1 and V2](#1-what-not-to-do--lessons-from-v1-and-v2)
2. [Design Principles](#2-design-principles)
3. [Tech Stack](#3-tech-stack)
4. [Directory Structure](#4-directory-structure)
5. [Core Systems — Detailed Design](#5-core-systems--detailed-design)
6. [Two-Layer Snapshot Architecture](#6-two-layer-snapshot-architecture)
7. [Process Walkthroughs](#7-process-walkthroughs)
8. [Tests](#8-tests)
9. [Development Priorities](#9-development-priorities)
10. [Non-Goals and Boundaries](#10-non-goals-and-boundaries)
11. [Telegram Commands](#11-telegram-commands)

---

## 1. What NOT to Do — Lessons from V1 and V2

> Every rule below exists because someone broke it and it caused real pain.

### 1.1 Do NOT mix market-data and account/execution in one provider

**What went wrong (V1)**: `OANDADataProvider` was an 869-line class with `fetch_candles()`, `get_price()`, `get_account_summary()`, `get_open_positions()`, and `get_open_trades()` all in one class. The indicator layer imported this provider and had access to account state it should never touch.

**The damage**: A detector accidentally read open positions to influence analysis output. This created a feedback loop — analysis depended on execution state. It also made testing impossible without mocking the entire OANDA API surface.

**The rule**: Two separate interfaces. `MarketDataProvider` returns candles and prices. `ExecutionProvider` returns account, positions, orders. The indicator layer only imports `MarketDataProvider`. No file in `smc/`, `indicators/`, or `filters/` may import `ExecutionProvider`.

---

### 1.2 Do NOT compute on forming bars without explicit labeling

**What went wrong (V1)**: The OANDA provider filtered out incomplete candles at the API level (`complete == True`), but there was no canonical utility enforcing this across all detectors. Only `CandlestickDetector` had an `include_current` parameter — every other detector silently operated on whatever DataFrame it received. If a consumer called `fetch_candles()` mid-bar and passed the result to a detector, the detector ran on incomplete data. When the candle closed, the output changed. This is **repainting**.

**The damage**: HTF bias computed on a forming H4 bar flipped direction when the bar closed, but the signal had already been sent. The user entered a trade based on a bias that reversed 20 minutes later.

**The rule**: One canonical `trim_to_closed(df, timeframe)` utility. All detectors call it as their first operation. If provisional/forming-bar analysis is needed, it must be explicitly tagged as `is_provisional=True` and returned separately. No exceptions.

---

### 1.3 Do NOT let `time` be both a column and an index

**What went wrong (V1/V2)**: Some code paths set `time` as the DataFrame index. Others kept it as a column. Some used UTC-aware datetimes; others used naive datetimes. Consumers had to guess which representation they were getting. The result was `KeyError` when code expected `df['time']` but time was the index, or silent NaN injection from timezone comparison failures.

**The rule**: `time` is **always a column**, never the index. It is **always UTC-aware** (`datetime64[ns, UTC]`). DatetimeIndex is used only when a specific pandas operation requires it, then immediately converted back. One canonical `validate_candle_df(df)` function enforces this at every boundary.

---

### 1.4 Do NOT use TTL-only cache freshness

**What went wrong (V2)**: Cache validity was based on wall-clock TTL (`time.monotonic()` + seconds). An H1 candle cached at 10:01 was "fresh" until 11:01 — but the H1 candle closed at 10:00 and a new one started forming at 10:00. The cache served data that was one full candle behind, and the staleness threshold in `CacheWarmer` was completely independent from the TTL in `OANDADataProvider`. Two systems disagreed about freshness.

**The damage**: Detectors ran on stale H4 data that was 3 hours old because TTL said "fresh." After a restart, CSV files from yesterday were loaded with no timestamp metadata and treated as current.

**The rule**: Cache validity is based on the **last completed candle timestamp** for the requested timeframe. For H1 data at 10:15: the cache is valid if it contains the 10:00 candle. It becomes stale at 11:00 when a new H1 completes. One freshness policy, not two. CSV persistence stores metadata (last_completed_candle, fetched_at).

---

### 1.5 Do NOT use a single generic spread threshold for all instruments

**What went wrong (V1)**: Only XAU_USD (15 pips) and XAG_USD (10 pips) had explicit spread limits. Every other instrument fell back to a generic 5-pip default. For EUR_USD (normal spread ~0.3 pips), a 3-pip spread was flagged as "acceptable" even though it was 10x normal. For JPY pairs (pip value 0.01, not 0.0001), the pip calculation was off by 100x because the metadata lookup had a silent 0.0001 fallback.

**The damage**: A 3-pip spread on EUR_USD during a news event went undetected as a spike. Gold spread was calculated with the wrong pip value, making the 15-pip threshold meaningless.

**The rule**: Per-instrument metadata registry with explicit pip_size, typical_spread, max_spread, spike_multiplier. Cover every scan target instrument. Unknown instruments fail loud, no silent defaults.

---

### 1.6 Do NOT label OTC tick-volume as "volume"

**What went wrong (V1)**: OANDA returns tick count (number of price ticks), not traded volume. The DataFrame column was named `volume`. OBV, MFI, and ADOSC indicators were computed as if this were exchange volume. A feature flag (`TA_FEATURE_VOLUME_ENABLED = False`) hid the problem — dead code that was tested and maintained but never used.

**The damage**: If anyone enabled the flag, MFI readings on XAU_USD would be treated as institutional money flow when they were actually tick activity. Dead feature flags created false confidence that the capability existed.

**The rule**: The column is named `tick_volume`. Volume indicators are prefixed `tick_` (e.g., `tick_obv`, `tick_mfi`). Every volume indicator output carries a `caveat` field stating it is computed from OANDA tick count. No dead feature flags — either the indicator is computed, labeled, and used, or the code doesn't exist.

---

### 1.7 Do NOT build without structured logging from day one

**What went wrong (V2)**: Logs were unstructured strings (`f"Fetched {count} candles for {instrument}"`). There was no fetch provenance (cache vs API), no detector timing, no snapshot versioning, no stale/fresh status in logs. When a signal fired on stale data, there was no way to determine after the fact what data it saw.

**The damage**: A detector started taking 5 seconds instead of 50ms due to a data issue. Nobody noticed for two weeks. Two signals fired 30 seconds apart with different results for the same instrument/timeframe — without snapshot versioning, there was no way to correlate which data each signal saw.

**The rule**: Use `structlog` from the first line of code. Every fetch, cache event, detector execution, snapshot publish, spread check, and bar exclusion gets a structured log with typed fields. Every log event is a dict, not a string.

---

### 1.8 Do NOT put raw DataFrames in public state

**What went wrong (V2)**: `ScanResult.context` was a `dict` that could contain full detector DataFrames — hundreds of rows of OB and liquidity data. Downstream consumers parsed these differently. The "schema" was whatever the detector happened to output. When a detector changed its output columns, consumers broke silently.

**The rule**: Public state contains typed Pydantic models only. Replace raw DataFrames with compact summaries: `ActiveZoneSummary` (nearest 10 OBs), `StructureEventSummary` (latest BOS/CHOCH), `LiquidityPoolSummary` (nearest levels), `IndicatorValueSummary` (flat typed values). DataFrames are internal compute structures, never part of the published contract.

---

### 1.9 Do NOT build stateful detectors that pretend to be stateless

**What went wrong (V1)**: The order block detector maintained an internal state machine (`ACTIVE → MITIGATED → INVALIDATED`) inside a class instance. The background scanner and command handlers used different instances. Same candle data produced different OB states depending on which code path called the detector.

**The rule**: Detectors are pure functions. Same input DataFrame → same output, every time. No instance state, no lifecycle tracking between calls. Use `smartmoneyconcepts` (stateless by design) for core SMC detection. Custom detectors (SFP, Turtle Soup, ORB) are stateless pure functions.

---

### 1.10 Do NOT build a god-function orchestrator

**What went wrong (V1)**: `scan_instrument()` was 600+ lines: fetch data, run all detectors, resolve direction, build a trade plan, evaluate grade — all sequentially. Impossible to cache intermediate results, run detectors in parallel, or test individual stages.

**The rule**: Decompose into independent, composable steps. Each detector is independently callable, cacheable, and testable. The orchestrator assembles results; it does not compute them.

---

### 1.11 Do NOT ship dead feature flags

**What went wrong (V1/V2)**: `TA_FEATURE_MOMENTUM_ENABLED`, `TA_FEATURE_VOLATILITY_ENABLED`, `TA_FEATURE_VOLUME_ENABLED`, `TA_FEATURE_TREND_HELPERS_ENABLED` — all defaulting to `false`. The confidence model claimed 7 families but only 3 contributed in production. Dead code was tested, maintained, and documented for no benefit.

**The rule**: Either commit to a feature or don't build it. No scaffolding for things that are turned off by default. If it ships, it's enabled.

---

### 1.12 Do NOT use matplotlib directly for financial charts

**What went wrong (V1)**: `charting.py` was 49KB of manual candlestick rendering with raw matplotlib patches. If an exception occurred between `plt.figure()` and `plt.close()`, the figure leaked memory. State isolation between charts was fragile.

**The rule**: Use `mplfinance` for all financial charting. Render in a `ProcessPoolExecutor` to fully isolate matplotlib state and clean up temporary chart artifacts after each render. Chart code should be ~5KB, not 49KB.

---

### 1.13 Do NOT mix async and sync I/O carelessly

**What went wrong (V1)**: `python-telegram-bot` v21+ is fully async, but file operations and OANDA calls were synchronous inside `async def` handlers. Blocking I/O stalled the entire event loop under load.

**The rule**: `oandapyV20` is sync (wraps `requests`). Do not fake async around it. Use `asyncio.to_thread()` honestly. For file I/O (TinyDB, CSV), wrap in `asyncio.to_thread()` at the boundary if called from an async context.

---

### 1.14 Do NOT ignore market closure

**What went wrong (V1)**: During weekends, OANDA returns no candles. The cache missed, hit the API, got an empty response, and detectors received NaN-filled DataFrames, producing garbage output.

**The rule**: Check `is_market_open()` before data fetches. If the market is closed, return a clear status. Do not feed empty DataFrames to detectors, and do not allow alert side effects to run on closed-market or stale snapshots.

---

### 1.15 Do NOT build multi-timeframe state without explicit freshness tracking

**What went wrong (V2)**: When a snapshot combined H1 and H4 data, there was no indication that H1 was fetched 30 seconds ago but H4 was fetched 2 hours ago. The snapshot appeared atomic but wasn't. HTF bias used stale H4 data alongside fresh H1 data with no warning.

**The rule**: Two-layer snapshot architecture. Layer 1 (`TimeframeSnapshot`) is per-timeframe, versioned, immutable. Layer 2 (`InstrumentBundle`) pins exact snapshot versions and exposes mixed freshness explicitly. The bundle says: *"HTF bias was computed using H1 v1842, H4 v771, D v229."*

---

## 2. Design Principles

1. **Detectors are pure functions.** Same closed-bar input → same output, every time. No instance state.

2. **Two-layer snapshot model.** Per-timeframe immutable snapshots (Layer 1) are assembled into versioned instrument bundles (Layer 2). Downstream consumers read bundles.

3. **Candle-boundary freshness.** Cache validity is based on the last completed candle timestamp, not wall-clock TTL.

4. **Explicit over implicit.** Tick-volume is labeled tick-volume. Forming bars are labeled provisional. Mixed freshness is visible. Pip conventions are documented per instrument.

5. **Structured observability from day one.** Every state change is logged with typed fields via `structlog`.

6. **No dead code.** Every feature is enabled and used, or it doesn't exist. No feature flags for things that are off by default.

7. **Clean module boundaries.** Market data provider has no account/execution methods. Indicator layer has no Telegram imports. Analysis has no execution coupling.

8. **Boring, durable code.** No clever abstractions. No premature generalization. Three similar lines of code is better than a premature helper.

---

## 3. Tech Stack

### Core Runtime

| Package | Role |
|---------|------|
| `python-dotenv` | `.env` file loading |
| `pydantic >= 2.0` | Settings validation, data models at boundaries |
| `pydantic-settings >= 2.0` | Type-safe env var loading |
| `oandapyV20 >= 0.7.2` | All OANDA REST API access (candles, prices). **No raw `requests`, no `v20` package, no custom HTTP clients.** |
| `pandas >= 2.0.0` | DataFrame for internal candle computation |
| `numpy >= 1.24.0` | Numerical operations |

### Analytics

| Package | Role |
|---------|------|
| `TA-Lib >= 0.6.8` | C-accelerated technical indicators. Primary engine for EMA, RSI, ATR, MACD, Bollinger, Stochastic, ADX, CCI, SAR. |
| `pandas-ta` | Supplementary indicators not in TA-Lib: VWAP, Squeeze Momentum, Ichimoku, Nadaraya-Watson. Used **alongside** TA-Lib, not as a replacement. |
| `smartmoneyconcepts >= 0.0.26` | SMC detection: BOS/CHOCH, order blocks, liquidity, sessions, retracements. Stateless by design. **FVG is explicitly excluded — not used in V3.** |
| `ruptures` | Offline changepoint detection (PELT, BinSeg) for HTF bias regime identification. Detects structural breaks in price series. |

### Charting

| Package | Role |
|---------|------|
| `mplfinance` | All financial charting. Candlestick, OHLC, volume panels, selector-based overlays, and ephemeral artifacts. Rendered in `ProcessPoolExecutor` for state isolation. |

### Persistence & Storage

| Package | Role |
|---------|------|
| `tinydb` | Document store for trade records, signal history, spread observations, cache metadata, alert state. Zero infrastructure. |

### Scheduling

| Package | Role |
|---------|------|
| `apscheduler >= 3.10` | Cron-style and interval job scheduling. Auto-scans at session opens (London 08:00, NY 13:00 UTC), market open (Sunday 22:00 UTC), and configurable intervals. |

### Calendar / News

| Package | Role |
|---------|------|
| *Direct HTTP fetch* | Economic calendar from `https://nfs.faireconomy.media/ff_calendar_thisweek.json`. Free, no credentials. JSON array of this week's events with impact ratings. Fetched hourly via `apscheduler`. |

### Market Data & Calendars

| Package | Role |
|---------|------|
| `yfinance >= 0.2.40` | VIX, DXY, and macro index data for correlation and regime context. Supplements OANDA data. |
| `pandas_market_calendars >= 4.4.0` | Market holiday and session detection. Used for skip-scheduling on holidays and market-closure awareness. |

### Observability

| Package | Role |
|---------|------|
| `structlog` | Structured key-value logging for all operations. Every log event is a dict. |

### Bot Layer

| Package | Role |
|---------|------|
| `python-telegram-bot >= 21.0` | Telegram interface (separate layer, consumes `InstrumentBundle`) |
| `requests >= 2.31.0` | Used internally by `oandapyV20` and for calendar fetch |

### Testing

| Package | Role |
|---------|------|
| `pytest` | Test runner |
| `pytest-asyncio >= 0.23.0` | Async test support |
| `pytest-mock` | Mocking |
| `time-machine` | Time mocking for candle-boundary and session tests |
| `freezegun >= 1.2.0` | Time mocking for broader datetime patching |
| `responses` | Mock `requests`-based HTTP calls (`oandapyV20` uses `requests` internally) |

### Not Included

| Package | Why NOT |
|---------|---------|
| `scikit-learn` | Overkill. Use numpy. |
| `scipy` | Replace with numpy equivalents. |
| `matplotlib` (direct) | Replaced by `mplfinance`. |
| `aiohttp` | `oandapyV20` is sync. Don't fake async. Use `asyncio.to_thread()`. |
| Extra async DB/file wrappers | TinyDB is sync. Wrap blocking boundaries honestly with `asyncio.to_thread()` when needed. |

---

## 4. Directory Structure

```
gold-signal-bot-v3/
├── .env.example
├── requirements.txt
├── pyproject.toml
│
├── config/
│   └── settings.py                  # Pydantic BaseSettings (type-safe, validated)
│
├── core/
│   ├── candle_policy.py             # trim_to_closed(), validate_candle_df(), CANONICAL_COLUMNS
│   ├── instrument_registry.py       # InstrumentSpec, INSTRUMENT_REGISTRY (all 12 instruments)
│   ├── models.py                    # TimeframeSnapshot, InstrumentBundle, all Pydantic models
│   ├── market_state.py              # MarketStateStore (two-layer, thread-safe, atomic)
│   └── logging_setup.py             # structlog configuration
│
├── providers/
│   ├── base.py                      # Abstract MarketDataProvider interface
│   ├── oanda.py                     # OANDA implementation via oandapyV20 (candles + prices only)
│   ├── oanda_execution.py           # Account/position/order access (NOT used by indicator layer)
│   └── cache.py                     # Candle-boundary-aware three-level cache
│
├── smc/
│   ├── provider.py                  # Wrapper around smartmoneyconcepts package
│   ├── htf_bias.py                  # HTF bias with ruptures changepoint detection
│   ├── sfp.py                       # Swing Failure Pattern (stateless pure function)
│   ├── turtle_soup.py               # Turtle Soup (stateless pure function)
│   └── orb.py                       # Opening Range Breakout (stateless pure function)
│
├── indicators/
│   ├── talib_wrappers.py            # TA-Lib indicator wrappers (stateless)
│   ├── pandasta_wrappers.py         # pandas-ta indicators (VWAP, Squeeze, Ichimoku)
│   └── tick_volume.py               # Tick-volume indicators with explicit caveats
│
├── filters/
│   ├── spread.py                    # Instrument-registry-aware spread filter
│   └── chop.py                      # Chop/ADX filter
│
├── data/
│   ├── forex_calendar.py            # ForexFactory calendar via faireconomy.media API
│   ├── market_hours.py              # Market open/close detection
│   ├── csv_persistence.py           # CSV candle storage with metadata
│   └── persistence/
│       └── trade_store.py           # TinyDB-based trade/signal/spread storage
│
├── journal/
│   ├── trade_repository.py          # TinyDB CRUD for journal records and notes
│   ├── excursion_repository.py      # TinyDB CRUD for excursion samples and MAE/MFE aggregation
│   └── journal_service.py           # Trade-event consumer and journal writer
│
├── tracking/
│   └── excursion_tracker.py         # Live tick consumer for MAE/MFE tracking
│
├── alerts/
│   ├── alert_repository.py          # TinyDB CRUD for price and indicator alerts
│   ├── price_alert_engine.py        # Fire-once price alert evaluation
│   └── indicator_alert_engine.py    # Scheduled RSI/Stochastic/MACD alert evaluation
│
├── notifications/
│   ├── message_builder.py           # Telegram text builders for trade and alert events
│   └── notifier.py                  # PTB-backed send_message/send_alert wrapper
│
├── background/
│   ├── stream_task.py               # Supervised live-price streaming with fan-out
│   ├── poller_task.py               # Supervised open-trade polling and diff emission
│   ├── indicator_scan_task.py       # APScheduler-backed indicator scan entrypoint
│   └── task_supervisor.py           # start_all/stop_all and graceful shutdown
│
├── charting/
│   └── renderer.py                  # mplfinance chart rendering (ProcessPoolExecutor, selectors, cleanup)
│
├── orchestration/
│   ├── scan_orchestrator.py         # Coordinated multi-instrument scanning
│   ├── scheduler.py                 # APScheduler-based task scheduling
│   └── cache_warmer.py              # Pre-fetch data at session opens
│
├── bot/
│   ├── __main__.py                  # Entry point
│   ├── bot.py                       # Telegram command handlers
│   ├── security_manager.py          # Auth, sessions, rate limiting
│   └── message_queue.py             # Priority message queue
│
└── tests/
    ├── conftest.py                  # Shared fixtures
    ├── unit/
    │   ├── test_candle_policy.py
    │   ├── test_instrument_registry.py
    │   ├── test_spread_filter.py
    │   ├── test_tick_volume.py
    │   ├── test_cache_freshness.py
    │   ├── test_candle_schema.py
│   ├── test_models.py
│   ├── test_forex_calendar.py
│   ├── test_trade_store.py
│   ├── test_trade_repository.py
│   ├── test_excursion_repository.py
│   ├── test_alert_repository.py
│   ├── test_account_poller.py
│   ├── test_journal_service.py
│   ├── test_excursion_tracker.py
│   ├── test_price_alert_engine.py
│   ├── test_indicator_alert_engine.py
│   ├── test_security.py
│   └── test_htf_bias_ruptures.py
└── integration/
    ├── test_snapshot_publication.py
    ├── test_provider_cache.py
    ├── test_observability.py
    ├── test_journal_lifecycle.py
    ├── test_price_alert_fire.py
    └── test_indicator_alert_fire.py
```

---

## 5. Core Systems — Detailed Design

### 5.1 Configuration

Pydantic `BaseSettings` for type-safe, validated configuration.

**Required keys** (startup fails without them):
- `OANDA_API_KEY`
- `OANDA_ACCOUNT_ID`
- `OANDA_ENVIRONMENT` (`practice` or `live`)
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`
- `TELEGRAM_BOT_PASSWORD`
- `TELEGRAM_ADMIN_IDS`

**Key runtime settings:**

| Key | Default | Purpose |
|-----|---------|---------|
| `LOG_LEVEL` | `INFO` | Logging verbosity |
| `LOG_JSON` | `false` | JSON log output for production |
| `DEFAULT_CANDLE_COUNT` | `500` | Bars to fetch per request |
| `DEFAULT_SWING_LENGTH` | `10` | SMC swing detection sensitivity |
| `RUPTURES_PENALTY` | `10.0` | Changepoint detection penalty (tune per timeframe) |
| `SCAN_INTERVAL_MINUTES` | `5` | Auto-scan interval |
| `CALENDAR_REFRESH_HOURS` | `1` | Calendar data refresh interval |
| `TINYDB_PATH` | `data/bot.json` | TinyDB database file path |
| `POLL_INTERVAL_SECONDS` | `30` | Open-trade poll cadence |
| `STREAM_INSTRUMENTS` | `XAU_USD,...` | Instruments subscribed to the live price stream |
| `MAE_MFE_MIN_PIP_MOVE` | `0.5` | Minimum pip delta before writing a new excursion sample |
| `INDICATOR_SCAN_INTERVAL_MINUTES` | `5` | Scheduled indicator-alert scan cadence |

---

### 5.2 Canonical Candle Schema

Every candle DataFrame in the system has exactly these columns:

```python
CANONICAL_COLUMNS = ["time", "open", "high", "low", "close", "tick_volume"]
```

| Column | Type | Notes |
|--------|------|-------|
| `time` | `datetime64[ns, UTC]` | Always a column, never the index. Always UTC-aware. |
| `open` | `float64` | Mid price open |
| `high` | `float64` | Mid price high |
| `low` | `float64` | Mid price low |
| `close` | `float64` | Mid price close |
| `tick_volume` | `int64` | OANDA tick count. NOT exchange volume. |

Enforced by `validate_candle_df(df) -> df` at every provider and cache boundary.

---

### 5.3 Instrument Registry

Single source of truth for all instrument metadata. Covers every scan target instrument.

```python
class InstrumentSpec(BaseModel):
    symbol: str                 # "XAU_USD"
    pip_size: float             # 0.01 for XAU_USD, 0.0001 for XAG_USD/EUR_USD, 0.01 for JPY pairs
    pip_value_per_lot: float    # value of 1 pip for 1 standard lot
    typical_spread_pips: float  # normal market hours spread
    max_spread_pips: float      # reject signals above this
    spike_multiplier: float     # current / typical > this = spiking
    lot_size: int               # 1 standard lot = this many units
    category: str               # "major_fx", "minor_fx", "metal"
```

**Registry entries:**

| Symbol | Category | Pip Size | Typical Spread | Max Spread | Spike Mult |
|--------|----------|----------|----------------|------------|------------|
| XAU_USD | metal | 0.01 | 25.0 | 80.0 | 3.0x |
| XAG_USD | metal | 0.0001 | 200.0 | 600.0 | 3.0x |
| EUR_USD | major_fx | 0.0001 | 0.3 | 3.0 | 5.0x |
| GBP_USD | major_fx | 0.0001 | 0.5 | 4.0 | 4.0x |
| USD_JPY | major_fx | 0.01 | 0.5 | 3.0 | 5.0x |
| AUD_USD | major_fx | 0.0001 | 0.4 | 3.5 | 5.0x |
| USD_CAD | major_fx | 0.0001 | 0.5 | 4.0 | 4.0x |
| USD_CHF | major_fx | 0.0001 | 0.5 | 4.0 | 4.0x |
| NZD_USD | major_fx | 0.0001 | 0.6 | 4.0 | 4.0x |
| EUR_GBP | minor_fx | 0.0001 | 0.5 | 4.0 | 4.0x |
| EUR_JPY | minor_fx | 0.01 | 0.7 | 5.0 | 4.0x |
| GBP_JPY | minor_fx | 0.01 | 1.2 | 6.0 | 3.5x |

Unknown instruments fail loud with `KeyError`. No silent defaults.

---

### 5.4 Market Data Provider

**Interface** (`providers/base.py`):

```python
class MarketDataProvider(ABC):
    """Market data access only. No account/execution methods."""

    @abstractmethod
    def get_candles(self, instrument: str, timeframe: str, count: int = 500) -> pd.DataFrame:
        """Return OHLCV DataFrame of CLOSED candles only.
        Columns: time, open, high, low, close, tick_volume
        Sorted oldest-first. 'time' is a column, not the index.
        """
        ...

    @abstractmethod
    def get_current_price(self, instrument: str) -> PriceSnapshot:
        """Return current bid/ask/spread."""
        ...

    @abstractmethod
    def get_candle_freshness(self, instrument: str, timeframe: str) -> CandleFreshness:
        """Return metadata about the last cached candle."""
        ...
```

**OANDA implementation** (`providers/oanda.py`):

- Uses `oandapyV20.API` for all requests
- `oandapyV20.endpoints.instruments.InstrumentsCandles` for candle data
- `oandapyV20.endpoints.pricing.PricingInfo` for current prices
- Normalizes response to canonical schema: OANDA `mid.o/h/l/c` → `open/high/low/close`, `volume` → `tick_volume`
- Skips candles where `complete == False`
- Applies `trim_to_closed()` after fetch
- Applies `validate_candle_df()` before returning
- Rate limited: shared token bucket (100 req/sec), 429 → exponential backoff + jitter, max 3 retries

**Execution provider** (`providers/oanda_execution.py`):

Separate class, separate file. Has `get_account_summary()`, `get_open_positions()`, `get_open_orders()`, `get_open_trades()`. **Never imported by the indicator layer.**

---

### 5.5 Three-Level Cache with Candle-Boundary Freshness

```
Request → Memory Cache → CSV Cache → OANDA API
              ↓               ↓            ↓
         CacheEntry      CacheEntry    CacheEntry
              ↓               ↓            ↓
         TinyDB metadata  TinyDB metadata
```

**Freshness rule:**

```python
def is_cache_fresh(cached_last_candle: datetime, timeframe: str, now_utc: datetime) -> bool:
    """Cache is fresh if it contains the most recent COMPLETED candle."""
    duration = TIMEFRAME_DURATIONS[timeframe]
    current_candle_start = floor_to_boundary(now_utc, duration)
    last_completed_boundary = current_candle_start - duration
    return cached_last_candle >= last_completed_boundary
```

**Cache entry:**

```python
@dataclass
class CacheEntry:
    candles: pd.DataFrame
    last_completed_candle: datetime   # time of the last CLOSED candle
    fetched_at: datetime              # when fetched from source
    source: str                       # "oanda_api" or "csv"
    candle_count: int
```

**Append semantics:** On cache miss, fetch only candles newer than the last cached candle and append. Full refetch only on cold start.

**Metadata persistence:** TinyDB stores `last_completed_candle` and `fetched_at` per `(instrument, timeframe)`. CSV stores the candle data. On restart, metadata is loaded to know what's available without parsing CSVs.

---

### 5.6 SMC Detection

All core SMC detection uses `smartmoneyconcepts` (stateless by design).

| Detection | Package Function |
|-----------|-----------------|
| Swing highs/lows | `smc.swing_highs_lows(ohlc, swing_length=10)` |
| BOS / CHOCH | `smc.bos_choch(ohlc, swing_hl, close_break=True)` |
| Order Blocks | `smc.ob(ohlc, swing_hl, close_mitigation=False)` |
| Liquidity sweeps | `smc.liquidity(ohlc, swing_hl, range_percent=0.01)` |
| Sessions | `smc.sessions(ohlc, session, start_time, end_time, time_zone)` |
| Previous high/low | `smc.previous_high_low(ohlc, time_frame="1D")` |
| Retracements | `smc.retracements(ohlc, swing_hl)` |

**Data format:** `smartmoneyconcepts` expects lowercase column names. Normalize before passing.

**Custom detectors** (not in the package):

| Detector | Implementation |
|----------|---------------|
| Swing Failure Pattern (SFP) | `smc/sfp.py` — stateless pure function |
| Turtle Soup | `smc/turtle_soup.py` — stateless pure function |
| Opening Range Breakout (ORB) | `smc/orb.py` — stateless, uses `smc.sessions()` for session detection |

---

### 5.7 HTF Bias with Ruptures Changepoint Detection

```python
class HTFBiasAnalyzer:
    def compute_bias(self, instrument: str, timeframes: list[str]) -> HTFBiasResult:
        for tf in timeframes:
            df = self.provider.get_candles(instrument, tf)
            df = trim_to_closed(df, tf)

            # SMC structure analysis
            swing_hl = smc.swing_highs_lows(df, swing_length=10)
            bos_choch = smc.bos_choch(df, swing_hl, close_break=True)

            # Ruptures changepoint detection
            changepoints = detect_regime_changes(df["close"].values, penalty=self.penalty)
            # If most recent changepoint is within last N bars → transitioning
            ...
```

**Output:**

```python
class HTFBiasResult(BaseModel):
    direction: str                              # "BULLISH", "BEARISH", "NEUTRAL"
    alignment_score: float                      # 0.0 to 1.0
    timeframe_votes: dict[str, str]             # {"D": "BULLISH", "H4": "BEARISH"}
    structure_breaks: list[StructureBreak]
    regime_changepoints: list[RegimeChangepoint] # from ruptures
    is_transitioning: bool                       # recent changepoint detected
    last_changepoint_bars_ago: int | None
```

---

### 5.8 Technical Indicators

**TA-Lib** (primary — C-accelerated):

| Family | Indicators |
|--------|-----------|
| Trend | EMA, SMA, TEMA, KAMA, SAR |
| Momentum | RSI, MACD, CCI, CMO, PPO, AROON, ADXR |
| Volatility | ATR, NATR, TRANGE, ADX, Bollinger Bands |
| Oscillator | Stochastic %K/%D |

**pandas-ta** (supplementary — fills TA-Lib gaps):

| Indicator | Why pandas-ta |
|-----------|--------------|
| VWAP | Not in TA-Lib. Intraday confluence. |
| Squeeze Momentum | Not in TA-Lib. LazyBear squeeze for low-vol breakout detection. |
| Ichimoku | Not in TA-Lib. Cloud, tenkan, kijun for trend context. |
| Nadaraya-Watson | Not in TA-Lib. Adaptive envelope for dynamic S/R. |

**Tick-volume indicators** (with explicit labeling):

```python
class TickVolumeIndicator(BaseModel):
    name: str                       # "tick_obv", "tick_mfi", "tick_adosc"
    value: float
    volume_type: str = "tick_count"
    source: str = "oanda_otc"
    caveat: str = (
        "Computed from OANDA tick count, not exchange-traded volume. "
        "Not equivalent to CME/NYSE volume. Reflects broker tick activity only."
    )
```

All indicator wrappers are stateless pure functions. NaN warmup values are preserved (not backfilled).

---

### 5.9 Spread Filter

```python
class SpreadFilter:
    def __init__(self, instrument: str):
        self.spec = INSTRUMENT_REGISTRY[instrument]  # fail loud if unknown

    def check(self, bid: float, ask: float) -> SpreadResult:
        raw_spread = ask - bid
        spread_pips = raw_spread / self.spec.pip_size
        is_acceptable = spread_pips <= self.spec.max_spread_pips
        is_spiking = spread_pips > (self.spec.typical_spread_pips * self.spec.spike_multiplier)
        return SpreadResult(
            instrument=self.spec.symbol,
            raw_spread=raw_spread,
            spread_pips=spread_pips,
            pip_size=self.spec.pip_size,
            typical_spread_pips=self.spec.typical_spread_pips,
            max_spread_pips=self.spec.max_spread_pips,
            is_acceptable=is_acceptable,
            is_spiking=is_spiking,
            spread_ratio=spread_pips / self.spec.typical_spread_pips,
        )
```

Spread history is persisted in TinyDB. On restart, the last 100 readings per instrument are loaded to seed the spike detector. No cold-start blind spot.

---

### 5.10 Economic Calendar

```python
CALENDAR_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"

class ForexCalendarProvider:
    def get_events(self) -> list[CalendarEvent]: ...
    def get_upcoming_high_impact(self, hours_ahead: int = 4) -> list[CalendarEvent]: ...
    def is_event_blackout(self, minutes_before: int = 30, minutes_after: int = 15) -> bool: ...
```

Free API, no credentials. Cached for 1 hour. Refreshed automatically via `apscheduler`. Degrades gracefully — calendar data is informational, not blocking.

---

### 5.11 Trade And Alert Storage (TinyDB)

```python
class TradeStore:
    def __init__(self, db_path: str = "data/bot.json"):
        self.db = TinyDB(db_path)
        self.trades = self.db.table("trades")
        self.signals = self.db.table("signals")
        self.spread_history = self.db.table("spread_history")
        self.cache_metadata = self.db.table("cache_metadata")
        self.excursion_samples = self.db.table("excursion_samples")
        self.price_alerts = self.db.table("price_alerts")
        self.indicator_alerts = self.db.table("indicator_alerts")

    def record_signal(self, signal: dict) -> int: ...
    def record_trade(self, trade: dict) -> int: ...
    def record_spread(self, instrument: str, spread_pips: float, is_spiking: bool): ...
    def get_recent_spreads(self, instrument: str, limit: int = 100) -> list[dict]: ...
    def get_cache_metadata(self, instrument: str, timeframe: str) -> dict | None: ...
    def update_cache_metadata(self, instrument: str, timeframe: str, metadata: dict): ...
    def upsert_trade_journal(self, trade: dict) -> int: ...
    def insert_excursion_sample(self, sample: dict) -> int: ...
    def get_trade_mae_mfe(self, trade_id: str) -> dict | None: ...
    def upsert_price_alert(self, alert: dict) -> int: ...
    def upsert_indicator_alert(self, alert: dict) -> int: ...
```

**Trade accounting rule:** Trade records must keep `pips` separate from monetary P&L. A trade can expose:

- `pips`: move in instrument pips, independent of account denomination
- `instrument_pnl`: P&L in the instrument or quote-currency context used by the broker payload
- `instrument_pnl_currency`: currency code for `instrument_pnl`
- `account_pnl`: P&L converted into the account currency
- `account_currency`: account denomination for `account_pnl`

Do not collapse these into a single `pnl` field, and do not infer account-currency P&L from `pips` alone.

**Merged TinyDB ownership:**

- `trades`: trade journal rows, close details, notes, and trade-lifecycle metadata
- `signals`: analysis-signal history if the runtime records it
- `spread_history`: spread observations and spike history
- `cache_metadata`: persisted freshness metadata for `(instrument, timeframe)`
- `excursion_samples`: MAE/MFE sample rows keyed by `trade_id`
- `price_alerts`: fire-once price alert records and status
- `indicator_alerts`: scheduled indicator alert records with repeat and cooloff metadata

Replaces all raw JSON file persistence (`scan_job_history.jsonl`, `price_alerts.json`, `alert_cache.json`). TinyDB is the only persistence backend for the merged runtime.

---

### 5.12 Task Scheduling (APScheduler)

```python
class BotScheduler:
    def start(self):
        # Auto-scan every 5 minutes
        self.scheduler.add_job(scan_fn, IntervalTrigger(minutes=5), id="auto_scan")
        # Open-trade poller
        self.scheduler.add_job(poll_open_trades_fn, IntervalTrigger(seconds=30), id="trade_poller")
        # Cache warm at session opens
        self.scheduler.add_job(warm_fn, CronTrigger(hour=8, minute=0, timezone="UTC"), id="london_warm")
        self.scheduler.add_job(warm_fn, CronTrigger(hour=13, minute=0, timezone="UTC"), id="ny_warm")
        self.scheduler.add_job(warm_fn, CronTrigger(day_of_week="sun", hour=22, timezone="UTC"), id="market_open_warm")
        # Calendar refresh hourly
        self.scheduler.add_job(cal_fn, IntervalTrigger(hours=1), id="calendar_refresh")
        # Indicator alert scan
        self.scheduler.add_job(indicator_scan_fn, IntervalTrigger(minutes=5), id="indicator_scan")
```

---

### 5.13 Observability

**structlog** for all indicator-layer modules. Every log event is a dict.

**Instrumentation points:**

| Event | Fields |
|-------|--------|
| `candles_fetched` | instrument, timeframe, source, candle_count, last_completed_candle, fetch_duration_ms |
| `cache_lookup` | instrument, timeframe, cache_level, hit, cached_last_candle, staleness_seconds |
| `detector_executed` | detector_name, instrument, timeframe, input_candle_count, duration_ms, output_count, is_provisional |
| `snapshot_published` | snapshot_version, instrument, timeframe, last_candle, is_stale |
| `bundle_published` | bundle_version, instrument, members, mixed_freshness, stalest_timeframe |
| `spread_checked` | instrument, spread_pips, threshold_pips, is_acceptable, is_spiking, spread_ratio |
| `current_bar_excluded` | instrument, timeframe, excluded, last_bar_time, reason |
| `htf_bias_computed` | instrument, direction, alignment_score, timeframe_votes, duration_ms |
| `changepoint_detected` | instrument, timeframe, changepoint_index, changepoint_time, method |
| `calendar_fetched` | event_count, next_high_impact, fetch_duration_ms |
| `scan_cycle_completed` | instruments_scanned, total_duration_ms, snapshots_published, errors |
| `trade_event_emitted` | trade_id, instrument, event_type, close_reason, gslo_present |
| `price_tick_fanned_out` | instrument, consumer_count, queue_depths |
| `journal_written` | trade_id, state, notes_present, source_event |
| `alert_fired` | alert_id, alert_kind, instrument, fire_value, repeat_enabled |

**Timing context manager:**

```python
@contextmanager
def timed(logger, event_name: str, **extra):
    start = time.perf_counter()
    try:
        yield
    finally:
        duration_ms = (time.perf_counter() - start) * 1000
        logger.info(event_name, duration_ms=round(duration_ms, 2), **extra)
```

---

## 6. Two-Layer Snapshot Architecture

### Layer 1 — TimeframeSnapshot

Per-timeframe, immutable after publish, versioned, self-contained, reproducible.

```python
class TimeframeSnapshot(BaseModel):
    """Immutable per-timeframe analysis result.

    Properties:
    - Immutable after publish — never mutated, only replaced by a new version.
    - Versioned — monotonic version per (instrument, timeframe).
    - Self-contained — carries all data needed to interpret it.
    - Reproducible — same closed-bar input produces the same snapshot.
    """
    instrument: str
    timeframe: str
    version: int                             # monotonic per (instrument, timeframe)
    last_completed_candle: datetime           # UTC — the bar this snapshot was built from
    computed_at: datetime                     # UTC — when compute finished
    candle_range_start: datetime
    candle_range_end: datetime

    # Analysis results
    indicators: IndicatorValueSummary
    structure: StructureEventSummary
    zones: ActiveZoneSummary
    liquidity: LiquidityPoolSummary
    spread: SpreadResult                     # point-in-time at compute
    chop: ChopResult
    orb: ORBResult | None                    # LTF only

    # Provenance
    freshness: SnapshotFreshness
```

### Layer 2 — InstrumentBundle

Instrument-level aggregate that pins exact snapshot versions. This is the object downstream consumers read.

```python
class InstrumentBundle(BaseModel):
    """Pins exact TimeframeSnapshot versions. Downstream consumers read this.

    It answers: 'HTF bias and instrument-level analysis were computed
    using exactly these member snapshots.'
    """
    instrument: str
    bundle_version: int                      # monotonic per instrument
    created_at: datetime

    # Pinned members
    members: dict[str, int]                  # {"H1": 1842, "H4": 771, "D": 229}

    # Cross-timeframe analysis (computed FROM pinned members)
    htf_bias: HTFBiasResult
    calendar: list[CalendarEvent]
    calendar_version: int

    # Freshness
    mixed_freshness: bool                    # True if any member TF is stale
    stalest_timeframe: str | None
    stalest_age_seconds: float
    member_freshness: dict[str, SnapshotFreshness]
```

### Why Two Layers

| Concern | Layer 1 (TimeframeSnapshot) | Layer 2 (InstrumentBundle) |
|---------|-----------------------------|----------------------------|
| Scope | Single (instrument, timeframe) | All timeframes for one instrument |
| Mutability | Immutable after publish | Immutable after publish |
| Versioning | Per-(instrument, timeframe) | Per-instrument |
| HTF bias | Not here — single TF | Lives here — computed from pinned members |
| Calendar | Not here — not TF-specific | Lives here — instrument-level context |
| Mixed freshness | N/A | Explicit: `mixed_freshness`, `stalest_timeframe` |
| Reproducibility | Same closed bars → same snapshot | Same member versions → same bundle |
| Who reads it | Internal — used to build bundles | Public — signal evaluator, commands, alerts |

### MarketStateStore

```python
class MarketStateStore:
    """Thread-safe, two-layer market state store."""

    # Layer 1
    def publish_snapshot(self, snapshot: TimeframeSnapshot): ...
    def get_snapshot(self, instrument: str, timeframe: str) -> TimeframeSnapshot | None: ...
    def get_snapshot_version(self, instrument: str, timeframe: str, version: int) -> TimeframeSnapshot | None: ...

    # Layer 2
    def publish_bundle(self, bundle: InstrumentBundle): ...
    def get_bundle(self, instrument: str) -> InstrumentBundle | None: ...
    def assemble_bundle(self, instrument: str, timeframes: list[str], htf_bias, calendar, calendar_version) -> InstrumentBundle: ...
```

All published objects are immutable deep copies. Readers never observe partial updates. Historical snapshot versions retained (default: 5 per key) for bundle resolution.

---

## 7. Process Walkthroughs

### 7.1 Full Scan Cycle

```
scan_all_instruments()
│
├── Check is_market_open() via pandas_market_calendars
│   └── If closed → log, return stale bundles
│
├── Check calendar blackout via ForexCalendarProvider
│   └── If blackout → log warning, proceed with caution flag
│
├── For each instrument (XAU_USD, EUR_USD, ...):
│   │
│   ├── For each timeframe (M15, H1, H4, D):
│   │   │
│   │   ├── Fetch candles (cache-aware, candle-boundary freshness)
│   │   │   └── Log: candles_fetched or cache_lookup
│   │   │
│   │   ├── trim_to_closed(df, timeframe)
│   │   │   └── Log: current_bar_excluded
│   │   │
│   │   ├── Run detectors:
│   │   │   ├── SMC: smc.analyze(df) → zones, structure, liquidity
│   │   │   ├── Indicators: talib + pandas-ta → IndicatorValueSummary
│   │   │   ├── Spread: SpreadFilter.check() → SpreadResult
│   │   │   ├── Chop: ChopFilter.check() → ChopResult
│   │   │   └── ORB (LTF only): ORBDetector.detect() → ORBResult
│   │   │   └── Log: detector_executed (per detector, with duration_ms)
│   │   │
│   │   └── Publish TimeframeSnapshot (Layer 1)
│   │       └── Log: snapshot_published
│   │
│   ├── Compute HTF bias (uses pinned snapshots for D, H4, H1)
│   │   ├── SMC BOS/CHOCH per timeframe
│   │   ├── Ruptures changepoint detection
│   │   └── Log: htf_bias_computed, changepoint_detected
│   │
│   └── Assemble & publish InstrumentBundle (Layer 2)
│       └── Log: bundle_published (with members, mixed_freshness)
│
└── Log: scan_cycle_completed
```

### 7.2 User Runs `/bias XAU_USD`

```
/bias XAU_USD
│
├── Read InstrumentBundle from MarketStateStore
│   └── If stale or missing → trigger scan for XAU_USD
│
├── Extract htf_bias from bundle
│   ├── direction, alignment_score
│   ├── timeframe_votes
│   ├── regime_changepoints (from ruptures)
│   └── is_transitioning
│
├── Check calendar events in bundle
│   └── Flag if near high-impact event
│
└── Return to bot layer for Telegram formatting
```

### 7.3 User Runs `/smc XAU_USD H1`

```
/smc XAU_USD H1
│
├── Read TimeframeSnapshot from MarketStateStore
│   └── If stale or missing → fetch + compute + publish
│
├── Extract from snapshot:
│   ├── zones (OBs)
│   ├── structure (BOS/CHOCH events)
│   ├── liquidity (equal highs/lows, sweeps)
│   └── freshness metadata
│
└── Return to bot layer for formatting
```

---

## 8. Tests

### Test Structure

```
tests/
├── conftest.py                          # Shared fixtures
├── unit/
│   ├── test_candle_policy.py            # trim_to_closed, validate_candle_df, determinism, immutability
│   ├── test_instrument_registry.py      # all 12 instruments present, pip values correct
│   ├── test_spread_filter.py            # per-instrument thresholds, spike detection, unknown instrument
│   ├── test_tick_volume.py              # tick_obv/mfi/adosc carry caveats, canonical columns
│   ├── test_cache_freshness.py          # candle-boundary freshness (not TTL), cross-boundary stale
│   ├── test_candle_schema.py            # column validation, time-as-index reset, naive→UTC coercion
│   ├── test_models.py                   # Pydantic model serialization/validation
│   ├── test_forex_calendar.py           # parse FF JSON, high-impact filter, event blackout
│   ├── test_trade_store.py              # TinyDB CRUD, spread history, cache metadata upsert
│   ├── test_trade_repository.py         # TinyDB trade journal CRUD and notes
│   ├── test_excursion_repository.py     # sample persistence and MAE/MFE aggregation
│   ├── test_alert_repository.py         # price and indicator alert CRUD
│   ├── test_account_poller.py           # open/close/modify detection and close reasons
│   ├── test_journal_service.py          # trade journal writes and notifications
│   ├── test_excursion_tracker.py        # pip math and min-move filtering
│   ├── test_price_alert_engine.py       # above/below crossing and fire-once behavior
│   ├── test_indicator_alert_engine.py   # RSI/STOCH/MACD evaluation and repeat logic
│   ├── test_security.py                 # admin gating and access checks
│   └── test_htf_bias_ruptures.py        # trend change detection, no false positives, transitioning flag
└── integration/
    ├── test_snapshot_publication.py      # Layer 1 + Layer 2 atomic publish, version monotonicity,
    │                                    #   concurrent read/write safety, mixed freshness detection,
    │                                    #   bundle pins new snapshot after update
    ├── test_provider_cache.py           # end-to-end cache hit/miss/stale, append semantics
    ├── test_observability.py            # structured log emission for fetch, cache, detector, snapshot,
    │                                    #   bundle, spread check events
    ├── test_journal_lifecycle.py        # open -> ticks -> close journal flow
    ├── test_price_alert_fire.py         # persisted alert -> tick cross -> fire
    └── test_indicator_alert_fire.py     # scheduled scan -> alert fire
```

### Key Test Invariants

| Test | What it proves |
|------|----------------|
| `trim_to_closed` drops forming bar | Detectors never run on incomplete data |
| `trim_to_closed` is idempotent | Trimming twice gives same result |
| `trim_to_closed` does not mutate input | Input DataFrame is never changed |
| `validate_candle_df` rejects missing columns | Schema violations caught at boundary |
| `validate_candle_df` resets time index to column | `time` is never the index in output |
| `validate_candle_df` coerces naive time to UTC | No timezone ambiguity |
| `validate_candle_df` rejects `volume` column | Must be `tick_volume` |
| `is_cache_fresh` uses candle boundary | TTL cannot override boundary staleness |
| H1 cache becomes stale at xx:00 | Candle-close triggers refresh |
| Snapshot version is monotonic | Versions always increase |
| Historical snapshot version retrievable | Bundle can resolve pinned members |
| Bundle detects mixed freshness | Stale + fresh members → `mixed_freshness=True` |
| Bundle pins new version after snapshot update | Reassembly picks up latest snapshots |
| Concurrent read/write never returns partial | Thread safety of MarketStateStore |
| Tick-volume indicators carry caveat | `volume_type == "tick_count"`, caveat present |
| Unknown instrument raises KeyError | No silent fallback to wrong defaults |
| All 12 scan instruments in registry | No instrument missed |
| Gold spread uses gold thresholds (not default) | Instrument-specific, not generic |
| Spread spike detected against typical (not deque) | No cold-start blind spot |
| `snapshot_published` log emits version | Observability contract enforced |
| `bundle_published` log emits members | Cross-timeframe traceability |
| Trade journal CRUD preserves notes and close fields | Journal persistence stays typed and explicit |
| Excursion aggregation returns stored MAE and MFE maxima | MAE/MFE is derived from persisted samples, not inferred ad hoc |
| Account poller emits open, close, and modify events deterministically | Read-only trade lifecycle tracking is reproducible |
| Price alerts fire once on the documented bid/ask rule | Background alerts do not double-fire |
| Indicator alerts honor repeat and cooloff | Scheduled evaluation stays bounded and predictable |

---

## 9. Development Priorities

| Priority | System | Effort | Impact |
|----------|--------|--------|--------|
| **P0** | Candle policy (`trim_to_closed`, `validate_candle_df`) | Low | Foundation — everything depends on this |
| **P0** | Instrument registry | Low | Foundation — spread, pip calc depend on this |
| **P0** | Market data provider (oandapyV20 + cache) | Medium | Foundation — all analysis depends on data |
| **P0** | Canonical models (TimeframeSnapshot, InstrumentBundle) | Medium | Foundation — all state depends on this |
| **P0** | MarketStateStore (two-layer) | Medium | Foundation — all reads depend on this |
| **P0** | structlog setup | Low | Foundation — observability from day one |
| **P1** | SMC detection (smartmoneyconcepts wrapper) | Medium | Core analysis |
| **P1** | TA-Lib indicator wrappers | Low | Core analysis |
| **P1** | pandas-ta supplementary indicators | Low | Core analysis |
| **P1** | Spread filter (instrument-aware) | Low | Signal quality gate |
| **P1** | Chop filter | Low | Signal quality gate |
| **P1** | HTF bias with ruptures | Medium | Directional context |
| **P1** | Tick-volume indicators (with caveats) | Low | Supplementary analysis |
| **P1** | Custom detectors (SFP, Turtle Soup, ORB) | Medium | Supplementary analysis |
| **P2** | Economic calendar (faireconomy API) | Low | Event awareness |
| **P2** | TinyDB trade store | Low | Persistence |
| **P2** | APScheduler integration | Low | Automation |
| **P2** | mplfinance charting | Low | Visualization |
| **P2** | Scan orchestrator | Medium | Ties everything together |
| **P3** | Macro data via yfinance (VIX, DXY) | Low | Context enrichment |
| **P3** | Market hours via pandas_market_calendars | Low | Scheduling refinement |

Implementation status note (2026-03-21): Stage 10 is complete. The P2 economic-calendar and TinyDB persistence foundation is implemented; the remaining P2 work continues in later stages.

---

## 10. Non-Goals and Boundaries

### Out of Scope

| Concern | Where It Lives |
|---------|---------------|
| Signal evaluation / confidence scoring | Separate signal layer — consumes `InstrumentBundle` |
| Trade plan generation (entry/SL/TP) | Separate signal layer |
| Grade assignment (A+/A/B/REJECT) | Separate signal layer |
| Automated execution | Separate execution layer — uses `OandaExecutionProvider` |
| ML-based signal classification | Requires accumulated data. Not part of the indicator layer. |
| Multi-broker abstraction | `MarketDataProvider` interface enables it, but only OANDA is implemented. |
| Backtesting framework | Separate concern. Indicator and trade-helper layers produce state; backtesting consumes it. |

### Known Risks

| Risk | Mitigation |
|------|------------|
| `smartmoneyconcepts` breaking API changes | Pin version. Test against specific version. |
| `oandapyV20` is sync (blocks event loop) | Wrap in `asyncio.to_thread()`. Don't fake async. |
| `ruptures` penalty sensitivity | Start with `penalty=10.0` for daily. Make configurable. Tune per timeframe. |
| TinyDB slows at scale (10k+ records) | Monitor query time and optimize document ownership before considering any future persistence-plan rewrite. |
| Calendar API (`nfs.faireconomy.media`) has no SLA | Cache aggressively. Degrade gracefully — informational, not blocking. |
| Tick-volume indicators may have zero predictive value | Monitor usage. Remove if unused after 3 months. |

### Critical Design Rules

1. Same closed-bar input → same detector output. No hidden state.
2. No account/execution leakage into analysis interfaces.
3. No ambiguous candle schema. `time` is a column. `tick_volume` is the name.
4. No TTL-only freshness. Candle-boundary freshness only.
5. No generic spread thresholds. Every instrument has explicit metadata.
6. No silent misuse of volume on OTC data. Everything is labeled `tick_*`.
7. No black-box state updates. Every state change is structlog'd.
8. Layer 1 snapshots are immutable and versioned. Layer 2 bundles pin exact versions.
9. No dead feature flags. Ship it enabled or don't ship it.
10. No god-function orchestrators. Each detector is independently callable and testable.
11. No FVG. Fair Value Gaps are not part of V3. Do not call `smc.fvg()`. Do not add FVG to any model, snapshot, or command.

---

## 11. Telegram Commands

> Rudimentary command list for V3. Analysis commands read from the snapshot and bundle layer, while trade-helper commands read or persist state through the approved TinyDB-backed service layer. No command triggers raw API calls or runs detectors inline. Auth-gated unless marked otherwise.

### General

| Command | Args | Description | Auth |
|---------|------|-------------|------|
| `/start` | — | Authenticate with bot password | No |
| `/help` | — | List available commands | No |
| `/logout` | — | End current session | Yes |
| `/status` | — | Bot uptime, scheduler state, last scan time, cache health, and trade-helper runtime health | Yes |

### Market Data

| Command | Args | Description |
|---------|------|-------------|
| `/price <instrument>` | `XAU_USD`, `EUR_USD`, … | Current bid/ask/spread from OANDA |
| `/marketstatus` | — | Market open/closed, session (London/NY/Tokyo/Sydney), next open/close |
| `/session` | — | Active trading session details and overlap windows |
| `/calendar` | `[today\|week]` | Upcoming high-impact economic events from ForexFactory |

### Analysis — Single Instrument

| Command | Args | Description |
|---------|------|-------------|
| `/smc <instrument> [timeframe]` | `XAU_USD H1` | SMC context: BOS/CHOCH, OBs, liquidity from snapshot |
| `/bias <instrument>` | `XAU_USD` | Multi-timeframe HTF bias with alignment score and regime changepoints |
| `/ob <instrument> [timeframe]` | `XAU_USD H4` | Order block zones (mitigated/unmitigated) |
| `/sr <instrument>` | `XAU_USD` | Support/resistance levels |
| `/sfp <instrument> [timeframe]` | `XAU_USD H4` | Swing Failure Patterns |
| `/turtlesoup <instrument> [timeframe]` | `GBP_USD M15` | Turtle Soup reversal patterns |
| `/indicators <instrument> [timeframe]` | `XAU_USD H1` | Technical indicators (EMA, RSI, ATR, MACD) from snapshot |
| `/structure <instrument> [timeframe]` | `XAU_USD H4` | Market structure (BOS/CHOCH history) |

### Multi-Instrument Scanning

| Command | Args | Description |
|---------|------|-------------|
| `/scan [instrument]` | `XAU_USD` or omit for all | Run scan cycle, publish snapshots + bundles, return signals |

### Charting

| Command | Args | Description |
|---------|------|-------------|
| `/chart <instrument> <timeframe> [--count N] [--overlays clean\|smc\|indicators] [--smc orderblocks\|structure\|liquidity] [--trade positions\|orders\|sl\|tp\|gslo] [--alert pricealerts] [--indicator ema\|bollinger\|vwap\|rsi\|macd]` | `XAU_USD H1 --count 500 --overlays smc` | mplfinance chart with process-isolated rendering, candle-focused overlays, runtime trade, pending-order, and alert layers, and explicit selector flags. |

### Alerts

| Command | Args | Description |
|---------|------|-------------|
| `/pricealert <instrument> <price>` | `XAU_USD 2000` | Set price alert (persisted in TinyDB) |
| `/listalerts` | — | List your active price alerts |
| `/clearalerts [id]` | `3` or omit for all | Clear one or all alerts |

### Trade Journal

| Command | Args | Description |
|---------|------|-------------|
| `/journal [trade_id] [--instrument <instrument>] [--from <date>] [--to <date>]` | `12345678` | View recent journal rows or one specific trade record |
| `/label <trade_id> <text>` | `12345678 runner idea` | Set or update the trade note |
| `/maemfe [trade_id]` | `12345678` | Show MAE/MFE for all open trades or one trade |

### Indicator Alerts

| Command | Args | Description |
|---------|------|-------------|
| `/indicatoralert <instrument> <timeframe> <indicator> <condition> [threshold] [note]` | `XAU_USD H1 RSI below 30 oversold watch` | Set an automatically evaluated RSI, Stochastic, MACD, or SMA-cross alert on `M15`, `H1`, `H4`, or `D` |
| `/listindicators` | — | List active indicator alerts |
| `/clearindicator <id>` | `4` | Clear one indicator alert |

### Account (via ExecutionProvider)

| Command | Args | Description |
|---------|------|-------------|
| `/account` | — | Account summary (balance, margin, unrealized P&L) |
| `/positions` | — | Open positions |
| `/orders` | — | Pending orders |

### Configuration

| Command | Args | Description |
|---------|------|-------------|
| `/config [key] [value]` | `spread_mode strict` | View or update runtime config (spread thresholds, chop filter, scan interval) |
| `/extractor <instrument> [count] [timeframes]` | `EUR_USD 200 H1 H4` | Export candles to CSV |

### Admin Only

| Command | Args | Description |
|---------|------|-------------|
| `/security` | — | Security stats: failed logins, active sessions, ban list |
| `/ban <user_id>` | `123456789` | Ban a user |
| `/unban <user_id>` | `123456789` | Unban a user |
| `/sessions` | — | View all active sessions |
| `/override <instrument> <direction>` | `XAU_USD long` | Clear signal cooldown for instrument |
| `/mute` | — | Suppress auto-scan signal alerts (commands still work) |
| `/unmute` | — | Resume auto-scan alerts |
| `/scheduler [pause\|resume\|status]` | — | Control APScheduler (pause/resume auto-scans, view job list) |

### Design Notes

- **No inline computation**: Every analysis command reads from `MarketStateStore`. If the snapshot or bundle is stale or missing, the command triggers a targeted scan cycle, then reads the result. Commands never call detectors directly.
- **Trade-helper command boundary**: Journal, MAE/MFE, and alert commands read or persist state through the documented TinyDB-backed services. They do not invent trade execution paths.
- **Instrument argument**: Accepts OANDA format (`XAU_USD`). Invalid instruments rejected against the instrument registry — no silent defaults.
- **Timeframe argument**: Optional on most analysis commands. Defaults to `H1` if omitted. Accepts: `M1`, `M5`, `M15`, `M30`, `H1`, `H4`, `D`.
- **Auth**: All commands require an active session except `/start` and `/help`. Auth handled by `require_auth` decorator.
- **New in V3**: `/calendar`, `/scheduler`. Removed: no standalone commands that duplicate scan output.
- **Explicitly excluded**: FVG (Fair Value Gaps) is **not** part of V3. Do not implement `smc.fvg()`, do not add an `/fvg` command, do not include FVG zones in snapshots or bundles. FVG detection adds noise without actionable signal value for the instruments traded.

---

### requirements.txt

```
# Core runtime
python-dotenv>=1.0.0
pydantic>=2.0
pydantic-settings>=2.0
oandapyV20>=0.7.2
pandas>=2.0.0
numpy>=1.24.0

# Analytics
TA-Lib>=0.6.8
pandas-ta
smartmoneyconcepts>=0.0.26
ruptures

# Charting
mplfinance

# Storage
tinydb

# Scheduling
apscheduler>=3.10

# Observability
structlog

# Telegram
python-telegram-bot>=21.0

# HTTP (used by oandapyV20 internally + calendar fetch)
requests>=2.31.0

# Market data & calendars
yfinance>=0.2.40
pandas_market_calendars>=4.4.0

# Testing
pytest
pytest-asyncio>=0.23.0
pytest-mock
time-machine
freezegun>=1.2.0
responses
```

---

*Document version: v3.0 — March 2026*
