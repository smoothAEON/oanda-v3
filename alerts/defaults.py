"""Default threshold values for indicator alerts."""

from __future__ import annotations

from core.enums import IndicatorKind

INDICATOR_ALERT_DEFAULTS: dict[tuple[IndicatorKind, str], float | None] = {
    (IndicatorKind.RSI, "above"): 70.0,
    (IndicatorKind.RSI, "below"): 30.0,
    (IndicatorKind.RSI, "cross_up"): None,
    (IndicatorKind.RSI, "cross_down"): None,
    (IndicatorKind.STOCH, "above"): 80.0,
    (IndicatorKind.STOCH, "below"): 20.0,
    (IndicatorKind.STOCH, "cross_up"): None,
    (IndicatorKind.STOCH, "cross_down"): None,
    (IndicatorKind.MACD, "above"): 0.0,
    (IndicatorKind.MACD, "below"): 0.0,
    (IndicatorKind.MACD, "cross_up"): None,
    (IndicatorKind.MACD, "cross_down"): None,
    (IndicatorKind.SMA_CROSS, "cross_up"): None,
    (IndicatorKind.SMA_CROSS, "cross_down"): None,
}


def get_default_threshold(kind: IndicatorKind, condition: str) -> float | None:
    """Return the default threshold for a (kind, condition) pair.

    Raises KeyError for unknown combinations — guards against future
    IndicatorKind additions that haven't been added to the table.
    """
    return INDICATOR_ALERT_DEFAULTS[(kind, condition)]


__all__ = ["INDICATOR_ALERT_DEFAULTS", "get_default_threshold"]
