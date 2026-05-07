# MCP Server: Known Problems and Recommended Additions

This document is the authoritative reference for known limitations, behavioural gaps, and recommended improvements to the MCP server. It is written from the perspective of an LLM agent consuming the server programmatically. All claims are grounded in the current codebase — file and line references are provided where the root cause is in code.

---

## Part 1 — Confirmed Problems

These are real issues traceable directly to the code.

---

### P1 — MAE/MFE is empty for all closed trades

**Severity: High**

`ExcursionTracker.process_tick()` at `tracking/excursion_tracker.py:34` calls `trade_repository.list_open()` exclusively. Samples are only written while the trade is open and the price stream is live. The moment a trade closes, sampling stops permanently.

`get_journal_trade` and `get_mae_mfe` both read from `excursion_repository.list_for_trade` and `get_mae_mfe`. For any trade that was closed before the excursion tracker had enough ticks, or that was closed during a stream outage, these will return `samples=[]` and `mae_mfe=null` with no error or warning.

**Consequence:** Post-trade review, exit efficiency analysis, and heat-to-reward calculations are unavailable for closed trades. The tools advertise this capability but deliver nothing for the closed case.

**What needs to happen:** Either a retroactive bar-based excursion backfill (reconstruct MAE/MFE from M1/M5 candles between `opened_at` and `closed_at`), or the tracker must snapshot final MAE/MFE on trade close and persist it separately so it survives after the open-trade list clears.

---

### P2 — `scan_all` silently skips instruments when the market is closed and cache is cold

**Severity: High**

`scan_orchestrator.py:338` returns `(None, 0, "market_closed_no_cache", False)` the moment any single timeframe for an instrument has no cache entry. The outer `_run_scan` loop at `scan_orchestrator.py:270-273` silently continues to the next instrument. The `ScanCycleStatus` returned by `scan_all` reports `scanned_instruments` as a tuple containing only the instruments that succeeded — the skipped ones appear only in the aggregate `skipped_reason` field which collapses all reasons into one string.

This means on a weekend cold start:
- 12 instruments requested
- 0-1 actually scanned (only those with warm cache)
- The agent has no way to know which instruments were skipped or why

`force=True` on `scan_all` does propagate to `_scan_instrument`, but `_scan_instrument` with `force=True` still uses `cache_only=False`, which means it will try to fetch from OANDA — which returns nothing meaningful for a closed market.

**Consequence:** Agents calling `scan_all` on a weekend will get a response that looks successful (no errors in the payload) but contains stale or partial coverage, with no per-instrument skip detail.

**What needs to happen:** `ScanCycleStatus` should include a `per_instrument` breakdown: `{instrument: "scanned" | "skipped:<reason>" | "error:<message>"}`. The current single `skipped_reason` field is not useful for multi-instrument workflows.

---

### P3 — `get_historical_bars` is a redundant alias

**Severity: Medium**

`adapters.py:201` shows `get_historical_bars` simply calls `get_candles` with identical parameters and returns the result unchanged. There is no functional difference between the two tools.

This wastes an LLM's tool selection budget and creates confusion about which tool to use. An LLM that discovers both tools via `goldsignal://tool-surface` may attempt both for the same request.

**What needs to happen:** Remove `get_historical_bars` from `TOOL_SPECS`. Document the removal in the capabilities resource. `get_ohlc` with `price_component="bid_ask"` is the genuine alternative for direct-fetch data; `get_candles` covers the cache path.

---

### P4 - Resolved: historical candle tools were cache-first with no cold-cache warning

**Severity: Medium**

Resolved by making MCP raw candle reads direct OANDA calls by default.
`_mid_ohlc_payload` now calls `account_client.get_candles()` and returns
`source="oanda_api_direct"` with `freshness=None`, so a cold analysis cache no
longer affects `get_candles` or `get_ohlc(price_component="mid")`.

`force=True` is retained only as a compatibility flag and has no additional effect
for raw MCP candle reads because direct fetch is already the default.

**Remaining note:** Scan, snapshot publication, chart rendering, and indicator
paths still use the analysis candle cache where freshness metadata matters.

---

### P5 — `_resolve_snapshot` refresh path ignores `force` when market is closed

**Severity: Medium**

`adapters.py:924-930` calls `scan_orchestrator.refresh_snapshot(resolved_instrument, resolved_timeframe)` without passing `force=True`. Inside `refresh_snapshot` at `scan_orchestrator.py:170`, when the market is closed, `cache_only=not instrument_market_status.is_market_open and not force` evaluates to `cache_only=True`. If there is no cache, `_build_snapshot` returns `None` and the refresh returns `None`.

