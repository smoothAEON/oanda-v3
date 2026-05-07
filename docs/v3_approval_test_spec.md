# V3 Approval Test Spec

> Historical approval-plan document. The repo has working code and test suites today, but this file is not maintained as the live implementation tracker. Use [tracker.md](./tracker.md) for current status and [README](../README.md) for current runtime behavior.

## Source Of Truth

`V3_PLAN.md` is the approval authority for this document. This spec translates the source plan's test inventory, critical design rules, process walkthroughs, and scope boundaries into phase gates. If any approval check below appears looser than the source plan, the source plan wins.

This document is phase-aligned with `docs/v3_delivery_phases.md` and intentionally adds scope-fidelity checks so approval can fail for incorrect widening, not just for missing functionality.

Stage reshuffle note:

- Stage 16 now owns operator and trader workflow completion and still requires feature-local tests.
- Stage 17 now owns the broad CI, deployment, backup, runbook, and core-release gate.
- Stage 18 now owns deferred macro and refined market-hours enrichment.
- Historical Stage 14 security and admin work remains non-blocking for the Stage 17 core release gate.

## Locked Contracts Under Test

- `MarketDataProvider` stays limited to candles, current price, and freshness. Read-only account polling and streaming stay separate and are not importable from analysis modules. Source anchors: Sections 1.1, 5.4, 10, 11.
- The only canonical candle schema is `time`, `open`, `high`, `low`, `close`, `tick_volume`, with `time` as a UTC-aware column and never an index. Source anchors: Sections 1.2, 1.3, 5.2, 8.
- The registry must cover the 12 instruments named in Section 5.3, and unknown instruments must raise loudly instead of falling back. Source anchors: Sections 1.5, 5.3, 8.
- Cache freshness is determined by the most recent completed candle boundary, not TTL-only recency. Source anchors: Sections 1.4, 5.5, 8, 10.
- `TimeframeSnapshot`, `InstrumentBundle`, and `MarketStateStore` must preserve immutability, version monotonicity, historical member resolution, atomic publication, and explicit mixed freshness. Source anchors: Sections 1.8, 1.15, 6, 8.
- The read-only trade-helper runtime must expose typed trade, excursion, pending-order, price-alert, indicator-alert, tick, heartbeat, and trade-event contracts rather than ad hoc blobs. Source anchors: Sections 1.1, 5.3-5.13, 10, 11.
- TinyDB is the merged persistence backend for runtime state. Approval fails if any persistence backend other than TinyDB is introduced into the merged plan.
- Chart rendering must use `mplfinance` in isolated workers, keep candlesticks in focus, and delete temporary artifacts after each render. Source anchors: Sections 1.12, 4, 11.
- Tick-volume outputs must remain clearly labeled as tick-count-derived with caveats. Source anchors: Sections 1.6, 5.8, 8.
- FVG remains excluded from every approval path. Source anchors: Intro scope note, Sections 5.6, 10, 11.

## Phase 1 Approval Gate - Foundation Contracts And Determinism (P0)

Source anchors: Sections 1.1-1.8, 1.10, 1.15, 5.1-5.5, 5.13, 6, 8, 9.

### Required Source-Test Coverage

- `tests/unit/test_settings.py`
  Must prove required-key validation, invalid-environment rejection, defaults, and trade-helper runtime setting coverage.
- `tests/unit/test_candle_policy.py`
  Must prove `trim_to_closed` drops the forming bar, is idempotent, and does not mutate its input.
- `tests/unit/test_candle_schema.py`
  Must prove `validate_candle_df` rejects missing columns, resets a time index back to a `time` column, coerces naive timestamps to UTC, and rejects `volume` in favor of `tick_volume`.
- `tests/unit/test_instrument_registry.py`
  Must prove all 12 instruments from Section 5.3 are present and their pip/spread metadata are explicit enough to prevent generic-default behavior.
- `tests/unit/test_cache_freshness.py`
  Must prove cache freshness is controlled by candle-boundary logic, including cross-boundary staleness, rather than TTL-only recency.
- `tests/unit/test_models.py`
  Must prove the Pydantic public-state models serialize and validate correctly, including trade-helper trade, excursion, alert, tick, heartbeat, and trade-event contracts.
- `tests/unit/test_account_runtime_clients.py`
  Must prove the read-only account runtime normalizes open trades, pending orders, pricing, candle responses, and transaction history enrichment, and keeps sync fetches behind `asyncio.to_thread()`.
