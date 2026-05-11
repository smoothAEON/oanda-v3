from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from config.settings import load_settings
from data.persistence.trade_store import TradeStore
from journal.trade_history_service import TradeHistoryService
from journal.trade_repository import TradeRepository


def write_env_file(path: Path, *, tinydb_path: Path) -> Path:
    path.write_text(
        "\n".join(
            (
                "OANDA_API_KEY=api-key",
                "OANDA_ACCOUNT_ID=account-id",
                "OANDA_ENVIRONMENT=practice",
                f"TINYDB_PATH={tinydb_path.as_posix()}",
                "JOURNAL_TIMEZONE=Asia/Singapore",
            )
        ),
        encoding="utf-8",
    )
    return path


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


def test_trade_history_backfill_projects_journal_and_trade_history(tmp_path: Path) -> None:
    settings = load_settings(
        env_file=write_env_file(tmp_path / ".env", tinydb_path=tmp_path / "history.json")
    )
    store = TradeStore(db_path=settings.tinydb_path, settings=settings)
    trade_repository = TradeRepository(store=store, settings=settings)
    service = TradeHistoryService(
        store=store,
        trade_repository=trade_repository,
        history_client=StubHistoryClient(),
        settings=settings,
    )

    try:
        result = service.backfill_history("2026-04-01", "2026-04-01")
        history = service.get_trade_history(
            "custom:2026-04-01:2026-04-01",
            "all",
            "SPX500_USD",
            page=1,
        )

        assert result["inserted"] == 4
        assert [trade.trade_id for trade in trade_repository.list_closed()] == ["trade-1"]
        assert history.summary.net_realized_pl == Decimal("6.00")
        assert {row.event_type for row in history.rows} == {
            "OPEN",
            "PARTIAL_CLOSE",
            "CLOSE",
        }
    finally:
        store.close()
