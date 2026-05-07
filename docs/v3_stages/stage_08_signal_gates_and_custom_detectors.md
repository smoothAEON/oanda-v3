# Stage 08 - Spread And Chop Gates Plus Custom Detectors

> Historical stage spec. The repo now has implemented code beyond this checklist. Keep this document for design lineage, and use [README](../../README.md), [tracker.md](../tracker.md), and [COMMANDS.md](../COMMANDS.md) for current behavior and status.

- Parent Phase: Phase 2 / P1 - Analysis Engine And Signal Gates
- Tracker: [tracker.md](../tracker.md)
- Feature IDs: `S08-F01`, `S08-F02`, `S08-F03`

## Purpose

Add the signal-quality gates and custom pattern detectors that sit beside the SMC and indicator layers. This stage keeps spread handling instrument-aware, confines chop filtering to the source-plan role, and defines the stateless custom detector set.

## Source Anchors

- [V3_PLAN.md](../V3_PLAN.md): Sections 1.5, 5.6, 5.9, 7.1, 8, 10
- [v3_delivery_phases.md](../v3_delivery_phases.md): Phase 2
- [v3_approval_test_spec.md](../v3_approval_test_spec.md): Phase 2 gate

## Scope

- Define the spread gate contract and threshold behavior, driven entirely by registry metadata and current-price snapshots.
- Define the narrow ADX-only chop-filter role for Stage 08. User-facing `/config spread` and `/config chop` persistence remains deferred to later runtime stages.
- Define stateless custom detectors for SFP, Turtle Soup, and ORB, with SFP and Turtle Soup published as additive snapshot summaries.

## Dependencies

- Stage 04 instrument registry and current price access.
- Stage 06 SMC layer and Stage 07 indicator layer.

## Implementation Checklist

- [x] `S08-F01` Implement spread evaluation against registry metadata, including typical spread comparison, max spread rejection, and spike detection with no generic fallback.
- [x] `S08-F02` Implement the chop or ADX gate narrowly as a signal-quality filter, not a broader market-regime engine, and document its pass, warn, and reject semantics.
- [x] `S08-F03` Implement SFP, Turtle Soup, and ORB as pure functions over closed candles, with ORB limited to the lower-timeframe usage described in the scan walkthrough.

## Public Interfaces And Contracts

- Spread output must include raw spread, spread in pips, threshold result, and spike status.
- Chop output must state whether the current market is passable, cautionary, or rejectable according to the ADX-only gate.
- `TimeframeSnapshot` now carries additive `sfp` and `turtle_soup` typed summaries, while `orb` remains lower-timeframe-only and is rejected on non-`M15` snapshots.
- Custom detector outputs publish bounded typed summaries rather than raw pattern frames.

## Tests And Approval Evidence

- `tests/unit/test_spread_filter.py`
  Must prove per-instrument thresholds, spike detection, instrument-specific handling, and unknown-instrument failure.
- `tests/unit/test_chop_filter.py`
  Must prove ADX pass, caution, reject, unavailable-metric behavior, and repeatability.
- `tests/unit/test_custom_detectors.py`
  Must prove SFP, Turtle Soup, and ORB remain deterministic, non-mutating, and bounded to their intended contracts.
- `tests/unit/test_models.py`
  Must prove the Stage 08 result models validate correctly and that `orb` remains limited to `M15`.
- `tests/integration/test_observability.py`
  Must prove `spread_checked` emits the required structured fields.
- Detector-statelessness tests must prove SFP, Turtle Soup, and ORB return the same result for the same closed-bar input.
- ORB tests must prove the detector remains constrained to the intended lower-timeframe usage.

## Risks And Watchpoints

- A generic spread threshold fallback would directly violate the source plan.
- Expanding the chop filter beyond its narrow role would widen scope and weaken testability.
- Stateful detector implementations would undermine determinism and replayability.

## Exit Criteria

- Spread handling is fully registry-driven and loud on unknown instruments.
- Chop filtering remains intentionally narrow and documented.
- Custom detectors are pure, closed-bar-only, and publish compact summaries.

## Explicit Exclusions

- No FVG or other off-plan detector additions.
- No hidden lifecycle state or stateful detector classes.
- No inline command execution of gates or detectors.