The `_resolve_snapshot` path then falls back to `snapshot is None` → raises `ValueError("Data unavailable")`.

This means tools using `refresh_policy="if_missing"` or `"always"` do not actually force a fresh fetch when the market is closed — they silently fail to refresh and raise an error. The `force` parameter that exists on `refresh_snapshot` is never forwarded from the adapter layer.

**What needs to happen:** `_resolve_snapshot` and `_resolve_bundle` should accept and forward a `force_refresh: bool` parameter, or at minimum document clearly that `refresh_policy="always"` does not override the closed-market cache gate.

---

### P6 — Alert state has no per-client isolation

**Severity: Medium**

All three alert families (price, indicator, time) route through a single `default_chat_id` configured at server startup (`adapters.py:54-58`). There is no per-session, per-agent, or per-workflow namespace.

Consequences:
- Multiple LLM agent sessions share the same alert stack
- Test runs that create alerts pollute the production alert state
- `seed_default_indicator_alerts` creates up to 96 alerts (12 instruments × 2 momentum thresholds × 2 + 12 × 4 timeframes × 2 SMA cross directions) all under the same chat ID
- `list_price_alerts` returns all alerts for the chat, which can be a large noisy payload when many agents have written to it

**What needs to happen:** A `namespace` or `tag` parameter on all alert create/list/clear tools, allowing agents to scope their alerts without interfering with other consumers.

---

### P7 — `get_mae_mfe` without `trade_id` only covers open trades

**Severity: Medium**

`adapters.py:552-569` shows that when called with no `trade_id`, `get_mae_mfe` calls `trade_repository.list_open()` exclusively. There is no way to get a MAE/MFE summary across all closed trades or a recent closed-trade window through this tool.

Combined with P1, this means MAE/MFE data is only meaningfully available for trades that are currently open and have been tracked since open.

---

### P8 — `create_time_alert` supports only three session names

**Severity: Low**

`adapters.py:798` validates against `_SUPPORTED_TIME_ALERT_SESSIONS = frozenset({"london", "newyork", "market_open"})`. Sydney and Tokyo sessions are not supported, despite appearing in `get_session_context` output.

An agent trying to set a Tokyo open reminder will receive a `ValueError` with no suggestion of supported alternatives.

---

### P9 — API key is passed as a URL query parameter

**Severity: Low (security hygiene)**

`auth.py:23` reads `request.query_params.get("api_key")`. Query parameters appear in server access logs, browser history, proxy logs, and any URL that gets copy-pasted. For a credential, this is poor hygiene.

The standard is an `Authorization: Bearer <token>` header. The current implementation is acceptable for local/internal use but is a liability if the MCP port is ever exposed beyond localhost.

---

### P10 — `get_trade_history` offers only period-based filtering

**Severity: Low**

`adapters.py:571-586` passes `period` (a string like `"day"`, `"week"`, `"month"`) to `trade_history_service.get_trade_history`. There is no ISO date range (`start_date`, `end_date`) parameter. Agents doing post-trade review for a specific date window cannot target it precisely.

---

### P11 — Raw `ValueError` is the only error contract

**Severity: Low**

All tool errors raise `ValueError` with a plain string message (`adapters.py:148, 507, 932, 952` etc.). There are no structured error codes. An LLM agent cannot branch on error type programmatically — it must parse the error string, which is fragile.

Errors that should be distinguishable:
- `DATA_UNAVAILABLE` — cache cold, instrument unknown, no bundle
- `MARKET_CLOSED` — market is closed and force not set
- `INSTRUMENT_UNKNOWN` — bad instrument string
- `ALERT_NOT_FOUND` — clear on missing ID
- `VALIDATION_ERROR` — bad parameter value

---

## Part 2 — Behavioural Limitations (Not Bugs, But Real Constraints)

These are not code defects but constraints an agent consumer must understand.

---

### L1 — No execution surface

The MCP server is read-only by design. `providers/oanda_execution.py` is an empty stub. There are no tools for placing orders, modifying stop-losses, closing positions, or adjusting take-profits. An LLM agent can analyse and plan but cannot act.

This is an intentional architectural constraint per `CLAUDE.md`. Any execution layer must be added as a separate, isolated surface with a full design review before touching this codebase.

---

### L2 — No push or wake-up mechanism

