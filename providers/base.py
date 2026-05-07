"""Analysis-layer market data contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

import pandas as pd


@dataclass(frozen=True)
class PriceSnapshot:
    """Typed current-price result for analysis-stage consumers."""

    instrument: str
    bid: float
    ask: float
    spread_price: float
    spread_pips: float
    fetched_at: datetime


@dataclass(frozen=True)
class CandleFreshness:
    """Metadata about the newest cached completed candle for a key."""

    instrument: str
    timeframe: str
    last_completed_candle: datetime | None
    fetched_at: datetime | None
    source: str | None
    candle_count: int
    is_fresh: bool
    staleness_seconds: float | None


class MarketDataProvider(Protocol):
    """Market data access only. No execution or account methods."""

    def get_candles(
        self,
        instrument: str,
        timeframe: str,
        count: int | None = None,
    ) -> pd.DataFrame:
        """Return canonical closed candles sorted oldest-first."""

    def get_current_price(self, instrument: str) -> PriceSnapshot:
        """Return current bid, ask, and spread in price and pips."""

    def get_candle_freshness(self, instrument: str, timeframe: str) -> CandleFreshness:
        """Return cached candle freshness metadata for a key."""

    def get_cached_candles(
        self,
        instrument: str,
        timeframe: str,
        count: int | None = None,
    ) -> pd.DataFrame | None:
        """Return cached candles only, never fetching from a live upstream."""


__all__ = [
    "CandleFreshness",
    "MarketDataProvider",
    "PriceSnapshot",
]
