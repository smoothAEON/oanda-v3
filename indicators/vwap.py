"""Dedicated VWAP read helpers shared by Telegram and MCP."""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Iterable

import pandas as pd

from core.candle_policy import get_timeframe_delta, trim_to_closed, validate_candle_df
from core.instrument_registry import get_instrument_spec
from core.models import VwapBand, VwapReadResult
from indicators.pandasta_wrappers import _load_pandasta_module

SUPPORTED_VWAP_TIMEFRAMES: tuple[str, ...] = ("M30", "H1", "H4", "D")

_ANCHOR_MAP: dict[str, tuple[str, str, str]] = {
    "D": ("D", "D", "daily"),
    "DAILY": ("D", "D", "daily"),
    "W": ("W", "W-FRI", "weekly"),
    "WEEKLY": ("W", "W-FRI", "weekly"),
    "M": ("M", "M", "monthly"),
    "MONTHLY": ("M", "M", "monthly"),
}

_VWAP_CAVEAT = (
    "Computed from OANDA tick count, not exchange-traded volume. "
    "Not equivalent to CME/NYSE volume. Reflects broker tick activity only."
)


def validate_vwap_timeframe(timeframe: str) -> str:
    """Return one supported VWAP timeframe or raise a user-facing ValueError."""

    resolved = str(timeframe).strip().upper()
    if resolved not in SUPPORTED_VWAP_TIMEFRAMES:
        supported = "', '".join(SUPPORTED_VWAP_TIMEFRAMES)
        raise ValueError(f"VWAP timeframe must be one of '{supported}'.")
    return resolved


def normalize_vwap_anchor(anchor: str | None = "D") -> tuple[str, str, str]:
    """Normalize a VWAP anchor into public code, pandas alias, and display name."""

    raw = "D" if anchor is None else str(anchor).strip().upper()
    try:
        return _ANCHOR_MAP[raw]
    except KeyError as exc:
        raise ValueError("anchor must be D, W, M, daily, weekly, or monthly.") from exc


def normalize_vwap_bands(bands: Iterable[object] | object | None) -> tuple[float, ...]:
    """Normalize optional VWAP band deviations into sorted unique floats."""

    if bands is None:
        return ()

    values: list[object]
    if isinstance(bands, str):
        values = [part.strip() for part in bands.split(",")]
    elif isinstance(bands, Iterable):
        values = []
        for item in bands:
            if isinstance(item, str):
                values.extend(part.strip() for part in item.split(","))
            else:
                values.append(item)
    else:
        values = [bands]

    normalized: set[float] = set()
    for value in values:
        if value in ("", None):
            continue
        try:
            candidate = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("VWAP bands must be comma-separated positive numbers.") from exc
        if not math.isfinite(candidate) or candidate <= 0:
            raise ValueError("VWAP bands must be comma-separated positive numbers.")
        normalized.add(candidate)
    return tuple(sorted(normalized))


def resolve_vwap_candle_count(
    timeframe: str,
    anchor: str | None = "D",
    *,
    now_utc: datetime | None = None,
    buffer_candles: int = 4,
) -> int:
    """Return a candle count large enough to cover the active anchor window."""

    resolved_timeframe = validate_vwap_timeframe(timeframe)
    _, internal_anchor, _ = normalize_vwap_anchor(anchor)
    current_time = _normalize_now(now_utc)
    anchor_start = _current_anchor_window_start(current_time, internal_anchor)
    delta = get_timeframe_delta(resolved_timeframe)
    elapsed = max(current_time - anchor_start, delta)
    periods = int(math.ceil(elapsed / delta))
    return max(2, periods + max(buffer_candles, 1))


