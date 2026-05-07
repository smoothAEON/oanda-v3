# Stage 09 - HTF Bias And Multi-Timeframe Assembly

> Historical stage spec. The repo now has implemented code beyond this checklist. Keep this document for design lineage, and use [README](../../README.md), [tracker.md](../tracker.md), and [COMMANDS.md](../COMMANDS.md) for current behavior and status.

- Parent Phase: Phase 2 / P1 - Analysis Engine And Signal Gates
- Tracker: [tracker.md](../tracker.md)
- Feature IDs: `S09-F01`, `S09-F02`, `S09-F03`

## Purpose

Build the higher-timeframe bias layer and the other instrument-level pinned-member assemblies that sit above single-timeframe snapshots. This stage defines the first cross-timeframe tracker publication path, the HTF bias vote model, changepoint logic, transition-state handling, and the public freshness semantics exposed to bias consumers.

## Source Anchors

- [V3_PLAN.md](../V3_PLAN.md): Sections 1.15, 5.7, 6, 7.1, 8, 10
- [v3_delivery_phases.md](../v3_delivery_phases.md): Phase 2
- [v3_approval_test_spec.md](../v3_approval_test_spec.md): Phase 2 gate and Phase 3 mixed-freshness checks

## Scope

- Define pinned instrument-level assemblies that consume exact snapshot versions, starting with the cross-timeframe order-block tracker.
- Define HTF bias inputs, outputs, and timeframe vote rules.
- Define `ruptures` changepoint usage and transition-state semantics.
- Define how freshness detail is attached to bundle-level HTF bias output.

## Dependencies

- Stage 05 two-layer state model.
- Stage 06 structure summaries and deterministic order-block candidates.
- Stage 08 signal gates.

## Implementation Checklist

- [x] `S09-F01` Implement the first pinned multi-timeframe assembly primitive with `InstrumentOrderBlockTracker` records plus `MarketStateStore` publish and assemble flows that reject unpublished snapshot versions and preserve exact source-member traceability.
- [x] `S09-F02` Implement HTF bias computation over pinned D, H4, and H1 snapshots, including direction, alignment score, timeframe votes, and `ruptures`-backed transition handling when structure evidence is shifting.
- [x] `S09-F03` Expose bundle freshness detail for HTF bias consumers through `mixed_freshness`, `stalest_timeframe`, `stalest_age_seconds`, and `member_freshness`.

## Public Interfaces And Contracts

- `InstrumentOrderBlockTracker` is an additive instrument-level state product that pins exact source snapshot versions for every included timeframe; it does not replace bundle publication.
- `MarketStateStore.assemble_order_block_tracker(...)` and `publish_order_block_tracker(...)` must reject unpublished snapshot versions and preserve immutable read semantics.
- HTF bias must live at the bundle layer, not inside single-timeframe snapshots.
- HTF bias output must include enough traceability to show which snapshot versions were used.
- Mixed-freshness visibility is mandatory and must not be hidden behind a single freshness boolean.

## Tests And Approval Evidence

- `tests/integration/test_order_block_tracker.py`
  Must prove order-block tracker publication pins exact snapshot versions, clones on read, increments versions monotonically, and rejects unpublished references.
- `tests/unit/test_models.py`
  Must prove tracker records and tracker publication contracts validate with exact timeframe-version pinning.
- `tests/unit/test_htf_bias_ruptures.py`
  Must prove trend-change detection, false-positive control, and transition-state surfacing.
- Publication-flow tests must prove both order-block trackers and HTF bias read pinned snapshot versions rather than whatever happens to be latest at read time.
- Mixed-freshness regression tests must prove bundles surface stale-member detail explicitly.

## Risks And Watchpoints

- Instrument-level trackers assembled from unpinned reads would be just as irreproducible as HTF bias built from live state.
- Computing HTF bias from unpinned live reads will make results irreproducible.
- Poorly tuned changepoint logic can overfire and make the transition state meaningless.
- If bundle freshness is flattened away, downstream readers will trust stale context incorrectly.

## Exit Criteria

- Cross-timeframe tracker state and HTF bias are computed from pinned snapshot members and publish stable, typed contracts.
- Transition-state handling is explicit and test-covered.
- Bundle consumers can see both the bias decision and the freshness quality of the inputs used to compute it.

## Explicit Exclusions

- No trade signal scoring, grading, or entry recommendation logic.
- No hidden use of forming bars or ad hoc timeframe fetching inside the bias calculation.
- No FVG-derived vote, feature, or field.
