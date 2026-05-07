from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from config.settings import Settings, load_settings
from core.enums import CloseReason
from core.models import TradeHistorySyncState
from data.persistence.trade_store import TradeStore
from journal.trade_history_service import TradeHistoryService
from journal.trade_normalizer import normalize_transactions
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


def build_settings(tmp_path: Path) -> Settings:
    return load_settings(
        env_file=write_env_file(tmp_path / ".env", tinydb_path=tmp_path / "history.json")
    )


class StubHistoryClient:
    def __init__(self) -> None:
        self.account_details = {
            "id": "account-id",
            "lastTransactionID": "100",
        }
        self.since_transactions: list[dict[str, object]] = []
        self.since_last_transaction_id = "100"
        self.window_transactions: list[dict[str, object]] = []
        self.since_calls: list[tuple[str, str]] = []
        self.window_calls: list[tuple[datetime, datetime, str]] = []

    def get_account_details_sync(self) -> dict[str, object]:
        return dict(self.account_details)

    def fetch_transactions_since_sync(
        self,
        last_transaction_id: str,
        type_filter: str,
    ) -> tuple[list[dict[str, object]], str]:
        self.since_calls.append((last_transaction_id, type_filter))
        return list(self.since_transactions), self.since_last_transaction_id

    def fetch_transactions_for_window_sync(
        self,
        start_utc: datetime,
        end_utc: datetime,
        type_filter: str,
    ) -> list[dict[str, object]]:
        self.window_calls.append((start_utc, end_utc, type_filter))
        return list(self.window_transactions)


def build_service(tmp_path: Path) -> tuple[TradeHistoryService, TradeStore, StubHistoryClient, Settings]:
    settings = build_settings(tmp_path)
    store = TradeStore(db_path=settings.tinydb_path, settings=settings)
    history_client = StubHistoryClient()
    service = TradeHistoryService(
        store=store,
        trade_repository=TradeRepository(store=store, settings=settings),
        history_client=history_client,
        settings=settings,
    )
    return service, store, history_client, settings


def sample_transactions() -> list[dict[str, object]]:
    return [
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
            "reason": "TAKE_PROFIT_ORDER",
            "time": "2026-04-01T02:00:00Z",
            "price": "3125.000",
            "units": "-10",
            "commission": "0.20",
            "tradesClosed": [
                {
                    "tradeID": "trade-1",
                    "units": "10",
                    "price": "3125.000",
                    "realizedPL": "5.00",
                    "financing": "-0.10",
                }
            ],
        },
        {
            "id": "103",
            "accountID": "account-id",
            "type": "ORDER_FILL",
            "instrument": "SPX500_USD",
            "orderID": "5003",
            "batchID": "6003",
            "reason": "MARKET_ORDER",
            "time": "2026-04-01T03:00:00Z",
            "price": "3128.110",
            "units": "-5",
            "commission": "0.10",
            "tradeReduced": {
                "tradeID": "trade-2",
                "units": "5",
                "price": "3128.110",
                "realizedPL": "2.00",
                "financing": "-0.05",
            },
        },
        {
            "id": "104",
            "accountID": "account-id",
            "type": "DAILY_FINANCING",
            "time": "2026-04-01T04:00:00Z",
            "positionFinancings": [
                {"instrument": "SPX500_USD", "financing": "-0.25"},
                {"instrument": "EUR_USD", "financing": "0.10"},
            ],
        },
        {
            "id": "105",
            "accountID": "account-id",
            "type": "MARKET_ORDER",
            "time": "2026-04-01T05:00:00Z",
        },
    ]


def sample_mit_transactions() -> list[dict[str, object]]:
    return [
        {
            "id": "401",
            "accountID": "account-id",
            "type": "ORDER_FILL",
            "instrument": "SPX500_USD",
            "orderID": "5401",
            "batchID": "6401",
            "reason": "MARKET_ORDER",
            "time": "2026-04-01T06:00:00Z",
            "price": "3123.456",
            "units": "10",
            "commission": "0.30",
            "tradeOpened": {"tradeID": "mit-trade", "units": "10", "price": "3123.456"},
        },
        {
            "id": "402",
            "accountID": "account-id",
            "type": "ORDER_FILL",
            "instrument": "SPX500_USD",
            "orderID": "5402",
            "batchID": "6402",
            "reason": "MARKET_ORDER",
            "time": "2026-04-01T07:00:00Z",
            "price": "3124.500",
            "units": "-10",
            "commission": "0.10",
            "orderCreateTransaction": {"type": "MARKET_IF_TOUCHED_ORDER"},
            "tradesClosed": [
                {
                    "tradeID": "mit-trade",
                    "units": "10",
                    "price": "3124.500",
                    "realizedPL": "5.00",
                    "financing": "0.00",
                }
            ],
        },
    ]


def seed_normalized_history(store: TradeStore, settings: Settings, transactions: list[dict[str, object]]) -> None:
    store.upsert_raw_transactions(transactions)
    store.upsert_trade_history_events(
        normalize_transactions(transactions, journal_timezone=settings.journal_timezone)
    )


