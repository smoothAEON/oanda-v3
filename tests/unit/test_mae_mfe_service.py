from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

from core.enums import TradeState
from core.models import ExcursionSample, TradeRecord
from data.persistence.trade_store import TradeStore
from journal.excursion_repository import ExcursionRepository
from journal.mae_mfe_service import MaeMfeService


BASE_TIME = datetime(2026, 4, 9, 9, 30, 20, tzinfo=timezone.utc)


class StubAccountClient:
    def __init__(self, frame: pd.DataFrame) -> None:
        self.frame = frame
        self.calls: list[tuple[str, str, datetime, datetime]] = []

    async def get_bid_ask_candles_range(
        self,
        instrument: str,
        granularity: str,
        start_utc: datetime,
        end_utc: datetime,
    ) -> pd.DataFrame:
        self.calls.append((instrument, granularity, start_utc, end_utc))
        return self.frame.copy(deep=True)


def build_trade(*, trade_id: str, units: float, open_price: float, instrument: str = "BCO_USD") -> TradeRecord:
    return TradeRecord(
        trade_id=trade_id,
        instrument=instrument,
        units=units,
        open_price=open_price,
        close_price=None,
        sl_price=None,
        tp_price=None,
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
        notes=None,
    )


def build_bid_ask_frame(rows: list[dict[str, object]]) -> pd.DataFrame:
    frame = pd.DataFrame(rows)
    frame["time"] = pd.to_datetime(frame["time"], utc=True)
    return frame


def test_open_trade_summary_uses_m1_bid_ask_extremes_over_underreported_samples(tmp_path: Path) -> None:
    store = TradeStore(db_path=tmp_path / "mae_mfe_service.json")
    excursion_repository = ExcursionRepository(store=store)
    trade = build_trade(trade_id="5899", units=3.0, open_price=99.575)
    try:
        excursion_repository.insert(
            ExcursionSample(
                trade_id=trade.trade_id,
                sampled_at=BASE_TIME + timedelta(minutes=1),
                bid=99.575,
                ask=99.585,
                adverse_pips=0.0,
                favorable_pips=100.8,
            )
        )
        account_client = StubAccountClient(
            build_bid_ask_frame(
                [
                    {
                        "time": "2026-04-09T09:31:00Z",
                        "bid_open": 99.57,
                        "bid_high": 99.60,
                        "bid_low": 99.55,
                        "bid_close": 99.58,
                        "ask_open": 99.58,
                        "ask_high": 99.61,
                        "ask_low": 99.56,
                        "ask_close": 99.59,
                        "tick_volume": 100,
                    },
                    {
                        "time": "2026-04-09T09:32:00Z",
                        "bid_open": 99.58,
                        "bid_high": 99.62,
                        "bid_low": 99.412,
                        "bid_close": 99.50,
                        "ask_open": 99.59,
                        "ask_high": 99.63,
                        "ask_low": 99.422,
                        "ask_close": 99.51,
                        "tick_volume": 110,
                    },
                    {
                        "time": "2026-04-09T11:23:00Z",
                        "bid_open": 100.80,
                        "bid_high": 100.919,
                        "bid_low": 100.70,
                        "bid_close": 100.85,
                        "ask_open": 100.81,
                        "ask_high": 100.929,
                        "ask_low": 100.71,
                        "ask_close": 100.86,
                        "tick_volume": 120,
                    },
                ]
            )
        )
        service = MaeMfeService(excursion_repository=excursion_repository, account_client=account_client)

        summary = asyncio.run(service.summary_for_trade(trade))

        assert summary is not None
        assert summary["summary_source"] == "m1_bid_ask_replay"
        assert summary["sample_count"] == 1
        assert summary["mae_pips"] == pytest.approx(16.3)
        assert summary["mfe_pips"] == pytest.approx(134.4)
        assert summary["mae_price"] == 99.412
        assert summary["mfe_price"] == 100.919
        assert summary["mae_at"] == datetime(2026, 4, 9, 9, 32, tzinfo=timezone.utc)
        assert summary["mfe_at"] == datetime(2026, 4, 9, 11, 23, tzinfo=timezone.utc)
        assert account_client.calls
    finally:
        store.close()


def test_open_trade_summary_uses_ask_extremes_for_shorts(tmp_path: Path) -> None:
    store = TradeStore(db_path=tmp_path / "mae_mfe_service_short.json")
    excursion_repository = ExcursionRepository(store=store)
    trade = build_trade(
        trade_id="short-1",
        units=-0.5,
        open_price=6766.9,
        instrument="SPX500_USD",
    )
    try:
        account_client = StubAccountClient(
            build_bid_ask_frame(
                [
                    {
                        "time": "2026-04-09T09:31:00Z",
                        "bid_open": 6766.0,
                        "bid_high": 6767.0,
                        "bid_low": 6762.3,
                        "bid_close": 6764.1,
                        "ask_open": 6766.2,
                        "ask_high": 6770.5,
                        "ask_low": 6762.5,
                        "ask_close": 6764.3,
                        "tick_volume": 100,
                    },
                    {
                        "time": "2026-04-09T09:38:00Z",
                        "bid_open": 6761.7,
                        "bid_high": 6762.9,
                        "bid_low": 6761.4,
                        "bid_close": 6761.8,
                        "ask_open": 6761.9,
                        "ask_high": 6763.1,
                        "ask_low": 6762.3,
                        "ask_close": 6762.0,
                        "tick_volume": 120,
                    },
                ]
            )
        )
        service = MaeMfeService(excursion_repository=excursion_repository, account_client=account_client)

        summary = asyncio.run(service.summary_for_trade(trade))

        assert summary is not None
        assert summary["summary_source"] == "m1_bid_ask_replay"
        assert summary["mae_pips"] == pytest.approx(3.6)
        assert summary["mfe_pips"] == pytest.approx(4.6)
        assert summary["mae_price"] == 6770.5
        assert summary["mfe_price"] == 6762.3
    finally:
        store.close()


def test_open_trade_summary_falls_back_to_samples_when_no_closed_m1_candles_exist(tmp_path: Path) -> None:
    store = TradeStore(db_path=tmp_path / "mae_mfe_service_fallback.json")
    excursion_repository = ExcursionRepository(store=store)
    trade = build_trade(trade_id="young-1", units=3.0, open_price=99.575)
    try:
        excursion_repository.insert(
            ExcursionSample(
                trade_id=trade.trade_id,
                sampled_at=BASE_TIME + timedelta(seconds=20),
                bid=99.50,
                ask=99.51,
                adverse_pips=7.5,
                favorable_pips=0.0,
            )
        )
        service = MaeMfeService(
            excursion_repository=excursion_repository,
            account_client=StubAccountClient(pd.DataFrame()),
        )

        summary = asyncio.run(service.summary_for_trade(trade))

        assert summary is not None
        assert summary["summary_source"] == "tick_samples_fallback"
        assert summary["mae_pips"] == 7.5
        assert summary["mfe_pips"] == 0.0
    finally:
        store.close()
