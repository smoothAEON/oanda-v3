# Stage 05 - Public Models, Snapshots, And State Store

> Historical stage spec. The repo now has implemented code beyond this checklist. Keep this document for design lineage, and use [README](../../README.md), [tracker.md](../tracker.md), and [COMMANDS.md](../COMMANDS.md) for current behavior and status.

- Parent Phase: Phase 1 / P0 - Foundation Contracts And Determinism
- Tracker: [tracker.md](../tracker.md)
- Feature IDs: `S05-F01`, `S05-F02`, `S05-F03`

## Purpose

Define the immutable public contracts that all downstream consumers read. This stage creates the two-layer state model, prevents raw DataFrame leakage, and guarantees atomic, versioned publication of analysis state.

## Source Anchors

- [V3_PLAN.md](../V3_PLAN.md): Sections 1.8, 1.15, 5.6, 6, 7.1, 8, 10
- [v3_delivery_phases.md](../v3_delivery_phases.md): Phase 1
- [v3_approval_test_spec.md](../v3_approval_test_spec.md): Phase 1 gate and Phase 3 publication-flow regression

## Scope

- Define public Pydantic model types for snapshots, bundles, freshness, and compact detector summaries.
- Define `MarketStateStore` publication and retrieval behavior.
- Define bundle assembly and mixed-freshness visibility rules.

## Dependencies

- Stage 03 canonical candle contract.
- Stage 04 instrument registry and provider freshness metadata.

## Implementation Checklist

- [x] `S05-F01` Define compact public models for structure, liquidity, zones, indicators, spreads, chop status, and freshness so raw DataFrames never escape internal compute boundaries.
- [x] `S05-F02` Define `MarketStateStore` with immutable deep-copy publication, monotonic versioning, historical snapshot retention, and safe concurrent read behavior.
- [x] `S05-F03` Define `InstrumentBundle` assembly so it pins exact snapshot versions, exposes `mixed_freshness`, `stalest_timeframe`, `stalest_age_seconds`, and `member_freshness`, and clearly shows which members were used for HTF bias.

## Public Interfaces And Contracts

- `TimeframeSnapshot`
  Immutable per `(instrument, timeframe)` state object with version, freshness, and compact summaries.
- `InstrumentBundle`
  Immutable per-instrument aggregate that references exact member snapshot versions and any instrument-level context.
- `MarketStateStore`
  Must support publish and get operations for both snapshots and bundles, plus historical snapshot retrieval by version.

## Tests And Approval Evidence

- `tests/unit/test_models.py`
  Must prove serialization and validation of public-state models.
- `tests/integration/test_snapshot_publication.py`
  Must prove version monotonicity, historical version lookup, atomic publish behavior, and no partial-read visibility.
- Mixed-freshness regression evidence must show that bundle consumers can see stale member detail instead of a falsely atomic aggregate.

## Risks And Watchpoints

- Letting raw DataFrames escape here will make downstream contracts unstable and hard to validate.
- Weak publication semantics will produce inconsistent reads between commands, alerts, and scans.
- If bundles do not pin member versions, HTF bias becomes irreproducible.

## Exit Criteria

- Public state is fully typed, compact, immutable, and versioned.
- Bundle assembly visibly preserves cross-timeframe freshness differences.
- `MarketStateStore` supports atomic publish and historical snapshot retrieval.

## Explicit Exclusions

- No detector implementation logic belongs here beyond the structure of their published outputs.
- No command, alert, or chart formatting logic belongs here.
- No execution, trade planning, grading, or FVG-related contract may be added to any public model.

## Trade Helper Additions

- Implementation update: `TradeRecord` now uses explicit split P&L fields (`pips`, `instrument_pnl`, `instrument_pnl_currency`, `account_pnl`, `account_currency`) instead of an ambiguous single realized-P&L field.
- Implementation update: `TradeClosedEvent` uses `realized_pnl`, and notifier/message-builder contracts now live in `notifications/notifier.py` and `notifications/message_builder.py`.

- Extend `core/models.py` with the following frozen Pydantic models for the trade-helper runtime. These are public contracts consumed by journal, tracking, alert, and notification code:
  - `TradeRecord` — `trade_id`, `instrument`, `units` (float; positive = LONG, negative = SHORT), `open_price`, `close_price`, `sl_price`, `tp_price`, `gslo_price`, `state` (`TradeState` enum), `close_reason` (`CloseReason` enum | None), `realised_pnl`, `pips_pnl`, `opened_at`, `closed_at`, `notes`. Direction is a computed property (`LONG` if `units > 0`, `SHORT` if `units < 0`) — not a stored field.
  - `ExcursionSample` — `trade_id`, `sampled_at`, `bid`, `ask`, `adverse_pips`, `favorable_pips`.
  - `PriceAlert` — `id`, `instrument`, `target_price`, `direction` (above/below), `status` (`AlertStatus` enum), `chat_id`, `notes`, `created_at`, `fired_at`.
  - `IndicatorAlert` — `id`, `instrument`, `granularity`, `indicator` (`IndicatorKind` enum), `condition` (above/below/cross_up/cross_down), `threshold` (float | None; None for cross conditions), `status`, `repeat` (bool), `cooloff_minutes` (int | None), `chat_id`, `notes`, `created_at`, `fired_at`.
- Add the following internal event dataclasses in `core/events.py` (not Pydantic — they are short-lived intra-process events, not persisted):
  - `PriceTick` — `instrument`, `bid`, `ask`, `mid`, `time`.
  - `Heartbeat` — `time`.
  - `TradeOpenedEvent` — `trade_id`, `instrument`, `units`, `open_price`, `sl`, `tp`, `gslo`, `opened_at`.
  - `TradeClosedEvent` — `trade_id`, `instrument`, `units`, `open_price`, `close_price`, `realised_pnl`, `close_reason`, `closed_at`.
  - `TradeModifiedEvent` — `trade_id`, `new_sl`, `new_tp`, `modified_at`.
- Add the following enums to `core/enums.py`: `TradeState` (OPEN, CLOSED), `CloseReason` (SL_HIT, TP_HIT, MANUAL), `AlertStatus` (PENDING, FIRED, CANCELLED), `IndicatorKind` (RSI, STOCH, MACD).
- All datetime fields in Pydantic models are UTC-aware. The existing `_to_utc` validator pattern from the analysis models applies here identically.
- Document pip-size rules for close-reason inference: if close price is within 1 pip of the SL price using the instrument's registry pip size, reason is `SL_HIT`; within 1 pip of TP is `TP_HIT`; otherwise `MANUAL`.
- No execution fields (order ID, broker fill, account mutation) may appear in any of these models.
- Define notifier and message-builder interfaces here as public runtime contracts only. Formatting content is allowed, but no execution or trading-decision fields may leak into these models.
