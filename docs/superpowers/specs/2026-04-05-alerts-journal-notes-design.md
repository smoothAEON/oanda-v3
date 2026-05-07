# Design: RSI Auto-Seeding, Dated Time Alerts, Journal Notes via MCP

**Date:** 2026-04-05
**Branch:** MCP

---

## Overview

Three independent features:

1. Auto-seed RSI overbought/oversold indicator alerts for four instruments across M15–D timeframes on bot startup.
2. Extend time alerts to accept a specific date+time string (`"YYYY-MM-DD HH:MM"`) in SGT, in addition to the existing recurring `"HH:MM"` format.
3. Add an MCP tool `set_journal_note` so an LLM can write/replace/clear the `notes` field on a journal trade.

---

## Feature 1 — RSI M15+ Auto-Seeding on Startup

### What it does

On bot startup, idempotently seed RSI overbought/oversold indicator alerts for a fixed instrument+timeframe matrix, routed to `settings.telegram_chat_id`.

### Scope

| Instruments | Timeframes | Conditions |
|---|---|---|
| XAU_USD, EUR_USD, USD_JPY, USD_CHF | M15, H1, H4, D | RSI above 70, RSI below 30 |

32 alerts maximum (4 × 4 × 2). Any that already exist (matched by instrument + granularity + indicator + condition + threshold) are skipped.

### New file: `alerts/seeder.py`

```python
def seed_rsi_alerts(
    alert_repository: AlertRepository,
    chat_id: int,
    *,
    logger: logging.Logger | None = None,
) -> int:
    """Idempotently seed RSI M15+ alerts. Returns count of newly created alerts."""
```

Instruments, timeframes, and thresholds are defined as module-level constants in `seeder.py` so they are easy to adjust without touching startup code.

### Startup integration

Called once in `bot/bot.py` inside the existing `post_init` callback (or equivalent startup hook), after `build_runtime()` returns. Uses `runtime.alert_repository` and `runtime.settings.telegram_chat_id`. Logs `rsi_alerts_seeded` with `created=N, skipped=M`.

### Error handling

Any exception during seeding is caught and logged as a warning. A seeding failure must not prevent the bot from starting.

---

## Feature 2 — Dated Time Alerts in SGT

### What changes

`create_time_alert` currently accepts `local_time="HH:MM"` for recurring or one-off daily alerts. After this change, it also accepts `local_time="YYYY-MM-DD HH:MM"`, which creates a one-time alert that fires at that exact SGT datetime.

### Format detection

A datetime string is detected when `local_time` contains a space and the portion before the space matches `YYYY-MM-DD` (10 characters, two dashes). The existing `"HH:MM"` path is unchanged.

### New function in `alerts/time_alert_engine.py`

```python
def next_fixed_datetime_fire_at(
    local_datetime_text: str,
    *,
    timezone_name: str = DEFAULT_TIME_ALERT_TIMEZONE,
) -> datetime:
    """
    Parse "YYYY-MM-DD HH:MM" as a specific local datetime and return UTC.
    Raises ValueError if the datetime is in the past.
    """
```

### Changes in `mcp_server/adapters.py`

In the `kind="at"` branch of `create_time_alert`:
- Detect `"YYYY-MM-DD HH:MM"` format.
- Call `next_fixed_datetime_fire_at` instead of `next_fixed_time_fire_at`.
- Force `schedule="once"` regardless of what the caller passed.
- Store the full `"YYYY-MM-DD HH:MM"` string in `local_time` for display.

The `schedule` parameter is still accepted in the tool signature but ignored when a date is detected. The tool response includes the resolved `next_fire_at` in UTC so the caller can confirm the correct interpretation.

### Past-datetime behaviour

If the specified SGT datetime has already passed, `create_time_alert` raises `ValueError("Datetime is in the past — provide a future date and time.")`. No alert is created.

### Timezone

Always SGT (`Asia/Singapore`, UTC+8). No other timezone is supported for dated alerts.

---

## Feature 3 — MCP `set_journal_note`

### New MCP tool

**Name:** `set_journal_note`
**Description:** `"Set or clear the notes field on a journal trade. Passing null or empty string clears the note."`

### Method signature on `BotMcpService`

```python
async def set_journal_note(
    self,
    trade_id: str,
    notes: str | None = None,
) -> dict[str, Any]:
```

### Behaviour

- Calls `trade_repository.set_notes(trade_id, notes or None)` via `asyncio.to_thread`.
- If the trade does not exist, raises `ValueError(f"Trade {trade_id} not found.")`.
- Returns the updated `TradeRecord` as JSON.
- Passing `""` (empty string) is treated the same as `None` — clears the note.
- Replaces the entire note; no append logic.

### Registration

Add to `TOOL_SPECS` in `mcp_server/server.py`:
```python
{"name": "set_journal_note", "description": "Set or clear the notes field on a journal trade. Passing null or empty string clears the note."},
```

---

## Files Changed

| File | Change |
|---|---|
| `alerts/seeder.py` | New — RSI seeding function and constants |
| `alerts/time_alert_engine.py` | Add `next_fixed_datetime_fire_at` |
| `mcp_server/adapters.py` | Date detection in `create_time_alert`; add `set_journal_note` |
| `mcp_server/server.py` | Register `set_journal_note` in `TOOL_SPECS` |
| `bot/bot.py` | Call `seed_rsi_alerts` in post-init |

---

## Testing

- `tests/unit/test_seeder.py` — idempotency, correct instrument/timeframe matrix, skip on existing
- `tests/unit/test_time_alert_engine.py` — `next_fixed_datetime_fire_at` parses correctly, rejects past datetimes, correct UTC conversion from SGT
- `tests/unit/test_mcp_server.py` — `set_journal_note` replaces, clears, and raises on missing trade
