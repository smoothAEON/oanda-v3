# V3 Delivery Phases

> Historical phase-planning document. The current implementation has moved ahead of some planned stages and still leaves parts of later stages open. Use [tracker.md](./tracker.md) for the live status board and [README](../README.md) for the current runtime description.

## Source Of Truth

`V3_PLAN.md` is the governing specification for this delivery plan. If any wording in this document is less specific than the source plan, or if any ambiguity appears, implementation must resolve it in favor of `V3_PLAN.md`, not this summary.

This document intentionally groups the original `P0`-`P3` priorities into readable milestones without changing scope, boundaries, or design rules from the source plan. The grouped milestones now cover both the analysis runtime and the read-only trade-helper runtime documented in the merged source plan.

## Locked Source Contracts

- `MarketDataProvider` is analysis-layer market data only: candles, current price, and candle freshness. Read-only account polling and streaming stay on adjacent runtime boundaries and must not leak into `smc/`, `indicators/`, or `filters/`. Source anchors: Sections 1.1, 5.4, 10, 11.
- The canonical candle schema is exactly `time`, `open`, `high`, `low`, `close`, `tick_volume`. `time` is always a UTC-aware column and never a DataFrame index. Source anchors: Sections 1.2, 1.3, 5.2.
- The instrument registry is the single source of truth for all 12 scan instruments: `XAU_USD`, `XAG_USD`, `EUR_USD`, `GBP_USD`, `USD_JPY`, `AUD_USD`, `USD_CAD`, `USD_CHF`, `NZD_USD`, `EUR_GBP`, `EUR_JPY`, `GBP_JPY`. Unknown instruments must fail loudly. Source anchors: Sections 1.5, 5.3, 8.
- Cache freshness is based on the last completed candle boundary, never wall-clock TTL alone. Metadata must persist `last_completed_candle` and `fetched_at`. Source anchors: Sections 1.4, 5.5, 8.
- `TimeframeSnapshot` is immutable, versioned, and per-timeframe. `InstrumentBundle` pins exact member snapshot versions and exposes mixed freshness explicitly. `MarketStateStore` publishes atomically and retains historical snapshot versions. Source anchors: Sections 1.15, 6, 8.
- Public state is typed Pydantic models only. Raw DataFrames are internal compute artifacts and are never part of the published contract. Source anchors: Sections 1.8, 5.6-5.9, 6.
- The trade-helper runtime may poll open trades, stream prices, persist journal and alert state in TinyDB, and send Telegram notifications, but it remains read-only against the broker and separate from analysis modules. Source anchors: Sections 1.1, 5.3-5.13, 10, 11.
- Tick-volume outputs must be explicitly labeled as tick-count-derived and carry caveats. They are not exchange volume. Source anchors: Sections 1.6, 5.8, 8.
- FVG is excluded from V3 in every layer: no detector, no model field, no snapshot content, no command, and no approval path. Source anchors: Intro scope note, Sections 5.6, 10, 11.
- TinyDB is the merged persistence backend for runtime state, including trade-helper collections. No other persistence backend is introduced by this plan.

## Phase 1 - Foundation Contracts And Determinism (P0)

Source anchors: Sections 1.1-1.8, 1.10, 1.15, 5.1-5.5, 5.13, 6, 8, 9.

### Objective

Establish the deterministic data, schema, state, and observability contracts that every later subsystem depends on. This phase exists to prevent the exact V1/V2 failures called out in the source plan before any higher-level analysis or trade-helper runtime behavior is added.

### Included Subsystems

- Configuration via Pydantic settings, including required startup keys and core runtime defaults from Section 5.1 plus the trade-helper runtime knobs.
- Candle policy and schema enforcement: `trim_to_closed`, `validate_candle_df`, `CANONICAL_COLUMNS`, UTC coercion, and time-as-column enforcement.
- Instrument metadata registry with explicit pip size, spread thresholds, spike multipliers, and loud failure on unknown instruments.
- `MarketDataProvider` interface and OANDA market-data implementation boundary, including candles, prices, and freshness only.
- Read-only account-runtime OANDA boundaries for open-trade snapshots, alert pricing, candle fetches, and live price streaming, kept separate from analysis modules.
- Three-level cache with candle-boundary freshness, append semantics, CSV persistence, and TinyDB cache metadata.
- Canonical Pydantic models, especially `TimeframeSnapshot`, `InstrumentBundle`, trade-helper trade and alert models, and the compact public summaries that replace raw DataFrames.
- `MarketStateStore` two-layer publication model with immutable deep-copy semantics, version monotonicity, and historical version lookup.
- `structlog` setup and the baseline instrumentation points required from day one.

