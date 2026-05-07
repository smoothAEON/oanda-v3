# Stage 03 - Candle Schema And Closed-Bar Policy

> Historical stage spec. The repo now has implemented code beyond this checklist. Keep this document for design lineage, and use [README](../../README.md), [tracker.md](../tracker.md), and [COMMANDS.md](../COMMANDS.md) for current behavior and status.

- Parent Phase: Phase 1 / P0 - Foundation Contracts And Determinism
- Tracker: [tracker.md](../tracker.md)
- Feature IDs: `S03-F01`, `S03-F02`, `S03-F03`

## Purpose

Lock the candle boundary rules that every detector, provider, cache, and command path must follow. This stage prevents repainting, schema drift, timezone ambiguity, and mutation of shared candle inputs.

## Source Anchors

- [V3_PLAN.md](../V3_PLAN.md): Sections 1.2, 1.3, 5.2, 7.1, 8, 10
- [v3_delivery_phases.md](../v3_delivery_phases.md): Phase 1
- [v3_approval_test_spec.md](../v3_approval_test_spec.md): Phase 1 gate

## Scope

- Define the canonical candle schema and validator.
- Define `trim_to_closed` and the only permitted provisional-bar behavior.
- Define timezone, sorting, null-handling, and immutability rules for candle DataFrames.

## Dependencies

- Stage 01 tooling baseline.
- Stage 02 settings for default candle counts and any timeframe policy constants.

## Implementation Checklist

- [ ] `S03-F01` Define `CANONICAL_COLUMNS` as exactly `time`, `open`, `high`, `low`, `close`, and `tick_volume`; reject `volume` and any public-state ambiguity.
- [ ] `S03-F02` Define `trim_to_closed(df, timeframe)` as the canonical entrypoint for detector-facing candle trimming, with provisional-bar output allowed only when explicitly tagged and kept separate from normal detector output.
- [ ] `S03-F03` Define `validate_candle_df(df)` so `time` is always a UTC-aware column, never an index, rows are sorted ascending, and the input DataFrame is not mutated in place.

## Public Interfaces And Contracts

- `validate_candle_df(df) -> pd.DataFrame`
  Returns a validated, normalized copy of the input DataFrame in canonical column order.
- `trim_to_closed(df, timeframe) -> pd.DataFrame`
  Returns only closed bars for normal analysis use.
- Optional provisional handling, if implemented later, must return an explicitly labeled structure separate from the normal closed-bar output and must never be used by default detector code paths.

## Tests And Approval Evidence

- `tests/unit/test_candle_policy.py`
  Must prove forming-bar exclusion, idempotence, and non-mutation.
- `tests/unit/test_candle_schema.py`
  Must prove missing-column rejection, `time` index reset, naive-to-UTC coercion, and `volume` rejection.
- Regression evidence that detectors in later stages call the canonical trimming path rather than reimplementing bar logic.

## Risks And Watchpoints

- A single detector bypassing `trim_to_closed` would reintroduce repainting.
- Timezone coercion must be explicit to avoid silent UTC or local mismatches.
- Returning mutated input frames would create hidden coupling between detectors.

## Exit Criteria

- The canonical schema and trim policy are documented, implemented, and test-covered.
- No public interface allows `time` as an index or `volume` as a public column name.
- Later stages can assume candle inputs are closed-bar, UTC-aware, and canonical.

## Explicit Exclusions

- No detector-specific calculations beyond schema validation and closed-bar handling.
- No cache freshness policy beyond the DataFrame normalization rules introduced here.
- No command or provider code may bypass these rules later.
