# Stage 16 - Historical Placeholder For The Old Release Stage

> This file remains only so older links still resolve. The old Stage 16 release-readiness scope has moved to Stage 17.

- Tracker: [tracker.md](../tracker.md)
- Current Stage 16 doc: [Stage 16 operator and trader workflow completion](./stage_16_operator_and_trader_workflow_completion.md)
- Replacement release stage: [Stage 17](./stage_17_core_release_readiness.md)

## Current Meaning

Historical Stage 16 used to own test, CI, deployment, backup, restore, runbooks, and release evidence.

That scope has been moved:

- `S16` now owns runtime-feature completion for the non-security backlog from the older bots.
- `S17` now owns CI, deployment, backup, restore, runbooks, and the core-release gate.
- `S17` is the first stage allowed to claim core release readiness.

## Mapping

- old `S16` tests and release scope -> new `S17`
- new runtime features such as `/tradeplan`, `/fib`, exact-datetime `/timealert`, `/exporttimealerts`, `/importtimealerts`, `/price --live`, chart modes, trade lifecycle pushes, and read-only history enrichment -> new `S16`

## Status

- No new work should be tracked against this historical file.
- Use [Stage 17](./stage_17_core_release_readiness.md) for the active release-readiness backlog.