- `tests/integration/test_snapshot_publication.py`
  At minimum, the Phase 1 gate must exercise version monotonicity, historical version retrieval, atomic publish behavior, and no partial-update visibility for the two-layer state model.

### Required Approval Proofs

- No detector-facing input path can run on a forming candle without explicit trimming or equivalent canonical handling.
- No public-state object can expose `time` as an index, naive datetimes, or a `volume` column name.
- No unknown instrument can silently inherit pip or spread defaults.
- No cache path can be considered fresh solely because a wall-clock TTL has not expired.
- No published public state can contain raw DataFrames.
- Startup validation must cover the merged trade-helper settings surface without introducing broker-write settings or hidden defaults.
- Structured logs must exist for the Phase 1 instrumentation baseline, especially fetch, cache, snapshot, bundle, spread, and bar exclusion events.

### Negative Scope-Fidelity Checks

- Static inspection or equivalent code review must confirm that analysis modules do not import read-only account-runtime clients or execution providers.
- Static inspection or equivalent code review must confirm there is no reintroduction of TTL-only freshness logic separate from the candle-boundary policy.
- Static inspection or equivalent code review must confirm TinyDB is the only persistence backend in the merged docs.

## Phase 2 Approval Gate - Analysis Engine And Signal Gates (P1)

Source anchors: Sections 1.2, 1.5, 1.6, 1.9, 5.6-5.9, 7.1, 8, 9.

### Required Source-Test Coverage

- `tests/unit/test_smc_adapter.py`
  Must prove deterministic SMC mapping, no input mutation, graceful insufficient-history behavior, additive session and retracement context, and deterministic order-block candidate ids.
- `tests/unit/test_indicator_layer.py`
  Must prove deterministic compact indicator publication, closed-bar-only behavior, and pandas-ta and TA-Lib outputs stay inside typed summary contracts.
- `tests/unit/test_spread_filter.py`
  Must prove per-instrument thresholds, spike detection, instrument-specific handling, and unknown-instrument failure.
- `tests/unit/test_chop_filter.py`
  Must prove ADX pass, caution, reject, unavailable-metric behavior, and repeatability.
- `tests/unit/test_custom_detectors.py`
  Must prove SFP, Turtle Soup, and ORB are deterministic, non-mutating, and contract-bounded.
- `tests/unit/test_tick_volume.py`
  Must prove `tick_*` outputs carry caveats and use canonical tick-volume semantics.
- `tests/unit/test_htf_bias_ruptures.py`
  Must prove trend-change detection, control false positives, and surface transition state.
- `tests/integration/test_order_block_tracker.py`
  Must prove instrument-level pinned-member tracker publication rejects unpublished snapshot versions and preserves exact source-version traceability.
- Existing determinism guarantees from `tests/unit/test_candle_policy.py`
  Must still hold once detectors are introduced, because all analysis remains closed-bar and non-mutating.

### Additional Required Detector Coverage

- Detector-statelessness tests under `tests/unit/` or equivalent coverage must prove the same closed-bar input yields the same SMC and custom-detector outputs every time.
- Detector-output contract tests must prove analysis results are published into compact typed summaries rather than raw DataFrames.
- Snapshot-model coverage must prove `SFP` and `Turtle Soup` summaries serialize through `TimeframeSnapshot` while `ORB` remains limited to `M15`.
- ORB coverage must prove it remains limited to the lower-timeframe usage described in the Section 7.1 walkthrough.

### Required Approval Proofs

- SMC, indicator, spread, chop, and custom detectors all consume closed candles only and remain pure and stateless.
- Any additive cross-timeframe tracker state must pin exact source snapshot versions and remain reproducible rather than reading whatever happens to be latest at publish time.
- Spread behavior is strictly registry-driven and never generic.
- `spread_checked` observability must emit threshold and spike fields when spread evaluation runs.
- Tick-volume outputs remain clearly labeled as tick-count-derived and caveated.
- HTF bias is computed from pinned timeframe members, not ad hoc mixed reads, and includes direction, alignment, timeframe votes, changepoints, and transition state.
- Chop filtering stays within the narrow "Chop/ADX filter" role named in the source plan and does not introduce unrelated behavior not present in the source text.

### Negative Scope-Fidelity Checks

- Static inspection or equivalent code review must confirm there is no `smc.fvg()` call, no `/fvg` command, and no FVG field in any model, snapshot, bundle, or command contract.
- Static inspection or equivalent code review must confirm there is no hidden detector instance state that can change output across identical inputs.
- Static inspection or equivalent code review must confirm there is no generic spread threshold fallback path.

