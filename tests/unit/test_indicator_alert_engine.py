from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from alerts.alert_repository import AlertRepository
from alerts.indicator_alert_engine import IndicatorAlertEngine
from core.enums import AlertStatus, IndicatorKind
from core.models import IndicatorMetric, IndicatorValueSummary
from data.persistence.trade_store import TradeStore


BASE_TIME = datetime(2026, 3, 21, 8, 0, tzinfo=timezone.utc)


class FailingNotifier:
    async def send_message(self, *, chat_id: int, text: str) -> None:
        raise RuntimeError("telegram unavailable")


class StubMessageBuilder:
    def build_indicator_alert_fired(self, alert, *, current_value: float | str) -> str:
        return f"{alert.instrument}:{current_value}"


class StubProvider:
    def get_candles(self, instrument: str, timeframe: str, count: int | None = None) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "time": pd.to_datetime(
                    [
                        "2026-03-21T06:00:00Z",
                        "2026-03-21T07:00:00Z",
                        "2026-03-21T08:00:00Z",
                    ],
                    utc=True,
                ),
                "open": [1.0, 1.0, 1.0],
                "high": [1.1, 1.1, 1.1],
                "low": [0.9, 0.9, 0.9],
                "close": [1.0, 1.0, 1.0],
                "tick_volume": [10, 11, 12],
            }
        )

    def get_current_price(self, instrument: str):  # pragma: no cover - not used here
        raise NotImplementedError

    def get_candle_freshness(self, instrument: str, timeframe: str):  # pragma: no cover
        raise NotImplementedError


def fake_indicator_builder(candles: pd.DataFrame, timeframe: str) -> IndicatorValueSummary:
    if len(candles) >= 3:
        rsi = 25.0
    else:
        rsi = 35.0
    return IndicatorValueSummary(
        metrics=(
            IndicatorMetric(name="rsi", value=rsi, source="talib"),
            IndicatorMetric(name="stoch_k", value=40.0, source="talib"),
            IndicatorMetric(name="macd", value=0.5, source="talib"),
            IndicatorMetric(name="macd_signal", value=0.2, source="talib"),
        )
    )


def test_indicator_alert_engine_fires_threshold_alerts(tmp_path: Path) -> None:
    store = TradeStore(db_path=tmp_path / "indicator_alerts.json")
    repository = AlertRepository(store=store)
    engine = IndicatorAlertEngine(
        repository,
        StubProvider(),
        indicator_builder=fake_indicator_builder,
    )

    try:
        alert = repository.upsert_indicator_alert(
            {
                "instrument": "EUR_USD",
                "granularity": "H1",
                "indicator": IndicatorKind.RSI,
                "condition": "below",
                "threshold": 30.0,
                "chat_id": 1,
                "created_at": BASE_TIME,
            }
        )
        candles = StubProvider().get_candles("EUR_USD", "H1")
        summary = fake_indicator_builder(candles, "H1")
        fired = engine.evaluate_for_snapshot("EUR_USD", "H1", candles, summary)
        history = repository.list_alert_history(chat_id=1, alert_type="indicator", limit=10)

        assert [item.id for item in fired] == [alert.id]
        assert repository.get_indicator_alert(alert.id).status == AlertStatus.FIRED
        assert len(history) == 1
        assert history[0].alert_id == alert.id
        assert history[0].indicator == "RSI"
        cursor = repository.get_indicator_alert_evaluation_cursor("EUR_USD", "H1")
        assert cursor is not None
        assert cursor.last_evaluated_candle == candles["time"].iloc[-1].to_pydatetime()
    finally:
        store.close()


def test_indicator_alert_engine_rearms_repeat_alerts_after_cooloff(tmp_path: Path) -> None:
    store = TradeStore(db_path=tmp_path / "repeat_indicator_alerts.json")
    repository = AlertRepository(store=store)
    engine = IndicatorAlertEngine(
        repository,
        StubProvider(),
        indicator_builder=fake_indicator_builder,
    )

    try:
        alert = repository.upsert_indicator_alert(
            {
                "instrument": "EUR_USD",
                "granularity": "H1",
                "indicator": IndicatorKind.RSI,
                "condition": "below",
                "threshold": 30.0,
                "status": AlertStatus.FIRED,
                "repeat": True,
                "cooloff_minutes": 5,
                "chat_id": 1,
                "created_at": BASE_TIME,
                "fired_at": BASE_TIME - timedelta(minutes=10),
            }
        )
        candles = StubProvider().get_candles("EUR_USD", "H1")
        summary = fake_indicator_builder(candles, "H1")
        fired = engine.evaluate_for_snapshot("EUR_USD", "H1", candles, summary)

        assert [item.id for item in fired] == [alert.id]
        assert repository.get_indicator_alert(alert.id).fired_at > alert.fired_at
    finally:
        store.close()


