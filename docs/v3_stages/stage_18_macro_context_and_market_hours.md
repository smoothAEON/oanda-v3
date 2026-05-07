# Stage 18 - Macro Context And Market-Hours Refinement

> Additive non-blocking stage. This stage remains outside the core release gate and does not widen public analysis contracts.

- Tracker: [tracker.md](../tracker.md)
- Historical placeholder: [Stage 15](./stage_15_macro_context_and_market_hours.md)

## Purpose

Keep the old macro-enrichment and refined market-hours backlog available without blocking the shipped runtime or the Stage 17 core release gate.

## Scope

- additive `yfinance` macro context such as VIX and DXY
- refined `pandas_market_calendars` behavior beyond the basic market-hours checks already present
- scheduling refinements that depend on the extra market-hours and holiday detail

## Current Implementation

- bounded VIX and DXY status via `yfinance`, exposed through runtime health and `/marketstatus`
- category-aware market-hours refinement for `major_fx` and `minor_fx` via `CME_FX`, and `metal` via `CMEGlobex_PreciousMetals`
- scheduler support for macro refresh cadence and dynamic market-open warm rescheduling based on the computed next open
- additive failure handling that keeps scans, stale bundles, and command health surfaces available when enrichment is unavailable

## Boundaries

- additive only
- no change to the candle schema, provider boundary, snapshot contract, or mixed-freshness model
- no scoring, grading, confidence, execution, or broker writes

## Implementation Checklist

- [x] `S18-F01` add bounded macro context
- [x] `S18-F02` refine market-hours and holiday behavior
- [x] `S18-F03` prove enrichment failure does not break core runtime availability

## Exit Criteria

- macro and market-hours refinement remain additive
- Stage 17 core release remains valid even if Stage 18 stays open
