from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Thread
from types import SimpleNamespace

import pytest

from background.poller_task import TradePollerTask
from core.enums import CloseReason
from data.persistence.trade_store import TradeStore
from journal.journal_service import JournalService
from journal.trade_repository import TradeRepository


BASE_TIME = datetime(2026, 3, 21, 8, 0, tzinfo=timezone.utc)


class StubAccountClient:
    def __init__(self) -> None:
        self.current_trades: list[dict[str, object]] = []
        self.trade_details: dict[str, dict[str, object]] = {}
        self.trade_transactions: dict[str, list[dict[str, object]]] = {}
        self.account_summary_currency = "SGD"

    def get_open_trades_sync(self) -> list[dict[str, object]]:
        return list(self.current_trades)

    async def get_open_trades(self) -> list[dict[str, object]]:
        return self.get_open_trades_sync()

    def get_trade_detail_sync(self, trade_id: str) -> dict[str, object]:
        return dict(self.trade_details[trade_id])

    async def get_trade_detail(self, trade_id: str) -> dict[str, object]:
        return self.get_trade_detail_sync(trade_id)

    def get_trade_transactions_sync(self, trade_id: str) -> list[dict[str, object]]:
        return [dict(item) for item in self.trade_transactions.get(trade_id, ())]

    async def get_trade_transactions(self, trade_id: str) -> list[dict[str, object]]:
        return self.get_trade_transactions_sync(trade_id)

    def get_account_summary_sync(self):
        return SimpleNamespace(currency=self.account_summary_currency)


@pytest.mark.asyncio
async def test_trade_poller_detects_open_modify_and_close(tmp_path: Path) -> None:
    store = TradeStore(db_path=tmp_path / "poller.json")
    repository = TradeRepository(store=store)
    service = JournalService(repository)
    client = StubAccountClient()
    poller = TradePollerTask(client, repository, service)

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
        first = await poller.poll_once()

        client.current_trades = [
            {
                "id": "trade-1",
                "instrument": "EUR_USD",
                "currentUnits": 1000.0,
                "price": 1.1000,
                "stop_loss_price": 1.0910,
                "take_profit_price": 1.1200,
                "gslo_price": None,
                "openTime": BASE_TIME,
            }
        ]
        second = await poller.poll_once()

        client.current_trades = []
        client.trade_details["trade-1"] = {
            "id": "trade-1",
            "instrument": "EUR_USD",
            "realizedPL": 10.0,
            "closePrice": 1.1200,
            "closeTime": BASE_TIME + timedelta(hours=1),
        }
        third = await poller.poll_once()

        assert first[0].trade_id == "trade-1"
        assert second[0].trade_id == "trade-1"
        assert third[0].close_reason == CloseReason.TP_HIT
        assert repository.list_open() == []
        assert repository.list_closed()[0].trade_id == "trade-1"
        assert repository.list_closed()[0].account_currency == "SGD"
    finally:
        store.close()


@pytest.mark.asyncio
async def test_trade_poller_defers_close_when_trade_detail_is_unavailable(tmp_path: Path) -> None:
    store = TradeStore(db_path=tmp_path / "poller_deferred_close.json")
    repository = TradeRepository(store=store)
    service = JournalService(repository)
    client = StubAccountClient()
    poller = TradePollerTask(client, repository, service)

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

        client.current_trades = []
        deferred = await poller.poll_once()

        assert deferred == []
        assert [trade.trade_id for trade in repository.list_open()] == ["trade-1"]
        assert repository.list_closed() == []
    finally:
        store.close()


