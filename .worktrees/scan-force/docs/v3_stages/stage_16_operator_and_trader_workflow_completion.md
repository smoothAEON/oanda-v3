# Stage 16 - Operator And Trader Workflow Completion

> Active Stage 16. This stage owns the non-security runtime backlog pulled forward from the older repos.

- Tracker: [tracker.md](../tracker.md)
- Historical release placeholder: [old Stage 16 file](./stage_16_test_ci_deployment_and_release.md)

## Purpose

Finish the operator and trader workflow surface on top of the shipped read-only runtime without widening into broker writes, grading, confidence scoring, or inline detector execution.

## Scope

- utility commands from published state: `/dayrange`, `/pdh`, `/pdl`, `/fib`, `/tradeplan`
- reminder commands and runtime: `/timealert`, `/listtimealerts`, `/cleartimealert`
- live-price preference: `/price <symbol> [--live]`
- chart presentation presets: `/chart ... --mode compact|balanced|full`
- runtime-config keys: `chart_mode`, `trade_push`, `session_alerts`
- trade-open and trade-close Telegram pushes
- session-open reminders for London, New York, and Sunday market open
- read-only transaction and trade-history enrichment for close-reason and realized-PnL detail

## Boundaries

- no broker writes
- no FVG
- no execution-analysis coupling
- no raw DataFrames in published state
- no inline detector execution inside command handlers
- `/tradeplan` stays read-only, deterministic, and state-derived

## Implementation Checklist

- [x] `S16-F01` add `/dayrange`, `/pdh`, `/pdl`, `/fib`, and bounded `/tradeplan`
- [x] `S16-F02` add `/timealert`, `/listtimealerts`, `/cleartimealert`, session reminders, and UTC-backed time-alert persistence
- [x] `S16-F03` add `/price --live` with explicit REST fallback reporting
- [x] `S16-F04` add chart modes and runtime-config support for `chart_mode`
- [x] `S16-F05` wire trade-open and trade-close events into the notifier path
- [x] `S16-F06` add read-only transaction enrichment for close-reason and notification detail
- [x] `S16-F07` add Stage 16 feature-local unit and integration coverage

## Public Interfaces

- `TimeAlert`, `TimeAlertKind`, `TimeAlertStatus`
- `FibSummary`
- `TradePlanSummary`
- TinyDB `time_alerts`
- runtime-config keys `chart_mode`, `trade_push`, `session_alerts`

## Tests And Approval Evidence

- parser and formatter coverage for the new command surface
- time-alert repository and scheduler coverage
- stream-quote freshness coverage for `/price --live`
- chart-mode resolution coverage
- trade notification dispatch coverage
- transaction-history normalization coverage
- trade-plan builder accept and reject coverage

## Exit Criteria

- the runtime feature surface is implemented and locally tested
- all Stage 16 work remains read-only and state-derived
- Stage 17 can take over as the first core-release gate