def test_timezone_windows_convert_correctly_for_sgt(tmp_path: Path) -> None:
    service, store, _, _ = build_service(tmp_path)
    try:
        now_utc = datetime(2026, 4, 1, 6, 32, tzinfo=timezone.utc)

        day = service.resolve_period_window("day", tz_name="Asia/Singapore", now_utc=now_utc)
        week = service.resolve_period_window("week", tz_name="Asia/Singapore", now_utc=now_utc)
        month = service.resolve_period_window("month", tz_name="Asia/Singapore", now_utc=now_utc)

        assert day.start_utc.isoformat() == "2026-03-31T16:00:00+00:00"
        assert week.start_utc.isoformat() == "2026-03-29T16:00:00+00:00"
        assert month.start_utc.isoformat() == "2026-03-31T16:00:00+00:00"
    finally:
        store.close()


def test_compute_realized_pnl_uses_decimal_and_instrument_specific_financing(tmp_path: Path) -> None:
    service, store, _, settings = build_service(tmp_path)
    try:
        seed_normalized_history(store, settings, sample_transactions())

        summary = service.compute_realized_pnl("custom:2026-04-01:2026-04-01", instrument="SPX500_USD")

        assert str(summary.gross_realized_pl) == "7.00"
        assert str(summary.financing) == "-0.40"
        assert str(summary.commission) == "0.60"
        assert str(summary.net_realized_pl) == "6.00"
    finally:
        store.close()


def test_get_trade_history_filters_rows_and_paginates(tmp_path: Path) -> None:
    service, store, history_client, settings = build_service(tmp_path)
    try:
        transactions = sample_transactions()
        extra_open_events: list[dict[str, object]] = []
        for index in range(30):
            extra_open_events.append(
                {
                    "id": str(200 + index),
                    "accountID": "account-id",
                    "type": "ORDER_FILL",
                    "instrument": "SPX500_USD",
                    "orderID": str(7000 + index),
                    "batchID": str(8000 + index),
                    "reason": "MARKET_ORDER",
                    "time": f"2026-04-01T06:{index:02d}:00Z",
                    "price": "3130.000",
                    "units": "1",
                    "commission": "0.01",
                    "tradeOpened": {"tradeID": f"open-{index}", "units": "1", "price": "3130.000"},
                }
            )
        seed_normalized_history(store, settings, transactions + extra_open_events)
        store.upsert_trade_history_sync_state(
            TradeHistorySyncState(
                account_id="account-id",
                last_transaction_id="250",
                last_sync_utc=datetime(2026, 4, 1, 0, 0, tzinfo=timezone.utc),
            )
        )
        history_client.since_transactions = []

        page = service.get_trade_history("custom:2026-04-01:2026-04-01", "closed", "SPX500_USD", 1)
        opened_page = service.get_trade_history("custom:2026-04-01:2026-04-01", "opened", "SPX500_USD", 2)

        assert [row.event_type for row in page.rows] == ["PARTIAL_CLOSE", "CLOSE"]
        assert page.page_date_local == date(2026, 4, 1)
        assert page.page_date_summary is not None
        assert str(page.page_date_summary.net_realized_pl) == "5.70"
        assert all(row.event_type == "OPEN" for row in opened_page.rows)
        assert opened_page.page == 2
        assert opened_page.total_pages == 2
        assert opened_page.page_date_local == date(2026, 4, 1)
        assert opened_page.page_date_summary is not None
        assert all(row.event_type in {"OPEN", "CLOSE", "PARTIAL_CLOSE"} for row in opened_page.rows)
    finally:
        store.close()


def test_incremental_sync_fetches_only_newer_transactions_and_advances_watermark(tmp_path: Path) -> None:
    service, store, history_client, _ = build_service(tmp_path)
    try:
        initialized = service.incremental_sync()
        assert initialized["mode"] == "initialized"
        assert store.get_trade_history_sync_state("account-id").last_transaction_id == "100"

        history_client.since_transactions = sample_transactions()[:2]
        history_client.since_last_transaction_id = "102"

        synced = service.incremental_sync()

        assert history_client.since_calls == [("100", "ORDER_FILL,DAILY_FINANCING")]
        assert synced["inserted"] == 2
        assert store.get_trade_history_sync_state("account-id").last_transaction_id == "102"
    finally:
        store.close()


def test_backfill_rerun_does_not_duplicate_rows(tmp_path: Path) -> None:
    service, store, history_client, _ = build_service(tmp_path)
    try:
        history_client.window_transactions = sample_transactions()[:3]

        first = service.backfill_history(date(2026, 4, 1), date(2026, 4, 1))
        second = service.backfill_history(date(2026, 4, 1), date(2026, 4, 1))

        assert first["inserted"] > 0
        assert second["inserted"] == 0
        assert second["updated"] == 0
        assert len(store.list_trade_history_trade_events()) == 3
    finally:
        store.close()