def build_vwap_read_result(
    candles: pd.DataFrame,
    *,
    instrument: str,
    timeframe: str,
    anchor: str | None = "D",
    bands: Iterable[object] | object | None = None,
    source: str | None = None,
) -> VwapReadResult:
    """Build a stable VWAP read result from canonical candle data."""

    get_instrument_spec(instrument)
    resolved_timeframe = validate_vwap_timeframe(timeframe)
    public_anchor, internal_anchor, anchor_name = normalize_vwap_anchor(anchor)
    resolved_bands = normalize_vwap_bands(bands)

    frame = validate_candle_df(candles)
    closed = trim_to_closed(frame, resolved_timeframe)
    if closed.empty:
        raise RuntimeError(f"VWAP requires closed candles for {instrument} {resolved_timeframe}.")

    indexed = closed.copy(deep=True)
    indexed.index = pd.DatetimeIndex(indexed["time"], name="time")
    pandas_ta = _load_pandasta_module()
    vwap_payload = pandas_ta.vwap(
        indexed["high"],
        indexed["low"],
        indexed["close"],
        indexed["tick_volume"].rename("volume"),
        anchor=internal_anchor,
        bands=list(resolved_bands) if resolved_bands else None,
    )

    if isinstance(vwap_payload, pd.DataFrame):
        vwap_series = vwap_payload.iloc[:, 0]
        band_frame = vwap_payload
    else:
        vwap_series = vwap_payload
        band_frame = None

    vwap_value = _coerce_scalar(vwap_series)
    if vwap_value is None:
        raise RuntimeError(f"VWAP is unavailable for {instrument} {resolved_timeframe}.")

    active_periods = indexed.index.to_period(internal_anchor)
    active_mask = active_periods == active_periods[-1]
    active_index = indexed.index[active_mask]
    anchor_start = active_index[0].to_pydatetime()
    last_completed_candle = indexed.index[-1].to_pydatetime()

    reference_close = float(indexed["close"].iloc[-1])
    pip_size = get_instrument_spec(instrument).pip_size
    raw_distance_price = reference_close - vwap_value
    if math.isclose(raw_distance_price, 0.0, abs_tol=max(pip_size * 1e-9, 1e-12)):
        distance_price = 0.0
    else:
        distance_price = raw_distance_price
    distance_pips = distance_price / pip_size
    if distance_price > 0:
        price_position = "above"
    elif distance_price < 0:
        price_position = "below"
    else:
        price_position = "at"

    band_models = _extract_band_models(band_frame, vwap_series.name, resolved_bands)
    return VwapReadResult(
        instrument=instrument,
        timeframe=resolved_timeframe,
        anchor=public_anchor,  # type: ignore[arg-type]
        anchor_name=anchor_name,  # type: ignore[arg-type]
        anchor_start=anchor_start,
        last_completed_candle=last_completed_candle,
        reference_close=reference_close,
        vwap=vwap_value,
        price_position=price_position,  # type: ignore[arg-type]
        distance_price=distance_price,
        distance_pips=distance_pips,
        bands=band_models,
        source=(source or "unknown").strip() or "unknown",
        volume_type="tick_count",
        caveat=_VWAP_CAVEAT,
    )


def _normalize_now(now_utc: datetime | None) -> datetime:
    current = datetime.now(timezone.utc) if now_utc is None else now_utc
    if current.tzinfo is None:
        raise ValueError("now_utc must be timezone-aware.")
    return current.astimezone(timezone.utc)


def _current_anchor_window_start(now_utc: datetime, internal_anchor: str) -> datetime:
    timestamp = pd.Timestamp(now_utc).tz_convert("UTC").tz_localize(None)
    period_start = timestamp.to_period(internal_anchor).start_time
    return period_start.tz_localize("UTC").to_pydatetime()


def _coerce_scalar(series: pd.Series | None) -> float | None:
    if series is None or len(series) == 0:
        return None
    value = float(series.iloc[-1])
    if not math.isfinite(value):
        return None
    return value


def _extract_band_models(
    frame: pd.DataFrame | None,
    base_column: str | None,
    deviations: tuple[float, ...],
) -> tuple[VwapBand, ...]:
    if frame is None or base_column is None or not deviations:
        return ()

    bands: list[VwapBand] = []
    for deviation in deviations:
        label = str(float(deviation))
        lower_column = f"{base_column}_L_{label}"
        upper_column = f"{base_column}_U_{label}"
        if lower_column not in frame or upper_column not in frame:
            raise RuntimeError(f"VWAP band columns missing for deviation {label}.")
        lower = _coerce_scalar(frame[lower_column])
        upper = _coerce_scalar(frame[upper_column])
        if lower is None or upper is None:
            raise RuntimeError(f"VWAP band values unavailable for deviation {label}.")
        bands.append(VwapBand(deviation=float(deviation), lower=lower, upper=upper))
    return tuple(bands)


__all__ = [
    "SUPPORTED_VWAP_TIMEFRAMES",
    "build_vwap_read_result",
    "normalize_vwap_anchor",
    "normalize_vwap_bands",
    "resolve_vwap_candle_count",
    "validate_vwap_timeframe",
]
