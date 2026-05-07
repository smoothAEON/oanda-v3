"""Canonical candle schema and closed-bar policy."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

CANONICAL_COLUMNS = ("time", "open", "high", "low", "close", "tick_volume")
_PRICE_COLUMNS = ("open", "high", "low", "close")
OANDA_ALIGNMENT_TIMEZONE = "America/New_York"
OANDA_DAILY_ALIGNMENT_HOUR = 17
OANDA_WEEKLY_ALIGNMENT = "Friday"
OANDA_MAX_CANDLE_COUNT = 5000
OANDA_CANDLE_GRANULARITIES: tuple[str, ...] = (
    "S5",
    "S10",
    "S15",
    "S30",
    "M1",
    "M2",
    "M4",
    "M5",
    "M10",
    "M15",
    "M30",
    "H1",
    "H2",
    "H3",
    "H4",
    "H6",
    "H8",
    "H12",
    "D",
    "W",
)
TIMEFRAME_DELTAS: dict[str, pd.Timedelta] = {
    "S5": pd.Timedelta(seconds=5),
    "S10": pd.Timedelta(seconds=10),
    "S15": pd.Timedelta(seconds=15),
    "S30": pd.Timedelta(seconds=30),
    "M1": pd.Timedelta(minutes=1),
    "M2": pd.Timedelta(minutes=2),
    "M4": pd.Timedelta(minutes=4),
    "M5": pd.Timedelta(minutes=5),
    "M10": pd.Timedelta(minutes=10),
    "M15": pd.Timedelta(minutes=15),
    "M30": pd.Timedelta(minutes=30),
    "H1": pd.Timedelta(hours=1),
    "H2": pd.Timedelta(hours=2),
    "H3": pd.Timedelta(hours=3),
    "H4": pd.Timedelta(hours=4),
    "H6": pd.Timedelta(hours=6),
    "H8": pd.Timedelta(hours=8),
    "H12": pd.Timedelta(hours=12),
    "D": pd.Timedelta(days=1),
    "W": pd.Timedelta(weeks=1),
}
_TIMEFRAME_FLOOR_RULES: dict[str, str] = {
    "S5": "5s",
    "S10": "10s",
    "S15": "15s",
    "S30": "30s",
    "M1": "1min",
    "M2": "2min",
    "M4": "4min",
    "M5": "5min",
    "M10": "10min",
    "M15": "15min",
    "M30": "30min",
    "H1": "1h",
}
_OANDA_DAY_ALIGNED_TIMEFRAMES = frozenset({"H2", "H3", "H4", "H6", "H8", "H12", "D"})
_OANDA_ALIGNMENT_TZ = ZoneInfo(OANDA_ALIGNMENT_TIMEZONE)
_OANDA_WEEKDAY_INDEX = {
    "Monday": 0,
    "Tuesday": 1,
    "Wednesday": 2,
    "Thursday": 3,
    "Friday": 4,
    "Saturday": 5,
    "Sunday": 6,
}
_RAW_GRANULARITY_ALIASES: dict[str, str] = {
    "5s": "S5",
    "s5": "S5",
    "10s": "S10",
    "s10": "S10",
    "15s": "S15",
    "s15": "S15",
    "30s": "S30",
    "s30": "S30",
    "1m": "M1",
    "m1": "M1",
    "2m": "M2",
    "m2": "M2",
    "4m": "M4",
    "m4": "M4",
    "5m": "M5",
    "m5": "M5",
    "10m": "M10",
    "m10": "M10",
    "15m": "M15",
    "m15": "M15",
    "30m": "M30",
    "m30": "M30",
    "1h": "H1",
    "h1": "H1",
    "2h": "H2",
    "h2": "H2",
    "3h": "H3",
    "h3": "H3",
    "4h": "H4",
    "h4": "H4",
    "6h": "H6",
    "h6": "H6",
    "8h": "H8",
    "h8": "H8",
    "12h": "H12",
    "h12": "H12",
    "1d": "D",
    "d": "D",
    "day": "D",
    "daily": "D",
    "1w": "W",
    "w": "W",
    "week": "W",
    "weekly": "W",
}
OANDA_CANDLE_ALIGNMENT_PARAMS: dict[str, str | int] = {
    "dailyAlignment": OANDA_DAILY_ALIGNMENT_HOUR,
    "alignmentTimezone": OANDA_ALIGNMENT_TIMEZONE,
    "weeklyAlignment": OANDA_WEEKLY_ALIGNMENT,
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


def normalize_oanda_candle_granularity(
    value: str | None,
    *,
    default: str = "H1",
) -> str:
    """Normalize one raw OANDA v20 candle granularity from S5 through W."""

    raw = default if value is None else str(value).strip()
    if not raw:
        raw = default
    normalized = _RAW_GRANULARITY_ALIASES.get(raw.casefold(), raw.upper())
    if normalized not in OANDA_CANDLE_GRANULARITIES:
        raise ValueError(
            f"Unsupported OANDA candle granularity '{normalized}'. "
            f"Supported values: {list(OANDA_CANDLE_GRANULARITIES)}."
        )
    return normalized


def floor_time_to_boundary(
    timestamp: datetime | pd.Timestamp,
    timeframe: str,
) -> pd.Timestamp:
    """Floor a UTC timestamp to the start of its candle boundary."""

    normalized = _normalize_utc_timestamp(timestamp)
    get_timeframe_delta(timeframe)
    if timeframe in _TIMEFRAME_FLOOR_RULES:
        return normalized.floor(_TIMEFRAME_FLOOR_RULES[timeframe])
    if timeframe in _OANDA_DAY_ALIGNED_TIMEFRAMES:
        return _floor_to_oanda_day_aligned_boundary(normalized, timeframe)
    if timeframe == "W":
        return _floor_to_oanda_weekly_boundary(normalized)
    raise ValueError(f"Unsupported timeframe '{timeframe}'. Supported values: {sorted(TIMEFRAME_DELTAS)}.")


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


def _floor_to_oanda_day_aligned_boundary(timestamp: pd.Timestamp, timeframe: str) -> pd.Timestamp:
    local = timestamp.tz_convert(_OANDA_ALIGNMENT_TZ)
    anchor = _oanda_daily_anchor_for(local)
    delta = get_timeframe_delta(timeframe)
    elapsed = timestamp - anchor.tz_convert("UTC")
    slots = int(elapsed // delta)
    return anchor.tz_convert("UTC") + (slots * delta)


def _floor_to_oanda_weekly_boundary(timestamp: pd.Timestamp) -> pd.Timestamp:
    local = timestamp.tz_convert(_OANDA_ALIGNMENT_TZ)
    daily_anchor = _oanda_daily_anchor_for(local)
    effective_date = daily_anchor.date()
    week_start_index = _OANDA_WEEKDAY_INDEX[OANDA_WEEKLY_ALIGNMENT]
    days_since_start = (effective_date.weekday() - week_start_index) % 7
    week_start_date = effective_date - timedelta(days=days_since_start)
    return _oanda_local_anchor(week_start_date).tz_convert("UTC")


def _oanda_daily_anchor_for(local_timestamp: pd.Timestamp) -> pd.Timestamp:
    local_date = local_timestamp.date()
    anchor = _oanda_local_anchor(local_date)
    if local_timestamp < anchor:
        return _oanda_local_anchor(local_date - timedelta(days=1))
    return anchor


def _oanda_local_anchor(local_date: date) -> pd.Timestamp:
    return pd.Timestamp(
        datetime(
            local_date.year,
            local_date.month,
            local_date.day,
            OANDA_DAILY_ALIGNMENT_HOUR,
            tzinfo=_OANDA_ALIGNMENT_TZ,
        )
    )


__all__ = [
    "CANONICAL_COLUMNS",
    "OANDA_CANDLE_ALIGNMENT_PARAMS",
    "OANDA_CANDLE_GRANULARITIES",
    "OANDA_DAILY_ALIGNMENT_HOUR",
    "OANDA_MAX_CANDLE_COUNT",
    "OANDA_ALIGNMENT_TIMEZONE",
    "OANDA_WEEKLY_ALIGNMENT",
    "TIMEFRAME_DELTAS",
    "calculate_candle_staleness_seconds",
    "floor_time_to_boundary",
    "get_current_candle_start",
    "get_last_completed_candle_start",
    "get_timeframe_delta",
    "normalize_oanda_candle_granularity",
    "trim_to_closed",
    "validate_candle_df",
]
