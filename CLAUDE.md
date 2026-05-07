# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repo Summary

Market Signal Bot V3 is a Python Telegram bot for read-only OANDA market analysis and account monitoring. It does not place trades. `providers/oanda_execution.py` is a reserved stub only.

Current shipped behavior: multi-timeframe scan pipeline (M15/H1/H4/D), SMC summaries, TA-Lib/pandas-ta indicators, raw spread evidence, LLM-first MCP context packs, Telegram bot with auth/account/analysis/chart/journal/alert commands, trade journaling with MAE/MFE, price and indicator alert evaluation with Telegram push delivery, trade-open and trade-close push delivery, CSV candle cache with TinyDB metadata.

Not wired yet: broker execution, admin commands, macro enrichment via yfinance, CI/release automation.

Authoritative docs: `README.md`, `docs/COMMANDS.md`, `docs/GLOSSARY.md`, `docs/tracker.md`. Historical planning docs under `docs/V3_PLAN.md` and `docs/v3_stages/` are lineage references, not the live status board.

## Build And Run

```powershell
py -3.10 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
pip install smartmoneyconcepts==0.0.26 --no-deps
pip install -e . --no-deps
Copy-Item .env.example .env
```

`smartmoneyconcepts` must be installed separately with `--no-deps` because its published dependency metadata conflicts with the approved pandas stack. TA-Lib must be installed on the host before the Python wrapper will work.

Run: `python main.py` or `python -m bot.main`

## Tests

```bash
pytest                                    # unit + integration (default from pyproject.toml)
pytest tests/unit -v                      # unit only
pytest tests/integration -v              # integration only
pytest tests/live -v -m live             # live tests (real OANDA credentials required)
pytest tests/unit/test_foo.py -v         # single file
pytest tests/unit/test_foo.py::test_bar  # single test
```

`asyncio_mode = "auto"` is set in `pyproject.toml` — no need for `@pytest.mark.asyncio`.

## Runtime Architecture

### Analysis path

```text
OANDA market data -> CandleCache -> ScanOrchestrator -> MarketStateStore -> command handlers
```

### Trade-helper path

```text
OANDA account REST + pricing stream -> TradePollerTask + PriceStreamTask -> TinyDB -> command handlers
```

Key boundaries:

- Analysis modules must not import account-state or execution paths
- Command handlers read published state from `MarketStateStore` first; trigger targeted refresh only when stale or missing — never run detectors inline
- Blocking provider and TinyDB calls from async handlers must go through `asyncio.to_thread()`
- Closed-market refreshes use cached candles only unless `force=True` is explicitly passed by the user; no fabricated freshness provenance

## Coding Conventions

### Models

All public contracts inherit from `FrozenModel(BaseModel)` with `ConfigDict(extra="forbid", frozen=True)`. All datetimes must be timezone-aware UTC — naive datetimes raise `ValidationError`. OANDA volume is tick count; always use `tick_volume`, never `volume`.

### Settings

`config/settings.py` uses Pydantic `BaseSettings` with `frozen=True`. Process-wide singleton via `@lru_cache(maxsize=1)` on `get_settings()`. For overridable runtime knobs, read from `RuntimeConfigManager` (TinyDB-backed), not `Settings` directly.

### Dependency wiring

`bot/runtime.py` assembles the full dependency graph in `build_runtime()`. Dependencies are stored in `BotRuntime.bot_data()` dict and injected into Telegram handlers via `application.bot_data`. Command handlers extract dependencies through private helper functions like `_runtime(context)`, `_security_manager(context)`.

### Provider abstraction

`providers/base.py` defines `MarketDataProvider` as a Protocol (structural typing). Analysis modules depend on this Protocol; account/execution modules are isolated.

### Auth pattern

Every authenticated command must call `await _require_session(update, context)` first. Only `/start <password>` is available without a session. `/help` requires authentication.

### Error handling

Use `log_failure(LOGGER, event_name, exception, **context_fields)` for structured error logging. Distinguish `PersistenceWriteError` from validation errors.

## Test Patterns

Tests use hand-rolled fakes and dummy objects (`DummyMessage`, `DummyContext`, `DummyUpdate`, `FakeSecurityManager`, etc.) defined inline in test files. Model factories like `build_freshness()`, `build_spread()` create realistic instances with defaults. Shared reference time: `BASE_TIME = datetime(2026, 3, 20, 10, 0, tzinfo=UTC)`.

## Current Command Surface

Registered in `bot/bot.py` via `COMMAND_REGISTRY`. See `docs/COMMANDS.md` for exact syntax. Old doc names like `/listalerts` and `/clearalerts` are stale; current names are `/listpricealerts`, `/clearpricealert`, etc.

## Runtime Config

Persisted runtime overrides via `bot/runtime_config.py` and the `/config` command:

- `chart`, `chart_mode`, `scan_interval`, `trade_push`, `session_alerts`

## Alert Runtime

Alert engines (`alerts/price_alert_engine.py`, `alerts/indicator_alert_engine.py`, `alerts/time_alert_engine.py`) can fire and push through the live Telegram runtime. `alerts/alert_repository.py` handles persistence.

## Architectural Guardrails

These are hard constraints from `docs/v3_must_not_do.md` — reject code that violates them:

- No automated trading, order placement, or broker write paths
- No Fair Value Gap support anywhere (no `smc.fvg()`, no FVG fields, no `/fvg` command)
- No detector output on forming candles — closed-bar analysis only
- No TTL-only freshness — must follow completed candle boundaries
- No generic spread fallback — every instrument needs explicit registry metadata
- No raw DataFrames in public state — use typed, compact models
- No analysis importing execution/account-state modules
- No inline detector execution in command handlers
- No direct TinyDB writes from Telegram handlers — go through the service layer
- No custom matplotlib candlestick rendering — use mplfinance
- No faking async around blocking sync calls — use `asyncio.to_thread()`
