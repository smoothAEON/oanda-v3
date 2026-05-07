"""Indicator package for Market Signal Bot V3."""

from __future__ import annotations

import pandas as pd

from core.candle_policy import trim_to_closed
from core.models import IndicatorValueSummary
from indicators.pandasta_wrappers import build_pandasta_metrics
from indicators.talib_wrappers import build_talib_metrics
from indicators.tick_volume import build_tick_volume_metrics
from indicators.vwap import (
    SUPPORTED_VWAP_TIMEFRAMES,
    build_vwap_read_result,
    normalize_vwap_anchor,
    normalize_vwap_bands,
    resolve_vwap_candle_count,
    validate_vwap_timeframe,
)


def build_indicator_summary(candles: pd.DataFrame, timeframe: str) -> IndicatorValueSummary:
    """Build the compact Stage 07 indicator summary for a timeframe."""

    closed = trim_to_closed(candles, timeframe)
    metrics = build_talib_metrics(closed) + build_pandasta_metrics(closed)
    tick_volume_metrics = build_tick_volume_metrics(closed)
    return IndicatorValueSummary(
        metrics=metrics,
        tick_volume_metrics=tick_volume_metrics,
    )


__all__ = [
    "SUPPORTED_VWAP_TIMEFRAMES",
    "build_indicator_summary",
    "build_vwap_read_result",
    "normalize_vwap_anchor",
    "normalize_vwap_bands",
    "resolve_vwap_candle_count",
    "validate_vwap_timeframe",
]