Agents must poll. There is no webhook, callback, or streaming endpoint that fires when a condition is met (price level crossed, structure break detected, alert fired). The alert engine pushes to Telegram — not to an agent process.

For autonomous workflows this means: the agent either polls `get_runtime_status`/`get_market_status` on a timer, or the operator must build an external trigger that calls the agent when the bot fires an alert to Telegram.

---

### L3 — Freshness is advisory, not enforced

Stale bundles are returned with freshness warnings in the payload but no error is raised. Tools like `get_trade_plan`, `get_htf_bias`, and `get_smc_snapshot` will return structure from a 72-hour-old scan if the cache has not been refreshed, with only a human-readable warning field that an agent may not check.

There is no `max_age_seconds` or `require_fresh` parameter on any snapshot tool.

---

### L4 — No position sizing or risk calculation surface

The account surface (`get_account_summary`, `list_open_positions`) provides raw balance, margin, and P&L numbers. There are no derived tools for:
- Position size given account risk % and stop distance
- Total exposure across open positions in pip or dollar terms
- Margin utilisation percentage
- Correlated instrument exposure (e.g., USD exposure across EUR_USD + USD_JPY + USD_CAD)

An agent building a trade plan would need to derive all of this from raw account data and pip metadata, which is error-prone.

---

### L5 — No atomic multi-instrument snapshot

Every snapshot and bundle tool targets one instrument per call. An agent scanning all 12 instruments for a setup must make 12 sequential calls (or attempt concurrent calls, which may overload the scan orchestrator). There is no `get_multi_snapshot` that returns a consistent, simultaneously-captured view across instruments.

---

### L6 — `prefer_live=True` on `get_price` has no hard-fail mode

When `prefer_live=True` and the stream is stale or unavailable, the tool silently falls back to REST pricing with a `fallback_note` field. An agent that requires genuinely live pricing (e.g., for spread gating before a decision) has no way to say "fail instead of falling back."

---

### L7 — `scan_all` with `force=True` does not bypass the closed-market gate for all instruments

When `force=True` is passed to `scan_all`, it propagates to `_scan_instrument` where `forced_market_fetch = force and not market_open`. This flag is tracked but `_build_snapshot` is still called with `cache_only=False` — it will attempt to fetch from OANDA. For closed-market instruments (forex on weekends), the OANDA API returns no usable candles. The instrument will still fail to build a bundle and will be skipped.

The practical effect is that `force=True` on `scan_all` only helps if the market is open. For weekend review workflows, it does not help.

---

## Part 3 — Recommended Additions

These are new tools or expansions to existing tools that would materially improve the server for agent use.

---

### A1 — `get_scan_health` (new tool)

**Purpose:** Return a per-instrument breakdown of cache state, last successful scan time, and skip reasons.

**Why:** The current `get_runtime_status` gives aggregate health. `scan_all` returns a status object that only names instruments that succeeded. Agents have no way to know which instruments are cache-ready, which are stale, and which failed.

**Proposed response:**
```json
{
  "as_of": "2026-04-05T10:00:00Z",
  "instruments": {
    "XAU_USD": {
      "status": "ready",
      "last_scanned_at": "2026-04-05T08:15:00Z",
      "bundle_age_seconds": 6300,
      "cached_timeframes": ["M15", "H1", "H4", "D"],
      "missing_timeframes": []
    },
    "EUR_USD": {
      "status": "cold",
      "last_scanned_at": null,
      "bundle_age_seconds": null,
      "cached_timeframes": [],
      "missing_timeframes": ["M15", "H1", "H4", "D"]
    }
  }
}
```

---

### A2 — `get_candle_range` (new tool)

**Purpose:** Fetch candles by time range (`start`, `end`) rather than a trailing count.

**Why:** `get_candles` and `get_ohlc` only support `count` (a trailing window). For post-trade review, backfill, or a specific session replay, agents need to request `2026-04-01T08:00Z` to `2026-04-01T16:00Z` H1 bars. This is not possible with the current tools.

**Parameters:** `instrument`, `timeframe`, `start` (ISO datetime), `end` (ISO datetime), `price_component`.

This should always go direct to OANDA (not cache) since date-range requests are inherently for historical data outside the rolling cache window.

---

### A3 — `get_excursion_summary` (new tool or expansion of `get_mae_mfe`)

**Purpose:** Return a real MAE/MFE summary for closed trades, built retroactively from bar data if tick samples are missing.

