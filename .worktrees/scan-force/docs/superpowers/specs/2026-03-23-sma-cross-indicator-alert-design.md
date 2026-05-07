# SMA Cross Indicator Alert — Design Spec

> Archived design artifact from March 2026. Keep this file for lineage only; use [README](../../../README.md) and [tracker.md](../../tracker.md) for current behavior and status.

**Date:** 2026-03-23
**Status:** Approved
**Scope:** Add SMA golden cross and death cross as a new `IndicatorKind` for candle-triggered indicator alerts, covering timeframes M15 through D.

---

## Problem

The indicator alert system supports RSI, STOCH, and MACD. The SMA 50/200 golden cross (SMA50 crosses above SMA200) and death cross (SMA50 crosses below SMA200) are widely used signals that should be alertable on the same candle-triggered path.

---

## Approach: `SMA_CROSS` as a spread with zero baseline

`IndicatorKind.SMA_CROSS` is added as a new enum value. The engine represents the SMA cross signal as a single float: `sma50 - sma200`. This is positive in "golden territory" and negative in "death territory". A golden cross fires when the spread crosses from ≤ 0 to > 0 (`cross_up`); a death cross fires when it crosses from ≥ 0 to < 0 (`cross_down`). The zero baseline is identical to MACD's baseline — the `_is_triggered` logic is extended to include `SMA_CROSS` in the zero-baseline set.

`_resolve_values` for SMA_CROSS calls `_metric_value` on all four metric lookups and always returns `tuple[float, float]` or raises `RuntimeError`. It never returns `(float, None)`.

---

## File Map

| File | Change |
|---|---|
| `indicators/talib_wrappers.py` | Add `sma_50`, `sma_200` metrics |
| `core/enums.py` | Add `SMA_CROSS = "SMA_CROSS"` to `IndicatorKind` |
| `alerts/defaults.py` | Add `(SMA_CROSS, "cross_up"): None`, `(SMA_CROSS, "cross_down"): None` |
| `alerts/indicator_alert_engine.py` | Add `SMA_CROSS` branch in `_resolve_values`; expand zero-baseline set in `_is_triggered` |
| `bot/parsing.py` | Update error message to mention `SMA_CROSS` |
| `bot/bot.py` | Add SMA_CROSS defaults in `_create_default_indicator_alerts`; update `/help` text; update confirmation reply |
| `tests/unit/test_indicator_alert_defaults.py` | Tests for SMA_CROSS entries; update combo count |
| `tests/unit/test_indicator_alert_engine.py` | Tests for SMA_CROSS `_resolve_values` and triggering |
| `tests/unit/test_talib_wrappers.py` | Verify `sma_50`, `sma_200` in metrics output |
| `CLAUDE.md` | No structural change needed — `IndicatorKind` is listed by class name only; the enum value is self-documenting in `core/enums.py` |

---

## Data Layer

### `indicators/talib_wrappers.py`

Add two metrics to `SUPPORTED_TALIB_WRAPPERS`:

```python
"sma_50",
"sma_200",
```

**Both `SUPPORTED_TALIB_WRAPPERS` and `metric_values` must be updated atomically.** `build_talib_metrics` iterates over `SUPPORTED_TALIB_WRAPPERS` and does `metric_values[name]` — adding to one without the other causes a `KeyError` on every call.

Add to the `metric_values` dict in `build_talib_metrics`:

```python
"sma_50": _coerce_scalar(talib.SMA(close, timeperiod=50)),
"sma_200": _coerce_scalar(talib.SMA(close, timeperiod=200)),
```

The existing `sma` metric (default TA-Lib period 30) is unchanged. Both new metrics return `None` when fewer candles than the period are available — `_coerce_scalar` already handles `NaN` → `None`.

---

## Enum + Defaults

### `core/enums.py`

```python
class IndicatorKind(StrEnum):
    RSI = "RSI"
    STOCH = "STOCH"
    MACD = "MACD"
    SMA_CROSS = "SMA_CROSS"
```

### `alerts/defaults.py`

Two new entries added (only `cross_up` and `cross_down` — no `above`/`below`):

```python
(IndicatorKind.SMA_CROSS, "cross_up"): None,   # golden cross
(IndicatorKind.SMA_CROSS, "cross_down"): None,  # death cross
```

`get_default_threshold(SMA_CROSS, "above")` raises `KeyError` as intended — `above`/`below` are not advertised combinations for SMA_CROSS. The combo count increases from 12 to 14.

---

## Alert Engine

### `alerts/indicator_alert_engine.py`

**`_resolve_values` — new branch:**

The SMA_CROSS branch must be inserted as an explicit `if` block **before** the MACD fallthrough code at the end of `_resolve_values`. The existing MACD logic has no `if alert.indicator == IndicatorKind.MACD:` guard — it is the implicit fallthrough after the RSI and STOCH branches. Inserting SMA_CROSS after the MACD fallthrough would cause MACD logic to execute for SMA_CROSS alerts.

```python
if alert.indicator == IndicatorKind.SMA_CROSS:
    sma_50 = self._metric_value(current_metrics, "sma_50")
    sma_200 = self._metric_value(current_metrics, "sma_200")
    prev_sma_50 = self._metric_value(previous_metrics, "sma_50")
    prev_sma_200 = self._metric_value(previous_metrics, "sma_200")
    return (sma_50 - sma_200, prev_sma_50 - prev_sma_200)

# MACD fallthrough follows here (unchanged)
```

All four `_metric_value` calls raise `RuntimeError` if the metric is `None` — this happens when fewer than 200 candles are available. The `RuntimeError` exits the entire `evaluate_for_snapshot` call (not just that one alert) — remaining alerts for the same instrument/timeframe are skipped in that cycle. The orchestrator's try/except catches it, logs a warning, and continues. This is the same behaviour as the other `IndicatorKind` values — no per-alert isolation exists in the engine today.

