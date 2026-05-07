from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from alerts.alert_repository import AlertRepository
from alerts.indicator_alert_engine import IndicatorAlertEngine
from core.enums import IndicatorKind
from core.models import IndicatorMetric, IndicatorValueSummary
from data.persistence.trade_store import TradeStore


BASE_TIME = datetime(2026, 3, 21, 8, 0, tzinfo=timezone.utc)


class RecordingNotifier:
    def __init__(self) -> None:
        self.messages: list[tuple[int, str]] = []

    async def send_message(self, *, chat_id: int, text: str) -> None:
        self.messages.append((chat_id, text))


class RecordingMessageBuilder:
    def build_indicator_alert_fired(self, alert, *, current_value: float | str) -> str:
        return f"{alert.instrument} {alert.granularity} {current_value}"


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

    def get_current_price(self, instrument: str):  # pragma: no cover
        raise NotImplementedError

    def get_candle_freshness(self, instrument: str, timeframe: str):  # pragma: no cover
        raise NotImplementedError


def fake_indicator_builder(candles: pd.DataFrame, timeframe: str) -> IndicatorValueSummary:
    return IndicatorValueSummary(
        metrics=(
            IndicatorMetric(name="rsi", value=25.0, source="talib"),
            IndicatorMetric(name="stoch_k", value=40.0, source="talib"),
            IndicatorMetric(name="macd", value=0.5, source="talib"),
            IndicatorMetric(name="macd_signal", value=0.2, source="talib"),
        )
    )


def test_indicator_alert_fire_marks_alert_fired(tmp_path: Path) -> None:
    store = TradeStore(db_path=tmp_path / "indicator_alert_fire.json")
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
                "chat_id": 123,
                "created_at": BASE_TIME,
            }
        )
        candles = StubProvider().get_candles("EUR_USD", "H1")
        summary = fake_indicator_builder(candles, "H1")
        fired = engine.evaluate_for_snapshot("EUR_USD", "H1", candles, summary)

        assert [item.id for item in fired] == [alert.id]
        assert repository.get_indicator_alert(alert.id).status == "FIRED"
    finally:
        store.close()


def test_indicator_alert_fire_dispatches_notification_when_notifier_wired(tmp_path: Path) -> None:
    store = TradeStore(db_path=tmp_path / "indicator_alert_notify.json")
    repository = AlertRepository(store=store)
    notifier = RecordingNotifier()
    engine = IndicatorAlertEngine(
        repository,
        StubProvider(),
        indicator_builder=fake_indicator_builder,
        notifier=notifier,
        message_builder=RecordingMessageBuilder(),
    )

    try:
        alert = repository.upsert_indicator_alert(
            {
                "instrument": "EUR_USD",
                "granularity": "H1",
                "indicator": IndicatorKind.RSI,
                "condition": "below",
                "threshold": 30.0,
                "chat_id": 789,
                "created_at": BASE_TIME,
            }
        )
        candles = StubProvider().get_candles("EUR_USD", "H1")
        summary = fake_indicator_builder(candles, "H1")
        fired = engine.evaluate_for_snapshot("EUR_USD", "H1", candles, summary)

        assert [item.id for item in fired] == [alert.id]
        assert notifier.messages == [(789, "EUR_USD H1 25.0")]
    finally:
        store.close()
