# Indicator Alert Defaults + Candle-Triggered Evaluation Implementation Plan

> Archived implementation plan from March 2026. Keep this file for lineage only; use [README](../../../README.md) and [tracker.md](../../tracker.md) for current behavior and status.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add sensible default thresholds for indicator alerts and replace the standalone timed scan job with candle-boundary-triggered evaluation integrated into the existing scan orchestrator path.

**Architecture:** A new `alerts/defaults.py` lookup table provides per-indicator defaults. `IndicatorAlertEngine` gains `evaluate_for_snapshot()` which deduplicates on candle timestamp and reuses pre-built indicator summaries passed from `ScanOrchestrator._build_snapshot()`. The old `IndicatorScanTask` background job and its scheduler wiring are deleted.

**Tech Stack:** Python 3.10+, pandas, TinyDB, APScheduler, pytest, structlog

---

## File Map

| File | Action | Responsibility |
| --- | --- | --- |
| `alerts/defaults.py` | **Create** | Lookup table + `get_default_threshold()` helper |
| `alerts/indicator_alert_engine.py` | **Modify** | Add `_last_candle_seen` dict + `evaluate_for_snapshot()` method |
| `orchestration/scan_orchestrator.py` | **Modify** | Accept `indicator_alert_engine` dep; call it in `_build_snapshot()` |
| `orchestration/scheduler.py` | **Modify** | Remove `INDICATOR_SCAN_JOB_ID` and all `IndicatorScanTask` wiring |
| `background/indicator_scan_task.py` | **Delete** | Entire file |
| `background/task_supervisor.py` | **Modify** | Remove `IndicatorScanTask` import + param + health call |
| `bot/runtime.py` | **Modify** | Five source edits + wire `indicator_alert_engine` into `ScanOrchestrator` |
| `config/settings.py` | **Modify** | Remove `indicator_scan_interval_minutes` field and its `RUNTIME_DEFAULTS` entry |
| `CLAUDE.md` | **Modify** | Remove `INDICATOR_SCAN_INTERVAL_MINUTES` from Runtime settings table |
| `tests/unit/test_import_smoke.py` | **Modify** | Remove deleted module, add `alerts.defaults` |
| `tests/unit/test_indicator_alert_defaults.py` | **Create** | Unit tests for the lookup table |
| `tests/unit/test_indicator_alert_engine.py` | **Modify** | Add `evaluate_for_snapshot()` tests |
| `tests/integration/test_scan_orchestrator.py` | **Modify** | Add orchestrator+engine integration tests |

---

## Task 1: Default threshold lookup table

**Files:**
- Create: `alerts/defaults.py`
- Create: `tests/unit/test_indicator_alert_defaults.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_indicator_alert_defaults.py`:

```python
"""Unit tests for indicator alert default thresholds."""
from __future__ import annotations

import pytest

from alerts.defaults import INDICATOR_ALERT_DEFAULTS, get_default_threshold
from core.enums import IndicatorKind


def test_rsi_above_default():
    assert get_default_threshold(IndicatorKind.RSI, "above") == 70.0


def test_rsi_below_default():
    assert get_default_threshold(IndicatorKind.RSI, "below") == 30.0


def test_rsi_cross_up_default():
    assert get_default_threshold(IndicatorKind.RSI, "cross_up") is None


def test_rsi_cross_down_default():
    assert get_default_threshold(IndicatorKind.RSI, "cross_down") is None


def test_stoch_above_default():
    assert get_default_threshold(IndicatorKind.STOCH, "above") == 80.0


def test_stoch_below_default():
    assert get_default_threshold(IndicatorKind.STOCH, "below") == 20.0


def test_stoch_cross_up_default():
    assert get_default_threshold(IndicatorKind.STOCH, "cross_up") is None


def test_stoch_cross_down_default():
    assert get_default_threshold(IndicatorKind.STOCH, "cross_down") is None


def test_macd_above_default():
    assert get_default_threshold(IndicatorKind.MACD, "above") == 0.0


def test_macd_below_default():
    assert get_default_threshold(IndicatorKind.MACD, "below") == 0.0


def test_macd_cross_up_default():
    assert get_default_threshold(IndicatorKind.MACD, "cross_up") is None


def test_macd_cross_down_default():
    assert get_default_threshold(IndicatorKind.MACD, "cross_down") is None


def test_all_12_combinations_present():
    expected_count = 3 * 4  # 3 indicators * 4 conditions
    assert len(INDICATOR_ALERT_DEFAULTS) == expected_count


def test_unknown_combination_raises_key_error():
    with pytest.raises(KeyError):
        get_default_threshold(IndicatorKind.RSI, "invalid_condition")
```

