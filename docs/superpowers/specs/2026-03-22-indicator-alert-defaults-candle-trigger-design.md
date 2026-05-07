# Indicator Alert Defaults + Candle-Triggered Evaluation

> Archived design artifact from March 2026. Keep this file for lineage only; use [README](../../../README.md) and [tracker.md](../../tracker.md) for current behavior and status.

**Date:** 2026-03-22
**Status:** Approved

---

## Problem

1. Creating an indicator alert requires the user to manually supply a threshold value even when the sensible default is well-known (RSI overbought = 70, oversold = 30, etc.).
2. The `IndicatorAlertEngine` runs on a fixed 5-minute APScheduler interval, re-evaluating every alert regardless of whether a new candle has actually closed. This wastes fetch cycles and can fire on the same candle multiple times.

---

## Goals

- Provide sensible default thresholds for every `(IndicatorKind, condition)` combination, applied when a user omits the threshold at alert-creation time.
- Evaluate indicator alerts only when a new closed candle is available, integrated into the existing scan path (M15-D).
- Eliminate redundant candle fetches by reusing data already computed by `ScanOrchestrator`.

---

## Out of Scope

- Pre-seeded alert records in TinyDB (no automatic alerts created at startup).
- Timeframes below M15 (M1, M5, M30 are not in `SCAN_TIMEFRAMES`).
- Changes to the `IndicatorAlert` model or TinyDB schema.
- Trade execution, planning, grading, or confidence scoring.

---

## Design

### 1. Default Thresholds — `alerts/defaults.py`

A new module with a single lookup table mapping `(IndicatorKind, condition)` to a default threshold value.

```text
INDICATOR_ALERT_DEFAULTS: dict[tuple[IndicatorKind, str], float | None]

(RSI,   "above")      -> 70.0
(RSI,   "below")      -> 30.0
(RSI,   "cross_up")   -> None
(RSI,   "cross_down") -> None
(STOCH, "above")      -> 80.0
(STOCH, "below")      -> 20.0
(STOCH, "cross_up")   -> None
(STOCH, "cross_down") -> None
(MACD,  "above")      -> 0.0
(MACD,  "below")      -> 0.0
(MACD,  "cross_up")   -> None
(MACD,  "cross_down") -> None
```

A helper `get_default_threshold(kind: IndicatorKind, condition: str) -> float | None` raises `KeyError` for unknown combinations. All 12 valid `(IndicatorKind, condition)` combinations are present in the table, so `KeyError` guards against future additions of a new `IndicatorKind` without updating the table — it is not a normal usage path.

**Usage contract:** When `get_default_threshold()` returns `None` (cross conditions), the bot command layer must omit the `threshold` field entirely when constructing `IndicatorAlert` — it must not pass `threshold=None`, because the model's `validate_contract` validator prohibits `threshold` from being set on cross-condition alerts. When it returns a `float`, that float is passed as `threshold=value`.

**Cross-condition baseline:** Cross conditions (`cross_up`, `cross_down`) use a hard-coded baseline inside the existing `_is_triggered()` method (50.0 for RSI and STOCH; 0.0 for MACD). This is existing engine behaviour — `alerts/defaults.py` does not control the baseline, only the model-level `threshold` field.

`alerts/defaults.py` is imported by module path (`from alerts.defaults import ...`). No re-export from `alerts/__init__.py` is needed.

Applies to M15-D timeframes only (enforced at the call site, not in this module).

---

### 2. Candle-Triggered Evaluation

#### 2a. `IndicatorAlertEngine` changes

**`market_data_provider` parameter is retained.** The `IndicatorAlertEngine.__init__` signature is unchanged — `market_data_provider: MarketDataProvider` remains as the second positional argument and `self.market_data_provider` remains set. The existing `evaluate_pending_alerts()` method still calls `self.market_data_provider.get_candles(...)` internally. Do not remove this parameter or attribute.

**`__init__` addition:** Add `self._last_candle_seen: dict[tuple[str, str], datetime] = {}` — no new constructor parameter required.

**New method:**

```python
def evaluate_for_snapshot(
    self,
    instrument: str,
    granularity: str,
    candles: pd.DataFrame,
    current_summary: IndicatorValueSummary,
) -> list[IndicatorAlert]:
```

**Behaviour:**