## Phase 3 Approval Gate - Operational Integration And Consumption (P2)

Source anchors: Sections 1.12, 3, 4, 5.10-5.12, 7, 8, 9, 10, 11.

### Required Source-Test Coverage

- `tests/unit/test_forex_calendar.py`
  Must prove calendar parsing, high-impact filtering, and blackout logic.
- `tests/unit/test_trade_store.py`
  Must prove TinyDB CRUD behavior, spread-history persistence, cache-metadata upsert behavior, and merged collection ownership for trades, excursion samples, price alerts, indicator alerts, time alerts, and runtime-config overrides.
- `tests/unit/test_trade_repository.py`
  Must prove trade upsert, get, open-list, closed-list, close, and note-tagging behavior on TinyDB-backed state.
- `tests/unit/test_excursion_repository.py`
  Must prove excursion inserts, sample listing, and MAE/MFE aggregation from stored samples.
- `tests/unit/test_alert_repository.py`
  Must prove price-alert, indicator-alert, and time-alert CRUD, pending-list behavior, fire-state transitions, and cancellation behavior.
- `tests/unit/test_account_poller.py`
  Must prove open, close, and modify event detection, close-reason inference, and GSLO capture.
- `tests/unit/test_journal_service.py`
  Must prove journal writes and trade-open or trade-close notification dispatch.
- `tests/unit/test_excursion_tracker.py`
  Must prove adverse and favorable pip math plus minimum-move write filtering.
- `tests/unit/test_price_alert_engine.py`
  Must prove above and below crossing semantics and fire-once behavior.
- `tests/unit/test_indicator_alert_engine.py`
  Must prove RSI, Stochastic, and MACD evaluation plus repeat and cooloff behavior.
- `tests/unit/test_chart_renderer.py`
  Must prove request validation, selector family parsing, chart-mode default bundles, default-bundle replacement, and isolated `mplfinance` rendering.
- `tests/unit/test_time_alert_engine.py`
  Must prove fixed-time, exact-datetime, and session reminder scheduling, due-alert advancement, notification dispatch, and rescheduling semantics.
- `tests/unit/test_tradeplan.py`
  Must prove the bounded read-only trade-plan builder accepts qualifying published-state setups and emits structured rejection reasons when the setup is invalid.
- `tests/unit/test_bot_commands.py`
  Must prove `/price --live`, `/tradeplan`, `/fib`, `/timealert`, `/listtimealerts`, `/cleartimealert`, `/exporttimealerts`, `/importtimealerts`, and the Stage 16 runtime-config keys behave through the command layer without inline detector execution.
- `tests/integration/test_chart_renderer.py`
  Must prove state-first refresh behavior, runtime overlay filtering, clipped far overlays, and order-block anchoring from the origin candle.
- `tests/unit/test_security.py`
  Must prove admin gating and command-access checks for trade-helper handlers.
- `tests/integration/test_provider_cache.py`
  Must prove end-to-end cache hit, miss, stale, and append semantics.
- `tests/integration/test_observability.py`
  Must prove the required structured log events are emitted with the expected fields.
- `tests/integration/test_snapshot_publication.py`
  Must prove full Layer 1 plus Layer 2 publication behavior, including mixed-freshness detection and bundle member pinning after updates.
- `tests/integration/test_journal_lifecycle.py`
  Must prove open-trade detection, tick fan-out, excursion persistence, and close-trade journal completion end to end.
- `tests/integration/test_price_alert_fire.py`
  Must prove alert persistence, tick crossing, state transition to fired, and notification dispatch.
- `tests/integration/test_indicator_alert_fire.py`
  Must prove scheduled indicator scans read alert state, evaluate candles, and fire notifications without inline command computation.
- `tests/integration/test_bot_trade_helper_commands.py`
  Must prove persistence-backed Stage 16 helper commands, including time-alert round trips and export/import flows, still operate through the authenticated chat scope and TinyDB-backed repositories.

### Required Workflow Scenarios

- Full scan cycle from Section 7.1
  Must prove market-open check, calendar blackout handling, per-timeframe fetch and trim, detector execution, snapshot publication, HTF bias computation, bundle publication, and final scan-cycle logging.
- `/bias <instrument>` refresh path from Section 7.2
  Must prove the command reads the bundle from `MarketStateStore`, triggers a targeted scan only if the bundle is stale or missing, and returns HTF bias plus calendar context from the bundle.