**Why:** P1 — excursion samples are only written for open trades. This tool should:
1. Check if tick samples exist (live path)
2. If not, fall back to M15 or H1 bars between `opened_at` and `closed_at` to reconstruct approximate MAE/MFE
3. Flag whether the result is tick-accurate or bar-approximated

**Parameters:** `trade_id`, `fallback_timeframe` (default `"M15"`).

---

### A4 — `get_account_risk` (new tool)

**Purpose:** Return a derived risk summary from live account state.

**Why:** L4 — agents currently have to derive risk metrics manually from raw account data.

**Proposed response:**
```json
{
  "nav": 12450.00,
  "margin_used": 980.00,
  "margin_utilisation_pct": 7.87,
  "open_positions": 3,
  "gross_exposure_usd": 45000.00,
  "unrealised_pnl": -42.50,
  "daily_pnl": 120.00,
  "usd_net_exposure_pips": 45.2,
  "positions_by_instrument": {
    "XAU_USD": {"direction": "long", "units": 1, "unrealised_pnl": -22.50},
    "EUR_USD": {"direction": "long", "units": 10000, "unrealised_pnl": -20.00}
  }
}
```

---

### A5 — `warm_cache` (new tool)

**Purpose:** Force a candle cache warm for one instrument across all scan timeframes, regardless of market state.

**Why:** `scan_instrument` skips when market is closed and cache is cold. `refresh_snapshot` has the same gate. There is no way for an agent to pre-warm the cache for weekend review without triggering a full open-market scan. This tool would call the OANDA historical candles API directly (which works for closed markets) and populate the cache, then return freshness metadata.

**Parameters:** `instrument`, `timeframes` (default all scan timeframes).

---

### A6 — `list_fired_alerts` (new tool)

**Purpose:** Return the history of alerts that have fired, with timestamps and matched values.

**Why:** Currently agents can only see pending alerts. There is no way to query what fired, when, and at what price level. This is useful for auditing agent decision loops.

**Parameters:** `alert_type` (`"price"`, `"indicator"`, `"time"`, or `"all"`), `instrument` (optional), `limit`, `since` (ISO datetime).

---

### A7 — `bulk_clear_alerts` (new tool)

**Purpose:** Clear all alerts of a given type, or all alerts matching a tag.

**Why:** `seed_default_indicator_alerts` can create up to 96 alerts in one call. Clearing them requires 96 individual `clear_indicator_alert` calls. For agent workflows that need to reset alert state, this is impractical.

**Parameters:** `alert_type` (`"price"`, `"indicator"`, `"time"`, `"all"`), `instrument` (optional filter), `confirm: bool` (required `true` to prevent accidental mass delete).

---

### A8 — `get_spread_snapshot` (new tool)

**Purpose:** Return current and recent spread history for one or more instruments.

**Why:** Spread gating is a core part of the scan pipeline (spread gate blocks entries when spread is too wide), but there is no MCP tool that exposes current or historical spread data. An agent making a pre-trade decision should be able to check spread independently.

**Parameters:** `instrument`, `include_history: bool` (last N REST pricing polls).

---

### A9 — Add `force_refresh` parameter to all snapshot tools

**Affected tools:** `get_smc_snapshot`, `get_htf_bias`, `get_trade_plan`, `get_structure`, `get_indicators`, `get_order_blocks`, `get_sfp`, `get_turtle_soup`, `get_support_resistance`, `get_fibonacci`, `get_session_context`, `get_day_range`, `get_previous_day_levels`.

**Why:** All these tools accept `refresh_policy` but the `_resolve_snapshot` and `_resolve_bundle` helpers do not forward a `force=True` signal into the orchestrator (P5). Adding `force_refresh: bool = False` with proper forwarding would let agents break through the closed-market cache gate when they explicitly want a fresh fetch.

---

### A10 — Add `max_age_seconds` parameter to snapshot tools

**Affected tools:** Same as A9.

**Why:** L3 — freshness is advisory. Adding `max_age_seconds: int | None = None` would let the tool raise `DATA_STALE` (structured error per P11 fix) if the published bundle is older than the requested threshold. This lets agents enforce freshness without manually checking `bundle_age_seconds` on every call.

---

### A11 — Add per-instrument breakdown to `scan_all` response

**Why:** P2 — the current `ScanCycleStatus` collapses all skip reasons into one field. Extend `ScanCycleStatus` with:

```python
per_instrument: dict[str, Literal["scanned", "skipped", "error"]]
per_instrument_reason: dict[str, str | None]
```