1. Guard: if `len(candles) < 2`, return `[]` immediately.
2. Read `last_candle: datetime = candles["time"].iloc[-1].to_pydatetime()`. Call `.to_pydatetime()` explicitly so the dict stores a `datetime`, not a `pd.Timestamp`, and equality comparison is reliable across pandas versions.
3. Compare against `self._last_candle_seen.get((instrument, granularity))`. If equal to `last_candle`, return `[]`.
4. Update `self._last_candle_seen[(instrument, granularity)] = last_candle`. The cursor advances unconditionally here — even if no alerts match in step 5. This is intentional: subsequent calls for the same candle will skip the `list_active_indicator_alerts()` query and filter entirely.
5. Filter `list_active_indicator_alerts()` to alerts where `alert.instrument == instrument` and `alert.granularity == granularity`. If the filtered list is empty, return `[]` without building `previous_summary`.
6. Skip FIRED non-repeat alerts and FIRED repeat alerts still in cooloff (same logic as `evaluate_pending_alerts()`). If all surviving alerts are skipped, return `[]`.
7. Build `previous_summary` **lazily** — only after at least one alert survives steps 5 and 6: call `self.indicator_builder(candles.iloc[:-1].reset_index(drop=True), granularity)`. The second argument is `granularity` (the method parameter), matching the existing pattern in `evaluate_pending_alerts()`.
8. Use `_resolve_values()` and `_is_triggered()` (unchanged internal methods).
9. Fire matching alerts via `mark_indicator_alert_fired()` and `_dispatch_notification()`.
10. Return list of fired alerts.

**Note on `_last_candle_seen` after restart:** The in-memory cache resets on process restart. On the first call after startup, `_last_candle_seen` is empty for all pairs, so every (instrument, granularity) evaluates once — even if the candle is hours old. A pre-existing triggered condition will fire on that first evaluation. This is acceptable given the existing cooloff and status guards.

**Note on errors:** Exceptions inside `evaluate_for_snapshot()` propagate to the caller (`_build_snapshot()`), which catches them (see 2b below).

The existing `evaluate_pending_alerts()` method is **preserved unchanged** so existing tests continue to pass.

#### 2b. `ScanOrchestrator` integration

`ScanOrchestrator.__init__` gains one new optional dependency:

```python
indicator_alert_engine: IndicatorAlertEngine | None = None
```

`_build_snapshot()` calls the engine after `indicators` is built, using the method's local `timeframe` variable. The call is inserted as a plain `if` block — it is intentionally **not** wrapped in `_timed_detector()`, unlike the other analysis steps in that method. Failures are caught and logged so a failing alert evaluation never aborts the snapshot build:

```python
if self.indicator_alert_engine is not None:
    try:
        self.indicator_alert_engine.evaluate_for_snapshot(
            instrument, timeframe, candles, indicators
        )
    except Exception as exc:
        self.logger.warning(
            "indicator_alert_eval_failed",
            instrument=instrument,
            timeframe=timeframe,
            error=str(exc),
        )
```

Note: the `evaluate_for_snapshot()` parameter is named `granularity`; `timeframe` is the local variable in `_build_snapshot()`. They refer to the same string value.

**`refresh_snapshot()` behaviour:** `refresh_snapshot()` also calls `_build_snapshot()`, so alert evaluation fires on ad-hoc single-timeframe refreshes too. This is intentional.

**`bot/runtime.py` wiring:** `build_runtime()` must pass the existing `indicator_alert_engine` into `ScanOrchestrator(...)`:

```python
scan_orchestrator = ScanOrchestrator(
    ...
    indicator_alert_engine=indicator_alert_engine,
)
```

Without this, the production path silently uses `indicator_alert_engine=None` and alert evaluation never fires at runtime.

**`bot/runtime.py` `bot_data()` method:** The `bot_data()` method does not include `indicator_scan_task` in its returned dict, so no changes are needed there beyond removing the dataclass field.

#### 2c. Removals

**`background/indicator_scan_task.py`** — delete the entire file.

**`orchestration/scheduler.py`:**

- Remove `from background.indicator_scan_task import IndicatorScanTask` import (line 14)
- Remove `INDICATOR_SCAN_JOB_ID` constant
- Remove `INDICATOR_SCAN_JOB_ID` from `__all__`
- Remove `indicator_scan_task: IndicatorScanTask | None = None` parameter from `SchedulerService.__init__`
- Remove `self.indicator_scan_task = indicator_scan_task` assignment
- Remove the `if self.indicator_scan_task is not None:` block in `_register_jobs()`

**`background/task_supervisor.py`:**

- Remove `from background.indicator_scan_task import IndicatorScanTask` import
- Remove `indicator_scan_task` constructor parameter
- Remove `.status()` call for it in `health_snapshot()`

**`bot/runtime.py`** — five source edits:

- Remove `from background.indicator_scan_task import IndicatorScanTask` import (line 11)
- Remove `indicator_scan_task: IndicatorScanTask` field from the `BotRuntime` dataclass (line 72)
- Remove `indicator_scan_task = IndicatorScanTask(indicator_alert_engine)` construction in `build_runtime()` (line 176)
- Remove `indicator_scan_task=indicator_scan_task` keyword argument from `TaskSupervisor(...)` call (line 192)
- Remove `indicator_scan_task=indicator_scan_task` keyword argument from `SchedulerService(...)` call (line 199)

