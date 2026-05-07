"""pandas-ta wrapper functions for Stage 07 supplemental indicators."""

from __future__ import annotations

from functools import lru_cache
import importlib
from typing import Any

import numpy as np
import pandas as pd

from core.models import IndicatorMetric

SUPPORTED_PANDASTA_WRAPPERS: tuple[str, ...] = (
    "vwap_d",
    "squeeze_momentum",
    "ichimoku_tenkan",
    "ichimoku_kijun",
    "ichimoku_span_a",
    "ichimoku_span_b",
)
DEFERRED_PANDASTA_WRAPPERS: tuple[str, ...] = ("nadaraya_watson",)


@lru_cache(maxsize=1)
def _load_pandasta_module() -> Any:
    try:
        return importlib.import_module("pandas_ta")
    except Exception as exc:  # pragma: no cover - exercised through tests
        raise RuntimeError(
            "pandas-ta is required for Stage 07 supplemental indicator computation."
        ) from exc


def build_pandasta_metrics(candles: pd.DataFrame) -> tuple[IndicatorMetric, ...]:
    """Build the compact pandas-ta summary for the latest closed candle."""

    if candles.empty:
        return tuple(
            IndicatorMetric(name=name, value=None, source="pandas_ta")
            for name in SUPPORTED_PANDASTA_WRAPPERS
        )

    pandas_ta = _load_pandasta_module()
    indexed = _build_indexed_frame(candles)

    vwap = pandas_ta.vwap(
        indexed["high"],
        indexed["low"],
        indexed["close"],
        indexed["tick_volume"].rename("volume"),
    )
    squeeze_result = pandas_ta.squeeze(indexed["high"], indexed["low"], indexed["close"])
    ichimoku_result = pandas_ta.ichimoku(
        indexed["high"],
        indexed["low"],
        indexed["close"],
        lookahead=False,
    )
    squeeze = squeeze_result if isinstance(squeeze_result, pd.DataFrame) else pd.DataFrame()
    if (
        isinstance(ichimoku_result, tuple)
        and len(ichimoku_result) == 2
        and all(item is None or isinstance(item, pd.DataFrame) for item in ichimoku_result)
    ):
        ichimoku, spans = ichimoku_result
    else:
        ichimoku, spans = None, None

    squeeze_value_column = str(squeeze.columns[0]) if not squeeze.empty else None
    metrics = (
        IndicatorMetric(
            name="vwap_d",
            value=_coerce_series_scalar(vwap),
            source="pandas_ta",
        ),
        IndicatorMetric(
            name="squeeze_momentum",
            value=None
            if squeeze_value_column is None
            else _coerce_series_scalar(squeeze[squeeze_value_column]),
            signal=_resolve_squeeze_signal(squeeze),
            source="pandas_ta",
        ),
        IndicatorMetric(
            name="ichimoku_tenkan",
            value=_coerce_frame_scalar(ichimoku, "ITS_9"),
            source="pandas_ta",
        ),
        IndicatorMetric(
            name="ichimoku_kijun",
            value=_coerce_frame_scalar(ichimoku, "IKS_26"),
            source="pandas_ta",
        ),
        IndicatorMetric(
            name="ichimoku_span_a",
            value=_coerce_first_row_scalar(spans, "ISA_9"),
            source="pandas_ta",
        ),
        IndicatorMetric(
            name="ichimoku_span_b",
            value=_coerce_first_row_scalar(spans, "ISB_26"),
            source="pandas_ta",
        ),
    )
    return metrics


def _build_indexed_frame(candles: pd.DataFrame) -> pd.DataFrame:
    indexed = candles.copy(deep=True)
    indexed.index = pd.DatetimeIndex(indexed["time"], name="time")
    return indexed


def _resolve_squeeze_signal(frame: pd.DataFrame) -> str | None:
    if frame.empty:
        return None

    if _column_is_on(frame, "SQZ_ON"):
        return "ON"
    if _column_is_on(frame, "SQZ_OFF"):
        return "OFF"
    if _column_is_on(frame, "SQZ_NO"):
        return "NO_SQUEEZE"
    return None


def _column_is_on(frame: pd.DataFrame, column_name: str) -> bool:
    if column_name not in frame:
        return False

    value = _coerce_series_scalar(frame[column_name])
    return value == 1.0


def _coerce_frame_scalar(frame: pd.DataFrame | None, column_name: str) -> float | None:
    if frame is None or column_name not in frame:
        return None
    return _coerce_series_scalar(frame[column_name])


def _coerce_first_row_scalar(frame: pd.DataFrame | None, column_name: str) -> float | None:
    if frame is None or frame.empty or column_name not in frame:
        return None

    value = float(frame.iloc[0][column_name])
    if not np.isfinite(value):
        return None
    return value


def _coerce_series_scalar(series: pd.Series | None) -> float | None:
    if series is None or len(series) == 0:
        return None

    value = float(series.iloc[-1])
    if not np.isfinite(value):
        return None
    return value


__all__ = [
    "DEFERRED_PANDASTA_WRAPPERS",
    "SUPPORTED_PANDASTA_WRAPPERS",
    "build_pandasta_metrics",
    "_load_pandasta_module",
]
