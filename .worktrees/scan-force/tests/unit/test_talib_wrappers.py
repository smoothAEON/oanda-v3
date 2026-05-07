"""Unit tests for TA-Lib indicator wrappers."""
from __future__ import annotations

import math
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from indicators.talib_wrappers import SUPPORTED_TALIB_WRAPPERS, build_talib_metrics


def _make_candles(n: int = 300) -> pd.DataFrame:
    """Build a minimal n-bar candle frame with realistic OHLCV data."""
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    times = pd.date_range(base, periods=n, freq="h", tz="UTC")
    close = np.linspace(1800.0, 2000.0, n)
    return pd.DataFrame(
        {
            "time": times,
            "open": close - 1.0,
            "high": close + 2.0,
            "low": close - 2.0,
            "close": close,
            "tick_volume": np.ones(n, dtype=int) * 100,
        }
    )


def test_sma_50_in_supported_wrappers() -> None:
    assert "sma_50" in SUPPORTED_TALIB_WRAPPERS


def test_sma_200_in_supported_wrappers() -> None:
    assert "sma_200" in SUPPORTED_TALIB_WRAPPERS


def test_sma_50_value_is_finite_with_sufficient_candles() -> None:
    candles = _make_candles(300)
    metrics = {m.name: m for m in build_talib_metrics(candles)}
    assert "sma_50" in metrics
    assert metrics["sma_50"].value is not None
    assert math.isfinite(metrics["sma_50"].value)


def test_sma_200_value_is_finite_with_sufficient_candles() -> None:
    candles = _make_candles(300)
    metrics = {m.name: m for m in build_talib_metrics(candles)}
    assert "sma_200" in metrics
    assert metrics["sma_200"].value is not None
    assert math.isfinite(metrics["sma_200"].value)


def test_sma_200_returns_none_with_insufficient_candles() -> None:
    """Fewer than 200 candles → sma_200 metric value is None."""
    candles = _make_candles(100)
    metrics = {m.name: m for m in build_talib_metrics(candles)}
    assert metrics["sma_200"].value is None


def test_sma_50_returns_none_with_insufficient_candles() -> None:
    """Fewer than 50 candles → sma_50 metric value is None."""
    candles = _make_candles(30)
    metrics = {m.name: m for m in build_talib_metrics(candles)}
    assert metrics["sma_50"].value is None
