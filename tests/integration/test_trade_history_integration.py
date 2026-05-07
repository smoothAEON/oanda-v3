from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from bot import bot as bot_module
from bot.security_manager import SecurityManager
from config.settings import load_settings
from data.persistence.trade_store import TradeStore
from journal.excursion_repository import ExcursionRepository
from journal.trade_history_service import TradeHistoryService
from journal.trade_repository import TradeRepository


def write_env_file(path: Path, *, tinydb_path: Path) -> Path:
    path.write_text(
        "\n".join(
            (
                "OANDA_API_KEY=api-key",
                "OANDA_ACCOUNT_ID=account-id",
                "OANDA_ENVIRONMENT=practice",
                "TELEGRAM_BOT_TOKEN=telegram-token",
                "TELEGRAM_CHAT_ID=123456789",
                "TELEGRAM_BOT_PASSWORD=bot-password",
                "TELEGRAM_ADMIN_IDS=111,222",
                f"TINYDB_PATH={tinydb_path.as_posix()}",
                "JOURNAL_TIMEZONE=Asia/Singapore",
            )
        ),
        encoding="utf-8",
    )
    return path


class RecorderMessage:
    def __init__(self) -> None:
        self.texts: list[str] = []

    async def reply_text(self, text: str, **kwargs) -> None:  # type: ignore[no-untyped-def]
        self.texts.append(text)


class StubHistoryClient:
    def __init__(self) -> None:
        self.window_transactions = [
            {
                "id": "101",
                "accountID": "account-id",
                "type": "ORDER_FILL",
                "instrument": "SPX500_USD",
                "orderID": "5001",
                "batchID": "6001",
                "reason": "MARKET_ORDER",
                "time": "2026-04-01T01:00:00Z",
                "price": "3123.456",
                "units": "10",
                "commission": "0.30",
                "tradeOpened": {"tradeID": "trade-1", "units": "10", "price": "3123.456"},
            },
            {
                "id": "102",
                "accountID": "account-id",
                "type": "ORDER_FILL",
                "instrument": "SPX500_USD",
                "orderID": "5002",
                "batchID": "6002",
                "reason": "MARKET_ORDER",
                "time": "2026-04-01T02:00:00Z",
                "price": "3126.000",
                "units": "-4",
                "commission": "0.10",
                "tradeReduced": {
                    "tradeID": "trade-1",
                    "units": "4",
                    "price": "3126.000",
                    "realizedPL": "2.00",
                    "financing": "-0.05",
                },
            },
            {
                "id": "103",
                "accountID": "account-id",
                "type": "ORDER_FILL",
                "instrument": "SPX500_USD",
                "orderID": "5003",
                "batchID": "6003",
                "reason": "TAKE_PROFIT_ORDER",
                "time": "2026-04-01T03:00:00Z",
                "price": "3128.000",
                "units": "-6",
                "commission": "0.20",
                "tradesClosed": [
                    {
                        "tradeID": "trade-1",
                        "units": "6",
                        "price": "3128.000",
                        "realizedPL": "5.00",
                        "financing": "-0.10",
                    }
                ],
            },
            {
                "id": "104",
                "accountID": "account-id",
                "type": "DAILY_FINANCING",
                "time": "2026-04-01T04:00:00Z",
                "positionFinancings": [{"instrument": "SPX500_USD", "financing": "-0.25"}],
            },
            {
                "id": "105",
                "accountID": "account-id",
                "type": "MARKET_ORDER",
                "time": "2026-04-01T04:30:00Z",
            },
        ]

    def fetch_transactions_for_window_sync(self, start_utc, end_utc, type_filter):
        return list(self.window_transactions)

    def get_account_details_sync(self):
        return {"id": "account-id", "lastTransactionID": "104"}

    def fetch_transactions_since_sync(self, last_transaction_id, type_filter):
        return [], str(last_transaction_id)


@pytest.mark.asyncio
async def test_trade_history_backfill_projects_journal_and_powers_tradehistory_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = load_settings(
        env_file=write_env_file(tmp_path / ".env", tinydb_path=tmp_path / "history.json")
    )
    store = TradeStore(db_path=settings.tinydb_path, settings=settings)
    security = SecurityManager(store=store, settings=settings)
    security.authenticate(
        user_id=111,
        chat_id=222,
        password="bot-password",
        username="tester",
        first_name="Tester",
    )
    trade_repository = TradeRepository(store=store, settings=settings)
    excursion_repository = ExcursionRepository(store=store)
    service = TradeHistoryService(
        store=store,
        trade_repository=trade_repository,
        history_client=StubHistoryClient(),
        settings=settings,
    )

    bot_data = {
        bot_module.SECURITY_MANAGER_KEY: security,
        bot_module.TRADE_REPOSITORY_KEY: trade_repository,
        bot_module.EXCURSION_REPOSITORY_KEY: excursion_repository,
        bot_module.TRADE_HISTORY_SERVICE_KEY: service,
        bot_module.SETTINGS_KEY: settings,
    }

    async def fake_to_thread(func, /, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(bot_module.asyncio, "to_thread", fake_to_thread)
    monkeypatch.setattr(bot_module, "_security_manager", lambda _context: security)

    try:
        result = service.backfill_history("2026-04-01", "2026-04-01")

        journal_update = SimpleNamespace(
            effective_message=RecorderMessage(),
            effective_user=SimpleNamespace(id=111, username="tester", first_name="Tester"),
            effective_chat=SimpleNamespace(id=222),
        )
        tradehistory_update = SimpleNamespace(
            effective_message=RecorderMessage(),
            effective_user=SimpleNamespace(id=111, username="tester", first_name="Tester"),
            effective_chat=SimpleNamespace(id=222),
        )

        await bot_module.journal_command(
            journal_update,
            SimpleNamespace(bot_data=bot_data, args=[]),
        )
        await bot_module.tradehistory_command(
            tradehistory_update,
            SimpleNamespace(
                bot_data=bot_data,
                args=["custom:2026-04-01:2026-04-01", "all", "SPX500_USD"],
            ),
        )

        assert result["inserted"] == 4
        assert [trade.trade_id for trade in trade_repository.list_closed()] == ["trade-1"]
        assert "trade-1" in journal_update.effective_message.texts[-1]

        output = tradehistory_update.effective_message.texts[-1]
        assert output.splitlines()[1] == "P&L (2026-04-01): +6.00"
        assert "OPEN" in output
        assert "PARTIAL_CLOSE" in output
        assert "CLOSE" in output
        assert "DAILY_FINANCING" not in output
        assert "MARKET_ORDER" not in output
        assert "Net Realized PnL: +6.00" in output
    finally:
        store.close()
