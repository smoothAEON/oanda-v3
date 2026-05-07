from __future__ import annotations

from decimal import Decimal

from core.models import FinancingEvent, TradeHistoryEvent
from journal.trade_normalizer import normalize_transaction


BASE_TRANSACTION = {
    "id": "1001",
    "accountID": "account-id",
    "type": "ORDER_FILL",
    "instrument": "SPX500_USD",
    "orderID": "5001",
    "batchID": "6001",
    "reason": "MARKET_ORDER",
    "time": "2026-04-01T01:15:00Z",
    "price": "3123.456",
    "units": "40",
}


def test_order_fill_with_trade_opened_produces_open_only() -> None:
    transaction = {
        **BASE_TRANSACTION,
        "commission": "1.20",
        "tradeOpened": {
            "tradeID": "trade-1",
            "units": "40",
            "price": "3123.456",
        },
    }

    events = normalize_transaction(transaction, journal_timezone="Asia/Singapore")

    assert len(events) == 1
    assert isinstance(events[0], TradeHistoryEvent)
    assert events[0].event_type == "OPEN"
    assert events[0].trade_id == "trade-1"
    assert events[0].realized_pl == Decimal("0")
    assert events[0].net_realized_pl == Decimal("-1.20")


def test_order_fill_with_trades_closed_produces_one_close_per_leg() -> None:
    transaction = {
        **BASE_TRANSACTION,
        "id": "1002",
        "commission": "1.50",
        "tradesClosed": [
            {
                "tradeID": "trade-1",
                "units": "10",
                "price": "3124.100",
                "realizedPL": "5.25",
                "financing": "-0.10",
            },
            {
                "tradeID": "trade-2",
                "units": "30",
                "price": "3125.200",
                "realizedPL": "-2.00",
                "financing": "0.00",
            },
        ],
    }

    events = normalize_transaction(transaction, journal_timezone="Asia/Singapore")

    assert [event.event_type for event in events] == ["CLOSE", "CLOSE"]
    assert [event.trade_id for event in events] == ["trade-1", "trade-2"]
    assert sum(event.realized_pl for event in events) == Decimal("3.25")
    assert sum(event.financing for event in events) == Decimal("-0.10")
    assert sum(event.commission for event in events) == Decimal("1.50")


def test_order_fill_with_trade_reduced_produces_partial_close() -> None:
    transaction = {
        **BASE_TRANSACTION,
        "id": "1003",
        "commission": "0.25",
        "tradeReduced": {
            "tradeID": "trade-3",
            "units": "20",
            "price": "3128.110",
            "realizedPL": "12.00",
            "financing": "-0.05",
        },
    }

    events = normalize_transaction(transaction, journal_timezone="Asia/Singapore")

    assert len(events) == 1
    assert events[0].event_type == "PARTIAL_CLOSE"
    assert events[0].realized_pl == Decimal("12.00")
    assert events[0].financing == Decimal("-0.05")
    assert events[0].net_realized_pl == Decimal("11.70")


def test_order_fill_with_open_and_close_emits_both_rows() -> None:
    transaction = {
        **BASE_TRANSACTION,
        "id": "1004",
        "commission": "0.90",
        "tradeOpened": {
            "tradeID": "trade-4",
            "units": "20",
            "price": "3123.456",
        },
        "tradesClosed": [
            {
                "tradeID": "trade-5",
                "units": "20",
                "price": "3120.500",
                "realizedPL": "-20.10",
                "financing": "0.00",
            }
        ],
    }

    events = normalize_transaction(transaction, journal_timezone="Asia/Singapore")

    assert [event.event_type for event in events] == ["OPEN", "CLOSE"]
    assert events[0].trade_id == "trade-4"
    assert events[1].trade_id == "trade-5"
    assert sum(event.commission for event in events) == Decimal("0.90")


def test_daily_financing_affects_pnl_but_not_trade_rows() -> None:
    transaction = {
        "id": "2001",
        "accountID": "account-id",
        "type": "DAILY_FINANCING",
        "time": "2026-04-01T21:00:00Z",
        "positionFinancings": [
            {"instrument": "SPX500_USD", "financing": "-1.20"},
            {"instrument": "EUR_USD", "financing": "0.15"},
        ],
    }

    events = normalize_transaction(transaction, journal_timezone="Asia/Singapore")

    assert all(isinstance(event, FinancingEvent) for event in events)
    assert [event.instrument for event in events] == ["SPX500_USD", "EUR_USD"]
    assert sum(event.financing for event in events) == Decimal("-1.05")


def test_commission_allocation_sums_exactly_to_transaction_commission() -> None:
    transaction = {
        **BASE_TRANSACTION,
        "id": "1005",
        "commission": "0.05",
        "tradeOpened": {
            "tradeID": "trade-6",
            "units": "1",
            "price": "3123.456",
        },
        "tradesClosed": [
            {
                "tradeID": "trade-7",
                "units": "1",
                "price": "3123.500",
                "realizedPL": "0.01",
                "financing": "0.00",
            },
            {
                "tradeID": "trade-8",
                "units": "1",
                "price": "3123.600",
                "realizedPL": "0.02",
                "financing": "0.00",
            },
        ],
    }

    events = normalize_transaction(transaction, journal_timezone="Asia/Singapore")

    assert len(events) == 3
    assert sum(event.commission for event in events) == Decimal("0.05")