def test_backfill_accepts_historical_oanda_instruments_outside_scan_registry(tmp_path: Path) -> None:
    service, store, history_client, _ = build_service(tmp_path)
    try:
        history_client.window_transactions = [
            {
                "id": "301",
                "accountID": "account-id",
                "type": "ORDER_FILL",
                "instrument": "SGD_JPY",
                "orderID": "9301",
                "batchID": "9401",
                "reason": "MARKET_ORDER",
                "time": "2026-04-01T01:00:00Z",
                "price": "92.100",
                "units": "100",
                "commission": "0.00",
                "tradeOpened": {"tradeID": "sgd-trade", "units": "100", "price": "92.100"},
            },
            {
                "id": "302",
                "accountID": "account-id",
                "type": "ORDER_FILL",
                "instrument": "SGD_JPY",
                "orderID": "9302",
                "batchID": "9402",
                "reason": "MARKET_ORDER",
                "time": "2026-04-01T02:00:00Z",
                "price": "92.350",
                "units": "-100",
                "commission": "0.00",
                "tradesClosed": [
                    {
                        "tradeID": "sgd-trade",
                        "units": "100",
                        "price": "92.350",
                        "realizedPL": "12.50",
                        "financing": "0.00",
                    }
                ],
            },
        ]

        result = service.backfill_history(date(2026, 4, 1), date(2026, 4, 1))
        projected = service.trade_repository.get("sgd-trade")

        assert result["inserted"] == 2
        assert projected is not None
        assert projected.instrument == "SGD_JPY"
        assert projected.pips == pytest.approx(25.0)
    finally:
        store.close()


def test_backfill_projects_market_if_touched_close_reason_from_raw_json(tmp_path: Path) -> None:
    service, store, history_client, _ = build_service(tmp_path)
    try:
        history_client.window_transactions = sample_mit_transactions()

        service.backfill_history(date(2026, 4, 1), date(2026, 4, 1))
        projected = service.trade_repository.get("mit-trade")

        assert projected is not None
        assert projected.close_reason == CloseReason.MIT
    finally:
        store.close()


def test_incremental_sync_repairs_existing_manual_close_to_mit_from_history(tmp_path: Path) -> None:
    service, store, history_client, _ = build_service(tmp_path)
    try:
        history_client.window_transactions = sample_mit_transactions()
        service.backfill_history(date(2026, 4, 1), date(2026, 4, 1))

        projected = service.trade_repository.get("mit-trade")
        assert projected is not None
        service.trade_repository.upsert(projected.model_copy(update={"close_reason": CloseReason.MANUAL}))

        history_client.since_transactions = []
        history_client.since_last_transaction_id = "402"

        service.incremental_sync()
        repaired = service.trade_repository.get("mit-trade")

        assert repaired is not None
        assert repaired.close_reason == CloseReason.MIT
    finally:
        store.close()


def test_get_trade_history_serves_stale_store_data_when_sync_fails(tmp_path: Path) -> None:
    service, store, history_client, settings = build_service(tmp_path)
    try:
        seed_normalized_history(store, settings, sample_transactions()[:2])
        store.upsert_trade_history_sync_state(
            TradeHistorySyncState(
                account_id="account-id",
                last_transaction_id="102",
                last_sync_utc=datetime(2026, 4, 1, 0, 0, tzinfo=timezone.utc),
            )
        )

        def failing_since(last_transaction_id: str, type_filter: str):
            raise RuntimeError("network down")

        history_client.fetch_transactions_since_sync = failing_since  # type: ignore[assignment]
        page = service.get_trade_history("custom:2026-04-01:2026-04-01", "all", "SPX500_USD", 1)

        assert page.stale_warning is not None
        assert "stored trade-history data" in page.stale_warning
        assert len(page.rows) == 2
    finally:
        store.close()


def test_get_trade_history_raises_when_sync_fails_and_store_is_empty(tmp_path: Path) -> None:
    service, store, history_client, _ = build_service(tmp_path)
    try:
        store.upsert_trade_history_sync_state(
            TradeHistorySyncState(
                account_id="account-id",
                last_transaction_id="100",
                last_sync_utc=datetime(2026, 4, 1, 0, 0, tzinfo=timezone.utc),
            )
        )

        def failing_since(last_transaction_id: str, type_filter: str):
            raise RuntimeError("network down")

        history_client.fetch_transactions_since_sync = failing_since  # type: ignore[assignment]

        with pytest.raises(RuntimeError, match="no stored data"):
            service.get_trade_history("custom:2026-04-01:2026-04-01", "all", None, 1)
    finally:
        store.close()


def test_trade_history_date_range_override_uses_custom_period_selector(tmp_path: Path) -> None:
    service, store, _, settings = build_service(tmp_path)
    try:
        seed_normalized_history(store, settings, sample_transactions())

        page = service.get_trade_history(
            "day",
            "all",
            "SPX500_USD",
            1,
            start_date="2026-04-01",
            end_date="2026-04-01",
        )

        assert page.period == "custom:2026-04-01:2026-04-01"
        assert str(page.summary.net_realized_pl) == "6.00"
    finally:
        store.close()


def test_trade_history_date_range_override_rejects_one_sided_dates(tmp_path: Path) -> None:
    service, store, _, _ = build_service(tmp_path)
    try:
        with pytest.raises(ValueError, match="provided together"):
            service.get_trade_history("day", "all", None, 1, start_date="2026-04-01")
    finally:
        store.close()
