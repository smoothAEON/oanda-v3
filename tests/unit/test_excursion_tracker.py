from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from core.enums import TradeState
from core.events import PriceTick
from core.models import TradeRecord
from data.persistence.trade_store import TradeStore
from journal.excursion_repository import ExcursionRepository
from journal.trade_repository import TradeRepository
from tracking.excursion_tracker import ExcursionTracker


BASE_TIME = datetime(2026, 3, 21, 8, 0, tzinfo=timezone.utc)


def build_open_trade() -> TradeRecord:
    return TradeRecord(
        trade_id="trade-1",
        instrument="EUR_USD",
        units=1000.0,
        open_price=1.1000,
        close_price=None,
        sl_price=1.0900,
        tp_price=1.1200,
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
    )


def test_excursion_tracker_writes_on_first_tick_and_min_move_boundary(tmp_path: Path) -> None:
    store = TradeStore(db_path=tmp_path / "excursions.json")
    trade_repository = TradeRepository(store=store)
    excursion_repository = ExcursionRepository(store=store)
    tracker = ExcursionTracker(trade_repository, excursion_repository)

    try:
        trade_repository.upsert(build_open_trade())

        first = tracker.process_tick(
            PriceTick(
                instrument="EUR_USD",
                bid=1.1001,
                ask=1.1003,
                time=BASE_TIME,
            )
        )
        second = tracker.process_tick(
            PriceTick(
                instrument="EUR_USD",
                bid=1.10012,
                ask=1.10032,
                time=BASE_TIME,
            )
        )
        third = tracker.process_tick(
            PriceTick(
                instrument="EUR_USD",
                bid=1.1007,
                ask=1.1009,
                time=BASE_TIME,
            )
        )

        assert len(first) == 1
        assert second == []
        assert len(third) == 1
        assert len(excursion_repository.list_for_trade("trade-1")) == 2
    finally:
        store.close()