**`config/settings.py`:**

- Remove `indicator_scan_interval_minutes` model field
- Remove `INDICATOR_SCAN_INTERVAL_MINUTES` from `RUNTIME_DEFAULTS` (the key disappears transitively from `ALL_SETTING_KEYS` and `STARTUP_ONLY_KEYS` via computed sets — no literal edit to those sets is needed)

**`CLAUDE.md`:**

- Remove `INDICATOR_SCAN_INTERVAL_MINUTES` (5) from the "Runtime settings" environment variable table

**`tests/unit/test_import_smoke.py`:**

- Remove `"background.indicator_scan_task"` from the reserved-module list
- Add `"alerts.defaults"` to the reserved-module list (consistent with existing `alerts.*` entries)

The standalone `evaluate_pending_alerts()` path remains for tests and potential future CLI use.

---

## Data Flow (after change)

```text
APScheduler (5 min) -> ScanOrchestrator.scan_all()
  -> for each instrument in SCAN_INSTRUMENTS:
       -> for each timeframe in SCAN_TIMEFRAMES (M15, H1, H4, D):
            -> get_candles()                      [existing]
            -> build_indicator_summary()          [existing]
            -> evaluate_for_snapshot()            [NEW - no-op if same candle]
            -> publish_snapshot()                 [existing]

Bot command -> ScanOrchestrator.refresh_snapshot(instrument, timeframe)
  -> get_candles()
  -> build_indicator_summary()
  -> evaluate_for_snapshot()                      [NEW - same path]
  -> publish_snapshot()
```

---

## Files Changed

| File | Change |
| --- | --- |
| `alerts/defaults.py` | New - default threshold lookup table + `get_default_threshold()` helper |
| `alerts/indicator_alert_engine.py` | Add `_last_candle_seen` to `__init__`; add `evaluate_for_snapshot()`; retain `market_data_provider` |
| `orchestration/scan_orchestrator.py` | Add `indicator_alert_engine` dependency; call engine in `_build_snapshot()` |
| `orchestration/scheduler.py` | Remove `INDICATOR_SCAN_JOB_ID` (constant + `__all__`); remove `indicator_scan_task` wiring |
| `background/indicator_scan_task.py` | Deleted |
| `background/task_supervisor.py` | Remove `IndicatorScanTask` import, constructor param, `health_snapshot()` call |
| `bot/runtime.py` | Five source edits listed in section 2c; add `indicator_alert_engine` to `ScanOrchestrator(...)` call |
| `config/settings.py` | Remove `indicator_scan_interval_minutes` field and `INDICATOR_SCAN_INTERVAL_MINUTES` from `RUNTIME_DEFAULTS` |
| `CLAUDE.md` | Remove `INDICATOR_SCAN_INTERVAL_MINUTES` from Runtime settings table |
| `tests/unit/test_import_smoke.py` | Remove `"background.indicator_scan_task"`; add `"alerts.defaults"` |
| `tests/unit/test_indicator_alert_defaults.py` | New - covers all 12 pairs + `KeyError` for unknown |
| `tests/unit/test_indicator_alert_engine.py` | Add `evaluate_for_snapshot()` tests (see below) |
| `tests/integration/test_scan_orchestrator.py` | Extend with engine integration tests (see below) |

---

## Testing Strategy

**Unit - `alerts/defaults.py`:**

- All 12 `(kind, condition)` pairs return expected values.
- Unknown pair raises `KeyError`.

**Unit - `evaluate_for_snapshot()`:**

- Returns `[]` when called twice with the same candle timestamp (same-candle dedup).
- Fires the correct alert on a new candle (threshold condition - above/below).
- Fires the correct alert on a new candle (cross_up/cross_down condition, verifying `previous_summary` is built and used).
- Returns `[]` when `len(candles) < 2`.
- Returns `[]` without building `previous_summary` when no alerts match instrument+granularity.
- Respects cooloff for repeat alerts.
- Skips alerts with non-matching instrument or granularity.
- On startup (empty `_last_candle_seen`): a pre-existing triggered condition fires on the first call.

**Integration - `tests/integration/test_scan_orchestrator.py` (appended to existing file):**

- `evaluate_for_snapshot()` is called with the computed `candles` and `indicators` when `indicator_alert_engine` is set.
- `evaluate_for_snapshot()` is not called when `indicator_alert_engine` is `None`.
- An exception raised inside `evaluate_for_snapshot()` is caught; the snapshot is still published and an `indicator_alert_eval_failed` warning is logged.

**Existing tests:** Zero changes required - `evaluate_pending_alerts()` is untouched.
