from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from core.events import PriceTick
from data.persistence.trade_store import TradeStore
from alerts.alert_repository import AlertRepository
from alerts.price_alert_engine import PriceAlertEngine


BASE_TIME = datetime(2026, 3, 21, 8, 0, tzinfo=timezone.utc)


class FailingNotifier:
    async def send_message(self, *, chat_id: int, text: str) -> None:
        raise RuntimeError("telegram unavailable")


class StubMessageBuilder:
    def build_price_alert_fired(self, alert, *, current_price: float) -> str:
        return f"{alert.instrument}:{current_price}"


def test_price_alert_engine_uses_ask_for_above_and_bid_for_below(tmp_path: Path) -> None:
    store = TradeStore(db_path=tmp_path / "alerts.json")
    repository = AlertRepository(store=store)
    engine = PriceAlertEngine(repository)

    try:
        above = repository.upsert_price_alert(
            {
                "instrument": "SPX500_USD",
                "target_price": 3050.0,
                "direction": "above",
                "chat_id": 1,
                "created_at": BASE_TIME,
            }
        )
        below = repository.upsert_price_alert(
            {
                "instrument": "SPX500_USD",
                "target_price": 3048.5,
                "direction": "below",
                "chat_id": 1,
                "created_at": BASE_TIME,
            }
        )

        armed = engine.evaluate_tick(
            PriceTick(
                instrument="SPX500_USD",
                bid=3049.8,
                ask=3049.9,
                time=BASE_TIME,
            )
        )
        first = engine.evaluate_tick(
            PriceTick(
                instrument="SPX500_USD",
                bid=3049.8,
                ask=3050.1,
                time=BASE_TIME,
            )
        )
        second = engine.evaluate_tick(
            PriceTick(
                instrument="SPX500_USD",
                bid=3048.4,
                ask=3048.7,
                time=BASE_TIME,
            )
        )

        assert armed == []
        assert [item.alert.id for item in first] == [above.id]
        assert first[0].fire_value == 3050.1
        assert [item.alert.id for item in second] == [below.id]
        assert second[0].fire_value == 3048.4
        assert repository.list_pending_price_alerts() == []
    finally:
        store.close()


def test_price_alert_engine_requires_safe_side_before_firing(tmp_path: Path) -> None:
    store = TradeStore(db_path=tmp_path / "alerts_crossing.json")
    repository = AlertRepository(store=store)
    engine = PriceAlertEngine(repository)

    try:
        alert = repository.upsert_price_alert(
            {
                "instrument": "SPX500_USD",
                "target_price": 3050.0,
                "direction": "above",
                "chat_id": 1,
                "created_at": BASE_TIME,
            }
        )

        first = engine.evaluate_tick(
            PriceTick(
                instrument="SPX500_USD",
                bid=3050.0,
                ask=3050.2,
                time=BASE_TIME,
            )
        )
        second = engine.evaluate_tick(
            PriceTick(
                instrument="SPX500_USD",
                bid=3049.7,
                ask=3049.9,
                time=BASE_TIME,
            )
        )
        third = engine.evaluate_tick(
            PriceTick(
                instrument="SPX500_USD",
                bid=3049.9,
                ask=3050.1,
                time=BASE_TIME,
            )
        )

        assert first == []
        assert second == []
        assert [item.alert.id for item in third] == [alert.id]
        assert repository.list_pending_price_alerts() == []
    finally:
        store.close()


def test_price_alert_engine_writes_alert_history_after_successful_fire(tmp_path: Path) -> None:
    store = TradeStore(db_path=tmp_path / "alerts_history.json")
    repository = AlertRepository(store=store)
    engine = PriceAlertEngine(repository)

    try:
        alert = repository.upsert_price_alert(
            {
                "instrument": "SPX500_USD",
                "target_price": 3050.0,
                "direction": "above",
                "chat_id": 1,
                "created_at": BASE_TIME,
            }
        )

        engine.evaluate_tick(
            PriceTick(
                instrument="SPX500_USD",
                bid=3049.7,
                ask=3049.9,
                time=BASE_TIME,
            )
        )
        fired = engine.evaluate_tick(
            PriceTick(
                instrument="SPX500_USD",
                bid=3049.8,
                ask=3050.1,
                time=BASE_TIME,
            )
        )
        history = repository.list_alert_history(chat_id=1, alert_type="price", limit=10)

        assert [item.alert.id for item in fired] == [alert.id]
        assert len(history) == 1
        assert history[0].alert_id == alert.id
        assert history[0].trigger_value == 3050.1
    finally:
        store.close()