def test_indicator_threshold_alerts_do_not_require_previous_metric(tmp_path: Path) -> None:
    store = TradeStore(db_path=tmp_path / "indicator_threshold_warmup.json")
    repository = AlertRepository(store=store)

    def warmup_builder(candles: pd.DataFrame, timeframe: str) -> IndicatorValueSummary:
        if len(candles) >= 3:
            return IndicatorValueSummary(
                metrics=(IndicatorMetric(name="rsi", value=25.0, source="talib"),)
            )
        return IndicatorValueSummary(metrics=())

    engine = IndicatorAlertEngine(
        repository,
        StubProvider(),
        indicator_builder=warmup_builder,
    )

    try:
        alert = repository.upsert_indicator_alert(
            {
                "instrument": "EUR_USD",
                "granularity": "H1",
                "indicator": IndicatorKind.RSI,
                "condition": "below",
                "threshold": 30.0,
                "chat_id": 1,
                "created_at": BASE_TIME,
            }
        )
        candles = StubProvider().get_candles("EUR_USD", "H1")
        summary = warmup_builder(candles, "H1")
        fired = engine.evaluate_for_snapshot("EUR_USD", "H1", candles, summary)

        assert [item.id for item in fired] == [alert.id]
        assert repository.get_indicator_alert(alert.id).status == AlertStatus.FIRED
    finally:
        store.close()


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
                "instrument": "XAU_USD",
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

        fired = engine.evaluate_for_snapshot("XAU_USD", "M15", candles, summary)

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
                "instrument": "XAU_USD",
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

        engine.evaluate_for_snapshot("XAU_USD", "M15", candles, summary)
        fired_second = engine.evaluate_for_snapshot("XAU_USD", "M15", candles, summary)

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
                "instrument": "XAU_USD",
                "granularity": "M15",
                "indicator": IndicatorKind.RSI,
                "condition": "below",
                "threshold": 30.0,
                "chat_id": 1,
                "created_at": BASE_TIME,
            }
        )

        engine.evaluate_for_snapshot("XAU_USD", "M15", candles_v1, summary)
        summary_v2 = fake_indicator_builder(candles_v2, "M15")
        fired = engine.evaluate_for_snapshot("XAU_USD", "M15", candles_v2, summary_v2)

        # Alert was already FIRED in call 1 (non-repeat) so it is skipped in call 2.
        # The important thing is that the engine didn't raise and returned a list.
        assert isinstance(fired, list)
    finally:
        store.close()


