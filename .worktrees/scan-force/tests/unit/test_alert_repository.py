from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from alerts.alert_repository import AlertRepository, EVALUATED_INDICATOR_ALERT_TIMEFRAMES
from core.enums import AlertStatus, IndicatorKind, TimeAlertKind, TimeAlertStatus
from data.persistence.trade_store import TradeStore


BASE_TIME = datetime(2026, 3, 21, 8, 0, tzinfo=timezone.utc)


def test_price_alert_repository_allocates_ids_and_tracks_pending_and_fired(tmp_path: Path) -> None:
    store = TradeStore(db_path=tmp_path / "price_alerts.json")
    try:
        alert = store.upsert_price_alert(
            {
                "instrument": "XAU_USD",
                "target_price": 3050.0,
                "direction": "above",
                "chat_id": 123,
                "notes": "london breakout",
                "created_at": BASE_TIME,
            }
        )

        pending = store.list_pending_price_alerts()
        fired = store.mark_price_alert_fired(alert.id, fired_at=BASE_TIME + timedelta(minutes=1))

        assert alert.id == 1
        assert [item.id for item in pending] == [1]
        assert fired is not None
        assert fired.status == AlertStatus.FIRED
        assert fired.fired_at == BASE_TIME + timedelta(minutes=1)
        assert store.list_pending_price_alerts() == []
    finally:
        store.close()


def test_indicator_alert_repository_supports_cancel_and_preserves_repeat_state(tmp_path: Path) -> None:
    store = TradeStore(db_path=tmp_path / "indicator_alerts.json")
    try:
        alert = store.upsert_indicator_alert(
            {
                "instrument": "EUR_USD",
                "granularity": "M30",
                "indicator": IndicatorKind.RSI,
                "condition": "below",
                "threshold": 30.0,
                "repeat": True,
                "cooloff_minutes": 15,
                "chat_id": 321,
                "created_at": BASE_TIME,
            }
        )

        pending = store.list_pending_indicator_alerts()
        cancelled = store.cancel_indicator_alert(alert.id)
        fetched = store.get_indicator_alert(alert.id)

        assert alert.id == 1
        assert alert.repeat is True
        assert alert.cooloff_minutes == 15
        assert [item.id for item in pending] == [1]
        assert cancelled is not None
        assert cancelled.status == AlertStatus.CANCELLED
        assert fetched is not None
        assert fetched.status == AlertStatus.CANCELLED
        assert fetched.fired_at is None
        assert store.list_pending_indicator_alerts() == []
    finally:
        store.close()


def test_indicator_alert_repository_rejects_unscheduled_create_timeframes(tmp_path: Path) -> None:
    store = TradeStore(db_path=tmp_path / "indicator_timeframe_contract.json")
    repository = AlertRepository(store=store)
    try:
        with pytest.raises(ValueError, match="Automatic indicator alerts support"):
            repository.upsert_indicator_alert(
                {
                    "instrument": "EUR_USD",
                    "granularity": "M30",
                    "indicator": IndicatorKind.RSI,
                    "condition": "below",
                    "threshold": 30.0,
                    "chat_id": 321,
                    "created_at": BASE_TIME,
                }
            )

        created = repository.upsert_indicator_alert(
            {
                "instrument": "EUR_USD",
                "granularity": "H1",
                "indicator": IndicatorKind.RSI,
                "condition": "below",
                "threshold": 30.0,
                "chat_id": 321,
                "created_at": BASE_TIME,
            }
        )

        assert created.granularity in EVALUATED_INDICATOR_ALERT_TIMEFRAMES
    finally:
        store.close()


def test_time_alert_repository_supports_create_list_and_cancel(tmp_path: Path) -> None:
    store = TradeStore(db_path=tmp_path / "time_alerts.json")
    repository = AlertRepository(store=store)
    try:
        alert = repository.upsert_time_alert(
            {
                "id": None,
                "chat_id": 321,
                "kind": TimeAlertKind.FIXED_TIME,
                "status": TimeAlertStatus.ACTIVE,
                "schedule": "daily",
                "timezone_name": "Asia/Singapore",
                "local_time": "09:30",
                "session_name": None,
                "note": "london prep",
                "created_at": BASE_TIME,
                "next_fire_at": BASE_TIME + timedelta(hours=1),
                "last_fired_at": None,
            }
        )

        pending = repository.list_active_time_alerts_for_chat(321)
        cancelled = repository.cancel_time_alert_for_chat(alert.id, 321)

        assert alert.id == 1
        assert [item.id for item in pending] == [1]
        assert cancelled is not None
        assert cancelled.status == TimeAlertStatus.CANCELLED
        assert repository.list_active_time_alerts_for_chat(321) == []
    finally:
        store.close()
