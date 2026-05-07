"""Shared broker pricing resolution helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from core.instrument_registry import get_pip_size

DEFAULT_LIVE_PRICE_MAX_AGE_SECONDS = 30.0


@dataclass(frozen=True)
class ResolvedPriceQuote:
    instrument: str
    bid: float
    ask: float
    spread_pips: float
    fetched_at: datetime
    source: str
    fallback_note: str | None = None


async def resolve_price_quote(
    *,
    instrument: str,
    account_client: Any,
    stream_task: Any | None,
    prefer_live: bool = False,
    live_max_age_seconds: float = DEFAULT_LIVE_PRICE_MAX_AGE_SECONDS,
    on_resolved: Callable[[ResolvedPriceQuote], None] | None = None,
) -> ResolvedPriceQuote:
    """Resolve broker pricing with `/price` semantics."""

    fallback_note: str | None = None
    if prefer_live and stream_task is not None:
        latest_quote = getattr(stream_task, "latest_quote", None)
        if callable(latest_quote):
            tick = latest_quote(instrument, max_age_seconds=live_max_age_seconds)
            if tick is not None:
                pip_size = get_pip_size(instrument)
                quote = ResolvedPriceQuote(
                    instrument=instrument,
                    bid=tick.bid,
                    ask=tick.ask,
                    spread_pips=(tick.ask - tick.bid) / pip_size,
                    fetched_at=tick.time,
                    source="live_stream",
                )
                if on_resolved is not None:
                    on_resolved(quote)
                return quote
        fallback_note = "live stream unavailable or stale; REST pricing used"

    snapshot = await account_client.get_pricing(instrument)
    quote = ResolvedPriceQuote(
        instrument=instrument,
        bid=snapshot.bid,
        ask=snapshot.ask,
        spread_pips=snapshot.spread_pips,
        fetched_at=snapshot.fetched_at,
        source="rest_pricing",
        fallback_note=fallback_note,
    )
    if on_resolved is not None:
        on_resolved(quote)
    return quote


__all__ = [
    "DEFAULT_LIVE_PRICE_MAX_AGE_SECONDS",
    "ResolvedPriceQuote",
    "resolve_price_quote",
]
