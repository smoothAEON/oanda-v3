"""Pricing payload helpers for the local agent runtime."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class ResolvedPriceQuote:
    instrument: str
    bid: float
    ask: float
    spread_pips: float
    fetched_at: datetime
    source: str
    fallback_note: str | None = None


__all__ = ["ResolvedPriceQuote"]