### Dependencies

- No prior implementation dependency. This is the prerequisite for all later phases.
- The source plan's critical design rules must already govern module boundaries in this phase, especially no execution-analysis coupling, no raw DataFrames in public state, and no god-function orchestration.

### Deliverables

- A validated configuration layer that fails startup when required keys from Section 5.1 are missing.
- A single canonical candle policy that every later detector and provider path must reuse, rather than ad hoc bar handling.
- A complete instrument registry covering all 12 scan instruments listed in Section 5.3 with no silent defaults.
- A market-data access layer whose public analysis contract remains limited to candles, price, and freshness.
- A separate read-only account-runtime boundary for trade polling, live ticks, and alert inputs without weakening the analysis boundary.
- A cache layer whose freshness checks are driven by candle completion boundaries and persisted metadata, not TTL-only logic.
- Immutable snapshot and bundle models with versioned publication and retrieval through `MarketStateStore`.
- Public trade-helper models for journaling, MAE/MFE tracking, and alert lifecycle that stay typed and persistence-ready.
- Structured log events for fetch, cache, detector, snapshot, bundle, spread, bar exclusion, HTF bias, changepoints, calendar, scan cycle, and trade-helper runtime events as defined in the source plan.

### Explicit Exclusions

- No SMC detection, TA wrappers, spread/chop evaluation, HTF bias, or custom pattern detectors beyond the contracts they will depend on in later phases.
- No economic calendar, scheduling, orchestration, chart rendering, trade journal services, alert engines, or macro context yet beyond the contracts they will consume later.
- No execution/account logic inside the analysis layer, even if adjacent read-only runtime files exist in the source structure.
- No signal evaluation, confidence scoring, trade planning, or automated execution. Telegram command and notification surfaces are allowed later, but not in this phase.

### Exit Criteria

- The candle schema, registry, provider contract, cache freshness policy, state models, and state store all match the source-plan contracts exactly.
- No public API in this phase permits `time` as an index, `volume` as a column name, raw DataFrames in published state, or unknown-instrument fallback behavior.
- Structured logging exists from the start rather than being deferred.
- Phase 1 approval tests in `docs/v3_approval_test_spec.md` pass.

## Phase 2 - Analysis Engine And Signal Gates (P1)

Source anchors: Sections 1.2, 1.5, 1.6, 1.9, 5.6-5.9, 7.1, 8, 9.

### Objective

Add the actual analysis engine on top of the deterministic Phase 1 contracts, while preserving statelessness, closed-bar-only behavior, explicit tick-volume labeling, and instrument-aware gating.

### Included Subsystems

- `smartmoneyconcepts` wrapper coverage for swing highs/lows, BOS/CHOCH, order blocks, liquidity, sessions, previous high/low, and retracements.
- HTF bias computation using pinned snapshots, SMC structure, and `ruptures` changepoint detection.
- TA-Lib indicator wrappers for the named trend, momentum, volatility, and oscillator families in Section 5.8.
- pandas-ta supplementary indicators for VWAP, Squeeze Momentum, and Ichimoku, with Nadaraya-Watson explicitly deferred until the approved package surface exposes it.
- Tick-volume indicators with explicit `tick_*` naming, `volume_type == "tick_count"`, and caveat-bearing outputs.
- Instrument-aware spread filtering driven by the registry, with spike detection relative to typical spread rather than a generic fallback.
- Chop filtering bounded to the source plan's stated responsibility as a "Chop/ADX filter".
- Stateless custom detectors for `SFP`, `Turtle Soup`, and `ORB`, with ORB limited to the lower-timeframe usage shown in the scan walkthrough.

### Dependencies

- Phase 1 must be complete, because every detector in this phase depends on closed-candle enforcement, canonical schema validation, registry metadata, typed models, and snapshot publication.
- All detectors in this phase must behave as pure functions over closed-bar inputs and must write their results into the compact snapshot summaries defined by the Phase 1 contracts.

