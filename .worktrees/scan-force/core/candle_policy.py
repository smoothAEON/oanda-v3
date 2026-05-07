"""Canonical candle schema and closed-bar policy."""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pandas as pd

CANONICAL_COLUMNS = ("time", "open", "high", "low", "close", "tick_volume")
_PRICE_COLUMNS = ("open", "high", "low", "close")
TIMEFRAME_DELTAS: dict[str, pd.Timedelta] = {
    "M1": pd.Timedelta(minutes=1),
    "M5": pd.Timedelta(minutes=5),
    "M15": pd.Timedelta(minutes=15),
    "M30": pd.Timedelta(minutes=30),
    "H1": pd.Timedelta(hours=1),
    "H4": pd.Timedelta(hours=4),
    "D": pd.Timedelta(days=1),
}
_TIMEFRAME_FLOOR_RULES: dict[str, str] = {
    "M1": "1min",
    "M5": "5min",
    "M15": "15min",
    "M30": "30min",
    "H1": "1h",
    "H4": "4h",
    "D": "1D",
}


def validate_candle_df(df: pd.DataFrame) -> pd.DataFrame:
    """Return a normalized copy of the candle frame in canonical order."""

    if not isinstance(df, pd.DataFrame):
        raise TypeError("validate_candle_df expects a pandas DataFrame.")

    normalized = df.copy(deep=True)

    if "time" not in normalized.columns and normalized.index.name == "time":
        normalized = normalized.reset_index()
    elif "time" in normalized.columns:
        normalized = normalized.reset_index(drop=True)
    else:
        normalized = normalized.reset_index(drop=True)

    if "volume" in normalized.columns:
        raise ValueError("Candle data must use 'tick_volume', not 'volume'.")

    missing_columns = [column for column in CANONICAL_COLUMNS if column not in normalized.columns]
    extra_columns = [column for column in normalized.columns if column not in CANONICAL_COLUMNS]

    if missing_columns:
        raise ValueError(f"Missing candle columns: {missing_columns}.")

    if extra_columns:
        raise ValueError(f"Unexpected candle columns: {extra_columns}.")

    normalized = normalized.loc[:, list(CANONICAL_COLUMNS)].copy()

    normalized["time"] = _coerce_time_column(normalized["time"])

    for column in _PRICE_COLUMNS:
        normalized[column] = _coerce_float_column(normalized[column], column)

    normalized["tick_volume"] = _coerce_tick_volume(normalized["tick_volume"])

    normalized = normalized.sort_values("time", kind="mergesort").reset_index(drop=True)
    return normalized


def trim_to_closed(df: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    """Return only bars that are closed as of the current UTC time."""

    validated = validate_candle_df(df)
    delta = get_timeframe_delta(timeframe)

    if validated.empty:
        return validated

    now_utc = pd.Timestamp(datetime.now(timezone.utc))
    close_times = validated["time"] + delta
    closed_mask = close_times <= now_utc

    return validated.loc[closed_mask].reset_index(drop=True)


def get_timeframe_delta(timeframe: str) -> pd.Timedelta:
    """Return the canonical duration for a supported timeframe."""

    if timeframe not in TIMEFRAME_DELTAS:
        raise ValueError(
            f"Unsupported timeframe '{timeframe}'. Supported values: {sorted(TIMEFRAME_DELTAS)}."
        )

    return TIMEFRAME_DELTAS[timeframe]


def floor_time_to_boundary(
    timestamp: datetime | pd.Timestamp,
    timeframe: str,
) -> pd.Timestamp:
    """Floor a UTC timestamp to the start of its candle boundary."""

    normalized = _normalize_utc_timestamp(timestamp)
    get_timeframe_delta(timeframe)
    return normalized.floor(_TIMEFRAME_FLOOR_RULES[timeframe])


def get_current_candle_start(
    timeframe: str,
    *,
    now_utc: datetime | pd.Timestamp | None = None,
) -> pd.Timestamp:
    """Return the active candle boundary start for a timeframe."""

    reference = (
        pd.Timestamp(datetime.now(timezone.utc))
        if now_utc is None
        else _normalize_utc_timestamp(now_utc)
    )
    return floor_time_to_boundary(reference, timeframe)


def get_last_completed_candle_start(
    timeframe: str,
    *,
    now_utc: datetime | pd.Timestamp | None = None,
) -> pd.Timestamp:
    """Return the most recent completed candle start for a timeframe."""

    current_start = get_current_candle_start(timeframe, now_utc=now_utc)
    return current_start - get_timeframe_delta(timeframe)


def calculate_candle_staleness_seconds(
    last_completed_candle: datetime | pd.Timestamp,
    timeframe: str,
    *,
    now_utc: datetime | pd.Timestamp | None = None,
) -> float:
    """Return how many seconds a cached candle lags the expected boundary."""

    expected = get_last_completed_candle_start(timeframe, now_utc=now_utc)
    observed = _normalize_utc_timestamp(last_completed_candle)
    if observed >= expected:
        return 0.0
    return float((expected - observed).total_seconds())


def _coerce_time_column(series: pd.Series) -> pd.Series:
    try:
        normalized = pd.to_datetime(series, utc=True, errors="raise")
    except (TypeError, ValueError) as exc:
        raise ValueError("Invalid values in candle 'time' column.") from exc

    if normalized.isna().any():
        raise ValueError("Candle 'time' column must not contain null values.")

    return pd.Series(normalized, name="time")


def _coerce_float_column(series: pd.Series, column_name: str) -> pd.Series:
    try:
        numeric = pd.to_numeric(series, errors="raise")
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Candle '{column_name}' column must be numeric.") from exc

    if numeric.isna().any():
        raise ValueError(f"Candle '{column_name}' column must not contain null values.")

    return numeric.astype(np.float64)


def _coerce_tick_volume(series: pd.Series) -> pd.Series:
    try:
        numeric = pd.to_numeric(series, errors="raise")
    except (TypeError, ValueError) as exc:
        raise ValueError("Candle 'tick_volume' column must be integer-like.") from exc

    if numeric.isna().any():
        raise ValueError("Candle 'tick_volume' column must not contain null values.")

    if not np.all(np.equal(numeric, np.floor(numeric))):
        raise ValueError("Candle 'tick_volume' column must be integer-like.")

    return numeric.astype(np.int64)


def _normalize_utc_timestamp(value: datetime | pd.Timestamp) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        return timestamp.tz_localize("UTC")
    return timestamp.tz_convert("UTC")


__all__ = [
    "CANONICAL_COLUMNS",
    "TIMEFRAME_DELTAS",
    "calculate_candle_staleness_seconds",
    "floor_time_to_boundary",
    "get_current_candle_start",
    "get_last_completed_candle_start",
    "get_timeframe_delta",
    "trim_to_closed",
    "validate_candle_df",
]