This is a model-level change but requires no new tool — just a richer response from the existing `scan_all`.

---

### A12 - Superseded: add `hint` to empty candle responses

**Why:** Superseded by the P4 fix. Raw MCP candle reads now fetch directly from
OANDA by default, so a cold analysis cache no longer produces a successful empty
raw candle response. The old cache-empty hint is no longer needed for
`get_candles` or `get_ohlc(price_component="mid")`.

Old proposed response:
```json
{
  "returned_count": 0,
  "bars": [],
  "hint": "Cache is empty for this instrument/timeframe. Retry with force=true to fetch from OANDA directly."
}
```

This is a low-effort code change in `_mid_ohlc_payload` with high agent-usability payoff.

---

### A13 — Structured error envelope

**Why:** P11 — replace bare `ValueError` raises with a structured error response. FastMCP surfaces tool exceptions as error content in the MCP response. Define a small error schema:

```python
class McpToolError(Exception):
    def __init__(self, code: str, message: str, **detail):
        self.code = code
        self.message = message
        self.detail = detail
```

Return codes: `DATA_UNAVAILABLE`, `MARKET_CLOSED`, `INSTRUMENT_UNKNOWN`, `ALERT_NOT_FOUND`, `VALIDATION_ERROR`, `DATA_STALE`.

Agents can then match on `code` rather than parsing error strings.

---

### A14 — Remove `get_historical_bars` from tool surface

**Why:** P3 — it is a dead alias of `get_candles` with no functional difference. Removing it from `TOOL_SPECS` reduces the tool surface by one and eliminates the confusion. The capabilities resource and tool-surface resource should be updated to reflect the removal.

---

### A15 — Session names for `create_time_alert`

**Why:** P8 — extend `_SUPPORTED_TIME_ALERT_SESSIONS` to include `"sydney"` and `"tokyo"`, adding the corresponding `next_session_fire_at` logic in `alerts/time_alert_engine.py`. Both sessions have well-defined open times (Sydney ~22:00 UTC, Tokyo ~00:00 UTC).

---

## Summary Table

| ID | Type | Severity | Effort | Impact |
|---|---|---|---|---|
| P1 | Bug | High | High | Closed-trade MAE/MFE always empty |
| P2 | Bug | High | Low | Scan skips instruments silently |
| P3 | Bug | Medium | Trivial | Tool surface bloat |
| P4 | Bug | Medium | Low | Empty candle responses with no recovery hint |
| P5 | Bug | Medium | Low | refresh_policy="always" doesn't force-refresh when market closed |
| P6 | Bug | Medium | Medium | All agents share one alert namespace |
| P7 | Bug | Medium | Low | get_mae_mfe summary excludes closed trades |
| P8 | Bug | Low | Trivial | Missing Sydney/Tokyo session time alerts |
| P9 | Hygiene | Low | Low | API key in query string |
| P10 | Gap | Low | Low | No date-range filter on trade history |
| P11 | Gap | Low | Medium | Unstructured error strings |
| L1 | Constraint | — | — | No execution surface (by design) |
| L2 | Constraint | — | — | No push/wake-up mechanism |
| L3 | Constraint | — | Low | Freshness is advisory only |
| L4 | Constraint | — | Medium | No risk calculation surface |
| L5 | Constraint | — | Medium | No atomic multi-instrument snapshot |
| L6 | Constraint | — | Low | prefer_live has no hard-fail mode |
| L7 | Constraint | — | — | force=True on scan_all doesn't help closed markets |
| A1 | Addition | — | Low | get_scan_health |
| A2 | Addition | — | Medium | get_candle_range (time-based) |
| A3 | Addition | — | High | get_excursion_summary with bar fallback |
| A4 | Addition | — | Low | get_account_risk |
| A5 | Addition | — | Medium | warm_cache |
| A6 | Addition | — | Low | list_fired_alerts |
| A7 | Addition | — | Low | bulk_clear_alerts |
| A8 | Addition | — | Low | get_spread_snapshot |
| A9 | Addition | — | Low | force_refresh on all snapshot tools |
| A10 | Addition | — | Low | max_age_seconds on all snapshot tools |
| A11 | Addition | — | Low | per_instrument breakdown in scan_all |
| A12 | Addition | — | Trivial | hint field in empty candle responses |
| A13 | Addition | — | Medium | Structured error envelope |
| A14 | Addition | — | Trivial | Remove get_historical_bars alias |
| A15 | Addition | — | Low | Sydney/Tokyo session time alerts |
