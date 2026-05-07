# Stage 11 - Scheduler, Orchestration, And Observability

> Historical stage spec. The repo now has implemented code beyond this checklist. Keep this document for design lineage, and use [README](../../README.md), [tracker.md](../tracker.md), and [COMMANDS.md](../COMMANDS.md) for current behavior and status.

- Parent Phase: Phase 3 / P2 - Operational Integration And Consumption
- Tracker: [tracker.md](../tracker.md)
- Feature IDs: `S11-F01`, `S11-F02`, `S11-F03`, `S11-TH01`

## Purpose

Turn the analysis subsystems into an operational scan runtime. This stage defines the APScheduler job set, the end-to-end scan pipeline, the publish order, and the orchestration-level structured logging needed to observe every state transition on top of the Stage 04 logging bootstrap.

## Source Anchors

- [V3_PLAN.md](../V3_PLAN.md): Sections 1.7, 5.12, 5.13, 7.1, 9, 10
- [v3_delivery_phases.md](../v3_delivery_phases.md): Phase 3
- [v3_approval_test_spec.md](../v3_approval_test_spec.md): Phase 3 gate

## Scope

- Define scheduler jobs and their UTC timing.
- Define the full scan orchestration pipeline from market-open checks through bundle publication.
- Extend structured log coverage from the Stage 04 fetch, cache, and bar-exclusion baseline into orchestration and health-reporting expectations.

## Dependencies

- Stage 04 logging bootstrap and provider-cache event baseline.
- Stage 09 HTF bias.
- Stage 10 calendar and persistence.

## Implementation Checklist

- [x] `S11-F01` Implement APScheduler jobs for auto-scan, London warm, New York warm, Sunday market-open warm, and hourly calendar refresh using the source-plan UTC schedule.
- [x] `S11-F02` Implement scan orchestration in the source-plan order: market-open check, blackout awareness, per-timeframe fetch, trim, detector execution, snapshot publication, HTF bias computation, bundle publication, and scan-cycle completion.
- [x] `S11-F03` Extend the Stage 04 `structlog` setup with structured log events and health outputs for detector timing, spread check, snapshot publish, bundle publish, changepoint detection, calendar refresh, and scan completion without reintroducing a second logging bootstrap.
- [x] `S11-TH01` Implement supervised background tasks for account polling (REST diff on `POLL_INTERVAL_SECONDS` cadence), price-stream fan-out (two independent `asyncio.Queue[PriceTick]` consumers, one for excursion tracking and one for price-alert evaluation), excursion tracking, price-alert evaluation, and indicator-alert scheduling; define restart policy with exponential backoff (1s -> 2s -> 4s -> max 60s) and graceful SIGTERM shutdown for all tasks.

## Public Interfaces And Contracts

- Scheduler controls must support start, pause, resume, and status reporting without changing analysis contracts.
- Orchestrator steps must remain composable; no single monolithic function should own fetch, compute, state mutation, and formatting together.
- Health reporting must expose enough detail for `/status` and admin diagnostics without requiring raw log scraping, while reusing the existing Stage 04 logger configuration.

## Tests And Approval Evidence

- `tests/integration/test_observability.py`
  Must prove the Stage 04 baseline events still exist and the Stage 11 orchestration events are emitted with the required fields.
- `tests/integration/test_snapshot_publication.py`
  Must prove publish order and bundle pinning remain correct during full-flow scenarios.
- Workflow tests must prove the Section 7.1 full scan cycle executes in the documented order.

## Risks And Watchpoints

- Job timing drift or duplicate jobs can produce conflicting writes and noisy alerts.
- An orchestration god-function will be hard to test, cache, or reason about.
- Weak log coverage or a second logger bootstrap will make stale-data or detector-regression incidents hard to diagnose later.

## Exit Criteria

- Scheduler jobs match the source-plan cadence and remain controllable.
- The orchestrator publishes snapshots before bundles and logs every critical transition.
- Operational status can be surfaced through structured data rather than inferred from side effects.

## Explicit Exclusions

- No Telegram formatting or message composition in the orchestrator.
- No command handler may bypass the orchestrator by running detectors inline.
- No scoring or execution behavior is added to scan jobs.

## Trade Helper Additions

- Add supervised runtime tasks for account polling, price-stream fan-out, excursion tracking, price-alert evaluation, and scheduled indicator scans.
- Account polling must diff open-trade snapshots into typed open, close, and modify events, including GSLO capture and close-reason inference without broker writes.
- Stream orchestration must fan out the same `PriceTick` flow to independent consumers so excursion tracking and price-alert evaluation cannot block each other.
- Restart, backoff, and shutdown rules belong here for both the analysis scheduler and the trade-helper background services.