### Deliverables

- A deterministic SMC analysis layer that normalizes data correctly for `smartmoneyconcepts` and never introduces hidden detector state.
- HTF bias output that captures direction, alignment score, timeframe votes, changepoints, and transition state from pinned timeframe members.
- Technical indicator summaries sourced from TA-Lib and pandas-ta without exposing raw indicator DataFrames publicly.
- Supplemental indicator scope remains limited to the approved pandas-ta surface; no custom Nadaraya-Watson replacement is introduced.
- Tick-volume indicator outputs that remain clearly labeled as OTC tick activity rather than exchange volume.
- A spread gate that uses per-instrument pip metadata and thresholding exactly as intended by the registry.
- A chop gate and custom detector set that can be run independently and composed by the orchestrator later.

### Explicit Exclusions

- No FVG under any name and no `smc.fvg()` usage.
- No hidden lifecycle state inside detectors.
- No signal scoring, trade planning, grading, alert deduplication, or execution logic.
- No command handlers calling detectors inline. Section 11 requires commands to read snapshots and bundles and only trigger targeted scans when state is stale or missing.

### Exit Criteria

- Every detector and gate in this phase consumes closed candles, remains stateless, and publishes only compact typed results.
- Spread handling is fully instrument-aware and never falls back to a generic threshold.
- Tick-volume analysis stays explicitly caveated.
- No model, snapshot, bundle, or command surface includes FVG.
- Phase 2 approval tests in `docs/v3_approval_test_spec.md` pass.

## Phase 3 - Operational Integration And Consumption (P2)

Source anchors: Sections 1.12, 3, 4, 5.10-5.12, 7, 9, 10, 11.

Implementation status note: the repository now ships code through the repurposed Stage 16 runtime-feature surface. Historical Stage 14 remains the deferred security and admin backlog, historical Stage 15 is retired as a placeholder, Stage 17 now owns CI and core-release readiness, and Stage 18 holds the deferred macro and refined market-hours work. Use [tracker.md](./tracker.md) for the live status board.

### Objective

Integrate the analysis engine into the operational runtime described by the source plan: event awareness, persistence, scheduling, charting, scan orchestration, trade journaling, MAE/MFE tracking, and alert delivery that publishes snapshots first and bundles second.

### Included Subsystems

- ForexFactory calendar ingestion from `https://nfs.faireconomy.media/ff_calendar_thisweek.json`, including high-impact filtering, blackout detection, hourly refresh, and graceful degradation.
- TinyDB-backed persistence for trades, signals, spread history, cache metadata, trade journals, excursion samples, price alerts, and indicator alerts.
- APScheduler jobs for auto-scan cadence, session-open warming, market-open warming, hourly calendar refresh, and scheduled indicator scans.
- Background tasks for account polling, price-stream fan-out, excursion tracking, price-alert evaluation, and supervised restart or shutdown handling.
- `mplfinance` chart rendering in a `ProcessPoolExecutor` for state isolation rather than direct `matplotlib` chart construction.
- Scan orchestration aligned to the Section 7.1 walkthrough: market-open check, calendar blackout awareness, per-instrument/per-timeframe fetch, trimming, detector execution, snapshot publication, HTF bias computation, bundle assembly, and scan-cycle logging.
- Consumer-side command behavior from Section 11 as a read-path contract: commands read from `MarketStateStore` or the approved trade-helper services, trigger targeted refresh when state is stale or missing, and never compute inline.
- Telegram command behavior for `/journal`, `/label`, `/maemfe`, `/pricealert`, `/indicatoralert`, `/listalerts`, `/listindicators`, and the matching clear commands, all backed by typed state and services rather than ad hoc payloads.

### Dependencies

- Phases 1 and 2 must be complete and stable, because this phase integrates their outputs rather than redefining them.
- Snapshot and bundle publication order must remain the same as the source walkthrough: publish Layer 1 members first, then compute HTF bias from pinned members, then publish the Layer 2 bundle.

### Deliverables