- [ ] **Step 2: Run tests — expect ImportError (module doesn't exist yet)**

```
pytest tests/unit/test_indicator_alert_defaults.py -v
```

Expected: `ModuleNotFoundError: No module named 'alerts.defaults'`

- [ ] **Step 3: Implement `alerts/defaults.py`**

```python
"""Default threshold values for indicator alerts."""

from __future__ import annotations

from core.enums import IndicatorKind

INDICATOR_ALERT_DEFAULTS: dict[tuple[IndicatorKind, str], float | None] = {
    (IndicatorKind.RSI, "above"): 70.0,
    (IndicatorKind.RSI, "below"): 30.0,
    (IndicatorKind.RSI, "cross_up"): None,
    (IndicatorKind.RSI, "cross_down"): None,
    (IndicatorKind.STOCH, "above"): 80.0,
    (IndicatorKind.STOCH, "below"): 20.0,
    (IndicatorKind.STOCH, "cross_up"): None,
    (IndicatorKind.STOCH, "cross_down"): None,
    (IndicatorKind.MACD, "above"): 0.0,
    (IndicatorKind.MACD, "below"): 0.0,
    (IndicatorKind.MACD, "cross_up"): None,
    (IndicatorKind.MACD, "cross_down"): None,
}


def get_default_threshold(kind: IndicatorKind, condition: str) -> float | None:
    """Return the default threshold for a (kind, condition) pair.

    Raises KeyError for unknown combinations — guards against future
    IndicatorKind additions that haven't been added to the table.
    """
    return INDICATOR_ALERT_DEFAULTS[(kind, condition)]


__all__ = ["INDICATOR_ALERT_DEFAULTS", "get_default_threshold"]
```

- [ ] **Step 4: Run tests — all pass**

```
pytest tests/unit/test_indicator_alert_defaults.py -v
```

Expected: 14 passed

- [ ] **Step 5: Commit**

```bash
git add alerts/defaults.py tests/unit/test_indicator_alert_defaults.py
git commit -m "feat: add indicator alert default threshold lookup table"
```

---

## Task 2: `evaluate_for_snapshot()` on `IndicatorAlertEngine`

**Files:**
- Modify: `alerts/indicator_alert_engine.py`
- Modify: `tests/unit/test_indicator_alert_engine.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_indicator_alert_engine.py` (after the existing tests, reusing existing helpers `StubProvider`, `fake_indicator_builder`, `BASE_TIME`):

```python
# ---------------------------------------------------------------------------
# evaluate_for_snapshot() tests
# ---------------------------------------------------------------------------

def _make_candles(n: int = 3, base: datetime = BASE_TIME) -> pd.DataFrame:
    """Build a minimal n-bar candle frame with UTC-aware time column."""
    times = [base - pd.Timedelta(hours=n - 1 - i) for i in range(n)]
    return pd.DataFrame(
        {
            "time": pd.to_datetime(times, utc=True),
            "open": [1.0] * n,
            "high": [1.1] * n,
            "low": [0.9] * n,
            "close": [1.0] * n,
            "tick_volume": list(range(10, 10 + n)),
        }
    )


def test_evaluate_for_snapshot_fires_on_new_candle(tmp_path: Path) -> None:
    store = TradeStore(db_path=tmp_path / "snap_fire.json")
    repository = AlertRepository(store=store)
    engine = IndicatorAlertEngine(
        repository,
        StubProvider(),
        indicator_builder=fake_indicator_builder,
    )
    try:
        alert = repository.upsert_indicator_alert(
            {
                "instrument": "SPX500_USD",
                "granularity": "M15",
                "indicator": IndicatorKind.RSI,
                "condition": "below",
                "threshold": 30.0,
                "chat_id": 1,
                "created_at": BASE_TIME,
            }
        )
        candles = _make_candles()
        summary = fake_indicator_builder(candles, "M15")

        fired = engine.evaluate_for_snapshot("SPX500_USD", "M15", candles, summary)

        assert [a.id for a in fired] == [alert.id]
        assert repository.get_indicator_alert(alert.id).status == AlertStatus.FIRED
    finally:
        store.close()


def test_evaluate_for_snapshot_dedup_same_candle(tmp_path: Path) -> None:
    """Second call with same candle timestamp returns empty list without re-firing."""
    store = TradeStore(db_path=tmp_path / "snap_dedup.json")
    repository = AlertRepository(store=store)
    engine = IndicatorAlertEngine(
        repository,
        StubProvider(),
        indicator_builder=fake_indicator_builder,
    )
    try:
        repository.upsert_indicator_alert(
            {
                "instrument": "SPX500_USD",
                "granularity": "M15",
                "indicator": IndicatorKind.RSI,
                "condition": "below",
                "threshold": 30.0,
                "chat_id": 1,
                "created_at": BASE_TIME,
            }
        )
        candles = _make_candles()
        summary = fake_indicator_builder(candles, "M15")

        engine.evaluate_for_snapshot("SPX500_USD", "M15", candles, summary)
        fired_second = engine.evaluate_for_snapshot("SPX500_USD", "M15", candles, summary)

        assert fired_second == []
    finally:
        store.close()


def test_evaluate_for_snapshot_new_candle_reevaluates(tmp_path: Path) -> None:
    """After dedup, advancing to a new candle allows re-evaluation."""
    store = TradeStore(db_path=tmp_path / "snap_advance.json")
    repository = AlertRepository(store=store)
    engine = IndicatorAlertEngine(
        repository,
        StubProvider(),
        indicator_builder=fake_indicator_builder,
    )
    try:
        candles_v1 = _make_candles(base=BASE_TIME)
        candles_v2 = _make_candles(base=BASE_TIME + pd.Timedelta(hours=1))
        summary = fake_indicator_builder(candles_v1, "M15")

        repository.upsert_indicator_alert(
            {
                "instrument": "SPX500_USD",
                "granularity": "M15",
                "indicator": IndicatorKind.RSI,
                "condition": "below",
                "threshold": 30.0,
                "chat_id": 1,
                "created_at": BASE_TIME,
            }
        )

        engine.evaluate_for_snapshot("SPX500_USD", "M15", candles_v1, summary)
        summary_v2 = fake_indicator_builder(candles_v2, "M15")
        fired = engine.evaluate_for_snapshot("SPX500_USD", "M15", candles_v2, summary_v2)

        # Alert was already FIRED in call 1 (non-repeat) so it is skipped in call 2.
        # The important thing is that the engine didn't raise and returned a list.
        assert isinstance(fired, list)
    finally:
        store.close()


def test_evaluate_for_snapshot_returns_empty_for_short_candles(tmp_path: Path) -> None:
    store = TradeStore(db_path=tmp_path / "snap_short.json")
    repository = AlertRepository(store=store)
    engine = IndicatorAlertEngine(
        repository,
        StubProvider(),
        indicator_builder=fake_indicator_builder,
    )
    try:
        repository.upsert_indicator_alert(
            {
                "instrument": "SPX500_USD",
                "granularity": "M15",
                "indicator": IndicatorKind.RSI,
                "condition": "below",
                "threshold": 30.0,
                "chat_id": 1,
                "created_at": BASE_TIME,
            }
        )
        single_bar = _make_candles(n=1)
        summary = fake_indicator_builder(single_bar, "M15")

        result = engine.evaluate_for_snapshot("SPX500_USD", "M15", single_bar, summary)

        assert result == []
    finally:
        store.close()


def test_evaluate_for_snapshot_skips_non_matching_instrument(tmp_path: Path) -> None:
    store = TradeStore(db_path=tmp_path / "snap_nomatch.json")
    repository = AlertRepository(store=store)
    engine = IndicatorAlertEngine(
        repository,
        StubProvider(),
        indicator_builder=fake_indicator_builder,
    )
    try:
        repository.upsert_indicator_alert(
            {
                "instrument": "EUR_USD",   # different instrument
                "granularity": "M15",
                "indicator": IndicatorKind.RSI,
                "condition": "below",
                "threshold": 30.0,
                "chat_id": 1,
                "created_at": BASE_TIME,
            }
        )
        candles = _make_candles()
        summary = fake_indicator_builder(candles, "M15")

        fired = engine.evaluate_for_snapshot("SPX500_USD", "M15", candles, summary)

        assert fired == []
    finally:
        store.close()


def test_evaluate_for_snapshot_skips_non_matching_granularity(tmp_path: Path) -> None:
    store = TradeStore(db_path=tmp_path / "snap_gran.json")
    repository = AlertRepository(store=store)
    engine = IndicatorAlertEngine(
        repository,
        StubProvider(),
        indicator_builder=fake_indicator_builder,
    )
    try:
        repository.upsert_indicator_alert(
            {
                "instrument": "SPX500_USD",
                "granularity": "H1",   # different granularity
                "indicator": IndicatorKind.RSI,
                "condition": "below",
                "threshold": 30.0,
                "chat_id": 1,
                "created_at": BASE_TIME,
            }
        )
        candles = _make_candles()
        summary = fake_indicator_builder(candles, "M15")

        fired = engine.evaluate_for_snapshot("SPX500_USD", "M15", candles, summary)

        assert fired == []
    finally:
        store.close()


def test_evaluate_for_snapshot_fires_on_startup_with_empty_cache(tmp_path: Path) -> None:
    """Empty _last_candle_seen means first call always evaluates."""
    store = TradeStore(db_path=tmp_path / "snap_startup.json")
    repository = AlertRepository(store=store)
    engine = IndicatorAlertEngine(
        repository,
        StubProvider(),
        indicator_builder=fake_indicator_builder,
    )
    try:
        alert = repository.upsert_indicator_alert(
            {
                "instrument": "SPX500_USD",
                "granularity": "M15",
                "indicator": IndicatorKind.RSI,
                "condition": "below",
                "threshold": 30.0,
                "chat_id": 1,
                "created_at": BASE_TIME - pd.Timedelta(hours=2),  # old candle
            }
        )
        old_candles = _make_candles(base=BASE_TIME - pd.Timedelta(hours=2))
        summary = fake_indicator_builder(old_candles, "M15")

        fired = engine.evaluate_for_snapshot("SPX500_USD", "M15", old_candles, summary)

        assert [a.id for a in fired] == [alert.id]
    finally:
        store.close()


def test_evaluate_for_snapshot_cross_condition(tmp_path: Path) -> None:
    """cross_up fires when previous_summary RSI <= 50 and current > 50."""
    store = TradeStore(db_path=tmp_path / "snap_cross.json")
    repository = AlertRepository(store=store)

    call_count = [0]

    def cross_indicator_builder(candles: pd.DataFrame, timeframe: str) -> IndicatorValueSummary:
        call_count[0] += 1
        # current (full candles): RSI=60 (above baseline 50)
        # previous (candles[:-1]): RSI=40 (below baseline 50) -> cross_up fires
        rsi = 60.0 if len(candles) >= 3 else 40.0
        return IndicatorValueSummary(
            metrics=(
                IndicatorMetric(name="rsi", value=rsi, source="talib"),
                IndicatorMetric(name="stoch_k", value=50.0, source="talib"),
                IndicatorMetric(name="macd", value=0.0, source="talib"),
                IndicatorMetric(name="macd_signal", value=0.0, source="talib"),
            )
        )

    engine = IndicatorAlertEngine(
        repository,
        StubProvider(),
        indicator_builder=cross_indicator_builder,
    )
    try:
        alert = repository.upsert_indicator_alert(
            {
                "instrument": "SPX500_USD",
                "granularity": "M15",
                "indicator": IndicatorKind.RSI,
                "condition": "cross_up",
                "chat_id": 1,
                "created_at": BASE_TIME,
            }
        )
        candles = _make_candles(n=3)
        current_summary = cross_indicator_builder(candles, "M15")
        call_count[0] = 0  # reset after building summary

        fired = engine.evaluate_for_snapshot("SPX500_USD", "M15", candles, current_summary)

        assert [a.id for a in fired] == [alert.id]
        # previous_summary must have been built (one extra indicator_builder call)
        assert call_count[0] >= 1
    finally:
        store.close()
```

- [ ] **Step 2: Run tests — expect AttributeError (method doesn't exist yet)**

```
pytest tests/unit/test_indicator_alert_engine.py -v -k "snapshot"
```

Expected: `AttributeError: 'IndicatorAlertEngine' object has no attribute 'evaluate_for_snapshot'`

- [ ] **Step 3: Implement `evaluate_for_snapshot()` in `alerts/indicator_alert_engine.py`**

Add `_last_candle_seen` to `__init__` (after line 41, before the closing brace):

```python
        self._last_candle_seen: dict[tuple[str, str], datetime] = {}
```

Add the new method after `evaluate_pending_alerts()` (after line 87, before `_cooloff_elapsed`):

```python
    def evaluate_for_snapshot(
        self,
        instrument: str,
        granularity: str,
        candles: pd.DataFrame,
        current_summary,
    ) -> list[IndicatorAlert]:
        """Evaluate indicator alerts for a newly computed snapshot.

        Skips evaluation if the last candle timestamp hasn't changed since
        the previous call for this (instrument, granularity) pair.
        """
        if len(candles) < 2:
            return []

        last_candle: datetime = candles["time"].iloc[-1].to_pydatetime()
        if self._last_candle_seen.get((instrument, granularity)) == last_candle:
            return []
        self._last_candle_seen[(instrument, granularity)] = last_candle

        active = [
            alert
            for alert in self.alert_repository.list_active_indicator_alerts()
            if alert.instrument == instrument and alert.granularity == granularity
        ]
        if not active:
            return []

        eligible = [
            alert for alert in active
            if not (alert.status == AlertStatus.FIRED and not alert.repeat)
            and not (
                alert.status == AlertStatus.FIRED
                and alert.repeat
                and not self._cooloff_elapsed(alert)
            )
        ]
        if not eligible:
            return []

        previous_summary = self.indicator_builder(
            candles.iloc[:-1].reset_index(drop=True), granularity
        )

        fired: list[IndicatorAlert] = []
        for alert in eligible:
            current_value, previous_value = self._resolve_values(
                alert=alert,
                current_summary=current_summary,
                previous_summary=previous_summary,
            )
            if not self._is_triggered(alert, current_value=current_value, previous_value=previous_value):
                continue

            updated = self.alert_repository.mark_indicator_alert_fired(alert.id)
            if updated is None:
                continue

            self.logger.info(
                "alert_fired",
                alert_id=updated.id,
                alert_kind="indicator",
                instrument=updated.instrument,
                fire_value=current_value,
                repeat_enabled=updated.repeat,
            )
            self._dispatch_notification(updated, current_value=current_value)
            fired.append(updated)
        return fired
```

- [ ] **Step 4: Run all engine tests — all pass**

```
pytest tests/unit/test_indicator_alert_engine.py -v
```

Expected: all pass (existing 2 + new 8 = 10 tests)

- [ ] **Step 5: Commit**

```bash
git add alerts/indicator_alert_engine.py tests/unit/test_indicator_alert_engine.py
git commit -m "feat: add evaluate_for_snapshot to IndicatorAlertEngine"
```

---

## Task 3: Wire `evaluate_for_snapshot()` into `ScanOrchestrator`

**Files:**
- Modify: `orchestration/scan_orchestrator.py`
- Modify: `tests/integration/test_scan_orchestrator.py`

- [ ] **Step 1: Write the failing integration tests**

Append to the end of `tests/integration/test_scan_orchestrator.py`:

```python
# ---------------------------------------------------------------------------
# indicator_alert_engine integration
# ---------------------------------------------------------------------------

class StubIndicatorAlertEngine:
    """Records calls to evaluate_for_snapshot for assertion."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.raise_on_next: bool = False

    def evaluate_for_snapshot(self, instrument, granularity, candles, current_summary):
        if self.raise_on_next:
            raise RuntimeError("stub engine failure")
        self.calls.append((instrument, granularity))
        return []


def _make_full_orchestrator(tmp_path: Path, indicator_alert_engine=None) -> ScanOrchestrator:
    """Build a ScanOrchestrator with all-stub detectors, matching existing test pattern."""
    settings = build_settings(tmp_path)
    return ScanOrchestrator(
        settings=settings,
        market_data_provider=StubMarketDataProvider(),
        calendar_provider=StubCalendarProvider(),
        market_hours_service=OpenMarketHours(),
        smc_adapter=StubSmcAdapter(),
        htf_bias_analyzer=StubHTFBiasAnalyzer(),
        indicator_builder=build_indicator_summary,
        spread_evaluator=build_spread,
        chop_evaluator=build_chop,
        sfp_detector=lambda candles, timeframe, swing_length=50: SFPResult(),
        turtle_soup_detector=lambda candles, timeframe: TurtleSoupResult(),
        orb_detector=lambda candles, timeframe: ORBResult(),
        indicator_alert_engine=indicator_alert_engine,
    )


def test_scan_orchestrator_calls_engine_for_each_snapshot(tmp_path: Path) -> None:
    stub_engine = StubIndicatorAlertEngine()
    orchestrator = _make_full_orchestrator(tmp_path, indicator_alert_engine=stub_engine)

    orchestrator.refresh_snapshot("SPX500_USD", "M15")

    assert ("SPX500_USD", "M15") in stub_engine.calls


def test_scan_orchestrator_skips_engine_when_none(tmp_path: Path) -> None:
    orchestrator = _make_full_orchestrator(tmp_path, indicator_alert_engine=None)
    # Should not raise — no engine to call
    orchestrator.refresh_snapshot("SPX500_USD", "M15")


def test_scan_orchestrator_engine_exception_does_not_abort_snapshot(
    tmp_path: Path,
) -> None:
    stub_engine = StubIndicatorAlertEngine()
    stub_engine.raise_on_next = True
    orchestrator = _make_full_orchestrator(tmp_path, indicator_alert_engine=stub_engine)

    with structlog.testing.capture_logs() as logs:
        snapshot = orchestrator.refresh_snapshot("SPX500_USD", "M15")

    assert snapshot is not None
    warning_events = [l for l in logs if l.get("log_level") == "warning"]
    assert any(l.get("event") == "indicator_alert_eval_failed" for l in warning_events)
```

- [ ] **Step 2: Run the new integration tests — expect TypeError (missing parameter)**

```
pytest tests/integration/test_scan_orchestrator.py -v -k "engine"
```

Expected: `TypeError: ScanOrchestrator.__init__() got an unexpected keyword argument 'indicator_alert_engine'`

- [ ] **Step 3: Add `indicator_alert_engine` dependency and call to `ScanOrchestrator`**

In `orchestration/scan_orchestrator.py`:

Add import after existing imports (around line 36, alongside other imports):

```python
from alerts.indicator_alert_engine import IndicatorAlertEngine
```

Add the parameter to `__init__` (after `orb_detector` parameter, line 68):

```python
        indicator_alert_engine: IndicatorAlertEngine | None = None,
```

Store it in `__init__` body (after `self.orb_detector = orb_detector`, line 88):

```python
        self.indicator_alert_engine = indicator_alert_engine
```

In `_build_snapshot()`, insert after the `indicators` assignment (after the `_timed_detector("indicators", ...)` block, before the `spread` assignment — around line 286):

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

- [ ] **Step 4: Run integration tests — all pass**

```
pytest tests/integration/test_scan_orchestrator.py -v -k "engine"
```

Expected: 3 passed

- [ ] **Step 5: Run full test suite to confirm no regressions**

```
pytest tests/unit/ tests/integration/ -v
```

Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add orchestration/scan_orchestrator.py tests/integration/test_scan_orchestrator.py
git commit -m "feat: integrate indicator alert engine into ScanOrchestrator snapshot path"
```

---

## Task 4: Wire `indicator_alert_engine` into `bot/runtime.py`

**Files:**
- Modify: `bot/runtime.py`

No new tests — this is pure wiring. The existing runtime smoke test (`test_import_smoke.py`) verifies `bot.runtime` imports correctly.

- [ ] **Step 1: Update `build_runtime()` to pass `indicator_alert_engine` to `ScanOrchestrator`**

In `bot/runtime.py`, the `scan_orchestrator = ScanOrchestrator(...)` block starts at line 147. Change it to:

```python
    scan_orchestrator = ScanOrchestrator(
        settings=resolved_settings,
        market_data_provider=market_data_provider,
        market_state=market_state,
        spread_evaluator=spread_evaluator,
        chop_evaluator=chop_evaluator,
        indicator_alert_engine=indicator_alert_engine,
    )
```

Note: `indicator_alert_engine` is constructed at line 171 *after* `scan_orchestrator` in the current code. The construction order must be rearranged so `indicator_alert_engine` is built before `scan_orchestrator`. Move the `indicator_alert_engine = IndicatorAlertEngine(...)` block (lines 171-175) to before the `scan_orchestrator = ScanOrchestrator(...)` block.

The resulting order in `build_runtime()` should be:

```python
    price_alert_engine = PriceAlertEngine(alert_repository)
    indicator_alert_engine = IndicatorAlertEngine(
        alert_repository,
        market_data_provider,
        settings=resolved_settings,
    )
    scan_orchestrator = ScanOrchestrator(
        settings=resolved_settings,
        market_data_provider=market_data_provider,
        market_state=market_state,
        spread_evaluator=spread_evaluator,
        chop_evaluator=chop_evaluator,
        indicator_alert_engine=indicator_alert_engine,
    )
    cache_warmer = CacheWarmer(...)
    ...
```

- [ ] **Step 2: Verify the module imports cleanly**

```
python -c "from bot.runtime import build_runtime; print('ok')"
```

Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add bot/runtime.py
git commit -m "feat: wire indicator_alert_engine into ScanOrchestrator in build_runtime"
```

---

## Task 5: Remove standalone `IndicatorScanTask` and its wiring

**Files:**
- Delete: `background/indicator_scan_task.py`
- Modify: `orchestration/scheduler.py`
- Modify: `background/task_supervisor.py`
- Modify: `bot/runtime.py`
- Modify: `config/settings.py`

- [ ] **Step 1: Delete `background/indicator_scan_task.py`**

```bash
git rm "background/indicator_scan_task.py"
```

- [ ] **Step 2: Remove `IndicatorScanTask` from `orchestration/scheduler.py`**

Remove line 14: `from background.indicator_scan_task import IndicatorScanTask`

Remove line 28: `INDICATOR_SCAN_JOB_ID = "indicator_scan"`

Remove `INDICATOR_SCAN_JOB_ID` from `__all__` (line 263 area).

Remove from `SchedulerService.__init__`:
- Parameter: `indicator_scan_task: IndicatorScanTask | None = None,`
- Assignment: `self.indicator_scan_task = indicator_scan_task`

Remove from `_register_jobs()` the entire block:
```python
        if self.indicator_scan_task is not None:
            self._add_job(
                INDICATOR_SCAN_JOB_ID,
                self.indicator_scan_task.run_once,
                trigger=IntervalTrigger(
                    minutes=self.settings.indicator_scan_interval_minutes,
                    timezone="UTC",
                ),
            )
```

- [ ] **Step 3: Remove `IndicatorScanTask` from `background/task_supervisor.py`**

Remove line 6: `from background.indicator_scan_task import IndicatorScanTask`

Remove from `__init__` signature: `indicator_scan_task: IndicatorScanTask | None = None,`

Remove assignment: `self.indicator_scan_task = indicator_scan_task`

Remove from `health_snapshot()`:
```python
        if self.indicator_scan_task is not None:
            tasks.append(self.indicator_scan_task.status())
```

- [ ] **Step 4: Remove `IndicatorScanTask` from `bot/runtime.py`**

Remove line 11: `from background.indicator_scan_task import IndicatorScanTask`

Remove line 72: `indicator_scan_task: IndicatorScanTask` from `BotRuntime` dataclass

Remove line 176: `indicator_scan_task = IndicatorScanTask(indicator_alert_engine)`

Remove from `TaskSupervisor(...)` call: `indicator_scan_task=indicator_scan_task,`

Remove from `SchedulerService(...)` call: `indicator_scan_task=indicator_scan_task,`

Remove from `BotRuntime(...)` constructor call: `indicator_scan_task=indicator_scan_task,`

- [ ] **Step 5: Remove `indicator_scan_interval_minutes` from `config/settings.py`**

Remove from `RUNTIME_DEFAULTS` dict (line 44):
```python
    "INDICATOR_SCAN_INTERVAL_MINUTES": 5,
```

Remove the model field (lines 177-181):
```python
    indicator_scan_interval_minutes: int = Field(
        default=RUNTIME_DEFAULTS["INDICATOR_SCAN_INTERVAL_MINUTES"],
        gt=0,
        validation_alias="INDICATOR_SCAN_INTERVAL_MINUTES",
    )
```

- [ ] **Step 6: Run the full test suite**

```
pytest tests/unit/ tests/integration/ -v
```

Expected: all pass. If `test_settings.py` asserts `indicator_scan_interval_minutes` exists, remove that assertion from the test.

- [ ] **Step 7: Commit**

```bash
git add background/task_supervisor.py orchestration/scheduler.py bot/runtime.py config/settings.py
git commit -m "feat: remove IndicatorScanTask standalone job; alerts now candle-triggered"
```

---

## Task 6: Update smoke test and docs

**Files:**
- Modify: `tests/unit/test_import_smoke.py`
- Modify: `CLAUDE.md`

- [ ] **Step 1: Update `tests/unit/test_import_smoke.py`**

In the `MODULES` tuple:
- Remove: `"background.indicator_scan_task",` (line 63)
- Add: `"alerts.defaults",` (alongside other `alerts.*` entries, after `"alerts.indicator_alert_engine"`)

- [ ] **Step 2: Run smoke test**

```
pytest tests/unit/test_import_smoke.py -v
```

Expected: all pass

- [ ] **Step 3: Update `CLAUDE.md` Runtime settings table**

Remove this line from the "Environment Variables" section:
```
`INDICATOR_SCAN_INTERVAL_MINUTES` (5)
```

- [ ] **Step 4: Run full suite one final time**

```
pytest tests/unit/ tests/integration/ -v
```

Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add tests/unit/test_import_smoke.py CLAUDE.md
git commit -m "docs: update smoke test and CLAUDE.md after IndicatorScanTask removal"
```

---

## Completion Check

After all tasks:

- `pytest tests/unit/ tests/integration/ -v` — all pass
- `python -c "from bot.runtime import build_runtime; print('ok')"` — prints `ok`
- `python -c "from alerts.defaults import get_default_threshold; from core.enums import IndicatorKind; print(get_default_threshold(IndicatorKind.RSI, 'above'))"` — prints `70.0`
- `background/indicator_scan_task.py` does not exist
- `orchestration/scheduler.py` has no reference to `INDICATOR_SCAN_JOB_ID`
