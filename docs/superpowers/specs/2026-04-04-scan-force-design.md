# Design: `/scan force` — Weekend/Closed-Market Bypass

**Date:** 2026-04-04

## Problem

When the market is closed (weekend, holiday) and the candle cache has not yet been seeded, `/scan` returns `Skipped: market_closed_no_cache` and produces no analysis. OANDA's REST API still serves historical completed candles regardless of market hours, so a scan is possible — it just requires a live fetch to seed the cache.

## Solution

Add an optional `force` keyword to the `/scan` command. When `force` is present, the closed-market `cache_only` gate is bypassed: `get_candles` is called (OANDA historical fetch) instead of `get_cached_candles`. This seeds the cache on the first weekend run; subsequent `/scan` calls (without `force`) serve from the warm cache as normal.

When the market is open, `force` is a no-op.

## Command Surface

```text
/scan force              — full scan, all instruments, bypass closed-market gate
/scan spx500usd force       — single instrument, same bypass
/scan spx500usd             — existing behaviour unchanged
/scan                    — existing behaviour unchanged
```

`force` is parsed case-insensitively from `context.args`. It is valid in any position after the optional symbol argument.

## Architecture

### Orchestrator (`orchestration/scan_orchestrator.py`)

- `scan_all(*, force: bool = False) -> ScanCycleStatus`
- `refresh_instrument(instrument: str, *, force: bool = False) -> InstrumentBundle | None`
- `_scan_instrument(instrument: str, *, force: bool = False) -> tuple[...]`

In `_scan_instrument`, the `cache_only` flag passed to `_build_snapshot` becomes:

```python
cache_only = not market_open and not force
```

No changes to `_build_snapshot` itself — the existing `cache_only=False` path already calls `get_candles` correctly.

### Bot handler (`bot/bot.py`)

- Parse `force` from `context.args` in both the full-scan and single-instrument branches of the `/scan` handler.
- Pass `force=force` to `scan_orchestrator.scan_all()` / `refresh_instrument()`.
- When `force=True` and the market was closed, append to the reply:

```text
Note: forced scan used live fetch (market closed)
```

### Help text (`bot/bot.py`, `/help` output and `docs/COMMANDS.md`)

```text
/scan [symbol] [force]
```

## Data Flow

```text
/scan force
  → bot handler parses force=True
  → scan_orchestrator.scan_all(force=True)
  → _scan_instrument(instrument, force=True)
  → market_open=False, cache_only = not True = False
  → _build_snapshot(..., cache_only=False)
  → get_candles() → OANDA historical fetch → cache seeded
  → bundle assembled and published
```

## Error Handling

- If OANDA returns no candles on a forced scan (e.g., very new instrument with no history), `_replace_from_api` raises `RuntimeError`. The existing per-instrument error handling in `_run_scan` catches this and records it in `ScanCycleStatus.errors` — no special handling needed.
- `force` does not affect `get_current_price`; that call is gated on `market_open` independently and remains skipped when closed.

## Tests

| Test | Change |
| ---- | ------ |
| `test_scan_orchestrator_reports_market_closed_no_cache_when_cache_absent` | Confirm non-force path still returns `market_closed_no_cache` (no change needed, just verify) |
| New: `test_scan_orchestrator_force_bypasses_closed_market_gate` | `force=True` + market closed + `cached_available=False` → `get_candles` called, bundle published |
| New: `test_scan_force_noop_when_market_open` | `force=True` + market open → identical to normal scan |
| Bot handler test | `force` keyword parsed correctly; `force=True` forwarded to orchestrator |

## CLAUDE.md Update

The architectural guardrail:

> Closed-market refreshes use cached candles only; no fabricated freshness provenance

Updates to:

> Closed-market refreshes use cached candles only unless `force=True` is explicitly passed by the user; no fabricated freshness provenance.

## Out of Scope

- Automated weekend scans (background scheduler does not pass `force`)
- Force-fetching current price (market closed means no live pricing stream)
- Admin-only restriction on `force` (any authenticated user may use it)
- `refresh_snapshot` — used by analysis commands (`/smc`, `/bias`, etc.), not `/scan`; same `cache_only` pattern exists there but is not changed by this spec
