# Stage 07 - Indicator Layer And Tick-Volume Semantics

> Historical stage spec. The repo now has implemented code beyond this checklist. Keep this document for design lineage, and use [README](../../README.md), [tracker.md](../tracker.md), and [COMMANDS.md](../COMMANDS.md) for current behavior and status.

- Parent Phase: Phase 2 / P1 - Analysis Engine And Signal Gates
- Tracker: [tracker.md](../tracker.md)
- Feature IDs: `S07-F01`, `S07-F02`, `S07-F03`

## Purpose

Build the indicator layer on top of canonical candles while keeping public outputs compact, OTC volume semantics explicit, and package responsibilities clear. This stage prevents indicator sprawl, mislabeled volume usage, and public-state leakage of raw indicator frames.

## Source Anchors

- [V3_PLAN.md](../V3_PLAN.md): Sections 1.6, 5.8, 8, 10
- [v3_delivery_phases.md](../v3_delivery_phases.md): Phase 2
- [v3_approval_test_spec.md](../v3_approval_test_spec.md): Phase 2 gate

## Scope

- Define the full TA-Lib indicator wrapper set used by Section 5.8 families.
- Define the pandas-ta supplemental indicator wrapper set that is currently exposed by the approved package surface.
- Define the tick-volume labeling, caveat, and serialization rules.

## Dependencies

- Stage 03 canonical candles and trim policy.
- Stage 05 public summary models.

## Implementation Checklist

- [x] `S07-F01` Implement TA-Lib wrappers for the full Section 5.8 families shipped in Stage 07: EMA, SMA, TEMA, KAMA, SAR, RSI, MACD, CCI, CMO, PPO, AROON, ADXR, ATR, NATR, TRANGE, ADX, Bollinger Bands, and Stochastic.
- [x] `S07-F02` Implement pandas-ta wrappers for VWAP, Squeeze Momentum, and Ichimoku, pin the resolvable package version in `requirements.txt`, and document Nadaraya-Watson as deferred until the approved package surface exposes it.
- [x] `S07-F03` Define tick-volume outputs with `tick_*` names, `volume_type == "tick_count"`, and a caveat field that states the values are computed from OANDA tick count rather than exchange volume.

## Public Interfaces And Contracts

- `indicators.build_indicator_summary(candles, timeframe) -> IndicatorValueSummary` is the package entrypoint for compact public indicator publication.
- Public indicator output must be flattened into compact typed summaries, not full rolling series.
- TA-Lib remains the primary engine where the indicator exists there; pandas-ta is supplemental rather than a replacement.
- Any indicator derived from `tick_volume` must carry a caveat and must never be labeled simply as `volume`.
- Ichimoku must call pandas-ta with `lookahead=False`, and public span values must be taken from the forward-span output rather than publishing full future frames.
- Nadaraya-Watson remains deferred in Stage 07; no custom off-plan implementation replaces the missing approved-package surface.

## Tests And Approval Evidence

- `tests/unit/test_tick_volume.py`
  Must prove caveat propagation and canonical tick-volume naming.
- `tests/unit/test_indicator_layer.py`
  Must prove deterministic output ordering, closed-bar-only behavior, pandas-ta index handling, and TimeframeSnapshot serialization.
- Contract tests must prove public indicator summaries serialize through Stage 05 models without raw DataFrame leakage.
- Regression checks must prove indicator wrappers only consume closed canonical candles.

## Risks And Watchpoints

- Indicator wrappers can easily drift into dumping raw columns into public models.
- Mislabeling OTC tick count as exchange volume would invalidate downstream interpretation.
- Using pandas-ta where TA-Lib already covers the indicator will create inconsistent implementations.
- The currently approved pandas-ta package line exposes VWAP, Squeeze Momentum, and Ichimoku but not a Nadaraya-Watson implementation.

## Exit Criteria

- Core and supplemental indicators are separated cleanly by package role.
- Public indicator outputs are compact and serializable.
- Tick-volume indicators are clearly labeled and caveated everywhere they appear.
- The Stage 07 docs, tracker, and dependency lock reflect the temporary Nadaraya-Watson deferment explicitly.

## Explicit Exclusions

- No feature flags for disabled indicator families.
- No exchange-volume claims or unlabeled `volume` outputs.
- No scoring or trade recommendation logic built on top of indicator values.
- No custom Nadaraya-Watson implementation outside the approved package set.
