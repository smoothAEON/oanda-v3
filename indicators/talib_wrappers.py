"""TA-Lib indicator wrappers for Stage 07."""

from __future__ import annotations

from functools import lru_cache
import importlib
from typing import Any

import numpy as np
import pandas as pd

from core.models import IndicatorMetric

SUPPORTED_TALIB_WRAPPERS: tuple[str, ...] = (
    "ema",
    "sma",
    "sma_50",
    "sma_200",
    "tema",
    "kama",
    "sar",
    "rsi",
    "macd",
    "macd_signal",
    "macd_hist",
    "cci",
    "cmo",
    "ppo",
    "ppo_signal",
    "ppo_hist",
    "aroon_up",
    "aroon_down",
    "adxr",
    "atr",
    "natr",
    "trange",
    "adx",
    "bollinger_upper",
    "bollinger_middle",
    "bollinger_lower",
    "stoch_k",
    "stoch_d",
)

_PPO_SIGNAL_PERIOD = 9


@lru_cache(maxsize=1)
def _load_talib_module() -> Any:
    try:
        return importlib.import_module("talib")
    except Exception as exc:  # pragma: no cover - exercised through tests
        raise RuntimeError(
            "TA-Lib is required for Stage 07 indicator computation."
        ) from exc


def build_talib_metrics(candles: pd.DataFrame) -> tuple[IndicatorMetric, ...]:
    """Build the compact TA-Lib summary for the latest closed candle."""

    if candles.empty:
        return tuple(
            IndicatorMetric(name=name, value=None, source="talib")
            for name in SUPPORTED_TALIB_WRAPPERS
        )

    talib = _load_talib_module()

    close = candles["close"].to_numpy(dtype=np.float64)
    high = candles["high"].to_numpy(dtype=np.float64)
    low = candles["low"].to_numpy(dtype=np.float64)

    macd, macd_signal, macd_hist = talib.MACD(close)
    ppo = talib.PPO(close)
    ppo_signal = talib.EMA(ppo, timeperiod=_PPO_SIGNAL_PERIOD)
    ppo_hist = ppo - ppo_signal
    aroon_down, aroon_up = talib.AROON(high, low)
    bollinger_upper, bollinger_middle, bollinger_lower = talib.BBANDS(close)
    stoch_k, stoch_d = talib.STOCH(high, low, close)

    metric_values: dict[str, float | None] = {
        "ema": _coerce_scalar(talib.EMA(close)),
        "sma": _coerce_scalar(talib.SMA(close)),
        "sma_50": _coerce_scalar(talib.SMA(close, timeperiod=50)),
        "sma_200": _coerce_scalar(talib.SMA(close, timeperiod=200)),
        "tema": _coerce_scalar(talib.TEMA(close)),
        "kama": _coerce_scalar(talib.KAMA(close)),
        "sar": _coerce_scalar(talib.SAR(high, low)),
        "rsi": _coerce_scalar(talib.RSI(close)),
        "macd": _coerce_scalar(macd),
        "macd_signal": _coerce_scalar(macd_signal),
        "macd_hist": _coerce_scalar(macd_hist),
        "cci": _coerce_scalar(talib.CCI(high, low, close)),
        "cmo": _coerce_scalar(talib.CMO(close)),
        "ppo": _coerce_scalar(ppo),
        "ppo_signal": _coerce_scalar(ppo_signal),
        "ppo_hist": _coerce_scalar(ppo_hist),
        "aroon_up": _coerce_scalar(aroon_up),
        "aroon_down": _coerce_scalar(aroon_down),
        "adxr": _coerce_scalar(talib.ADXR(high, low, close)),
        "atr": _coerce_scalar(talib.ATR(high, low, close)),
        "natr": _coerce_scalar(talib.NATR(high, low, close)),
        "trange": _coerce_scalar(talib.TRANGE(high, low, close)),
        "adx": _coerce_scalar(talib.ADX(high, low, close)),
        "bollinger_upper": _coerce_scalar(bollinger_upper),
        "bollinger_middle": _coerce_scalar(bollinger_middle),
        "bollinger_lower": _coerce_scalar(bollinger_lower),
        "stoch_k": _coerce_scalar(stoch_k),
        "stoch_d": _coerce_scalar(stoch_d),
    }

    return tuple(
        IndicatorMetric(
            name=name,
            value=metric_values[name],
            source="talib",
        )
        for name in SUPPORTED_TALIB_WRAPPERS
    )


def _coerce_scalar(values: Any) -> float | None:
    array = np.asarray(values, dtype=np.float64)
    if array.size == 0:
        return None

    scalar = float(array[-1])
    if not np.isfinite(scalar):
        return None
    return scalar


__all__ = [
    "SUPPORTED_TALIB_WRAPPERS",
    "build_talib_metrics",
    "_load_talib_module",
]
