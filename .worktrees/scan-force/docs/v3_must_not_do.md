# V3 Must Not Do

> Active guardrail reference. This document still describes the architectural boundaries the current repo is supposed to respect, but it is not a current feature or status board. Use [tracker.md](./tracker.md) for implementation status.

## Purpose

This document is the plain-language guardrail summary for V3. It translates the source plan's lessons, exclusions, and approval blockers into a direct list of things the analysis runtime and the read-only trade-helper runtime must not do. If an implementation violates any item here, it should be rejected even if it appears functional.

## Source Authority

- [V3_PLAN.md](./V3_PLAN.md)
- [v3_delivery_phases.md](./v3_delivery_phases.md)
- [v3_approval_test_spec.md](./v3_approval_test_spec.md)
- [v3_stage_index.md](./v3_stage_index.md)

If this document is ever looser than the source plan, the source plan wins.

## Absolute Scope Boundaries

- Do not add automated trading, order placement, or broker write paths.
- Do not add broker-writing trade planning, grade assignment, or confidence scoring to the V3 analysis and bot runtime scope.
- Do not widen the bounded read-only `/tradeplan` summary into inline detector execution, automated advice, or execution behavior.
- Do not treat journaling, MAE/MFE tracking, price alerts, or indicator alerts as permission to mutate broker state.
- Do not add Fair Value Gap support anywhere.
  - No `smc.fvg()`
  - No FVG fields in models
  - No FVG zones in snapshots or bundles
  - No `/fvg` command
- Do not widen the system into an execution-coupled trading platform under the label of "bot completion."

## Data And Analysis Prohibitions

- Do not compute normal detector output on forming candles.
  - Closed-bar analysis is the default.
  - Provisional analysis, if ever added, must be explicit and separate.
- Do not allow `time` to drift between column and index representations.
  - `time` must remain a UTC-aware column.
- Do not publish `volume` as if it were exchange-traded volume.
  - OANDA data is tick count and must remain labeled as `tick_volume`.
- Do not use TTL-only freshness.
  - Freshness must follow last completed candle boundaries.
- Do not use a generic spread threshold fallback.
  - Every supported instrument must have explicit registry metadata.
- Do not hide mixed freshness in multi-timeframe outputs.
  - Bundles must expose stale-member detail clearly.
- Do not publish raw DataFrames in public state.
  - Public state must remain typed, compact, and stable.

## Architecture Prohibitions

- Do not let analysis modules import or depend on execution or account state.
- Do not build stateful detectors that return different answers for the same closed-bar input.
- Do not collapse scanning into a god-function that fetches, computes, mutates state, and formats outputs in one block.
- Do not split the merged persistence model across TinyDB and an undocumented second database backend.
- Do not ship dead feature flags for functionality that is off by default and not part of the accepted system.
- Do not reintroduce custom `matplotlib` candlestick rendering in place of `mplfinance`.
- Do not fake async around blocking sync calls.
  - OANDA and TinyDB boundaries must be handled honestly with thread offloading where needed.

## Runtime And Bot Prohibitions

- Do not let command handlers run detectors inline.
  - Commands must read from `MarketStateStore` and trigger targeted refresh only when stale or missing.
- Do not allow invalid instruments to silently pass through command or alert paths.
- Do not start the bot with missing required secrets or invalid environment configuration.
- Do not treat calendar data or macro enrichment as hard blockers for core runtime availability.
- Do not let alerts or admin controls bypass auth and session rules.
- Do not let muted background alerts suppress normal command responses.
- Do not let journal or alert handlers bypass the documented service layer and write ad hoc TinyDB blobs directly from Telegram handlers.

## Operational Prohibitions

- Do not ignore market closure and feed empty or bad data into detectors.
- Do not skip structured logging for fetch, cache, detector, snapshot, bundle, calendar, and scan-cycle events.
- Do not release a stage as `completed` without exit criteria and evidence.
- Do not mark a phase `completed` if any of its stages are still incomplete.
- Do not let [tracker.md](./tracker.md) drift away from actual implementation and approval state.

## Release Blockers

Reject the implementation if any of the following are true:

- FVG exists anywhere in code, docs, commands, models, or tests.
- Execution and analysis boundaries are mixed.
- Public state exposes raw DataFrames or ambiguous candle schema.
- Candle freshness relies on TTL rather than completed-candle boundaries.
- Spread logic has silent defaults for unknown instruments.
- Commands compute analysis inline instead of reading published state.
- The bot runtime requires macro or calendar enrichment to remain usable.
- A persistence backend other than TinyDB has been introduced into the merged persistence plan.
- The tracker says `completed` without real approval evidence.
