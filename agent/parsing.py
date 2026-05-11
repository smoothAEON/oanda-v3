"""Input normalization helpers for local MCP tools."""

from __future__ import annotations

from typing import Literal, cast

from core.candle_policy import get_timeframe_delta
from core.instrument_registry import SCAN_INSTRUMENTS, ensure_scan_instrument, normalize_instrument, validate_live_instrument

DEFAULT_COMMAND_INSTRUMENT = "SPX500_USD"
DEFAULT_COMMAND_TIMEFRAME = "H1"
SUPPORTED_COMMAND_TIMEFRAMES: tuple[str, ...] = ("M1", "M5", "M15", "M30", "H1", "H4", "D")
TRACKED_CALENDAR_CURRENCIES: tuple[str, ...] = (
    "AUD", "CAD", "CHF", "EUR", "GBP", "JPY", "NZD", "USD"
)
OrderBlockMitigationFilter = Literal["all", "mitigated", "unmitigated"]
ORDER_BLOCK_MITIGATION_FILTERS: tuple[OrderBlockMitigationFilter, ...] = (
    "all",
    "mitigated",
    "unmitigated",
)
ORDER_BLOCK_MITIGATION_ALIASES: dict[str, OrderBlockMitigationFilter] = {
    "all": "all",
    "mitigated": "mitigated",
    "miltigated": "mitigated",
    "unmitigated": "unmitigated",
    "unmiltigated": "unmitigated",
}

TIMEFRAME_ALIASES: dict[str, str] = {
    "m1": "M1",
    "1m": "M1",
    "m5": "M5",
    "5m": "M5",
    "m15": "M15",
    "15m": "M15",
    "m30": "M30",
    "30m": "M30",
    "h1": "H1",
    "1h": "H1",
    "h4": "H4",
    "4h": "H4",
    "d": "D",
    "1d": "D",
    "day": "D",
    "daily": "D",
    "w": "W",
    "1w": "W",
    "weekly": "W",
}


def normalize_command_instrument(
    value: str | None,
    *,
    default: str = DEFAULT_COMMAND_INSTRUMENT,
) -> str:
    raw = default if value is None else value
    try:
        return ensure_scan_instrument(raw)
    except KeyError as exc:
        raise ValueError(str(exc)) from exc


def normalize_broker_instrument(
    value: str | None,
    *,
    default: str = DEFAULT_COMMAND_INSTRUMENT,
) -> str:
    raw = default if value is None else value
    instrument = normalize_instrument(raw)
    try:
        return validate_live_instrument(instrument)
    except KeyError as exc:
        raise ValueError(str(exc)) from exc


def normalize_command_timeframe(
    value: str | None,
    *,
    default: str = DEFAULT_COMMAND_TIMEFRAME,
) -> str:
    raw = default if value is None else value
    normalized = TIMEFRAME_ALIASES.get(str(raw).strip().lower(), str(raw).strip().upper())
    if normalized not in SUPPORTED_COMMAND_TIMEFRAMES:
        raise ValueError(
            f"Unsupported timeframe '{normalized}'. Supported values: {sorted(SUPPORTED_COMMAND_TIMEFRAMES)}."
        )
    get_timeframe_delta(normalized)
    return normalized


def normalize_order_block_mitigation_status(
    value: str | None,
) -> OrderBlockMitigationFilter:
    raw = "all" if value is None else str(value).strip().lower()
    normalized = ORDER_BLOCK_MITIGATION_ALIASES.get(raw, raw)
    if normalized not in ORDER_BLOCK_MITIGATION_FILTERS:
        raise ValueError("Order-block mitigation filter must be all, mitigated, or unmitigated.")
    return cast(OrderBlockMitigationFilter, normalized)


__all__ = [
    "DEFAULT_COMMAND_INSTRUMENT",
    "DEFAULT_COMMAND_TIMEFRAME",
    "OrderBlockMitigationFilter",
    "TRACKED_CALENDAR_CURRENCIES",
    "normalize_broker_instrument",
    "normalize_command_instrument",
    "normalize_command_timeframe",
    "normalize_order_block_mitigation_status",
]
