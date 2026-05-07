from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from collections.abc import AsyncGenerator as AsyncGeneratorABC
from pathlib import Path
from typing import get_args, get_origin, get_type_hints

import pytest

import providers.account_client as account_client_module
import providers.stream_client as stream_client_module
from config.settings import Settings, load_settings
from core.enums import PendingOrderType
from core.events import Heartbeat, PriceTick
from core.models import PendingOrder
from providers.account_client import OandaAccountClient
from providers.base import PriceSnapshot
from providers.stream_client import OandaStreamClient


def write_env_file(path: Path, **overrides: str) -> Path:
    values = {
        "OANDA_API_KEY": "api-key",
        "OANDA_ACCOUNT_ID": "account-id",
        "OANDA_ENVIRONMENT": "practice",
        "TELEGRAM_BOT_TOKEN": "telegram-token",
        "TELEGRAM_CHAT_ID": "123456789",
        "TELEGRAM_BOT_PASSWORD": "bot-password",
        "TELEGRAM_ADMIN_IDS": "111,222",
        "STREAM_INSTRUMENTS": "EUR_USD,SPX500_USD",
    }
    values.update(overrides)
    path.write_text(
        "\n".join(f"{key}={value}" for key, value in values.items()),
        encoding="utf-8",
    )
    return path


class StubAccountClient(OandaAccountClient):
    def __init__(
        self,
        *,
        settings: Settings,
        open_trades_payload: dict | None = None,
        open_orders_payload: dict | None = None,
        account_summary_payload: dict | None = None,
        trade_detail_payload: dict | None = None,
        transaction_detail_payload: dict | None = None,
        pricing_payload: dict | None = None,
        candles_payload: dict | None = None,
    ) -> None:
        super().__init__(settings=settings, api_client=object())
        self._open_trades_payload = open_trades_payload or {}
        self._open_orders_payload = open_orders_payload or {}
        self._account_summary_payload = account_summary_payload or {}
        self._trade_detail_payload = trade_detail_payload or {}
        self._transaction_detail_payload = transaction_detail_payload or {}
        self._pricing_payload = pricing_payload or {}
        self._candles_payload = candles_payload or {}

    def _request_open_trades_payload(self) -> dict:
        return self._open_trades_payload

    def _request_open_orders_payload(self) -> dict:
        return self._open_orders_payload

    def _request_account_summary_payload(self) -> dict:
        return self._account_summary_payload

    def _request_trade_detail_payload(self, trade_id: str) -> dict:
        return self._trade_detail_payload

    def _request_transaction_detail_payload(self, transaction_id: str) -> dict:
        if transaction_id in self._transaction_detail_payload:
            return self._transaction_detail_payload[transaction_id]
        return self._transaction_detail_payload

    def _request_pricing_payload(self, instrument: str) -> dict:
        return self._pricing_payload

    def _request_candles_payload(
        self,
        instrument: str,
        granularity: str,
        count: int,
        price_component: str = "M",
    ) -> dict:
        return self._candles_payload


class StubStreamClient(OandaStreamClient):
    def __init__(self, *, settings: Settings, payloads: list[dict]) -> None:
        super().__init__(settings=settings, api_client=object())
        self._payloads = payloads

    def _open_stream(self, instruments: tuple[str, ...]) -> tuple[object, object]:
        return object(), iter(self._payloads)


def build_settings(tmp_path: Path, **overrides: str) -> Settings:
    env_file = write_env_file(tmp_path / ".env", **overrides)
    return load_settings(env_file=env_file)


