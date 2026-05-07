"""Shared alerting runtime constants."""

from __future__ import annotations

EVALUATED_INDICATOR_ALERT_TIMEFRAMES: tuple[str, str, str, str] = (
    "M15",
    "H1",
    "H4",
    "D",
)

__all__ = ["EVALUATED_INDICATOR_ALERT_TIMEFRAMES"]
