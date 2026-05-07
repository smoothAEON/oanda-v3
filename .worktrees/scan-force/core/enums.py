"""Shared enum contracts for the read-only trade-helper runtime."""

from __future__ import annotations

from enum import Enum


class StrEnum(str, Enum):
    """Python 3.10-compatible string enum base."""


class TradeState(StrEnum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"


class CloseReason(StrEnum):
    SL_HIT = "SL_HIT"
    TP_HIT = "TP_HIT"
    MANUAL = "MANUAL"


class AlertStatus(StrEnum):
    PENDING = "PENDING"
    FIRED = "FIRED"
    CANCELLED = "CANCELLED"


class TimeAlertKind(StrEnum):
    FIXED_TIME = "FIXED_TIME"
    SESSION = "SESSION"


class TimeAlertStatus(StrEnum):
    ACTIVE = "ACTIVE"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class IndicatorKind(StrEnum):
    RSI = "RSI"
    STOCH = "STOCH"
    MACD = "MACD"
    SMA_CROSS = "SMA_CROSS"


class PendingOrderType(StrEnum):
    LIMIT = "LIMIT"
    STOP = "STOP"
    MARKET_IF_TOUCHED = "MARKET_IF_TOUCHED"
    TAKE_PROFIT = "TAKE_PROFIT"
    STOP_LOSS = "STOP_LOSS"
    GUARANTEED_STOP_LOSS = "GUARANTEED_STOP_LOSS"


class ChartRenderStyle(StrEnum):
    CANDLESTICK = "candlestick"
    LINE = "line"


class ChartMode(StrEnum):
    COMPACT = "compact"
    BALANCED = "balanced"
    FULL = "full"


class RuntimeConfigKey(StrEnum):
    TOLERANCE = "tolerance"
    SPREAD = "spread"
    CHOP = "chop"
    CHART = "chart"
    CHART_MODE = "chart_mode"
    SCAN_INTERVAL = "scan_interval"
    TRADE_PUSH = "trade_push"
    SESSION_ALERTS = "session_alerts"


__all__ = [
    "AlertStatus",
    "ChartMode",
    "ChartRenderStyle",
    "CloseReason",
    "IndicatorKind",
    "PendingOrderType",
    "RuntimeConfigKey",
    "StrEnum",
    "TimeAlertKind",
    "TimeAlertStatus",
    "TradeState",
]
