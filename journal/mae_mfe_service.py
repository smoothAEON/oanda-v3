"""Read-time MAE/MFE resolution for trade-helper surfaces."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from core.enums import TradeState
from core.instrument_registry import get_pip_size
from core.logging_setup import get_logger, log_failure
from core.models import ExcursionSample, TradeRecord
from journal.excursion_repository import ExcursionRepository

_BID_ASK_COLUMNS = [
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


class MaeMfeService:
    """Resolve reported MAE/MFE using candle replay for open trades."""

    def __init__(
        self,
        *,
        excursion_repository: ExcursionRepository,
        account_client: Any,
    ) -> None:
        self.excursion_repository = excursion_repository
        self.account_client = account_client
        self.logger = get_logger(__name__)

    async def summary_for_trade(
        self,
        trade: TradeRecord,
        *,
        samples: list[ExcursionSample] | None = None,
    ) -> dict[str, Any] | None:
        resolved_samples = (
            samples
            if samples is not None
            else await asyncio.to_thread(self._samples_for_trade, trade.trade_id)
        )
        if trade.state != TradeState.OPEN:
            return self._summary_from_samples(trade, resolved_samples) or self._stored_summary_for_trade(trade.trade_id)
        summary_map = await self.summary_map_for_open_trades([trade], sample_map={trade.trade_id: resolved_samples})
        return summary_map.get(trade.trade_id)

    async def summary_map_for_open_trades(
        self,
        trades: list[TradeRecord],
        *,
        sample_map: dict[str, list[ExcursionSample]] | None = None,
    ) -> dict[str, dict[str, Any] | None]:
        if not trades:
            return {}

        resolved_sample_map = (
            sample_map
            if sample_map is not None
            else await asyncio.to_thread(self._sample_map_for_trades, trades)
        )
        summaries: dict[str, dict[str, Any] | None] = {}
        grouped: dict[str, list[TradeRecord]] = {}
        for trade in trades:
            grouped.setdefault(trade.instrument, []).append(trade)

        now = datetime.now(timezone.utc)
        for instrument, instrument_trades in grouped.items():
            frame = await self._load_bid_ask_frame(
                instrument=instrument,
                start_utc=min(trade.opened_at for trade in instrument_trades),
                end_utc=now,
            )
            for trade in instrument_trades:
                samples = resolved_sample_map.get(trade.trade_id, [])
                replay = self._summary_from_frame(
                    trade,
                    self._slice_trade_frame(frame, trade.opened_at),
                    sample_count=len(samples),
                    last_sampled_at=None if not samples else samples[-1].sampled_at,
                )
                summaries[trade.trade_id] = (
                    replay
                    or self._summary_from_samples(trade, samples)
                    or self._stored_summary_for_trade(trade.trade_id)
                )
        return summaries

    def _sample_map_for_trades(self, trades: list[TradeRecord]) -> dict[str, list[ExcursionSample]]:
        return {
            trade.trade_id: self._samples_for_trade(trade.trade_id)
            for trade in trades
        }

    def _samples_for_trade(self, trade_id: str) -> list[ExcursionSample]:
        loader = getattr(self.excursion_repository, "list_for_trade", None)
        if not callable(loader):
            return []
        return loader(trade_id)

    async def _load_bid_ask_frame(
        self,
        *,
        instrument: str,
        start_utc: datetime,
        end_utc: datetime,
    ) -> pd.DataFrame:
        loader = getattr(self.account_client, "get_bid_ask_candles_range", None)
        if not callable(loader):
            return self._empty_bid_ask_frame()

        try:
            return await loader(instrument, "M1", start_utc, end_utc)
        except Exception as exc:
            log_failure(
                self.logger,
                "mae_mfe_replay_fetch_failed",
                exc,
                level="warning",
                instrument=instrument,
                start_utc=start_utc,
                end_utc=end_utc,
            )
            return self._empty_bid_ask_frame()

    @staticmethod
    def _slice_trade_frame(frame: pd.DataFrame, opened_at: datetime) -> pd.DataFrame:
        if frame.empty:
            return frame
        opened_minute = opened_at.astimezone(timezone.utc).replace(second=0, microsecond=0)
        return frame.loc[frame["time"] >= opened_minute].reset_index(drop=True)

    def _summary_from_frame(
        self,
        trade: TradeRecord,
        frame: pd.DataFrame,
        *,
        sample_count: int,
        last_sampled_at: datetime | None,
    ) -> dict[str, Any] | None:
        if frame.empty:
            return None

        pip_size = get_pip_size(trade.instrument)
        if trade.units > 0:
            mae_idx = int(frame["bid_low"].idxmin())
            mfe_idx = int(frame["bid_high"].idxmax())
            mae_row = frame.loc[mae_idx]
            mfe_row = frame.loc[mfe_idx]
            mae_price = float(mae_row["bid_low"])
            mfe_price = float(mfe_row["bid_high"])
            mae_pips = max(0.0, (trade.open_price - mae_price) / pip_size)
            mfe_pips = max(0.0, (mfe_price - trade.open_price) / pip_size)
        else:
            mae_idx = int(frame["ask_high"].idxmax())
            mfe_idx = int(frame["ask_low"].idxmin())
            mae_row = frame.loc[mae_idx]
            mfe_row = frame.loc[mfe_idx]
            mae_price = float(mae_row["ask_high"])
            mfe_price = float(mfe_row["ask_low"])
            mae_pips = max(0.0, (mae_price - trade.open_price) / pip_size)
            mfe_pips = max(0.0, (trade.open_price - mfe_price) / pip_size)

        return {
            "trade_id": trade.trade_id,
            "sample_count": sample_count,
            "mae_pips": mae_pips,
            "mfe_pips": mfe_pips,
            "mae_price": mae_price,
            "mfe_price": mfe_price,
            "mae_at": self._frame_time(mae_row["time"]),
            "mfe_at": self._frame_time(mfe_row["time"]),
            "last_sampled_at": last_sampled_at,
            "summary_source": "m1_bid_ask_replay",
        }

    def _summary_from_samples(
        self,
        trade: TradeRecord,
        samples: list[ExcursionSample],
    ) -> dict[str, Any] | None:
        if not samples:
            return None

        mae_sample = max(samples, key=lambda sample: sample.adverse_pips)
        mfe_sample = max(samples, key=lambda sample: sample.favorable_pips)
        if trade.units > 0:
            mae_price = mae_sample.bid
            mfe_price = mfe_sample.bid
        else:
            mae_price = mae_sample.ask
            mfe_price = mfe_sample.ask

        return {
            "trade_id": trade.trade_id,
            "sample_count": len(samples),
            "mae_pips": mae_sample.adverse_pips,
            "mfe_pips": mfe_sample.favorable_pips,
            "mae_price": mae_price,
            "mfe_price": mfe_price,
            "mae_at": mae_sample.sampled_at,
            "mfe_at": mfe_sample.sampled_at,
            "last_sampled_at": samples[-1].sampled_at,
            "summary_source": "tick_samples_fallback",
        }

    def _stored_summary_for_trade(self, trade_id: str) -> dict[str, Any] | None:
        loader = getattr(self.excursion_repository, "get_mae_mfe", None)
        if not callable(loader):
            return None
        summary = loader(trade_id)
        if summary is None:
            return None
        resolved = dict(summary)
        resolved.setdefault("summary_source", "tick_samples_fallback")
        return resolved

    @staticmethod
    def _empty_bid_ask_frame() -> pd.DataFrame:
        return pd.DataFrame(columns=_BID_ASK_COLUMNS)

    @staticmethod
    def _frame_time(value: object) -> datetime:
        if isinstance(value, pd.Timestamp):
            return value.to_pydatetime()
        if isinstance(value, datetime):
            return value.astimezone(timezone.utc)
        raise TypeError(f"Unsupported candle timestamp: {value!r}")


__all__ = ["MaeMfeService"]
