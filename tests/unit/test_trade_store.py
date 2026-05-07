"""Unit tests for TradeStore TinyDB persistence."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

import pytest

from core.enums import AlertStatus, ChartMode, ChartRenderStyle, IndicatorKind, RuntimeConfigKey, TimeAlertKind, TimeAlertStatus, TradeState
from core.models import (
    AlertHistoryRecord,
    BotSessionRecord,
    FinancingEvent,
    RuntimeConfigRecord,
    TimeAlert,
    TradeHistoryEvent,
    TradeHistorySyncState,
    TradeRecord,
)
from data.persistence.trade_store import AtomicJSONStorage, PersistenceWriteError, TradeStore


@pytest.fixture()
def store(tmp_path: Path) -> TradeStore:
    s = TradeStore(db_path=tmp_path / "test.json")
    yield s
    s.close()


class TestUpsertAndQuery:
    def test_upsert_then_get_returns_matching_record(self, store: TradeStore) -> None:
        store.upsert_cache_metadata(
            instrument="EUR_USD",
            timeframe="H1",
            last_completed_candle=datetime(2026, 3, 20, 9, 0, tzinfo=timezone.utc),
            fetched_at=datetime(2026, 3, 20, 10, 15, tzinfo=timezone.utc),
            candle_count=100,
            source="oanda_api",
        )

        result = store.get_cache_metadata("EUR_USD", "H1")
        assert result is not None
        assert result["instrument"] == "EUR_USD"
        assert result["timeframe"] == "H1"
        assert result["candle_count"] == 100
        assert result["source"] == "oanda_api"

    def test_get_returns_none_for_missing_key(self, store: TradeStore) -> None:
        assert store.get_cache_metadata("SPX500_USD", "M15") is None

    def test_upsert_overwrites_existing_record(self, store: TradeStore) -> None:
        kwargs = dict(
            instrument="EUR_USD",
            timeframe="H1",
            last_completed_candle=datetime(2026, 3, 20, 9, 0, tzinfo=timezone.utc),
            fetched_at=datetime(2026, 3, 20, 10, 15, tzinfo=timezone.utc),
            candle_count=100,
            source="oanda_api",
        )
        store.upsert_cache_metadata(**kwargs)
        store.upsert_cache_metadata(**{**kwargs, "candle_count": 200})

        result = store.get_cache_metadata("EUR_USD", "H1")
        assert result["candle_count"] == 200

    def test_separate_keys_do_not_collide(self, store: TradeStore) -> None:
        base = dict(
            last_completed_candle=datetime(2026, 3, 20, 9, 0, tzinfo=timezone.utc),
            fetched_at=datetime(2026, 3, 20, 10, 15, tzinfo=timezone.utc),
            candle_count=100,
            source="oanda_api",
        )
        store.upsert_cache_metadata(instrument="EUR_USD", timeframe="H1", **base)
        store.upsert_cache_metadata(instrument="EUR_USD", timeframe="M15", **base)
        store.upsert_cache_metadata(instrument="SPX500_USD", timeframe="H1", **base)

        assert store.get_cache_metadata("EUR_USD", "H1") is not None
        assert store.get_cache_metadata("EUR_USD", "M15") is not None
        assert store.get_cache_metadata("SPX500_USD", "H1") is not None
        assert store.get_cache_metadata("SPX500_USD", "M15") is None


class TestDatetimeSerialization:
    def test_roundtrips_utc_datetime(self, store: TradeStore) -> None:
        original = datetime(2026, 3, 20, 9, 0, 0, tzinfo=timezone.utc)
        store.upsert_cache_metadata(
            instrument="EUR_USD",
            timeframe="H1",
            last_completed_candle=original,
            fetched_at=original,
            candle_count=1,
            source="test",
        )

        result = store.get_cache_metadata("EUR_USD", "H1")
        assert result["last_completed_candle"] == original
        assert result["fetched_at"] == original

    def test_naive_datetime_coerced_to_utc_on_read(self, store: TradeStore) -> None:
        naive = datetime(2026, 3, 20, 9, 0, 0)
        store.upsert_cache_metadata(
            instrument="EUR_USD",
            timeframe="H1",
            last_completed_candle=naive,
            fetched_at=naive,
            candle_count=1,
            source="test",
        )

        result = store.get_cache_metadata("EUR_USD", "H1")
        assert result["last_completed_candle"].tzinfo is not None

    def test_rejects_non_datetime_value(self, store: TradeStore) -> None:
        with pytest.raises((TypeError, ValueError)):
            store.upsert_cache_metadata(
                instrument="EUR_USD",
                timeframe="H1",
                last_completed_candle="not-a-datetime",
                fetched_at=datetime(2026, 3, 20, 9, 0, tzinfo=timezone.utc),
                candle_count=1,
                source="test",
            )


class TestPersistenceAcrossRestart:
    def test_data_survives_close_and_reopen(self, tmp_path: Path) -> None:
        db_path = tmp_path / "restart.json"
        store1 = TradeStore(db_path=db_path)
        store1.upsert_cache_metadata(
            instrument="GBP_USD",
            timeframe="H4",
            last_completed_candle=datetime(2026, 3, 20, 8, 0, tzinfo=timezone.utc),
            fetched_at=datetime(2026, 3, 20, 12, 5, tzinfo=timezone.utc),
            candle_count=50,
            source="oanda_api",
        )
        store1.close()

        store2 = TradeStore(db_path=db_path)
        result = store2.get_cache_metadata("GBP_USD", "H4")
        store2.close()

        assert result is not None
        assert result["candle_count"] == 50

    def test_creates_parent_directory_if_missing(self, tmp_path: Path) -> None:
        db_path = tmp_path / "nested" / "dir" / "store.json"
        store = TradeStore(db_path=db_path)
        store.close()
        assert db_path.exists()


class TestStage10Collections:
    def test_initializes_stage_10_tables(self, store: TradeStore) -> None:
        assert store.trades is not None
        assert store.signals is not None
        assert store.spread_history is not None
        assert store.cache_metadata is not None
        assert store.excursion_samples is not None
        assert store.price_alerts is not None
        assert store.indicator_alerts is not None
        assert store.time_alerts is not None
        assert store.sessions is not None
        assert store.runtime_config is not None
        assert store.raw_transactions is not None
        assert store.trade_history_events is not None
        assert store.trade_history_sync is not None
        assert store.alert_history is not None

    def test_record_signal_and_recent_spreads_round_trip(self, store: TradeStore) -> None:
        signal_id = store.record_signal(
            {
                "instrument": "EUR_USD",
                "timeframe": "H1",
                "direction": "BULLISH",
            }
        )
        store.record_spread(
            "EUR_USD",
            0.5,
            recorded_at=datetime(2026, 3, 20, 10, 0, tzinfo=timezone.utc),
            metadata={"source": "test", "bid": 1.1000, "ask": 1.10005, "spread_price": 0.00005},
        )
        store.record_spread(
            "EUR_USD",
            1.2,
            recorded_at=datetime(2026, 3, 20, 10, 5, tzinfo=timezone.utc),
            metadata={"source": "test", "reason": "round_trip", "bid": 1.1000, "ask": 1.10012, "spread_price": 0.00012},
        )

        spreads = store.get_recent_spreads("EUR_USD")

        assert signal_id > 0
        assert [entry["spread_pips"] for entry in spreads] == [1.2, 0.5]
        assert "is_" + "spiking" not in spreads[0]
        assert spreads[0]["reason"] == "round_trip"
        assert spreads[0]["spread_price"] == pytest.approx(0.00012)
        assert spreads[0]["recorded_at"] == datetime(2026, 3, 20, 10, 5, tzinfo=timezone.utc)


def test_trade_store_round_trips_trade_history_records(store: TradeStore) -> None:
    seen_raw = store.upsert_raw_transactions(
        [
            {
                "id": "101",
                "accountID": "account-id",
                "type": "ORDER_FILL",
                "time": "2026-04-01T01:00:00Z",
                "instrument": "SPX500_USD",
                "tradeOpened": {"tradeID": "trade-1", "units": "10"},
            }
        ]
    )
    seen_events = store.upsert_trade_history_events(
        [
            TradeHistoryEvent(
                event_id="101:OPEN:trade-1",
                transaction_id="101",
                batch_id="500",
                event_type="OPEN",
                account_id="account-id",
                instrument="SPX500_USD",
                trade_id="trade-1",
                order_id="9001",
                units=Decimal("10"),
                abs_units=Decimal("10"),
                side="LONG",
                price=Decimal("3123.456"),
                realized_pl=Decimal("0"),
                financing=Decimal("0"),
                commission=Decimal("0.25"),
                net_realized_pl=Decimal("-0.25"),
                time_utc=datetime(2026, 4, 1, 1, 0, tzinfo=timezone.utc),
                time_local=datetime(2026, 4, 1, 9, 0, tzinfo=ZoneInfo("Asia/Singapore")),
                reason="MARKET_ORDER",
                raw_json="{}",
            ),
            FinancingEvent(
                event_id="102:DAILY_FINANCING:SPX500_USD",
                transaction_id="102",
                account_id="account-id",
                instrument="SPX500_USD",
                financing=Decimal("-0.10"),
                time_utc=datetime(2026, 4, 1, 21, 0, tzinfo=timezone.utc),
                time_local=datetime(2026, 4, 2, 5, 0, tzinfo=ZoneInfo("Asia/Singapore")),
                raw_json="{}",
            ),
        ]
    )
    state = store.upsert_trade_history_sync_state(
        TradeHistorySyncState(
            account_id="account-id",
            last_transaction_id="102",
            last_sync_utc=datetime(2026, 4, 1, 22, 0, tzinfo=timezone.utc),
        )
    )

    records = store.list_trade_history_records()
    trade_events = store.list_trade_history_trade_events()
    financing_events = store.list_trade_history_financing_events()

    assert seen_raw == (1, 1, 0)
    assert seen_events == (2, 2, 0)
    assert store.get_trade_history_sync_state("account-id") == state
    assert store.list_trade_history_sync_states() == [state]
    assert len(records) == 2
    assert len(trade_events) == 1
    assert len(financing_events) == 1
    assert store.has_trade_history_data() is True


def test_trade_store_trade_history_upserts_are_idempotent(store: TradeStore) -> None:
    event = TradeHistoryEvent(
        event_id="201:OPEN:trade-2",
        transaction_id="201",
        batch_id="501",
        event_type="OPEN",
        account_id="account-id",
        instrument="EUR_USD",
        trade_id="trade-2",
        order_id="9002",
        units=Decimal("1000"),
        abs_units=Decimal("1000"),
        side="LONG",
        price=Decimal("1.1000"),
        realized_pl=Decimal("0"),
        financing=Decimal("0"),
        commission=Decimal("0.20"),
        net_realized_pl=Decimal("-0.20"),
        time_utc=datetime(2026, 4, 1, 1, 0, tzinfo=timezone.utc),
        time_local=datetime(2026, 4, 1, 9, 0, tzinfo=ZoneInfo("Asia/Singapore")),
        reason="MARKET_ORDER",
        raw_json="{}",
    )

    first = store.upsert_trade_history_events([event])
    second = store.upsert_trade_history_events([event])

    assert first == (1, 1, 0)
    assert second == (1, 0, 0)


def test_trade_store_degrades_gracefully_when_tinydb_open_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken_tinydb(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise OSError("disk unavailable")

    monkeypatch.setattr("data.persistence.trade_store.TinyDB", broken_tinydb)

    store = TradeStore(db_path=tmp_path / "broken.json")

    assert store.get_cache_metadata("EUR_USD", "H1") is None
    assert store.get_recent_spreads("EUR_USD") == []
    assert store.record_signal({"instrument": "EUR_USD"}) == 0
    assert store.record_spread("EUR_USD", 0.4) == 0


def test_trade_store_round_trips_sessions_and_runtime_config(store: TradeStore) -> None:
    session = store.upsert_session(
        BotSessionRecord(
            user_id=111,
            chat_id=222,
            is_admin=True,
            username="admin_user",
            first_name="Admin",
            authenticated_at=datetime(2026, 3, 22, 10, 0, tzinfo=timezone.utc),
            last_activity_at=datetime(2026, 3, 22, 10, 5, tzinfo=timezone.utc),
        )
    )
    config = store.upsert_runtime_config(
        RuntimeConfigRecord(
            key=RuntimeConfigKey.CHART,
            value=ChartRenderStyle.LINE,
            updated_at=datetime(2026, 3, 22, 10, 6, tzinfo=timezone.utc),
        )
    )

    assert store.get_session(111) == session
    assert store.list_sessions() == [session]
    assert store.get_runtime_config("chart") == config
    assert store.list_runtime_configs() == [config]
    assert store.delete_session(111) == session
    assert store.delete_runtime_config("chart") == config


def test_trade_store_round_trips_stage16_runtime_config_values(store: TradeStore) -> None:
    updated_at = datetime(2026, 3, 22, 10, 7, tzinfo=timezone.utc)
    chart_mode = store.upsert_runtime_config(
        RuntimeConfigRecord(
            key=RuntimeConfigKey.CHART_MODE,
            value=ChartMode.FULL,
            updated_at=updated_at,
        )
    )
    trade_push = store.upsert_runtime_config(
        RuntimeConfigRecord(
            key=RuntimeConfigKey.TRADE_PUSH,
            value=False,
            updated_at=updated_at,
        )
    )
    session_alerts = store.upsert_runtime_config(
        RuntimeConfigRecord(
            key=RuntimeConfigKey.SESSION_ALERTS,
            value=True,
            updated_at=updated_at,
        )
    )

    assert store.get_runtime_config("chart_mode") == chart_mode
    assert store.get_runtime_config("trade_push") == trade_push
    assert store.get_runtime_config("session_alerts") == session_alerts
    assert chart_mode.value == ChartMode.FULL
    assert trade_push.value is False
    assert session_alerts.value is True


def test_trade_store_round_trips_time_alerts(store: TradeStore) -> None:
    created = store.upsert_time_alert(
        TimeAlert(
            id=1,
            chat_id=222,
            kind=TimeAlertKind.FIXED_TIME,
            status=TimeAlertStatus.ACTIVE,
            schedule="daily",
            timezone_name="Asia/Singapore",
            local_time="09:30",
            session_name=None,
            note="london prep",
            created_at=datetime(2026, 3, 22, 10, 0, tzinfo=timezone.utc),
            next_fire_at=datetime(2026, 3, 23, 1, 30, tzinfo=timezone.utc),
            last_fired_at=None,
        )
    )

    fetched = store.get_time_alert(1)
    active = store.list_active_time_alerts_for_chat(222)
    advanced = store.mark_time_alert_triggered(
        1,
        fired_at=datetime(2026, 3, 23, 1, 30, tzinfo=timezone.utc),
        next_fire_at=datetime(2026, 3, 24, 1, 30, tzinfo=timezone.utc),
    )
    cancelled = store.cancel_time_alert_for_chat(1, 222)

    assert fetched == created
    assert active == [created]
    assert advanced is not None
    assert advanced.status == TimeAlertStatus.ACTIVE
    assert advanced.last_fired_at == datetime(2026, 3, 23, 1, 30, tzinfo=timezone.utc)
    assert cancelled is not None
    assert cancelled.status == TimeAlertStatus.CANCELLED
    assert cancelled.next_fire_at is None


def test_trade_store_create_time_alerts_creates_new_batch_atomically(store: TradeStore) -> None:
    created = store.create_time_alerts(
        [
            {
                "chat_id": 222,
                "kind": TimeAlertKind.FIXED_TIME,
                "status": TimeAlertStatus.ACTIVE,
                "schedule": "daily",
                "timezone_name": "Asia/Singapore",
                "local_time": "09:30",
                "session_name": None,
                "note": "desk prep",
                "created_at": datetime(2026, 3, 22, 10, 0, tzinfo=timezone.utc),
                "next_fire_at": datetime(2026, 3, 23, 1, 30, tzinfo=timezone.utc),
                "last_fired_at": None,
            },
            {
                "chat_id": 222,
                "kind": TimeAlertKind.FIXED_TIME,
                "status": TimeAlertStatus.ACTIVE,
                "schedule": "once",
                "timezone_name": "Asia/Singapore",
                "local_time": "2026-03-25 09:30",
                "session_name": None,
                "note": None,
                "created_at": datetime(2026, 3, 22, 10, 0, tzinfo=timezone.utc),
                "next_fire_at": datetime(2026, 3, 25, 1, 30, tzinfo=timezone.utc),
                "last_fired_at": None,
            },
        ]
    )

    assert [alert.id for alert in created] == [1, 2]
    assert store.list_active_time_alerts_for_chat(222) == created


def test_trade_store_round_trips_indicator_alert_evaluation_cursor(store: TradeStore) -> None:
    cursor = store.upsert_indicator_alert_evaluation_cursor(
        {
            "instrument": "EUR_USD",
            "granularity": "H1",
            "last_evaluated_candle": datetime(2026, 3, 22, 10, 0, tzinfo=timezone.utc),
            "updated_at": datetime(2026, 3, 22, 10, 1, tzinfo=timezone.utc),
        }
    )

    assert store.get_indicator_alert_evaluation_cursor("EUR_USD", "H1") == cursor
    assert store.list_indicator_alert_evaluation_cursors() == [cursor]


def test_trade_store_cancel_price_alerts_for_chat_filters_instrument(store: TradeStore) -> None:
    keep = store.upsert_price_alert(
        {
            "instrument": "EUR_USD",
            "target_price": 1.11,
            "direction": "above",
            "chat_id": 7,
            "created_at": datetime(2026, 3, 22, 10, 0, tzinfo=timezone.utc),
        }
    )
    clear_one = store.upsert_price_alert(
        {
            "instrument": "SPX500_USD",
            "target_price": 3050.0,
            "direction": "above",
            "chat_id": 7,
            "created_at": datetime(2026, 3, 22, 10, 1, tzinfo=timezone.utc),
        }
    )
    other_chat = store.upsert_price_alert(
        {
            "instrument": "SPX500_USD",
            "target_price": 3055.0,
            "direction": "above",
            "chat_id": 8,
            "created_at": datetime(2026, 3, 22, 10, 2, tzinfo=timezone.utc),
        }
    )

    cancelled = store.cancel_price_alerts_for_chat(7, instrument="SPX500_USD")

    assert [alert.id for alert in cancelled] == [clear_one.id]
    assert store.get_price_alert(clear_one.id).status == AlertStatus.CANCELLED
    assert store.get_price_alert(keep.id).status == AlertStatus.PENDING
    assert store.get_price_alert(other_chat.id).status == AlertStatus.PENDING


def test_trade_store_replace_price_alert_grid_replaces_pending_alerts_atomically(store: TradeStore) -> None:
    old_grid = store.upsert_price_alert(
        {
            "instrument": "SPX500_USD",
            "target_price": 3050.0,
            "direction": "above",
            "chat_id": 7,
            "created_at": datetime(2026, 3, 22, 10, 0, tzinfo=timezone.utc),
        }
    )
    untouched = store.upsert_price_alert(
        {
            "instrument": "EUR_USD",
            "target_price": 1.11,
            "direction": "below",
            "chat_id": 7,
            "created_at": datetime(2026, 3, 22, 10, 1, tzinfo=timezone.utc),
        }
    )

    created = store.replace_price_alert_grid(
        chat_id=7,
        instrument="SPX500_USD",
        alerts=[
            {"target_price": 3048.0, "direction": "below", "notes": "fade"},
            {"target_price": 3060.0, "direction": "above"},
        ],
    )

    pending = store.list_pending_price_alerts_for_chat(7)

    assert [alert.target_price for alert in created] == [3048.0, 3060.0]
    assert store.get_price_alert(old_grid.id).status == AlertStatus.CANCELLED
    assert store.get_price_alert(untouched.id).status == AlertStatus.PENDING
    assert {(alert.instrument, alert.target_price) for alert in pending} == {
        ("EUR_USD", 1.11),
        ("SPX500_USD", 3048.0),
        ("SPX500_USD", 3060.0),
    }


def test_trade_store_replace_price_alert_grid_preserves_existing_rows_on_validation_failure(store: TradeStore) -> None:
    original = store.upsert_price_alert(
        {
            "instrument": "SPX500_USD",
            "target_price": 3050.0,
            "direction": "above",
            "chat_id": 7,
            "created_at": datetime(2026, 3, 22, 10, 0, tzinfo=timezone.utc),
        }
    )

    with pytest.raises(PersistenceWriteError):
        store.replace_price_alert_grid(
            chat_id=7,
            instrument="SPX500_USD",
            alerts=[{"target_price": -1.0, "direction": "below"}],
        )

    stored = store.get_price_alert(original.id)
    assert stored is not None
    assert stored.status == AlertStatus.PENDING
    assert stored.target_price == 3050.0
    assert store.list_pending_price_alerts_for_chat(7) == [stored]


def test_trade_store_lists_alert_history_with_filters(store: TradeStore) -> None:
    first = store.insert_alert_history(
        AlertHistoryRecord(
            id=1,
            alert_type="price",
            alert_id=11,
            chat_id=7,
            instrument="SPX500_USD",
            granularity=None,
            indicator=None,
            triggered_at=datetime(2026, 3, 22, 10, 5, tzinfo=timezone.utc),
            trigger_value=3050.1,
            alert_snapshot={"direction": "above"},
            trigger_context={"bid": 3049.9},
        )
    )
    second = store.insert_alert_history(
        AlertHistoryRecord(
            id=2,
            alert_type="indicator",
            alert_id=12,
            chat_id=8,
            instrument="EUR_USD",
            granularity="H1",
            indicator="RSI",
            triggered_at=datetime(2026, 3, 22, 10, 6, tzinfo=timezone.utc),
            trigger_value=28.4,
            alert_snapshot={"condition": "below"},
            trigger_context={"threshold": 30.0},
        )
    )

    filtered = store.list_alert_history(chat_id=7, alert_type="price", instrument="SPX500_USD", limit=10)

    assert filtered == [first]
    assert store.list_alert_history(chat_id=8, limit=10) == [second]


def test_trade_store_strict_writes_raise_when_db_unavailable(store: TradeStore) -> None:
    store.db = None

    with pytest.raises(PersistenceWriteError):
        store.upsert_session(
            BotSessionRecord(
                user_id=111,
                chat_id=222,
                is_admin=False,
                username="user",
                first_name="User",
                authenticated_at=datetime(2026, 3, 22, 10, 0, tzinfo=timezone.utc),
                last_activity_at=datetime(2026, 3, 22, 10, 1, tzinfo=timezone.utc),
            )
        )

    with pytest.raises(PersistenceWriteError):
        store.upsert_runtime_config(
            RuntimeConfigRecord(
                key=RuntimeConfigKey.CHART,
                value=ChartRenderStyle.LINE,
                updated_at=datetime(2026, 3, 22, 10, 2, tzinfo=timezone.utc),
            )
        )

    with pytest.raises(PersistenceWriteError):
        store.upsert_trade(
            TradeRecord(
                trade_id="trade-1",
                instrument="SPX500_USD",
                units=1.0,
                open_price=3020.5,
                close_price=None,
                sl_price=3010.0,
                tp_price=3040.0,
                gslo_price=None,
                state=TradeState.OPEN,
                close_reason=None,
                pips=None,
                instrument_pnl=None,
                instrument_pnl_currency=None,
                account_pnl=None,
                account_currency=None,
                opened_at=datetime(2026, 3, 22, 10, 3, tzinfo=timezone.utc),
                closed_at=None,
                notes=None,
            )
        )

    with pytest.raises(PersistenceWriteError):
        store.insert_excursion_sample(
            {
                "trade_id": "trade-1",
                "sampled_at": datetime(2026, 3, 22, 10, 4, tzinfo=timezone.utc),
                "bid": 3020.4,
                "ask": 3020.6,
                "adverse_pips": 0.0,
                "favorable_pips": 1.0,
            }
        )

    with pytest.raises(PersistenceWriteError):
        store.upsert_price_alert(
            {
                "instrument": "SPX500_USD",
                "target_price": 3050.0,
                "direction": "above",
                "chat_id": 222,
                "created_at": datetime(2026, 3, 22, 10, 5, tzinfo=timezone.utc),
            }
        )

    with pytest.raises(PersistenceWriteError):
        store.upsert_indicator_alert(
            {
                "instrument": "EUR_USD",
                "granularity": "H1",
                "indicator": IndicatorKind.RSI,
                "condition": "below",
                "threshold": 30.0,
                "chat_id": 222,
                "created_at": datetime(2026, 3, 22, 10, 6, tzinfo=timezone.utc),
            }
        )


def test_trade_store_degrades_gracefully_when_existing_json_is_corrupt(tmp_path: Path) -> None:
    db_path = tmp_path / "corrupt.json"
    db_path.write_bytes(
        b'{"price_alerts": {"1": {"id": 1, "instrument": "USD_JPY", "target_price": 159.52, "di'
        + (b"\x00" * 32)
    )

    store = TradeStore(db_path=db_path)

    assert store.db is None
    assert store.list_price_alerts() == []
    assert store.list_indicator_alerts() == []
    assert store.list_sessions() == []


def test_concurrent_alert_creation_produces_unique_ids(tmp_path: Path) -> None:
    store = TradeStore(db_path=tmp_path / "concurrent_alerts.json")
    try:
        def create_alert(index: int) -> int:
            return store.upsert_price_alert(
                {
                    "instrument": "SPX500_USD",
                    "target_price": 3050.0 + index,
                    "direction": "above",
                    "chat_id": 1000 + index,
                    "created_at": datetime(2026, 3, 22, 11, 0, tzinfo=timezone.utc),
                }
            ).id

        with ThreadPoolExecutor(max_workers=8) as executor:
            ids = list(executor.map(create_alert, range(20)))

        assert len(ids) == 20
        assert len(set(ids)) == 20
        assert sorted(ids) == list(range(1, 21))
        assert len(store.list_price_alerts()) == 20
    finally:
        store.close()


def test_atomic_json_storage_replaces_file_without_nul_padding(tmp_path: Path) -> None:
    db_path = tmp_path / "atomic.json"
    storage = AtomicJSONStorage(db_path)

    try:
        storage.write(
            {
                "sessions": {
                    "1": {
                        "user_id": 111,
                        "username": "admin_user" * 20,
                        "first_name": "Admin",
                    }
                }
            }
        )
        storage.write(
            {
                "price_alerts": {
                    "1": {
                        "id": 1,
                        "instrument": "USD_JPY",
                        "target_price": 159.52,
                        "direction": "ABOVE",
                    }
                }
            }
        )

        raw = db_path.read_bytes()
        assert b"\x00" not in raw
        assert json.loads(raw.decode("utf-8")) == {
            "price_alerts": {
                "1": {
                    "id": 1,
                    "instrument": "USD_JPY",
                    "target_price": 159.52,
                    "direction": "ABOVE",
                }
            }
        }
    finally:
        storage.close()


def test_atomic_json_storage_allows_multiple_idle_instances(tmp_path: Path) -> None:
    db_path = tmp_path / "atomic-shared.json"
    storage_a = AtomicJSONStorage(db_path)
    storage_b = AtomicJSONStorage(db_path)

    try:
        storage_a.write(
            {
                "sessions": {
                    "1": {
                        "user_id": 111,
                        "chat_id": 222,
                    }
                }
            }
        )
        storage_b.write(
            {
                "price_alerts": {
                    "1": {
                        "id": 1,
                        "instrument": "USD_JPY",
                        "target_price": 159.52,
                        "direction": "ABOVE",
                    }
                }
            }
        )

        raw = db_path.read_bytes()
        assert b"\x00" not in raw
        assert json.loads(raw.decode("utf-8")) == {
            "price_alerts": {
                "1": {
                    "id": 1,
                    "instrument": "USD_JPY",
                    "target_price": 159.52,
                    "direction": "ABOVE",
                }
            }
        }
    finally:
        storage_a.close()
        storage_b.close()


def test_trade_store_second_process_fails_fast_on_runtime_lock(tmp_path: Path) -> None:
    db_path = tmp_path / "locked.json"
    store = TradeStore(db_path=db_path)
    try:
        script = """
from pathlib import Path
import sys
from data.persistence.trade_store import TradeStore

db_path = Path(sys.argv[1])
try:
    store = TradeStore(db_path=db_path)
except RuntimeError as exc:
    if "runtime lock unavailable" not in str(exc).lower():
        raise
    raise SystemExit(0)
else:
    store.close()
    raise SystemExit(1)
"""
        result = subprocess.run(
            [sys.executable, "-c", script, str(db_path)],
            cwd=str(Path(__file__).resolve().parents[2]),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )

        assert result.returncode == 0, result.stderr
    finally:
        store.close()
