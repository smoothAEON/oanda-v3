# Stage 15 - Historical Placeholder

> This file remains only so older links still resolve. Historical Stage 15 is no longer an active delivery stage.

- Tracker: [tracker.md](../tracker.md)
- Replacement stage: [Stage 18](./stage_18_macro_context_and_market_hours.md)

## Current Meaning

Historical Stage 15 used to own macro context and refined market-hours work.

That scope has been moved:

- `S15` is now a retired placeholder with no active checklist.
- The real macro and market-hours backlog now lives in `S18`.
- `S18` is explicitly post-release and non-blocking for the Stage 17 core release gate.

## What Moved To Stage 18

- `yfinance` macro context such as VIX and DXY
- refined `pandas_market_calendars` behavior beyond the basic market-hours checks already in the repo
- additive fallback rules for deferred enrichment

## What Did Not Move Here

- Stage 16 runtime completion work such as `/tradeplan`, `/fib`, `/timealert`, `/price --live`, chart modes, and trade lifecycle pushes
- Stage 17 release-readiness work such as CI, deployment, backup, restore, and release evidence

## Status

- No active implementation work should be tracked against `S15`.
- Use [Stage 18](./stage_18_macro_context_and_market_hours.md) for the deferred backlog.
