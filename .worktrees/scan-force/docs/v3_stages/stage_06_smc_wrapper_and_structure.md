# Stage 06 - SMC Wrapper And Structure Summaries

> Historical stage spec. The repo now has implemented code beyond this checklist. Keep this document for design lineage, and use [README](../../README.md), [tracker.md](../tracker.md), and [COMMANDS.md](../COMMANDS.md) for current behavior and status.

- Parent Phase: Phase 2 / P1 - Analysis Engine And Signal Gates
- Tracker: [tracker.md](../tracker.md)
- Feature IDs: `S06-F01`, `S06-F02`, `S06-F03`

## Purpose

Add the deterministic SMC layer on top of the Phase 1 contracts. This stage standardizes the `smartmoneyconcepts` adapter, produces compact structure and liquidity summaries, and enforces stateless detector behavior.

## Source Anchors

- [V3_PLAN.md](../V3_PLAN.md): Sections 1.9, 5.6, 7.1, 8, 10
- [v3_delivery_phases.md](../v3_delivery_phases.md): Phase 2
- [v3_approval_test_spec.md](../v3_approval_test_spec.md): Phase 2 gate

## Scope

- Normalize canonical candle inputs for `smartmoneyconcepts` and keep the package boundary deterministic.
- Produce compact summaries for swing highs and lows, BOS and CHOCH, order blocks, liquidity, sessions, previous high and low, and retracement state.
- Emit deterministic order-block candidates as internal typed intermediates for the pinned instrument-level tracker assembled later in Stage 09.
- Define negative checks that forbid FVG and hidden instance state.

## Dependencies

- Stage 03 closed-bar and canonical schema rules.
- Stage 05 public models and snapshot publication contracts.

## Implementation Checklist

- [x] `S06-F01` Implement `smc.provider.SmcAdapter` as a thin adapter that accepts only closed canonical candles, suppresses package-side credit noise, and returns deterministic typed adapter results without leaking raw package frames into public contracts.
- [x] `S06-F02` Map SMC outputs into compact public summaries for structure events, latest swing highs and lows, order-block zones, liquidity context, sessions, previous high and low, and retracement state, while also emitting deterministic order-block candidates for Stage 09 tracker assembly.
- [x] `S06-F03` Add explicit negative checks and tests that forbid `smc.fvg()` usage, hidden detector state, and any output path that changes under identical closed-bar input.

## Public Interfaces And Contracts

- `SmcAdapter.analyze(instrument, timeframe, candles) -> SmcAnalysisResult` is the Stage 06 entrypoint for deterministic SMC analysis over canonical closed candles.
- `SmcAnalysisResult` carries `structure`, `zones`, `liquidity`, `smc_context`, and internal `order_block_candidates`; only the typed summary fields are published into `TimeframeSnapshot`.
- `StructureEventSummary` must include bounded recent BOS or CHOCH history plus latest swing-high and swing-low context.
- `TimeframeSnapshot.smc_context` must publish bounded session context, previous-high and previous-low context, and retracement context without exposing raw `smartmoneyconcepts` frames.
- Order-block and liquidity summaries must remain bounded in size so snapshots stay compact and stable, while deterministic order-block candidate ids remain stable enough for later pinned tracker publication.

## Tests And Approval Evidence

- `tests/unit/test_smc_adapter.py`
  Must prove deterministic output, no input mutation, graceful insufficient-history behavior, additive session and retracement context, and deterministic order-block candidate ids.
- `tests/unit/test_models.py`
  Must prove the Stage 06 structure, session, previous-high and previous-low, retracement, and tracker-adjacent models validate and serialize through `TimeframeSnapshot`.
- `tests/unit/test_analysis_boundaries.py`
  Must prove no `smc.fvg()` path or FVG field exists anywhere in repo code or public models.
- Detector-output contract tests must prove raw SMC DataFrames do not reach public state, and Stage 09 handoff coverage must prove Stage 06 order-block candidates can be pinned later without losing snapshot-version traceability.

## Risks And Watchpoints

- Overexposing raw `smartmoneyconcepts` outputs will create unstable public contracts.
- Hidden mutable detector state will break reproducibility and command consistency.
- Adding FVG for completeness would directly violate the source plan.

## Exit Criteria

- SMC outputs are deterministic, stateless, and published as compact summaries only.
- Snapshot consumers can read structure, swing, order-block, liquidity, session, previous-high and previous-low, and retracement context without parsing raw frames.
- The SMC layer stays within the named source-plan scope and excludes FVG entirely while emitting stable order-block candidates for later multi-timeframe assembly.

## Explicit Exclusions

- No trade planning, entry selection, or confidence grading is part of SMC output.
- No standalone FVG detector, field, or command is allowed.
- No command handler may call the SMC adapter directly instead of reading published state.
