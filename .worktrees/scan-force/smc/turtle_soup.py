"""Turtle Soup detector placeholder reserved for Stage 08."""

from __future__ import annotations

from datetime import datetime

import pandas as pd

from core.candle_policy import trim_to_closed, validate_candle_df
from core.models import TurtleSoupResult


def detect_turtle_soup(
    candles: pd.DataFrame,
    timeframe: str,
    *,
    lookback_bars: int = 20,
) -> TurtleSoupResult:
    """Detect a latest-bar false breakout that closes back inside the prior range."""

    if lookback_bars <= 0:
        raise ValueError("lookback_bars must be positive.")

    validated = validate_candle_df(candles)
    closed = trim_to_closed(validated, timeframe)
    if len(closed) < lookback_bars + 1:
        return TurtleSoupResult()

    prior_window = closed.iloc[-(lookback_bars + 1) : -1].reset_index(drop=True)
    last_bar = closed.iloc[-1]
    prior_high, prior_high_time = _latest_reference_extreme(prior_window, column="high", mode="max")
    prior_low, prior_low_time = _latest_reference_extreme(prior_window, column="low", mode="min")
    close_price = float(last_bar["close"])

    bearish = (
        float(last_bar["high"]) > prior_high
        and prior_low <= close_price < prior_high
    )
    bullish = (
        float(last_bar["low"]) < prior_low
        and prior_low < close_price <= prior_high
    )
    if bullish == bearish:
        return TurtleSoupResult()

    last_time = pd.Timestamp(last_bar["time"]).to_pydatetime()
    if bullish:
        return TurtleSoupResult(
            detected=True,
            direction="BULLISH",
            reference_level=prior_low,
            reference_time=prior_low_time,
            lookback_bars=lookback_bars,
            sweep_price=float(last_bar["low"]),
            close_price=close_price,
            occurred_at=last_time,
        )
    return TurtleSoupResult(
        detected=True,
        direction="BEARISH",
        reference_level=prior_high,
        reference_time=prior_high_time,
        lookback_bars=lookback_bars,
        sweep_price=float(last_bar["high"]),
        close_price=close_price,
        occurred_at=last_time,
    )


def _latest_reference_extreme(
    prior_window: pd.DataFrame,
    *,
    column: str,
    mode: str,
) -> tuple[float, datetime]:
    values = [float(value) for value in prior_window[column].tolist()]
    if mode == "max":
        target = max(values)
    elif mode == "min":
        target = min(values)
    else:  # pragma: no cover - internal guard
        raise ValueError(f"Unsupported mode '{mode}'.")

    relative_index = max(
        index for index, value in enumerate(values) if value == target
    )
    reference_time = pd.Timestamp(prior_window["time"].iloc[relative_index])
    return float(target), reference_time.to_pydatetime()


__all__ = ["detect_turtle_soup"]
