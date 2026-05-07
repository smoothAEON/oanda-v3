"""Tick-volume indicators with explicit OTC caveats."""

from __future__ import annotations

from functools import lru_cache
import importlib
from typing import Any

import numpy as np
import pandas as pd

from core.models import TickVolumeIndicator

SUPPORTED_TICK_VOLUME_WRAPPERS: tuple[str, ...] = (
    "tick_obv",
    "tick_mfi",
    "tick_adosc",
)
VOLUME_TYPE = "tick_count"


@lru_cache(maxsize=1)
def _load_talib_module() -> Any:
    try:
        return importlib.import_module("talib")
    except Exception as exc:  # pragma: no cover - exercised through tests
        raise RuntimeError(
            "TA-Lib is required for Stage 07 tick-volume indicator computation."
        ) from exc


def build_tick_volume_metrics(candles: pd.DataFrame) -> tuple[TickVolumeIndicator, ...]:
    """Build tick-volume indicators for the latest closed candle."""

    if candles.empty:
        return ()

    talib = _load_talib_module()

    close = candles["close"].to_numpy(dtype=np.float64)
    high = candles["high"].to_numpy(dtype=np.float64)
    low = candles["low"].to_numpy(dtype=np.float64)
    tick_volume = candles["tick_volume"].to_numpy(dtype=np.float64)

    metric_values = (
        ("tick_obv", _coerce_scalar(talib.OBV(close, tick_volume))),
        ("tick_mfi", _coerce_scalar(talib.MFI(high, low, close, tick_volume))),
        ("tick_adosc", _coerce_scalar(talib.ADOSC(high, low, close, tick_volume))),
    )

    return tuple(
        TickVolumeIndicator(name=name, value=value)
        for name, value in metric_values
        if value is not None
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
    "SUPPORTED_TICK_VOLUME_WRAPPERS",
    "VOLUME_TYPE",
    "build_tick_volume_metrics",
    "_load_talib_module",
]
