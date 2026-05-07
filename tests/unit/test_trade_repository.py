from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.enums import CloseReason, TradeState
from core.models import TradeRecord
from data.persistence.trade_store import TradeStore


BASE_TIME = datetime(2026, 3, 21, 8, 0, tzinfo=timezone.utc)


def build_open_trade(*, trade_id: str = "trade-1", notes: str | None = None) -> TradeRecord:
    return TradeRecord(
        trade_id=trade_id,
        instrument="SPX500_USD",
        units=1.0,
        open_price=3020.50,
        close_price=None,
        sl_price=3010.00,
        tp_price=3040.00,
        gslo_price=None,
        state=TradeState.OPEN,
        close_reason=None,
        pips=None,
        instrument_pnl=None,
        instrument_pnl_currency=None,
        account_pnl=None,
        account_currency=None,
        opened_at=BASE_TIME,
        closed_at=None,
        notes=notes,
    )


def test_trade_repository_upsert_get_close_and_list(tmp_path: Path) -> None:
    store = TradeStore(db_path=tmp_path / "trades.json")
    try:
        opened = store.upsert_trade(build_open_trade())

        fetched_open = store.get_trade("trade-1")
        open_trades = store.list_open_trades()

        assert fetched_open == opened
        assert [trade.trade_id for trade in open_trades] == ["trade-1"]

        closed = store.close_trade(
            "trade-1",
            close_price=3035.50,
            close_reason=CloseReason.TP_HIT,
            pips=1500.0,
            instrument_pnl=15.0,
            instrument_pnl_currency="USD",
            account_pnl=15.0,
            account_currency="USD",
            closed_at=BASE_TIME + timedelta(hours=2),
        )

        assert closed is not None
        assert closed.state == TradeState.CLOSED
        assert closed.pips == 1500.0
        assert closed.instrument_pnl == 15.0
        assert closed.account_pnl == 15.0
        assert store.list_open_trades() == []
        assert [trade.trade_id for trade in store.list_closed_trades()] == ["trade-1"]
    finally:
        store.close()


def test_trade_repository_updates_notes_without_losing_state(tmp_path: Path) -> None:
    store = TradeStore(db_path=tmp_path / "notes.json")
    try:
        store.upsert_trade(build_open_trade(notes="initial"))

        updated = store.set_trade_notes("trade-1", "runner idea")

        assert updated is not None
        assert updated.notes == "runner idea"
        assert updated.state == TradeState.OPEN
        assert updated.close_price is None
    finally:
        store.close()
