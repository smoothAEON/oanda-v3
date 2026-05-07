"""Unit tests for indicator alert default thresholds."""
from __future__ import annotations

import pytest

from alerts.defaults import INDICATOR_ALERT_DEFAULTS, get_default_threshold
from core.enums import IndicatorKind


def test_rsi_above_default():
    assert get_default_threshold(IndicatorKind.RSI, "above") == 70.0


def test_rsi_below_default():
    assert get_default_threshold(IndicatorKind.RSI, "below") == 30.0


def test_rsi_cross_up_default():
    assert get_default_threshold(IndicatorKind.RSI, "cross_up") is None


def test_rsi_cross_down_default():
    assert get_default_threshold(IndicatorKind.RSI, "cross_down") is None


def test_stoch_above_default():
    assert get_default_threshold(IndicatorKind.STOCH, "above") == 80.0


def test_stoch_below_default():
    assert get_default_threshold(IndicatorKind.STOCH, "below") == 20.0


def test_stoch_cross_up_default():
    assert get_default_threshold(IndicatorKind.STOCH, "cross_up") is None


def test_stoch_cross_down_default():
    assert get_default_threshold(IndicatorKind.STOCH, "cross_down") is None


def test_macd_above_default():
    assert get_default_threshold(IndicatorKind.MACD, "above") == 0.0


def test_macd_below_default():
    assert get_default_threshold(IndicatorKind.MACD, "below") == 0.0


def test_macd_cross_up_default():
    assert get_default_threshold(IndicatorKind.MACD, "cross_up") is None


def test_macd_cross_down_default():
    assert get_default_threshold(IndicatorKind.MACD, "cross_down") is None


def test_sma_cross_cross_up_default():
    assert get_default_threshold(IndicatorKind.SMA_CROSS, "cross_up") is None


def test_sma_cross_cross_down_default():
    assert get_default_threshold(IndicatorKind.SMA_CROSS, "cross_down") is None


def test_sma_cross_above_raises_key_error():
    with pytest.raises(KeyError):
        get_default_threshold(IndicatorKind.SMA_CROSS, "above")


def test_all_14_combinations_present():
    expected_count = 14  # 12 RSI/STOCH/MACD + 2 SMA_CROSS
    assert len(INDICATOR_ALERT_DEFAULTS) == expected_count


def test_unknown_combination_raises_key_error():
    with pytest.raises(KeyError):
        get_default_threshold(IndicatorKind.RSI, "invalid_condition")
