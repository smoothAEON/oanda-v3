# Stage 04 - Instrument Registry, Market Data, And Cache

> Historical stage spec. The repo now has implemented code beyond this checklist. Keep this document for design lineage, and use [README](../../README.md), [tracker.md](../tracker.md), and [COMMANDS.md](../COMMANDS.md) for current behavior and status.

- Parent Phase: Phase 1 / P0 - Foundation Contracts And Determinism
- Tracker: [tracker.md](../tracker.md)
- Feature IDs: `S04-F01`, `S04-F02`, `S04-F03`

## Purpose

Define the explicit instrument metadata registry, the analysis-safe market-data provider boundary, and the candle-boundary-aware cache model. This stage removes silent fallbacks, generic spread behavior, and ambiguous freshness semantics. It also pulls forward the baseline `structlog` bootstrap and the narrow `TradeStore` ownership of cache metadata so later runtime stages extend these seams instead of duplicating them.

## Source Anchors

- [V3_PLAN.md](../V3_PLAN.md): Sections 1.1, 1.4, 1.5, 5.3, 5.4, 5.5, 8, 10
- [v3_delivery_phases.md](../v3_delivery_phases.md): Phase 1
- [v3_approval_test_spec.md](../v3_approval_test_spec.md): Phase 1 gate and Phase 3 provider-cache regression

## Scope

- Create the instrument registry for the 12 supported scan instruments (see [`GLOSSARY.md`](../GLOSSARY.md#supported-scan-instruments) for the canonical list and instrument classes).
- Define the `MarketDataProvider` contract and the OANDA market-data implementation boundary.
- Define the three-level cache and persisted freshness metadata model.
- Bootstrap provider and cache logging for the Phase 1 fetch, cache, and bar-exclusion evidence.
- Introduce the TinyDB-backed `TradeStore` only as far as needed to own `cache_metadata`.

## Dependencies

- Stage 02 settings for OANDA access and cache path configuration.
- Stage 03 canonical candle schema and closed-bar policy.

## Implementation Checklist

- [x] `S04-F01` Define `InstrumentSpec` and register all 12 source-plan instruments with explicit pip size, typical spread, max spread, and spike multiplier metadata.
- [x] `S04-F02` Define `MarketDataProvider` to expose candles, current price, and freshness only; implement OANDA market-data access through `oandapyV20` with canonical schema normalization and no execution methods.
- [x] `S04-F03` Define the three-level cache as memory, CSV, and API, with freshness controlled by `last_completed_candle` plus `fetched_at` metadata persisted in TinyDB, never by TTL alone.

## Public Interfaces And Contracts

- `InstrumentSpec`
  Must be the single source of truth for pip convention and spread behavior.
- `INSTRUMENT_REGISTRY`
  Must include exactly the 12 supported scan instruments listed in [`GLOSSARY.md`](../GLOSSARY.md#supported-scan-instruments). Symbol aliases and input normalization rules are also defined there.
- `MarketDataProvider`
  Must expose candles, current price, and freshness. It must not expose account, trades, orders, or positions.
- Cache metadata contract
  Must persist `(instrument, timeframe)` keys with `last_completed_candle` and `fetched_at`.
- `TradeStore`
  Owns the `cache_metadata` table in Stage 04; later stages extend this same store rather than replacing it.
- `configure_logging()`
  Bootstraps process-wide `structlog` configuration in Stage 04 so provider and cache events exist before orchestration work begins.

## Tests And Approval Evidence

- `tests/unit/test_instrument_registry.py`
  Must prove all 12 instruments exist and prevent generic-default behavior.
- `tests/unit/test_cache_freshness.py`
  Must prove candle-boundary freshness and cross-boundary staleness.
- `tests/integration/test_provider_cache.py`
  Must prove cache hit, miss, append, and stale-refresh behavior end to end.
- `tests/integration/test_observability.py`
  Must prove `cache_lookup`, `candles_fetched`, and `current_bar_excluded` are emitted with the required fields.
- Static inspection must prove analysis modules do not import or depend on `ExecutionProvider`.
- `tests/unit/test_account_runtime_clients.py`
  Must prove the read-only account and stream clients normalize UTC timestamps, emit typed stream events, and use `asyncio.to_thread()` honestly at the public async boundary.

## Risks And Watchpoints

- Any unknown-instrument fallback will invalidate spread logic and pip calculations downstream.
- Freshness logic duplicated in multiple places will create disagreement between cache and scheduler behavior.
- Provider methods that return account state will reintroduce analysis-execution coupling.

## Exit Criteria

- The registry covers all 12 supported instruments and fails loudly on unknown ones.
- Market-data access is limited to candles, price, and freshness.
- Cache freshness is driven by candle completion boundaries with persisted metadata and no TTL-only shortcut.
- The baseline fetch, cache, and bar-exclusion log events exist before Stage 11 orchestration work.

## Explicit Exclusions

- No execution provider behavior is part of this stage.
- No spread gate logic lives here beyond raw metadata and current-price delivery.
- No charting, orchestration, or command behavior is implemented here.

## Trade Helper Additions

- Define a separate read-only OANDA runtime boundary in `providers/account_client.py`. This file wraps the OANDA REST calls that return account and trade state: `get_open_trades()`, `get_trade_detail(trade_id)`, `get_pricing(instrument)`, and `get_candles(instrument, granularity, count)`. It is the only file in the trade-helper path that touches OANDA REST directly.
- Define `providers/stream_client.py` to wrap the OANDA price-stream endpoint as an async generator yielding `PriceTick` and `Heartbeat` dataclasses. It performs no analysis and no persistence — only typed event emission.
- Both clients use `asyncio.to_thread()` for blocking `oandapyV20` calls, consistent with the existing `providers/oanda.py` pattern.
- `providers/account_client.py` and `providers/stream_client.py` must not be imported by any analysis module (`smc/`, `indicators/`, `filters/`). The import-smoke boundary test must assert this separation alongside the existing `ExecutionProvider` exclusion check.
- The instrument registry pip-size metadata (already defined for all 12 scan instruments) is reused directly for pip-distance calculations in trade alerts and MAE/MFE. No second pip-size table is introduced.
- Keep this boundary adjacent to, but separate from, the analysis-safe `MarketDataProvider`. Analysis modules still must not import account-runtime clients.
- Price streaming and trade polling contracts defined here become the provider inputs for later journal, excursion-tracking, and alert-engine stages.
- These clients remain read-only in this stage. Polling, fan-out, and supervised background runtime behavior stay deferred to Stage 11.
