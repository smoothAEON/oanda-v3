# Stage 12 - Chart Rendering And Exports

> Historical stage spec. The repo now has implemented code beyond this checklist. Keep this document for design lineage, and use [README](../../README.md), [tracker.md](../tracker.md), and [COMMANDS.md](../COMMANDS.md) for current behavior and status.

- Parent Phase: Phase 3 / P2 - Operational Integration And Consumption
- Tracker: [tracker.md](../tracker.md)
- Feature IDs: `S12-F01`, `S12-F02`, `S12-F03`

## Purpose

Provide the charting and export pipeline for the bot without reintroducing direct `matplotlib` state problems. This stage defines chart request validation, selector families, widened runtime overlays, worker isolation, and per-render artifact cleanup.

## Source Anchors

- [V3_PLAN.md](../V3_PLAN.md): Sections 1.12, 3, 4, 11
- [v3_delivery_phases.md](../v3_delivery_phases.md): Phase 3
- [v3_approval_test_spec.md](../v3_approval_test_spec.md): Phase 3 gate

## Scope

- Define the chart request contract used by commands (see [`COMMANDS.md`](../COMMANDS.md#charting) for the `/chart` command signature).
- Define the renderer based on `mplfinance`, process isolation, and state-first refresh behavior for missing or stale snapshots.
- Define export naming and cleanup for ephemeral chart artifacts.
- Define the runtime overlay families for SMC, trades, pending orders, price alerts, and indicator overlays.

## Dependencies

- Stage 04 read-only account runtime for open trades and pending orders.
- Stage 05 snapshots and bundle state.
- Stage 10 TinyDB-backed journal, excursion, and alert state.
- Stage 11 orchestration and health logging.

## Implementation Checklist

- [x] `S12-F01` Define the chart request contract for instrument, timeframe, candle count, overlay presets, and selector families. Support `--count 2..5000`, remove the old `--mode compact|balanced|full` syntax, and keep explicit selector flags as the override for the default bundle.
- [x] `S12-F02` Implement chart rendering through `mplfinance` inside a `ProcessPoolExecutor` so chart state is isolated from the main runtime and failures do not poison later renders. The renderer must build serializable payloads from published snapshots, runtime trades, pending orders, and alerts, keep candlesticks in focus, and draw order blocks from their originating candle.
- [x] `S12-F03` Define chart artifact paths, filename conventions, and temporary file cleanup so command delivery can attach charts without leaving files behind after the render completes.

## Public Interfaces And Contracts

- Chart requests must validate instruments and timeframes against the same registry and timeframe policy used elsewhere in the system.
- Renderer inputs come from published state or freshly fetched canonical candles, not from ad hoc mixed formats.
- The default runtime overlay bundle includes open trades, pending orders, SL/TP/GSLO, and price alerts unless explicit selector flags replace it.
- Runtime overlays come from typed open-trade, pending-order, and alert state, plus snapshot SMC structure, zones, and liquidity summaries.
- Exported chart artifacts must be ephemeral operational outputs, not a new persistent analysis store, and the artifact handle must delete files after use.

## Tests And Approval Evidence

- Renderer tests must prove `mplfinance` is used instead of a direct `matplotlib` candlestick implementation.
- Failure-path tests must prove render exceptions are isolated and do not break future renders.
- Request-validation tests must prove the selector families, count bounds, and explicit-flag override behavior.
- Integration tests must prove state-first refresh behavior, overlay filtering, clipped far overlays, and order-block anchoring from the origin candle.
- Live tests with OANDA data must prove the render path still works end to end.

## Risks And Watchpoints

- Falling back to hand-built `matplotlib` charting would recreate the exact problem called out in the source plan.
- Loose chart input validation can produce mismatched overlays or missing-state failures.
- Overlay layers that zoom out too far can make candles unreadable, so the renderer must keep price action in focus and clip distant overlays.
- Leaving temporary artifacts behind would create operational clutter and possible disk issues.

## Exit Criteria

- Charts render through an isolated `mplfinance` pipeline with controlled overlay choices.
- Artifact creation and cleanup are predictable and per-render ephemeral.
- Chart request behavior is well-defined enough for command handlers to call safely.
- Live tests with OANDA data validate the real render path.
- Order blocks are drawn from their originating candle, not from the latest candle.

## Explicit Exclusions

- No custom financial chart engine beyond `mplfinance`.
- No permanent archival store for generated chart images in this stage.
- No trade grading, signal scoring, or other analysis-wide overlays beyond the documented read-only runtime overlays.
