from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from alerts.alert_repository import AlertRepository
from alerts.price_alert_engine import PriceAlertEngine
from core.events import PriceTick
from data.persistence.trade_store import TradeStore


BASE_TIME = datetime(2026, 3, 21, 8, 0, tzinfo=timezone.utc)


class RecordingNotifier:
    def __init__(self) -> None:
        self.messages: list[tuple[int, str]] = []

    async def send_message(self, *, chat_id: int, text: str) -> None:
        self.messages.append((chat_id, text))


class RecordingMessageBuilder:
    def build_price_alert_fired(self, alert, *, current_price: float) -> str:
        return f"{alert.instrument} fired at {current_price:.4f}"


def test_price_alert_fire_transitions_to_fired_state(tmp_path: Path) -> None:
    store = TradeStore(db_path=tmp_path / "price_alert_fire.json")
    repository = AlertRepository(store=store)
    engine = PriceAlertEngine(repository)

    try:
        alert = repository.upsert_price_alert(
            {
                "instrument": "XAU_USD",
                "target_price": 3050.0,
                "direction": "above",
                "chat_id": 123,
                "created_at": BASE_TIME,
            }
        )

        armed = engine.evaluate_tick(
            PriceTick(
                instrument="XAU_USD",
                bid=3049.7,
                ask=3049.9,
                time=BASE_TIME,
            )
        )
        fired = engine.evaluate_tick(
            PriceTick(
                instrument="XAU_USD",
                bid=3049.8,
                ask=3050.1,
                time=BASE_TIME,
            )
        )

        assert armed == []
        assert [item.alert.id for item in fired] == [alert.id]
        assert repository.get_price_alert(alert.id).status == "FIRED"
    finally:
        store.close()


def test_price_alert_fire_dispatches_notification_when_notifier_wired(tmp_path: Path) -> None:
    store = TradeStore(db_path=tmp_path / "price_alert_notify.json")
    repository = AlertRepository(store=store)
    notifier = RecordingNotifier()
    engine = PriceAlertEngine(
        repository,
        notifier=notifier,
        message_builder=RecordingMessageBuilder(),
    )

    try:
        alert = repository.upsert_price_alert(
            {
                "instrument": "XAU_USD",
                "target_price": 3050.0,
                "direction": "above",
                "chat_id": 456,
                "created_at": BASE_TIME,
            }
        )

        armed = engine.evaluate_tick(
            PriceTick(
                instrument="XAU_USD",
                bid=3049.7,
                ask=3049.9,
                time=BASE_TIME,
            )
        )
        fired = engine.evaluate_tick(
            PriceTick(
                instrument="XAU_USD",
                bid=3049.8,
                ask=3050.1,
                time=BASE_TIME,
            )
        )

        assert armed == []
        assert [item.alert.id for item in fired] == [alert.id]
        assert notifier.messages == [(456, "XAU_USD fired at 3050.1000")]
    finally:
        store.close()