- `/smc <instrument> [timeframe]` refresh path from Section 7.3
  Must prove the command reads `TimeframeSnapshot`, triggers fetch, compute, and publish only if the snapshot is stale or missing, and returns snapshot-derived structure, zones, liquidity, and freshness.
- Open-trade journal lifecycle
  Must prove account polling detects trade open, price-stream fan-out feeds excursion tracking, and trade close persists final journal state and alert side effects.
- Price-alert crossing flow
  Must prove `/pricealert` persists a pending alert, the runtime evaluates bid or ask crossing correctly, and a fired alert does not re-enter the pending set.
- Indicator-alert scheduled scan flow
  Must prove `/indicatoralert` persists a pending rule, APScheduler invokes the scan path, and repeat plus cooloff behavior is enforced without broker writes.
- Live-price command flow
  Must prove `/price --live` prefers a fresh streamed quote and reports the explicit REST fallback when the stream cache is stale or unavailable.
- Read-only trade-plan flow
  Must prove `/tradeplan <instrument>` consumes published bundle and snapshot state, applies spread and chop gates plus qualifying trigger rules, and returns structured rejection reasons when no setup qualifies.
- Time-alert flow
  Must prove `/timealert`, `/listtimealerts`, `/cleartimealert`, `/exporttimealerts`, and `/importtimealerts` persist chat-scoped reminders, schedule them in UTC from SGT `HH:MM` and exact-datetime input, and dispatch notifications without coupling to market-open state.
- Trade-lifecycle push flow
  Must prove trade-open and trade-close journal events send Telegram pushes through the notifier path while modify events remain journal-only.
- Chart rendering flow
  Must prove `/chart` validates instrument, timeframe, count, selector families, and `--mode`; resolves the correct chart-mode default bundle; renders through isolated `mplfinance`; and cleans up the artifact after delivery or failure.
- Mixed-freshness visibility
  Must prove bundles surface `mixed_freshness`, `stalest_timeframe`, `stalest_age_seconds`, and `member_freshness` rather than hiding cross-timeframe staleness.

### Required Approval Proofs

- Commands consume state through `MarketStateStore` or approved trade-helper services and do not run detectors inline.
- Snapshot publication happens before bundle assembly, and bundle assembly uses pinned member versions.
- Calendar data remains informational and non-blocking, even when refresh or fetch fails.
- Any persisted or command-facing trade P&L fields must distinguish pip movement, instrument-currency P&L, and account-currency P&L explicitly rather than treating them as interchangeable.
- Journal, price-alert, and indicator-alert persistence must remain TinyDB-backed.
- Background mute behavior must suppress push alerts without suppressing normal command replies.
- Charting is built on `mplfinance` with process isolation, selector-based runtime overlays, and per-render cleanup rather than direct `matplotlib` chart assembly.
- Orchestration composes independently testable steps and does not collapse back into a god-function.

### Negative Scope-Fidelity Checks

- Approval must not require signal scoring, grade assignment, or automated execution.
- Bounded read-only trade-plan summaries are allowed, but they must remain state-derived, non-executing, and free of grading or confidence scoring.
- `ExecutionProvider` and account commands may be documented as adjacent interfaces, but their presence or absence must not block approval of the analysis-layer plan.
- Approval fails if the merged runtime introduces a non-TinyDB persistence backend or broker write paths.

## Phase 4 Approval Gate - Context Enrichment And Scheduling Refinement (P3)

Source anchors: Sections 3, 7.1, 9, 10.

### Required Coverage

- Unit or integration coverage must prove macro-context ingestion from `yfinance` is additive and bounded to the enrichment role described in the tech stack.
- Unit or integration coverage must prove `pandas_market_calendars`-driven market-hours refinement respects holidays, open and closed state, and scan-skip behavior without changing the core scan contract.
- Regression coverage must prove Phase 4 does not change the locked Phase 1-3 public contracts for candle schema, provider boundaries, snapshots, bundles, or mixed-freshness semantics.

### Required Approval Proofs

- Stage 17 core release readiness does not depend on Stage 14 security completion or Stage 18 enrichment.
- Macro data enriches context but does not become a prerequisite for core analysis availability.
- Market-hours refinement preserves the Section 7.1 rule that closed-market behavior returns stale bundles rather than feeding bad data to detectors.
- Enrichment remains additive and does not widen scope into signal scoring, execution, or ML classification.
- Read-only trade-plan support remains bounded and must not expand into broker-write behavior, grading, or confidence scoring.

## Named Test Coverage Matrix

Every named test module in Section 8 must be represented by at least one approval gate:

