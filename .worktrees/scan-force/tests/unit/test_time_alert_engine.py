from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from alerts.alert_repository import AlertRepository
from alerts.time_alert_engine import TimeAlertEngine, next_fixed_time_fire_at, next_session_fire_at
from core.enums import TimeAlertKind, TimeAlertStatus
from core.models import TimeAlert
from data.persistence.trade_store import TradeStore


BASE_TIME = datetime(2026, 3, 29, 0, 0, tzinfo=timezone.utc)


class StubNotifier:
    def __init__(self) -> None:
        self.messages: list[tuple[int, str]] = []

    async def send_message(self, *, chat_id: int, text: str) -> None:
        self.messages.append((chat_id, text))


class FailingNotifier:
    async def send_message(self, *, chat_id: int, text: str) -> None:
        raise RuntimeError("telegram unavailable")


def test_next_fixed_time_fire_at_interprets_sgt_local_time() -> None:
    fire_at = next_fixed_time_fire_at("09:30", now_utc=BASE_TIME)

    assert fire_at == datetime(2026, 3, 29, 1, 30, tzinfo=timezone.utc)


def test_next_session_fire_at_returns_next_named_session_open() -> None:
    london = next_session_fire_at("london", now_utc=BASE_TIME)
    market_open = next_session_fire_at("market_open", now_utc=BASE_TIME)

    assert london == datetime(2026, 3, 29, 8, 0, tzinfo=timezone.utc)
    assert market_open == datetime(2026, 3, 29, 22, 0, tzinfo=timezone.utc)


def test_next_fixed_time_fire_at_rejects_invalid_time_text() -> None:
    for value in ("25:99", "abc", "12", ""):
        try:
            next_fixed_time_fire_at(value, now_utc=BASE_TIME)
        except ValueError as exc:
            assert "HH:MM" in str(exc)
        else:  # pragma: no cover - defensive assertion path
            raise AssertionError(f"{value!r} should be rejected")


def test_time_alert_engine_marks_one_shot_alert_completed_and_dispatches(tmp_path: Path) -> None:
    store = TradeStore(db_path=tmp_path / "time_alert_engine_once.json")
    repository = AlertRepository(store=store)
    notifier = StubNotifier()
    engine = TimeAlertEngine(repository, notifier=notifier)
    try:
        repository.upsert_time_alert(
            TimeAlert(
                id=1,
                chat_id=123,
                kind=TimeAlertKind.FIXED_TIME,
                status=TimeAlertStatus.ACTIVE,
                schedule="once",
                timezone_name="Asia/Singapore",
                local_time="09:30",
                session_name=None,
                note="desk prep",
                created_at=BASE_TIME - timedelta(hours=1),
                next_fire_at=BASE_TIME,
                last_fired_at=None,
            )
        )

        fired = engine.evaluate_due_alerts(now_utc=BASE_TIME)
        stored = repository.get_time_alert(1)

        assert [alert.id for alert in fired] == [1]
        assert stored is not None
        assert stored.status == TimeAlertStatus.COMPLETED
        assert stored.last_fired_at == BASE_TIME
        assert notifier.messages == [(123, notifier.messages[0][1])]
        assert "Time Alert" in notifier.messages[0][1]
        assert "desk prep" in notifier.messages[0][1]
    finally:
        store.close()


def test_time_alert_engine_reschedules_daily_alerts(tmp_path: Path) -> None:
    store = TradeStore(db_path=tmp_path / "time_alert_engine_daily.json")
    repository = AlertRepository(store=store)
    engine = TimeAlertEngine(repository)
    try:
        repository.upsert_time_alert(
            TimeAlert(
                id=1,
                chat_id=123,
                kind=TimeAlertKind.FIXED_TIME,
                status=TimeAlertStatus.ACTIVE,
                schedule="daily",
                timezone_name="Asia/Singapore",
                local_time="09:30",
                session_name=None,
                note=None,
                created_at=BASE_TIME - timedelta(days=1),
                next_fire_at=BASE_TIME,
                last_fired_at=None,
            )
        )

        fired = engine.evaluate_due_alerts(now_utc=BASE_TIME)
        stored = repository.get_time_alert(1)

        assert [alert.id for alert in fired] == [1]
        assert stored is not None
        assert stored.status == TimeAlertStatus.ACTIVE
        assert stored.last_fired_at == BASE_TIME
        assert stored.next_fire_at == datetime(2026, 3, 29, 1, 30, tzinfo=timezone.utc)
    finally:
        store.close()


def test_time_alert_notification_failure_leaves_alert_due(tmp_path: Path) -> None:
    store = TradeStore(db_path=tmp_path / "time_alert_engine_fail.json")
    repository = AlertRepository(store=store)
    engine = TimeAlertEngine(repository, notifier=FailingNotifier())
    try:
        repository.upsert_time_alert(
            TimeAlert(
                id=1,
                chat_id=123,
                kind=TimeAlertKind.FIXED_TIME,
                status=TimeAlertStatus.ACTIVE,
                schedule="once",
                timezone_name="Asia/Singapore",
                local_time="09:30",
                session_name=None,
                note=None,
                created_at=BASE_TIME - timedelta(hours=1),
                next_fire_at=BASE_TIME,
                last_fired_at=None,
            )
        )

        fired = engine.evaluate_due_alerts(now_utc=BASE_TIME)
        stored = repository.get_time_alert(1)

        assert fired == []
        assert stored is not None
        assert stored.status == TimeAlertStatus.ACTIVE
        assert stored.next_fire_at == BASE_TIME
        assert stored.last_fired_at is None
    finally:
        store.close()
