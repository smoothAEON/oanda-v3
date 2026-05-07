from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from background.poller_task import TradePollerTask
from core.events import PriceTick
from data.persistence.trade_store import TradeStore
from journal.excursion_repository import ExcursionRepository
from journal.journal_service import JournalService
from journal.trade_repository import TradeRepository
from tracking.excursion_tracker import ExcursionTracker


BASE_TIME = datetime(2026, 3, 21, 8, 0, tzinfo=timezone.utc)


class StubAccountClient:
    def __init__(self) -> None:
        self.current_trades: list[dict[str, object]] = []
        self.trade_details: dict[str, dict[str, object]] = {}

    def get_open_trades_sync(self) -> list[dict[str, object]]:
        return list(self.current_trades)

    async def get_open_trades(self) -> list[dict[str, object]]:
        return self.get_open_trades_sync()

    def get_trade_detail_sync(self, trade_id: str) -> dict[str, object]:
        return dict(self.trade_details[trade_id])

    async def get_trade_detail(self, trade_id: str) -> dict[str, object]:
        return self.get_trade_detail_sync(trade_id)


@pytest.mark.asyncio
async def test_journal_lifecycle_open_tick_close(tmp_path: Path) -> None:
    store = TradeStore(db_path=tmp_path / "journal_lifecycle.json")
    trade_repository = TradeRepository(store=store)
    excursion_repository = ExcursionRepository(store=store)
    journal_service = JournalService(trade_repository)
    tracker = ExcursionTracker(trade_repository, excursion_repository)
    client = StubAccountClient()
    poller = TradePollerTask(client, trade_repository, journal_service)

    try:
        client.current_trades = [
            {
                "id": "trade-1",
                "instrument": "EUR_USD",
                "currentUnits": 1000.0,
                "price": 1.1000,
                "stop_loss_price": 1.0900,
                "take_profit_price": 1.1200,
                "gslo_price": None,
                "openTime": BASE_TIME,
            }
        ]
        await poller.poll_once()

        tracker.process_tick(
            PriceTick(
                instrument="EUR_USD",
                bid=1.1010,
                ask=1.1012,
                time=BASE_TIME + timedelta(minutes=10),
            )
        )

        client.current_trades = []
        client.trade_details["trade-1"] = {
            "id": "trade-1",
            "instrument": "EUR_USD",
            "realizedPL": 10.0,
            "closePrice": 1.1200,
            "closeTime": BASE_TIME + timedelta(hours=1),
        }
        await poller.poll_once()

        closed = trade_repository.list_closed()
        mae_mfe = excursion_repository.get_mae_mfe("trade-1")

        assert [trade.trade_id for trade in closed] == ["trade-1"]
        assert mae_mfe is not None
        assert mae_mfe["sample_count"] == 1
    finally:
        store.close()
