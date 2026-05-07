# Stage 10 - Calendar And Persistence

> Historical stage spec. The repo now has implemented code beyond this checklist. Keep this document for design lineage, and use [README](../../README.md), [tracker.md](../tracker.md), and [COMMANDS.md](../COMMANDS.md) for current behavior and status.

- Parent Phase: Phase 3 / P2 - Operational Integration And Consumption
- Tracker: [tracker.md](../tracker.md)
- Feature IDs: `S10-F01`, `S10-F02`, `S10-F03`, `S10-TH01`

## Purpose

Add the operational persistence layer for event awareness and durable state. This stage defines the Forex calendar fetch path, extends the TinyDB schema introduced in Stage 04, and adds failure handling rules that keep runtime behavior predictable.

## Source Anchors

- [V3_PLAN.md](../V3_PLAN.md): Sections 5.10, 5.11, 9, 10
- [v3_delivery_phases.md](../v3_delivery_phases.md): Phase 3
- [v3_approval_test_spec.md](../v3_approval_test_spec.md): Phase 3 gate

## Scope

- Define the Forex calendar provider against the source-plan endpoint.
- Define TinyDB tables and data ownership for persistent operational state.
- Define graceful degradation and recovery behavior when persistence or calendar fetch fails.

## Dependencies

- Stage 04 `TradeStore` and cache metadata contract.
- Stage 05 public state model.
- Stage 09 HTF bias and bundle usage.

## Implementation Checklist

- [x] `S10-F01` Implement the calendar fetch and parse path for `https://nfs.faireconomy.media/ff_calendar_thisweek.json`, including high-impact filtering and blackout evaluation while keeping the data informational and non-blocking.
- [x] `S10-F02` Extend the Stage 04 `TradeStore` with TinyDB tables for `trades`, `signals`, `spread_history`, `excursion_samples`, `price_alerts`, and `indicator_alerts`, preserve the existing `cache_metadata` ownership, and document write and read boundaries for every table.
- [x] `S10-F03` Define recovery rules so runtime behavior degrades gracefully when calendar refresh fails, TinyDB is unavailable, or persisted metadata is partially missing.
- [x] `S10-TH01` Extend the TinyDB store with `trades`, `excursion_samples`, `price_alerts`, and `indicator_alerts` collections; define upsert, retrieval, pending-list query, state-transition, note-tagging, and MAE/MFE aggregation methods on each; document table ownership boundaries so no collection is written by more than one runtime path.

## Public Interfaces And Contracts

- Calendar provider output must expose parsed events, impact level, event timing, and blackout evaluation in a typed form.
- Trade-store style persistence must expose helper methods for recent spreads, signal records, journal records, excursion samples, price alerts, indicator alerts, and the Stage 04 cache metadata lookup and update path.
- Future trade persistence must distinguish pip movement from monetary P&L. If trade records include both, they must expose `pips` separately from instrument-currency and account-currency P&L fields rather than a single ambiguous `pnl`.
- Persistence remains an operational support layer; it must not become a hidden source of analysis truth beyond approved metadata and alert state.

## Trade Helper Additions

- TinyDB is the only approved persistence backend for the merged runtime. No other persistence backend is introduced in this stage.
- `trades` owns trade lifecycle records and notes, `excursion_samples` owns time-series MAE/MFE samples, `price_alerts` owns fire-once price rules, and `indicator_alerts` owns scheduled indicator rules plus repeat and cooloff metadata.
- Repository contracts must support upsert, retrieval, pending-list queries, state transitions, note-tagging, and MAE/MFE aggregation from stored excursion samples.

## Tests And Approval Evidence

- `tests/unit/test_forex_calendar.py`
  Must prove parse behavior, impact filtering, and blackout logic.
- `tests/unit/test_trade_store.py`
  Must prove TinyDB CRUD behavior, spread-history persistence, and the Stage 04 cache-metadata upsert behavior remains intact while new tables are added.
- Trade-record coverage added in this stage must prove `pips`, instrument-currency P&L, and account-currency P&L remain distinct fields with explicit currency labeling.
- Failure-mode tests must prove calendar and persistence faults degrade to warnings and retained core analysis availability rather than crashing the read path unnecessarily.

## Risks And Watchpoints

- Treating the calendar as blocking would widen scope and harm resilience.
- TinyDB can become a hidden dumping ground if table ownership is not explicit.
- Persistence failures that fork cache metadata into a second storage path could reintroduce stale-data ambiguity.

## Exit Criteria

- Calendar and TinyDB operational contracts are documented, implemented, and test-covered.
- The bot can continue functioning in a degraded mode when non-critical calendar or persistence paths fail.
- Cache metadata persistence remains aligned with the candle-boundary freshness contract.

## Explicit Exclusions

- No trade execution or portfolio accounting behavior is added here.
- No calendar-based signal scoring or forced trade blocking is introduced.
- No replacement of TinyDB with a larger datastore is planned in this stage.