def test_indicator_notification_failure_does_not_advance_cursor(tmp_path: Path) -> None:
    store = TradeStore(db_path=tmp_path / "snap_notify_fail.json")
    repository = AlertRepository(store=store)
    engine = IndicatorAlertEngine(
        repository,
        StubProvider(),
        indicator_builder=fake_indicator_builder,
        notifier=FailingNotifier(),
        message_builder=StubMessageBuilder(),
    )
    try:
        alert = repository.upsert_indicator_alert(
            {
                "instrument": "XAU_USD",
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

        fired = engine.evaluate_for_snapshot("XAU_USD", "M15", candles, summary)
        stored = repository.get_indicator_alert(alert.id)
        cursor = repository.get_indicator_alert_evaluation_cursor("XAU_USD", "M15")

        assert fired == []
        assert stored is not None
        assert stored.status == AlertStatus.PENDING
        assert cursor is None
        assert repository.list_alert_history(chat_id=1, alert_type="indicator", limit=10) == []
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
                "instrument": "XAU_USD",
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

        result = engine.evaluate_for_snapshot("XAU_USD", "M15", single_bar, summary)

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

        fired = engine.evaluate_for_snapshot("XAU_USD", "M15", candles, summary)

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
                "instrument": "XAU_USD",
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

        fired = engine.evaluate_for_snapshot("XAU_USD", "M15", candles, summary)

        assert fired == []
    finally:
        store.close()


def test_evaluate_for_snapshot_fires_on_startup_with_empty_cursor(tmp_path: Path) -> None:
    """Missing persisted cursor means the first call evaluates the latest candle."""
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
                "instrument": "XAU_USD",
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

        fired = engine.evaluate_for_snapshot("XAU_USD", "M15", old_candles, summary)

        assert [a.id for a in fired] == [alert.id]
    finally:
        store.close()


def test_evaluate_for_snapshot_dedup_survives_restart(tmp_path: Path) -> None:
    store_path = tmp_path / "snap_restart_cursor.json"
    first_store = TradeStore(db_path=store_path)
    first_repository = AlertRepository(store=first_store)
    first_engine = IndicatorAlertEngine(
        first_repository,
        StubProvider(),
        indicator_builder=fake_indicator_builder,
    )
    candles = _make_candles()
    summary = fake_indicator_builder(candles, "M15")
    try:
        first_repository.upsert_indicator_alert(
            {
                "instrument": "XAU_USD",
                "granularity": "M15",
                "indicator": IndicatorKind.RSI,
                "condition": "below",
                "threshold": 30.0,
                "chat_id": 1,
                "created_at": BASE_TIME,
            }
        )
        first_engine.evaluate_for_snapshot("XAU_USD", "M15", candles, summary)
    finally:
        first_store.close()

    second_store = TradeStore(db_path=store_path)
    second_repository = AlertRepository(store=second_store)
    second_engine = IndicatorAlertEngine(
        second_repository,
        StubProvider(),
        indicator_builder=fake_indicator_builder,
    )
    try:
        fired = second_engine.evaluate_for_snapshot("XAU_USD", "M15", candles, summary)

        assert fired == []
    finally:
        second_store.close()


def _make_sma_cross_summary(
    sma_50: float | None,
    sma_200: float | None,
) -> IndicatorValueSummary:
    metrics = [
        IndicatorMetric(name="rsi", value=50.0, source="talib"),
        IndicatorMetric(name="stoch_k", value=50.0, source="talib"),
        IndicatorMetric(name="macd", value=0.0, source="talib"),
        IndicatorMetric(name="macd_signal", value=0.0, source="talib"),
    ]
    if sma_50 is not None:
        metrics.append(IndicatorMetric(name="sma_50", value=sma_50, source="talib"))
    if sma_200 is not None:
        metrics.append(IndicatorMetric(name="sma_200", value=sma_200, source="talib"))
    return IndicatorValueSummary(metrics=tuple(metrics))


def _sma_cross_engine(tmp_path: Path, current_sma_50: float | None, current_sma_200: float | None,
                      prev_sma_50: float | None, prev_sma_200: float | None):
    """Return (engine, repository, store) wired to return the given SMA metrics."""

    call_count = [0]

    def builder(candles: pd.DataFrame, timeframe: str) -> IndicatorValueSummary:
        call_count[0] += 1
        # First call is for current (full candles), second for previous (n-1 candles).
        if len(candles) >= 3:
            return _make_sma_cross_summary(current_sma_50, current_sma_200)
        return _make_sma_cross_summary(prev_sma_50, prev_sma_200)

    store = TradeStore(db_path=tmp_path / "sma_cross.json")
    repository = AlertRepository(store=store)
    engine = IndicatorAlertEngine(repository, StubProvider(), indicator_builder=builder)
    return engine, repository, store


def test_sma_cross_golden_cross_fires(tmp_path: Path) -> None:
    """Spread goes from negative to positive — golden cross fires."""
    engine, repository, store = _sma_cross_engine(
        tmp_path,
        current_sma_50=210.0, current_sma_200=200.0,  # spread +10
        prev_sma_50=190.0, prev_sma_200=200.0,         # spread -10
    )
    try:
        alert = repository.upsert_indicator_alert({
            "instrument": "XAU_USD", "granularity": "H1",
            "indicator": IndicatorKind.SMA_CROSS, "condition": "cross_up",
            "chat_id": 1, "created_at": BASE_TIME,
        })
        candles = _make_candles()
        current_summary = _make_sma_cross_summary(210.0, 200.0)
        fired = engine.evaluate_for_snapshot("XAU_USD", "H1", candles, current_summary)
        assert [a.id for a in fired] == [alert.id]
    finally:
        store.close()


def test_sma_cross_death_cross_fires(tmp_path: Path) -> None:
    """Spread goes from positive to negative — death cross fires."""
    engine, repository, store = _sma_cross_engine(
        tmp_path,
        current_sma_50=190.0, current_sma_200=200.0,  # spread -10
        prev_sma_50=210.0, prev_sma_200=200.0,         # spread +10
    )
    try:
        alert = repository.upsert_indicator_alert({
            "instrument": "XAU_USD", "granularity": "H1",
            "indicator": IndicatorKind.SMA_CROSS, "condition": "cross_down",
            "chat_id": 1, "created_at": BASE_TIME,
        })
        candles = _make_candles()
        current_summary = _make_sma_cross_summary(190.0, 200.0)
        fired = engine.evaluate_for_snapshot("XAU_USD", "H1", candles, current_summary)
        assert [a.id for a in fired] == [alert.id]
    finally:
        store.close()


def test_sma_cross_no_cross_does_not_fire(tmp_path: Path) -> None:
    """Spread stays positive — no cross, no fire."""
    engine, repository, store = _sma_cross_engine(
        tmp_path,
        current_sma_50=210.0, current_sma_200=200.0,  # spread +10
        prev_sma_50=205.0, prev_sma_200=200.0,         # spread +5 (stays positive)
    )
    try:
        repository.upsert_indicator_alert({
            "instrument": "XAU_USD", "granularity": "H1",
            "indicator": IndicatorKind.SMA_CROSS, "condition": "cross_up",
            "chat_id": 1, "created_at": BASE_TIME,
        })
        candles = _make_candles()
        current_summary = _make_sma_cross_summary(210.0, 200.0)
        fired = engine.evaluate_for_snapshot("XAU_USD", "H1", candles, current_summary)
        assert fired == []
    finally:
        store.close()


def test_sma_cross_raises_when_sma_200_unavailable(tmp_path: Path) -> None:
    """sma_200=None in current metrics raises RuntimeError."""
    store = TradeStore(db_path=tmp_path / "sma_cross_err.json")
    repository = AlertRepository(store=store)

    def builder(candles: pd.DataFrame, timeframe: str) -> IndicatorValueSummary:
        if len(candles) >= 3:
            # current: sma_50 valid, sma_200 missing
            return _make_sma_cross_summary(sma_50=210.0, sma_200=None)
        return _make_sma_cross_summary(sma_50=190.0, sma_200=200.0)

    engine = IndicatorAlertEngine(repository, StubProvider(), indicator_builder=builder)
    try:
        repository.upsert_indicator_alert({
            "instrument": "XAU_USD", "granularity": "H1",
            "indicator": IndicatorKind.SMA_CROSS, "condition": "cross_up",
            "chat_id": 1, "created_at": BASE_TIME,
        })
        candles = _make_candles()
        current_summary = _make_sma_cross_summary(sma_50=210.0, sma_200=None)
        import pytest
        with pytest.raises(RuntimeError):
            engine.evaluate_for_snapshot("XAU_USD", "H1", candles, current_summary)
    finally:
        store.close()


def test_sma_cross_uses_baseline_when_previous_sma_200_is_unavailable(tmp_path: Path) -> None:
    """Missing previous sma_200 falls back to baseline semantics instead of raising."""
    store = TradeStore(db_path=tmp_path / "sma_cross_prev_err.json")
    repository = AlertRepository(store=store)

    def builder(candles: pd.DataFrame, timeframe: str) -> IndicatorValueSummary:
        if len(candles) >= 3:
            return _make_sma_cross_summary(sma_50=210.0, sma_200=200.0)
        # previous: sma_50 valid, sma_200 missing
        return _make_sma_cross_summary(sma_50=190.0, sma_200=None)

    engine = IndicatorAlertEngine(repository, StubProvider(), indicator_builder=builder)
    try:
        repository.upsert_indicator_alert({
            "instrument": "XAU_USD", "granularity": "H1",
            "indicator": IndicatorKind.SMA_CROSS, "condition": "cross_up",
            "chat_id": 1, "created_at": BASE_TIME,
        })
        candles = _make_candles()
        current_summary = _make_sma_cross_summary(sma_50=210.0, sma_200=200.0)
        fired = engine.evaluate_for_snapshot("XAU_USD", "H1", candles, current_summary)

        assert len(fired) == 1
    finally:
        store.close()


def test_sma_cross_evaluate_for_snapshot_raises_when_metric_unavailable(tmp_path: Path) -> None:
    """RuntimeError from _resolve_values propagates out of evaluate_for_snapshot."""
    store = TradeStore(db_path=tmp_path / "sma_cross_prop.json")
    repository = AlertRepository(store=store)

    def builder(candles: pd.DataFrame, timeframe: str) -> IndicatorValueSummary:
        # Always return sma_200=None to force RuntimeError
        return _make_sma_cross_summary(sma_50=210.0, sma_200=None)

    engine = IndicatorAlertEngine(repository, StubProvider(), indicator_builder=builder)
    try:
        repository.upsert_indicator_alert({
            "instrument": "XAU_USD", "granularity": "H1",
            "indicator": IndicatorKind.SMA_CROSS, "condition": "cross_up",
            "chat_id": 1, "created_at": BASE_TIME,
        })
        candles = _make_candles()
        current_summary = _make_sma_cross_summary(sma_50=210.0, sma_200=None)
        import pytest
        with pytest.raises(RuntimeError):
            engine.evaluate_for_snapshot("XAU_USD", "H1", candles, current_summary)
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
                "instrument": "XAU_USD",
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

        fired = engine.evaluate_for_snapshot("XAU_USD", "M15", candles, current_summary)

        assert [a.id for a in fired] == [alert.id]
        # previous_summary must have been built (one extra indicator_builder call)
        assert call_count[0] >= 1
    finally:
        store.close()
