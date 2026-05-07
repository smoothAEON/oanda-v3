"""Internal event dataclasses for the read-only trade-helper runtime."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from core.enums import CloseReason
from core.instrument_registry import get_instrument_spec


def _to_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("Datetime fields must be timezone-aware.")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class PriceTick:
    """Typed price-stream tick used by the read-only runtime."""

    instrument: str
    bid: float
    ask: float
    time: datetime
    mid: float = field(init=False)

    def __post_init__(self) -> None:
        get_instrument_spec(self.instrument)
        if self.ask < self.bid:
            raise ValueError("ask must be greater than or equal to bid.")
        object.__setattr__(self, "time", _to_utc(self.time))
        object.__setattr__(self, "mid", (self.bid + self.ask) / 2.0)


@dataclass(frozen=True)
class Heartbeat:
    """Typed keepalive event from the price stream."""

    time: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "time", _to_utc(self.time))


@dataclass(frozen=True)
class TradeOpenedEvent:
    """Typed open-trade event emitted by a future poller path."""

    trade_id: str
    instrument: str
    units: float
    open_price: float
    sl: float | None
    tp: float | None
    gslo: float | None
    opened_at: datetime

    def __post_init__(self) -> None:
        get_instrument_spec(self.instrument)
        if self.units == 0:
            raise ValueError("units must be non-zero.")
        object.__setattr__(self, "opened_at", _to_utc(self.opened_at))


@dataclass(frozen=True)
class TradeClosedEvent:
    """Typed close-trade event emitted by a future poller path."""

    trade_id: str
    instrument: str
    units: float
    open_price: float
    close_price: float
    realized_pnl: float | None
    close_reason: CloseReason
    closed_at: datetime

    def __post_init__(self) -> None:
        get_instrument_spec(self.instrument)
        if self.units == 0:
            raise ValueError("units must be non-zero.")
        object.__setattr__(self, "closed_at", _to_utc(self.closed_at))


@dataclass(frozen=True)
class TradeModifiedEvent:
    """Typed trade-modification event emitted by a future poller path."""

    trade_id: str
    new_sl: float | None
    new_tp: float | None
    modified_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "modified_at", _to_utc(self.modified_at))


__all__ = [
    "Heartbeat",
    "PriceTick",
    "TradeClosedEvent",
    "TradeModifiedEvent",
    "TradeOpenedEvent",
]