def test_price_alert_engine_rearms_from_persisted_safe_side_after_restart(tmp_path: Path) -> None:
    store = TradeStore(db_path=tmp_path / "alerts_restart.json")
    repository = AlertRepository(store=store)

    try:
        alert = repository.upsert_price_alert(
            {
                "instrument": "SPX500_USD",
                "target_price": 3050.0,
                "direction": "above",
                "chat_id": 1,
                "created_at": BASE_TIME,
            }
        )
        PriceAlertEngine(repository).evaluate_tick(
            PriceTick(
                instrument="SPX500_USD",
                bid=3049.7,
                ask=3049.9,
                time=BASE_TIME,
            )
        )

        stored = repository.get_price_alert(alert.id)
        fired = PriceAlertEngine(repository).evaluate_tick(
            PriceTick(
                instrument="SPX500_USD",
                bid=3049.9,
                ask=3050.2,
                time=BASE_TIME,
            )
        )

        assert stored is not None
        assert stored.armed is True
        assert [item.alert.id for item in fired] == [alert.id]
    finally:
        store.close()


def test_price_alert_notification_failure_leaves_alert_pending_and_armed(tmp_path: Path) -> None:
    store = TradeStore(db_path=tmp_path / "alerts_notify_fail.json")
    repository = AlertRepository(store=store)
    engine = PriceAlertEngine(
        repository,
        notifier=FailingNotifier(),
        message_builder=StubMessageBuilder(),
    )

    try:
        alert = repository.upsert_price_alert(
            {
                "instrument": "SPX500_USD",
                "target_price": 3050.0,
                "direction": "above",
                "chat_id": 1,
                "created_at": BASE_TIME,
            }
        )

        engine.evaluate_tick(
            PriceTick(
                instrument="SPX500_USD",
                bid=3049.7,
                ask=3049.9,
                time=BASE_TIME,
            )
        )
        fired = engine.evaluate_tick(
            PriceTick(
                instrument="SPX500_USD",
                bid=3049.8,
                ask=3050.1,
                time=BASE_TIME,
            )
        )
        stored = repository.get_price_alert(alert.id)

        assert fired == []
        assert stored is not None
        assert stored.status == "PENDING"
        assert stored.armed is True
        assert stored.fired_at is None
        assert repository.list_alert_history(chat_id=1, alert_type="price", limit=10) == []
    finally:
        store.close()


@pytest.mark.asyncio
async def test_price_alert_delivery_blocks_even_when_called_from_async_context(tmp_path: Path) -> None:
    store = TradeStore(db_path=tmp_path / "alerts_notify_async.json")
    repository = AlertRepository(store=store)
    sent_messages: list[tuple[int, str]] = []

    class AsyncNotifier:
        async def send_message(self, *, chat_id: int, text: str) -> None:
            sent_messages.append((chat_id, text))

    engine = PriceAlertEngine(
        repository,
        notifier=AsyncNotifier(),
        message_builder=StubMessageBuilder(),
    )

    try:
        repository.upsert_price_alert(
            {
                "instrument": "SPX500_USD",
                "target_price": 3050.0,
                "direction": "above",
                "chat_id": 7,
                "created_at": BASE_TIME,
                "armed": True,
            }
        )

        fired = engine.evaluate_tick(
            PriceTick(
                instrument="SPX500_USD",
                bid=3049.8,
                ask=3050.1,
                time=BASE_TIME,
            )
        )

        assert [item.alert.chat_id for item in fired] == [7]
        assert sent_messages == [(7, "SPX500_USD:3050.1")]
    finally:
        store.close()