**`_is_triggered` — baseline expansion:**

```python
# Before:
baseline = 0.0 if alert.indicator == IndicatorKind.MACD else 50.0
# After:
baseline = 0.0 if alert.indicator in (IndicatorKind.MACD, IndicatorKind.SMA_CROSS) else 50.0
```

No other changes to `_is_triggered`. The `previous_value is None` baseline-substitution path in `_is_triggered` is unreachable for SMA_CROSS at runtime — enforced by `_resolve_values` raising before it can return `None`. The baseline expansion is still required for correctness if `_resolve_values` is ever called from a different path.

---

## Bot Layer

### `bot/parsing.py`

Error message update only:

```python
# Before:
raise ValueError("indicator must be RSI, STOCH, or MACD.") from exc
# After:
raise ValueError("indicator must be RSI, STOCH, MACD, or SMA_CROSS.") from exc
```

### `bot/bot.py`

Three changes:

1. **`_create_default_indicator_alerts`** — SMA_CROSS defaults added for M15, H1, H4, D per instrument. The existing RSI/STOCH defaults remain H1-only and are not changed.

```python
sma_cross_timeframes = ("M15", "H1", "H4", "D")
sma_cross_defaults = [
    (IndicatorKind.SMA_CROSS, "cross_up", None, "SMA golden cross"),
    (IndicatorKind.SMA_CROSS, "cross_down", None, "SMA death cross"),
]
for instrument in SCAN_INSTRUMENTS:
    for tf in sma_cross_timeframes:
        for indicator, condition, threshold, note in sma_cross_defaults:
            # upsert as before
```

2. **Confirmation reply** — updated to mention SMA cross alerts across M15/H1/H4/D alongside RSI/STOCH H1 defaults:

```
Created N default indicator alerts.
RSI 70/30, STOCH 80/20 on H1 per instrument.
SMA golden cross + death cross on M15/H1/H4/D per instrument.
Use /listindicators to view.
```

3. **`/help` text** — the `/indicatoralert` usage line is updated:

```
# Before:
/indicatoralert <symbol> <tf> <RSI|STOCH|MACD> <cond> [threshold] [note]
# After:
/indicatoralert <symbol> <tf> <RSI|STOCH|MACD|SMA_CROSS> <cond> [threshold] [note]
```

---

## Testing

### `tests/unit/test_indicator_alert_defaults.py`

- `test_sma_cross_cross_up_default` → `None`
- `test_sma_cross_cross_down_default` → `None`
- `test_sma_cross_above_raises_key_error` → `KeyError`
- `test_all_14_combinations_present` — set `expected_count = 14` (literal); update the formula comment from `# 3 * 4` to `# 12 RSI/STOCH/MACD + 2 SMA_CROSS`

### `tests/unit/test_indicator_alert_engine.py`

- `test_sma_cross_golden_cross_fires` — spread goes from negative to positive → fires
- `test_sma_cross_death_cross_fires` — spread goes from positive to negative → fires
- `test_sma_cross_no_cross_does_not_fire` — spread stays positive → no fire
- `test_sma_cross_raises_when_sma_200_unavailable` — `sma_200=None` in **current** metrics, `sma_50` is valid in current metrics → `RuntimeError` (isolates the `self._metric_value(current_metrics, "sma_200")` call)
- `test_sma_cross_raises_when_previous_sma_200_unavailable` — current metrics are fully valid; in previous metrics `sma_50` is a valid float but `sma_200=None` → `RuntimeError` (isolates the `self._metric_value(previous_metrics, "sma_200")` call)
- `test_sma_cross_evaluate_for_snapshot_raises_when_metric_unavailable` — call `evaluate_for_snapshot` with a stub `indicator_builder` that returns `sma_200=None` in current metrics; assert `RuntimeError` propagates out of the call (verifies the engine does not swallow the error internally)

Note: `above`/`below` conditions are not tested explicitly — they fall through to the generic `_is_triggered` path which is already covered by RSI/STOCH tests.

### `tests/unit/test_talib_wrappers.py`

- Verify `sma_50` and `sma_200` appear in the tuple returned by `build_talib_metrics`
- Verify values are finite floats when sufficient candles are provided

---

## Constraints

- SMA_CROSS `_resolve_values` requires ≥ 200 candles to produce non-None metrics for both `sma_50` and `sma_200`. The default `DEFAULT_CANDLE_COUNT` is 500, so this is satisfied in production. In tests, stub candle frames shorter than 200 bars will produce `None` metrics and raise `RuntimeError` — the `RuntimeError` exits the entire `evaluate_for_snapshot` call, skipping any remaining alerts for that instrument/timeframe in that cycle. The orchestrator catches this, logs a warning, and continues.
- **First-fire behaviour:** `_metric_value` always raises `RuntimeError` if a metric is `None`, so `previous_value` is never `None` in practice — it is always a real float computed from the previous candle slice (which has 499 candles in production, well above the 200-candle minimum for both SMA metrics). The baseline-substitution path in `_is_triggered` is unreachable for SMA_CROSS as a runtime invariant enforced by `_resolve_values`. A newly created SMA_CROSS alert does NOT fire on first evaluation unless an actual cross occurred on the most recent closed candle.
- No timeframe gating in engine code. The `/indicatoralert defaults` command creates alerts for M15, H1, H4, D only. Manual `/indicatoralert` allows any timeframe; behaviour on M1/M5 is valid but potentially noisy.
- `above`/`below` conditions work on the spread if a user explicitly specifies them (e.g. `/indicatoralert XAU_USD H1 SMA_CROSS above 0` = "alert when in golden territory"), but are not in the defaults table and not advertised.