| Section 8 test module | Approval phase |
|---|---|
| `tests/unit/test_settings.py` | Phase 1 |
| `tests/unit/test_candle_policy.py` | Phase 1, Phase 2 regression |
| `tests/unit/test_candle_schema.py` | Phase 1 |
| `tests/unit/test_instrument_registry.py` | Phase 1 |
| `tests/unit/test_cache_freshness.py` | Phase 1 |
| `tests/unit/test_models.py` | Phase 1 |
| `tests/unit/test_account_runtime_clients.py` | Phase 1 |
| `tests/integration/test_snapshot_publication.py` | Phase 1 foundation, Phase 3 full flow |
| `tests/unit/test_smc_adapter.py` | Phase 2 |
| `tests/unit/test_indicator_layer.py` | Phase 2 |
| `tests/unit/test_spread_filter.py` | Phase 2 |
| `tests/unit/test_chop_filter.py` | Phase 2 |
| `tests/unit/test_custom_detectors.py` | Phase 2 |
| `tests/unit/test_tick_volume.py` | Phase 2 |
| `tests/unit/test_htf_bias_ruptures.py` | Phase 2 |
| `tests/integration/test_order_block_tracker.py` | Phase 2 |
| `tests/unit/test_forex_calendar.py` | Phase 3 |
| `tests/unit/test_trade_store.py` | Phase 3 |
| `tests/unit/test_trade_repository.py` | Phase 3 |
| `tests/unit/test_excursion_repository.py` | Phase 3 |
| `tests/unit/test_alert_repository.py` | Phase 3 |
| `tests/unit/test_account_poller.py` | Phase 3 |
| `tests/unit/test_journal_service.py` | Phase 3 |
| `tests/unit/test_excursion_tracker.py` | Phase 3 |
| `tests/unit/test_price_alert_engine.py` | Phase 3 |
| `tests/unit/test_indicator_alert_engine.py` | Phase 3 |
| `tests/unit/test_chart_renderer.py` | Phase 3 |
| `tests/unit/test_security.py` | Phase 3 |
| `tests/integration/test_provider_cache.py` | Phase 3 |
| `tests/integration/test_observability.py` | Phase 3 |
| `tests/integration/test_journal_lifecycle.py` | Phase 3 |
| `tests/integration/test_price_alert_fire.py` | Phase 3 |
| `tests/integration/test_indicator_alert_fire.py` | Phase 3 |
| `tests/integration/test_chart_renderer.py` | Phase 3 |

## Critical Design Rule Coverage Matrix

Every critical design rule from Section 10 must map to at least one approval check:

| Section 10 rule | Approval coverage |
|---|---|
| Same closed-bar input -> same detector output | Phase 1 candle policy, Phase 2 detector-statelessness |
| No account/execution leakage into analysis interfaces | Phase 1 boundary audit, Phase 3 non-blocking adjacent interface rule |
| No ambiguous candle schema | Phase 1 schema gate |
| No TTL-only freshness | Phase 1 cache gate |
| No generic spread thresholds | Phase 1 registry gate, Phase 2 spread gate |
| No silent misuse of volume on OTC data | Phase 1 schema gate, Phase 2 tick-volume gate |
| No black-box state updates | Phase 1 and Phase 3 observability gates |
| Layer 1 snapshots immutable/versioned and Layer 2 bundles pin versions | Phase 1 state-model gate, Phase 3 publication-flow gate |
| No dead feature flags | Phase 3 scope-fidelity review and final release gate |
| No god-function orchestrators | Phase 3 orchestration gate |
| No FVG | Phase 2 and final release negative checks |
| No broker write paths in trade-helper runtime | Phase 1 boundary audit, Phase 3 scope-fidelity checks |
| TinyDB is the only persistence backend | Phase 1 and Phase 3 persistence checks |

## Final Release Gate Checklist

- All four phase gates have passed, with Phase 4 remaining additive rather than contract-changing.
- Every named Section 8 test module is represented in the approval path.
- Every Section 10 critical design rule maps to at least one approval check.
- Out-of-scope concerns remain non-blocking and non-required for approval, especially scoring, grading, and automated execution.
- Stage 17 is the first stage allowed to claim a core release. Stage 14 and Stage 18 remain explicitly non-blocking for that gate.
- Read-only live smoke remains limited to settings import, OANDA open-trade access, and an empty-state `/journal` bot reply.
- No approval path permits FVG, execution-analysis coupling, raw DataFrame publication, TTL-only freshness, generic spread defaults, non-TinyDB persistence backends, or inline detector execution from command handlers.