- A calendar provider whose outputs remain informational and non-blocking, matching the source plan's boundary.
- A durable TinyDB persistence layer for the named tables and metadata paths.
- A scheduler that performs the job categories described in Section 5.12.
- A trade-helper runtime that diffs read-only open-trade snapshots, fans out live price ticks, persists journal and alert state, and emits background notifications without broker write paths.
- A chart renderer built on `mplfinance`, not direct `matplotlib`, and isolated from the main process.
- A scan orchestrator that composes detectors rather than collapsing into a god-function and emits the structured log events defined earlier.
- Command-consumption contracts for `/bias`, `/smc`, `/journal`, `/maemfe`, and the rest of the Section 11 command surface that keep analysis and trade-helper state in services rather than in command handlers.

### Explicit Exclusions

- Signal evaluation, confidence scoring, grade assignment, and automated execution remain out of scope.
- Read-only trade-plan summaries derived from published state are allowed in this phase, but they must stay deterministic, bounded, non-executing, and free of grading or confidence scoring.
- Background alert pushes may exist in this phase, but they must stay bounded to price, indicator, time-alert, session-reminder, and trade-lifecycle notifications rather than widening into signal grading or execution advice.
- `ExecutionProvider` and account commands are adjacent boundaries only. They may exist as separate interfaces, but they are not approval-blocking for the analysis-layer plan.

### Exit Criteria

- The runtime can execute the Section 7.1 scan flow without bypassing the state model or detector boundaries established earlier.
- Commands consume snapshots, bundles, and approved trade-helper services through stable interfaces and do not execute detectors inline.
- Calendar, persistence, scheduling, charting, journaling, and alert behavior remain integrated but bounded, without widening scope into scoring or execution.
- Phase 3 approval tests in `docs/v3_approval_test_spec.md` pass.

## Phase 4 - Release Readiness And Deferred Enrichment (P3)

Source anchors: Sections 3, 7.1, 9, 10.

### Objective

Close the core-release path first, then keep macro context and refined market-hours work explicitly additive and post-release.

### Included Subsystems

- Stage 17 core-release readiness: broad unit and integration inventory ownership, CI, deployment topology, backup and restore procedures, incident runbooks, and release evidence.
- Stage 18 additive enrichment: macro context via `yfinance`, refined market-hours and holiday behavior via `pandas_market_calendars`, and the scheduling refinements that follow from those inputs.

### Dependencies

- Phases 1-3 must already be accepted.
- Stage 17 is the first stage allowed to claim core release readiness.
- Stage 18 is non-blocking additive enrichment and must not widen the core public-state contracts.

### Deliverables

- A core-release gate with the CI, operational, backup, restore, and runbook evidence needed to release the shipped runtime.
- Additive macro-context inputs that enrich analysis and scheduling without changing the canonical snapshot and bundle contract.
- More precise market-hours behavior that preserves the "if closed, return stale bundles" pattern from Section 7.1.

### Explicit Exclusions

- No expansion into signal scoring, execution, or ML-based classification.
- No widening of the read-only trade-plan summary into broker-write behavior, grading, or confidence scoring.
- No change to the locked candle schema, provider boundary, state model, or mixed-freshness semantics from earlier phases.

### Exit Criteria

- Stage 17 release-readiness evidence passes without requiring Stage 14 security completion or Stage 18 enrichment.
- Macro and market-hours enrichment remain additive and do not break or widen the previously locked interfaces.
- Scheduling refinement still respects the source plan's closed-market behavior and overall analysis-layer scope.
- Phase 4 approval tests in `docs/v3_approval_test_spec.md` pass.

## Fidelity Appendix

### Hard Exclusions

- No FVG detection, no `smc.fvg()` calls, no FVG fields in models, snapshots, bundles, commands, or approval criteria.
- No dead feature flags. If a feature ships in this plan, it is meant to be enabled and used.
- No raw DataFrames in public state. Public contracts stay typed and compact.
- No TTL-only freshness. Candle-boundary freshness is mandatory.
- No generic spread defaults. Every scan instrument must carry explicit registry metadata.
- No execution leakage into analysis modules.
- No broker write paths. Trade-helper scope remains read-only even when it observes mutable account state.
- TinyDB is the only persistence backend in the merged persistence plan.

### Boundary Handling

- `ExecutionProvider` and account commands remain valid adjacent interfaces in the source structure, but they are not part of the approvable analysis-layer milestones.
- Where the source plan is specific, implementation should mirror it closely. Where the source plan is intentionally sparse, implementation should stay narrow and avoid inventing new behavior that would widen scope.