@pytest.mark.asyncio
async def test_account_client_normalizes_trade_payloads_and_uses_to_thread(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = build_settings(tmp_path)
    client = StubAccountClient(
        settings=settings,
        open_trades_payload={
            "trades": [
                {
                    "id": 12345678,
                    "instrument": "eurusd",
                    "price": "1.1000",
                    "currentUnits": "1000",
                    "openTime": "2026-03-20T10:15:00Z",
                    "stopLossOrder": {"price": "1.0900"},
                }
            ]
        },
    )

    calls: list[str] = []

    async def fake_to_thread(func, /, *args, **kwargs):
        calls.append(func.__name__)
        return func(*args, **kwargs)

    monkeypatch.setattr(account_client_module.asyncio, "to_thread", fake_to_thread)

    trades = await client.get_open_trades()

    assert calls == ["_request_open_trades_payload"]
    assert trades[0]["id"] == "12345678"
    assert trades[0]["instrument"] == "EUR_USD"
    assert trades[0]["currentUnits"] == 1000.0
    assert trades[0]["openTime"].tzinfo is not None
    assert trades[0]["stop_loss_price"] == pytest.approx(1.0900)


@pytest.mark.asyncio
async def test_account_client_returns_pending_orders(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = build_settings(tmp_path)
    client = StubAccountClient(
        settings=settings,
        open_orders_payload={
            "orders": [
                {
                    "id": 987654,
                    "instrument": "eurusd",
                    "units": "1000",
                    "price": "1.1050",
                    "type": "limit",
                    "state": "pending",
                    "createTime": "2026-03-20T10:20:00Z",
                    "timeInForce": "gtc",
                    "positionFill": "default",
                    "triggerCondition": "ask",
                    "tradeID": 12345678,
                    "stopLossOnFill": {"price": "1.0950"},
                    "takeProfitOnFill": {"price": "1.1200"},
                    "guaranteedStopLossOnFill": {"price": "1.0900"},
                }
            ]
        },
    )

    calls: list[str] = []

    async def fake_to_thread(func, /, *args, **kwargs):
        calls.append(func.__name__)
        return func(*args, **kwargs)

    monkeypatch.setattr(account_client_module.asyncio, "to_thread", fake_to_thread)

    orders = await client.get_open_orders()

    assert calls == ["_request_open_orders_payload"]
    assert len(orders) == 1
    order = orders[0]
    assert isinstance(order, PendingOrder)
    assert order.order_id == "987654"
    assert order.instrument == "EUR_USD"
    assert order.units == pytest.approx(1000.0)
    assert order.price == pytest.approx(1.1050)
    assert order.order_type == PendingOrderType.LIMIT
    assert order.state == "PENDING"
    assert order.time_in_force == "GTC"
    assert order.position_fill == "DEFAULT"
    assert order.trigger_condition == "ASK"
    assert order.trade_id == "12345678"
    assert order.stop_loss_price == pytest.approx(1.0950)
    assert order.take_profit_price == pytest.approx(1.1200)
    assert order.gslo_price == pytest.approx(1.0900)
    assert order.created_at.tzinfo is not None
    assert order.direction == "LONG"


@pytest.mark.asyncio
async def test_account_client_returns_typed_open_positions(tmp_path: Path) -> None:
    settings = build_settings(tmp_path)
    client = StubAccountClient(
        settings=settings,
        open_trades_payload={
            "trades": [
                {
                    "id": 12345678,
                    "instrument": "eurusd",
                    "price": "1.1000",
                    "currentUnits": "-1000",
                    "openTime": "2026-03-20T10:15:00Z",
                    "unrealizedPL": "12.5",
                }
            ]
        },
    )

    positions = await client.get_open_positions()

    assert len(positions) == 1
    assert positions[0].trade_id == "12345678"
    assert positions[0].instrument == "EUR_USD"
    assert positions[0].direction == "SHORT"
    assert positions[0].unrealized_pl == pytest.approx(12.5)
    assert positions[0].account_currency == "USD"


@pytest.mark.asyncio
async def test_account_client_open_positions_prefer_live_summary_currency(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = build_settings(tmp_path)
    client = StubAccountClient(
        settings=settings,
        open_trades_payload={
            "trades": [
                {
                    "id": 12345678,
                    "instrument": "eurusd",
                    "price": "1.1000",
                    "currentUnits": "1000",
                    "openTime": "2026-03-20T10:15:00Z",
                    "unrealizedPL": "12.5",
                }
            ]
        },
        account_summary_payload={
            "account": {
                "id": "account-id",
                "currency": "sgd",
                "balance": "1000.5",
                "NAV": "1010.5",
                "unrealizedPL": "10.0",
                "pl": "42.0",
                "marginUsed": "100.0",
                "marginAvailable": "900.5",
                "openTradeCount": 1,
                "openPositionCount": 1,
                "pendingOrderCount": 0,
            }
        },
    )

    calls: list[str] = []

    async def fake_to_thread(func, /, *args, **kwargs):
        calls.append(func.__name__)
        return func(*args, **kwargs)

    monkeypatch.setattr(account_client_module.asyncio, "to_thread", fake_to_thread)

    positions = await client.get_open_positions()

    assert calls == ["_request_open_trades_payload", "_request_account_summary_payload"]
    assert positions[0].account_currency == "SGD"


@pytest.mark.asyncio
async def test_account_client_returns_account_summary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = build_settings(tmp_path)
    client = StubAccountClient(
        settings=settings,
        account_summary_payload={
            "account": {
                "id": "account-id",
                "currency": "usd",
                "balance": "1000.5",
                "NAV": "1010.5",
                "unrealizedPL": "10.0",
                "pl": "42.0",
                "marginUsed": "100.0",
                "marginAvailable": "900.5",
                "openTradeCount": 2,
                "openPositionCount": 2,
                "pendingOrderCount": 1,
                "hedgingEnabled": False,
            }
        },
    )

    calls: list[str] = []

    async def fake_to_thread(func, /, *args, **kwargs):
        calls.append(func.__name__)
        return func(*args, **kwargs)

    monkeypatch.setattr(account_client_module.asyncio, "to_thread", fake_to_thread)

    summary = await client.get_account_summary()

    assert calls == ["_request_account_summary_payload"]
    assert summary.account_id == "account-id"
    assert summary.currency == "USD"
    assert summary.balance == pytest.approx(1000.5)
    assert summary.pending_order_count == 1


@pytest.mark.asyncio
async def test_account_client_returns_price_snapshot(tmp_path: Path) -> None:
    settings = build_settings(tmp_path)
    client = StubAccountClient(
        settings=settings,
        pricing_payload={
            "prices": [
                {
                    "time": "2026-03-20T10:15:00Z",
                    "bids": [{"price": "3050.50"}],
                    "asks": [{"price": "3051.00"}],
                }
            ]
        },
    )

    snapshot = await client.get_pricing("spx500usd")

    assert isinstance(snapshot, PriceSnapshot)
    assert snapshot.instrument == "SPX500_USD"
    assert snapshot.spread_pips == pytest.approx(0.5)
    assert snapshot.fetched_at.tzinfo is not None


@pytest.mark.asyncio
async def test_account_client_returns_normalized_transaction_detail(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = build_settings(tmp_path)
    client = StubAccountClient(
        settings=settings,
        transaction_detail_payload={
            "transaction": {
                "id": 7654321,
                "type": "ORDER_FILL",
                "reason": "TAKE_PROFIT_ORDER",
                "tradeID": 12345678,
                "instrument": "eurusd",
                "price": "1.1200",
                "units": "1000",
                "time": "2026-03-20T11:15:00Z",
            }
        },
    )

    calls: list[str] = []

    async def fake_to_thread(func, /, *args, **kwargs):
        calls.append(func.__name__)
        return func(*args, **kwargs)

    monkeypatch.setattr(account_client_module.asyncio, "to_thread", fake_to_thread)

    transaction = await client.get_transaction_detail("7654321")

    assert calls == ["_request_transaction_detail_payload"]
    assert transaction["id"] == "7654321"
    assert transaction["tradeID"] == "12345678"
    assert transaction["instrument"] == "EUR_USD"
    assert transaction["price"] == pytest.approx(1.1200)
    assert transaction["units"] == pytest.approx(1000.0)
    assert transaction["time"].tzinfo is not None


@pytest.mark.asyncio
async def test_account_client_returns_trade_transactions_from_trade_detail(tmp_path: Path) -> None:
    settings = build_settings(tmp_path)
    client = StubAccountClient(
        settings=settings,
        trade_detail_payload={
            "trade": {
                "id": 12345678,
                "instrument": "eurusd",
                "price": "1.1000",
                "currentUnits": "1000",
                "openTime": "2026-03-20T10:15:00Z",
                "closingTransactionIDs": ["9001", "9002"],
            }
        },
        transaction_detail_payload={
            "9001": {
                "transaction": {
                    "id": 9001,
                    "type": "ORDER_FILL",
                    "reason": "TAKE_PROFIT_ORDER",
                    "tradeID": 12345678,
                    "instrument": "eurusd",
                    "price": "1.1200",
                    "units": "1000",
                    "time": "2026-03-20T11:15:00Z",
                }
            },
            "9002": {
                "transaction": {
                    "id": 9002,
                    "type": "ORDER_FILL",
                    "reason": "CLIENT_ORDER",
                    "tradeID": 12345678,
                    "instrument": "eurusd",
                    "price": "1.1180",
                    "units": "1000",
                    "time": "2026-03-20T11:16:00Z",
                }
            },
        },
    )

    transactions = await client.get_trade_transactions("12345678")

    assert [item["id"] for item in transactions] == ["9001", "9002"]
    assert transactions[0]["reason"] == "TAKE_PROFIT_ORDER"


@pytest.mark.asyncio
async def test_account_client_returns_trimmed_closed_candles(tmp_path: Path) -> None:
    settings = build_settings(tmp_path)
    client = StubAccountClient(
        settings=settings,
        candles_payload={
            "candles": [
                {
                    "time": "2026-03-20T08:00:00Z",
                    "complete": True,
                    "volume": 100,
                    "mid": {"o": "1.10", "h": "1.20", "l": "1.00", "c": "1.15"},
                },
                {
                    "time": "2026-03-20T09:00:00Z",
                    "complete": True,
                    "volume": 101,
                    "mid": {"o": "1.15", "h": "1.25", "l": "1.05", "c": "1.20"},
                },
                {
                    "time": "2026-03-20T10:00:00Z",
                    "complete": False,
                    "volume": 10,
                    "mid": {"o": "1.20", "h": "1.30", "l": "1.10", "c": "1.22"},
                },
            ]
        },
    )

    candles = await client.get_candles("EUR/USD", "H1", 2)

    assert list(candles.columns) == ["time", "open", "high", "low", "close", "tick_volume"]
    assert len(candles) == 2
    assert candles["time"].iloc[-1].isoformat() == "2026-03-20T09:00:00+00:00"


@pytest.mark.asyncio
async def test_account_client_returns_bid_ask_export_candles(tmp_path: Path) -> None:
    settings = build_settings(tmp_path)
    client = StubAccountClient(
        settings=settings,
        candles_payload={
            "candles": [
                {
                    "time": "2026-03-20T08:00:00Z",
                    "complete": True,
                    "volume": 100,
                    "bid": {"o": "1.10", "h": "1.20", "l": "1.00", "c": "1.15"},
                    "ask": {"o": "1.11", "h": "1.21", "l": "1.01", "c": "1.16"},
                },
                {
                    "time": "2026-03-20T09:00:00Z",
                    "complete": True,
                    "volume": 101,
                    "bid": {"o": "1.15", "h": "1.25", "l": "1.05", "c": "1.20"},
                    "ask": {"o": "1.16", "h": "1.26", "l": "1.06", "c": "1.21"},
                },
            ]
        },
    )

    candles = await client.get_bid_ask_candles("EUR/USD", "H1", 2)

    assert list(candles.columns) == [
        "time",
        "bid_open",
        "bid_high",
        "bid_low",
        "bid_close",
        "ask_open",
        "ask_high",
        "ask_low",
        "ask_close",
        "tick_volume",
    ]
    assert candles["bid_close"].iloc[-1] == pytest.approx(1.20)
    assert candles["ask_close"].iloc[-1] == pytest.approx(1.21)


def test_account_client_candle_request_caps_count_and_adds_oanda_alignment(tmp_path: Path) -> None:
    settings = build_settings(tmp_path)
    captured: dict[str, object] = {}

    class CaptureEndpoint:
        def __init__(self, *, instrument: str, params: dict[str, object]) -> None:
            captured["instrument"] = instrument
            captured["params"] = params
            self.response: dict[str, object] = {"candles": []}

    class FakeApi:
        def request(self, _endpoint: object) -> None:
            return None

    class CaptureClient(OandaAccountClient):
        @staticmethod
        def _import_instruments_candles_endpoint() -> object:
            return CaptureEndpoint

    client = CaptureClient(settings=settings, api_client=FakeApi())

    client._request_candles_payload("EUR_USD", "H1", 5000)

    params = captured["params"]
    assert captured["instrument"] == "EUR_USD"
    assert params["granularity"] == "H1"
    assert params["price"] == "M"
    assert params["count"] == 5000
    assert params["dailyAlignment"] == 17
    assert params["alignmentTimezone"] == "America/New_York"
    assert params["weeklyAlignment"] == "Friday"


@pytest.mark.asyncio
async def test_account_client_returns_chunked_bid_ask_range_candles(tmp_path: Path) -> None:
    settings = build_settings(tmp_path)

    class RangeStubAccountClient(StubAccountClient):
        def __init__(self, *, settings: Settings, payloads: list[dict]) -> None:
            super().__init__(settings=settings)
            self._range_payloads = payloads
            self.range_calls: list[tuple[str, str, datetime, datetime, str]] = []

        def _request_candles_range_payload(
            self,
            instrument: str,
            granularity: str,
            start_utc: datetime,
            end_utc: datetime,
            price_component: str = "BA",
        ) -> dict:
            self.range_calls.append((instrument, granularity, start_utc, end_utc, price_component))
            return self._range_payloads.pop(0)

    client = RangeStubAccountClient(
        settings=settings,
        payloads=[
            {
                "candles": [
                    {
                        "time": "2026-03-20T08:00:00Z",
                        "complete": True,
                        "volume": 100,
                        "bid": {"o": "1.10", "h": "1.20", "l": "1.00", "c": "1.15"},
                        "ask": {"o": "1.11", "h": "1.21", "l": "1.01", "c": "1.16"},
                    },
                    {
                        "time": "2026-03-20T08:01:00Z",
                        "complete": True,
                        "volume": 101,
                        "bid": {"o": "1.15", "h": "1.25", "l": "1.05", "c": "1.20"},
                        "ask": {"o": "1.16", "h": "1.26", "l": "1.06", "c": "1.21"},
                    },
                ]
            },
            {
                "candles": [
                    {
                        "time": "2026-03-20T08:01:00Z",
                        "complete": True,
                        "volume": 101,
                        "bid": {"o": "1.15", "h": "1.25", "l": "1.05", "c": "1.20"},
                        "ask": {"o": "1.16", "h": "1.26", "l": "1.06", "c": "1.21"},
                    },
                    {
                        "time": "2026-03-20T08:02:00Z",
                        "complete": True,
                        "volume": 102,
                        "bid": {"o": "1.20", "h": "1.30", "l": "1.10", "c": "1.22"},
                        "ask": {"o": "1.21", "h": "1.31", "l": "1.11", "c": "1.23"},
                    },
                ]
            },
        ],
    )

    candles = await client.get_bid_ask_candles_range(
        "EUR/USD",
        "M1",
        datetime(2026, 3, 20, 8, 0, tzinfo=timezone.utc),
        datetime(2026, 3, 20, 8, 3, tzinfo=timezone.utc),
        max_count_per_request=2,
    )

    assert len(client.range_calls) == 2
    assert [timestamp.isoformat() for timestamp in candles["time"]] == [
        "2026-03-20T08:00:00+00:00",
        "2026-03-20T08:01:00+00:00",
        "2026-03-20T08:02:00+00:00",
    ]
    assert candles["bid_close"].iloc[-1] == pytest.approx(1.22)
    assert candles["ask_close"].iloc[-1] == pytest.approx(1.23)


@pytest.mark.asyncio
async def test_stream_client_yields_typed_events_and_uses_to_thread(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = build_settings(tmp_path)
    client = StubStreamClient(
        settings=settings,
        payloads=[
            {
                "type": "PRICE",
                "instrument": "eurusd",
                "bids": [{"price": "1.1000"}],
                "asks": [{"price": "1.1002"}],
                "closeoutBid": "1.0990",
                "closeoutAsk": "1.1012",
                "time": "2026-03-20T10:15:00Z",
            },
            {
                "type": "HEARTBEAT",
                "time": "2026-03-20T10:15:05Z",
            },
        ],
    )

    calls: list[str] = []

    async def fake_to_thread(func, /, *args, **kwargs):
        calls.append(func.__name__)
        return func(*args, **kwargs)

    monkeypatch.setattr(stream_client_module.asyncio, "to_thread", fake_to_thread)

    events = [event async for event in client.stream_prices(["eurusd", "EUR_USD"])]

    assert calls == ["_open_stream", "_next_payload", "_next_payload", "_next_payload", "_close_stream"]
    assert isinstance(events[0], PriceTick)
    assert events[0].instrument == "EUR_USD"
    assert events[0].bid == pytest.approx(1.1000)
    assert events[0].ask == pytest.approx(1.1002)
    assert events[0].mid == pytest.approx(1.1001)
    assert isinstance(events[1], Heartbeat)
    assert events[1].time.tzinfo is not None


def test_stream_client_prefers_top_of_book_prices_over_closeout_prices() -> None:
    event = OandaStreamClient._normalize_stream_payload(
        {
            "type": "PRICE",
            "instrument": "SPX500_USD",
            "bids": [{"price": "4780.685", "liquidity": 100}],
            "asks": [{"price": "4781.595", "liquidity": 100}],
            "closeoutBid": "4771.085",
            "closeoutAsk": "4791.195",
            "time": "2026-04-08T12:21:27.508586826Z",
        }
    )

    assert isinstance(event, PriceTick)
    assert event.bid == pytest.approx(4780.685)
    assert event.ask == pytest.approx(4781.595)


def test_stream_client_falls_back_to_closeout_prices_when_book_prices_missing() -> None:
    event = OandaStreamClient._normalize_stream_payload(
        {
            "type": "PRICE",
            "instrument": "EUR_USD",
            "closeoutBid": "1.1000",
            "closeoutAsk": "1.1002",
            "time": "2026-03-20T10:15:00Z",
        }
    )

    assert isinstance(event, PriceTick)
    assert event.bid == pytest.approx(1.1000)
    assert event.ask == pytest.approx(1.1002)


@pytest.mark.asyncio
async def test_stream_client_rejects_invalid_instrument(tmp_path: Path) -> None:
    settings = build_settings(tmp_path)
    client = StubStreamClient(settings=settings, payloads=[])

    with pytest.raises(KeyError, match="Unknown live OANDA instrument"):
        events = client.stream_prices(["ZZZ_YYY"])
        await anext(events)


@pytest.mark.asyncio
async def test_stream_client_waits_for_pending_next_before_close_on_cancellation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = build_settings(tmp_path)
    client = OandaStreamClient(settings=settings)
    events: list[str] = []
    failures: list[str] = []
    next_started = asyncio.Event()
    release_next = asyncio.Event()

    class DummyEndpoint:
        def terminate(self, _message: str) -> None:
            events.append("terminate")

    class DummyIterator:
        def __init__(self) -> None:
            self._returned = False

        def __iter__(self) -> DummyIterator:
            return self

        def __next__(self) -> dict[str, str]:
            if self._returned:
                raise StopIteration
            self._returned = True
            return {
                "type": "PRICE",
                "instrument": "EUR_USD",
                "closeoutBid": "1.1000",
                "closeoutAsk": "1.1002",
                "time": "2026-03-20T10:15:00Z",
            }

        def close(self) -> None:
            events.append("iterator.close")

    endpoint = DummyEndpoint()
    iterator = DummyIterator()

    def _open_stream(_instruments: tuple[str, ...]) -> tuple[DummyEndpoint, DummyIterator]:
        return endpoint, iterator

    async def fake_to_thread(func, /, *args, **kwargs):
        events.append(func.__name__)
        if func.__name__ == "_next_payload":
            next_started.set()
            await release_next.wait()
        return func(*args, **kwargs)

    monkeypatch.setattr(client, "_open_stream", _open_stream)
    monkeypatch.setattr(stream_client_module.asyncio, "to_thread", fake_to_thread)
    monkeypatch.setattr(
        stream_client_module,
        "log_failure",
        lambda _logger, event, _exc, **_fields: failures.append(event),
    )

    stream = client.stream_prices(["EUR_USD"])
    next_task = asyncio.create_task(anext(stream))

    await next_started.wait()
    next_task.cancel()
    release_next.set()

    with pytest.raises(asyncio.CancelledError):
        await next_task

    assert failures == []
    assert events == [
        "_open_stream",
        "_next_payload",
        "_close_stream",
        "terminate",
        "iterator.close",
    ]


def test_stream_client_suppresses_expected_close_teardown_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = build_settings(tmp_path)
    client = OandaStreamClient(settings=settings)
    failures: list[str] = []

    class StreamTerminated(Exception):
        pass

    class DummyEndpoint:
        def terminate(self, _message: str) -> None:
            raise StreamTerminated("normal close")

    class DummyIterator:
        def close(self) -> None:
            raise ValueError("generator already executing")

    monkeypatch.setattr(
        stream_client_module,
        "log_failure",
        lambda _logger, event, _exc, **_fields: failures.append(event),
    )

    client._close_stream(DummyEndpoint(), DummyIterator())

    assert failures == []


@pytest.mark.asyncio
async def test_account_client_rejects_missing_trade_timestamp(tmp_path: Path) -> None:
    settings = build_settings(tmp_path)
    client = StubAccountClient(
        settings=settings,
        open_trades_payload={
            "trades": [
                {
                    "id": 12345678,
                    "instrument": "eurusd",
                    "price": "1.1000",
                    "currentUnits": "1000",
                    "openTime": None,
                }
            ]
        },
    )

    with pytest.raises(ValueError, match="timestamp is missing"):
        await client.get_open_trades()


@pytest.mark.asyncio
async def test_account_client_rejects_missing_pricing_timestamp(tmp_path: Path) -> None:
    settings = build_settings(tmp_path)
    client = StubAccountClient(
        settings=settings,
        pricing_payload={
            "prices": [
                {
                    "time": None,
                    "bids": [{"price": "3050.50"}],
                    "asks": [{"price": "3051.00"}],
                }
            ]
        },
    )

    with pytest.raises(ValueError, match="timestamp is missing"):
        await client.get_pricing("spx500usd")


def test_stream_client_stream_prices_is_annotated_as_async_generator() -> None:
    return_type = get_type_hints(OandaStreamClient.stream_prices)["return"]

    assert get_origin(return_type) is AsyncGeneratorABC
    assert get_args(return_type)[1] is type(None)


def test_stream_client_rejects_missing_stream_timestamp() -> None:
    with pytest.raises(ValueError, match="timestamp is missing"):
        OandaStreamClient._normalize_stream_payload({"type": "HEARTBEAT", "time": None})
