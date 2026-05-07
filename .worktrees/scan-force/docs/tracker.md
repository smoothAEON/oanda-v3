# Current Repo Tracker

This file is the current implementation status board for the repository.

Authoritative docs for the repo as it exists today:

- [README.md](../README.md)
- [COMMANDS.md](./COMMANDS.md)
- [GLOSSARY.md](./GLOSSARY.md)

Historical design docs:

- [V3_PLAN.md](./V3_PLAN.md)
- [v3_delivery_phases.md](./v3_delivery_phases.md)
- [v3_stage_index.md](./v3_stage_index.md)
- [v3_stages/](./v3_stages)

## Snapshot

Status date: `2026-03-29`

| Area | Status | Notes |
| --- | --- | --- |
| Foundation contracts and analysis core | `shipped` | Settings, candle policy, registry, cache, public models, market-state publication, SMC, indicators, spread and chop gates, SFP, Turtle Soup, ORB, HTF bias. |
| Scheduler and scan orchestration | `shipped` | APScheduler jobs, cache warmers, calendar refresh, trade poller scheduling, scan publication flow, closed-market cache-only scans, and explicit freshness provenance checks. |
| Telegram runtime and command handling | `shipped` | Auth, state-first refresh behavior, account commands, analysis commands, `/dayrange`, `/pdh`, `/pdl`, `/fib`, `/tradeplan`, charting, extractor, journal, MAE/MFE, time alerts, and runtime config. |
| Persistence | `shipped` | TinyDB sessions, runtime config, trades, excursion samples, price alerts, indicator alerts, time alerts, indicator evaluation cursors, plus CSV candle cache metadata. Durable user-facing writes fail explicitly, CSV writes are atomic, and a single-writer runtime lock fails fast on cross-process reuse. |
| Price and indicator alert CRUD | `shipped` | `/pricealert`, `/listpricealerts`, `/clearpricealert`, `/indicatoralert`, `/indicatoralert defaults`, `/listindicators`, `/clearindicator`. Alert list and clear operations are chat-scoped. Automatic indicator alerts accept `M15`, `H1`, `H4`, and `D`. |
| Time alerts and session reminders | `shipped` | `/timealert`, `/listtimealerts`, and `/cleartimealert` are implemented. Alerts persist in UTC, accept SGT fixed-time input, support London, New York, and market-open session reminders, and evaluate every minute. |
| Background alert evaluation | `shipped` | Price alerts evaluate off the live stream; indicator alerts evaluate during fresh open-market snapshot generation; time alerts evaluate every minute; same-candle dedupe is persisted across restarts; fired state is persisted. |
| Background Telegram push delivery | `shipped` | Runtime startup injects a concrete Telegram notifier and default message builder for fired price alerts, fired indicator alerts, time-alert reminders, and trade-open and trade-close lifecycle pushes. |
| Stage 16 operator and trader workflow completion | `shipped` | Live-price command fallback, chart modes, bounded read-only trade plans, fib summaries, previous-day helpers, time alerts, session reminders, trade lifecycle pushes, and read-only transaction enrichment are implemented. |
| Admin controls and security backlog | `open` | Historical Stage 14 only. No `/security`, `/sessions`, `/ban`, `/unban`, `/mute`, `/unmute`, or `/override` handlers are registered. |
| Stage 17 core release readiness | `open` | No repository CI workflow, deployment automation, backup drills, or release checklist automation is committed. |
| Stage 18 macro and refined market-hours enrichment | `shipped` | Bounded `yfinance` VIX/DXY status is exposed through runtime health and `/marketstatus`; refined `pandas_market_calendars` checks are category-aware for FX and metals; macro refresh and dynamic market-open warm scheduling are implemented without changing snapshot or bundle contracts. |

## Historical Stage Mapping

The older stage numbering is still useful as shorthand, but the old stage docs are no longer the live status board.

| Planned stage | Current repo state |
| --- | --- |
| `S01` to `S12` | Implemented in code. |
| `S13` | Implemented in `bot/` and covered by unit and integration tests. |
| `S14` | Open backlog only: security and admin controls remain deferred and non-blocking for the core release path. |
| `S15` | Retired placeholder: historical references still resolve, but the old scope moved to `S18`. |
| `S16` | Shipped: operator and trader workflow completion is implemented in code and covered by local tests. |
| `S17` | Open: broad CI, deployment, runbook, backup, and release evidence is not committed. |
| `S18` | Shipped additive enrichment: bounded macro status and refined FX/metals market-hours behavior are implemented and remain non-blocking for the core release path. |

## Current Gaps

- implement the historical Stage 14 security and admin surface without changing the Stage 16 storage and notifier model
- add Stage 17 CI, deployment, backup, runbook, and release evidence

## Still-Enforced Boundaries

These design rules are still accurate and still enforced by the current codebase:

- no automated broker execution
- no FVG support
- no execution-analysis coupling
- no raw DataFrames in published state
- no TTL-only freshness
- no generic spread fallback
- no inline detector execution inside command handlers
