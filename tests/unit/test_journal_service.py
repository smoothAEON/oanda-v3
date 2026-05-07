from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.enums import CloseReason
from core.events import TradeClosedEvent, TradeModifiedEvent, TradeOpenedEvent
from data.persistence.trade_store import TradeStore
from journal.journal_service import JournalService
from journal.trade_repository import TradeRepository


BASE_TIME = datetime(2026, 3, 21, 8, 0, tzinfo=timezone.utc)


def test_journal_service_writes_open_modify_and_close_events(tmp_path: Path) -> None:
    store = TradeStore(db_path=tmp_path / "journal.json")
    repository = TradeRepository(store=store)
    service = JournalService(repository)

    try:
        opened = service.handle_trade_opened(
            TradeOpenedEvent(
                trade_id="trade-1",
                instrument="SPX500_USD",
                units=1.0,
                open_price=3020.50,
                sl=3010.00,
                tp=3040.00,
                gslo=None,
                opened_at=BASE_TIME,
            )
        )
        modified = service.handle_trade_modified(
            TradeModifiedEvent(
                trade_id="trade-1",
                new_sl=3012.00,
                new_tp=3042.00,
                modified_at=BASE_TIME + timedelta(minutes=15),
            )
        )
        closed = service.handle_trade_closed(
            TradeClosedEvent(
                trade_id="trade-1",
                instrument="SPX500_USD",
                units=1.0,
                open_price=3020.50,
                close_price=3040.50,
                realized_pnl=20.0,
                close_reason=CloseReason.TP_HIT,
                account_currency="sgd",
                closed_at=BASE_TIME + timedelta(hours=1),
            )
        )

        assert opened.state == "OPEN"
        assert modified is not None
        assert modified.sl_price == 3012.00
        assert closed is not None
        assert closed.state == "CLOSED"
        assert closed.pips == 20.0
        assert closed.account_pnl == 20.0
        assert closed.account_currency == "SGD"
        assert closed.instrument_pnl_currency == "SGD"
        assert repository.list_open() == []
        assert [trade.trade_id for trade in repository.list_closed()] == ["trade-1"]
    finally:
        store.close()