@pytest.mark.asyncio
async def test_trade_poller_dispatches_trade_notifications_and_uses_transaction_reason(tmp_path: Path) -> None:
    store = TradeStore(db_path=tmp_path / "poller_notifications.json")
    repository = TradeRepository(store=store)
    service = JournalService(repository)
    client = StubAccountClient()
    sent_messages: list[tuple[int, str]] = []

    class StubRuntimeConfigManager:
        def trade_push_enabled(self) -> bool:
            return True

    class StubNotifier:
        async def send_message(self, *, chat_id: int, text: str) -> None:
            sent_messages.append((chat_id, text))

    class StubMessageBuilder:
        def build_trade_opened(self, trade, *, account_currency=None) -> str:
            return f"opened:{trade.trade_id}:{account_currency}"

        def build_trade_closed(self, trade) -> str:
            return f"closed:{trade.trade_id}:{trade.close_reason.value}"

    poller = TradePollerTask(
        client,
        repository,
        service,
        runtime_config_manager=StubRuntimeConfigManager(),
        notifier=StubNotifier(),
        message_builder=StubMessageBuilder(),
    )

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
        await asyncio.sleep(0)

        client.current_trades = []
        client.trade_details["trade-1"] = {
            "id": "trade-1",
            "instrument": "EUR_USD",
            "realizedPL": 10.0,
            "closePrice": 1.1190,
            "closeTime": BASE_TIME + timedelta(hours=1),
        }
        client.trade_transactions["trade-1"] = [
            {"id": "9001", "reason": "TAKE_PROFIT_ORDER", "type": "ORDER_FILL"}
        ]
        closed = await poller.poll_once()
        await asyncio.sleep(0)

        assert closed[0].close_reason == CloseReason.TP_HIT
        assert sent_messages[0][1] == "opened:trade-1:SGD"
        assert sent_messages[1][1] == "closed:trade-1:TP_HIT"
        assert repository.list_closed()[0].account_currency == "SGD"
    finally:
        store.close()


@pytest.mark.asyncio
async def test_trade_poller_marks_market_if_touched_closes_as_mit(tmp_path: Path) -> None:
    store = TradeStore(db_path=tmp_path / "poller_mit.json")
    repository = TradeRepository(store=store)
    service = JournalService(repository)
    client = StubAccountClient()
    poller = TradePollerTask(client, repository, service)

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

        client.current_trades = []
        client.trade_details["trade-1"] = {
            "id": "trade-1",
            "instrument": "EUR_USD",
            "realizedPL": 8.0,
            "closePrice": 1.1150,
            "closeTime": BASE_TIME + timedelta(hours=1),
            "closeReason": "MARKET_ORDER",
        }
        client.trade_transactions["trade-1"] = [
            {
                "id": "9001",
                "reason": "CLIENT_ORDER",
                "type": "ORDER_FILL",
                "orderCreateTransaction": {"type": "MARKET_IF_TOUCHED_ORDER"},
            }
        ]

        closed = await poller.poll_once()

        assert closed[0].close_reason == CloseReason.MIT
        assert repository.list_closed()[0].close_reason == CloseReason.MIT
    finally:
        store.close()


def test_trade_poller_run_once_completes_from_scheduler_thread(tmp_path: Path) -> None:
    store = TradeStore(db_path=tmp_path / "poller_thread.json")
    repository = TradeRepository(store=store)
    service = JournalService(repository)
    client = StubAccountClient()
    poller = TradePollerTask(client, repository, service)
    captured: list[tuple[object, ...]] = []

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

        thread = Thread(target=lambda: captured.append(poller.run_once()))
        thread.start()
        thread.join(timeout=2)

        assert thread.is_alive() is False
        assert captured
        assert captured[0][0].trade_id == "trade-1"
    finally:
        store.close()


@pytest.mark.asyncio
async def test_trade_poller_notification_failure_does_not_break_polling(tmp_path: Path) -> None:
    store = TradeStore(db_path=tmp_path / "poller_notify_fail.json")
    repository = TradeRepository(store=store)
    service = JournalService(repository)
    client = StubAccountClient()

    class StubRuntimeConfigManager:
        def trade_push_enabled(self) -> bool:
            return True

    class FailingNotifier:
        async def send_message(self, *, chat_id: int, text: str) -> None:
            raise RuntimeError("telegram unavailable")

    class StubMessageBuilder:
        def build_trade_opened(self, trade, *, account_currency=None) -> str:
            return f"opened:{trade.trade_id}"

        def build_trade_closed(self, trade) -> str:
            return f"closed:{trade.trade_id}"

    poller = TradePollerTask(
        client,
        repository,
        service,
        runtime_config_manager=StubRuntimeConfigManager(),
        notifier=FailingNotifier(),
        message_builder=StubMessageBuilder(),
    )

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

        opened = await poller.poll_once()
        status = poller.status()

        assert opened[0].trade_id == "trade-1"
        assert repository.list_open()[0].trade_id == "trade-1"
        assert status.state == "DEGRADED"
        assert "telegram unavailable" in (status.last_error or "")
    finally:
        store.close()
