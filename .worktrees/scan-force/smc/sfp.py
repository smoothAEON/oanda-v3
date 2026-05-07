"""SFP detector placeholder reserved for Stage 08."""

from __future__ import annotations

import pandas as pd

from core.candle_policy import trim_to_closed, validate_candle_df
from core.models import SFPResult
from smc.provider import _load_smc_module


def detect_sfp(
    candles: pd.DataFrame,
    timeframe: str,
    *,
    swing_length: int = 50,
) -> SFPResult:
    """Detect whether the latest closed bar sweeps a swing and closes back through it."""

    if swing_length <= 0:
        raise ValueError("swing_length must be positive.")

    validated = validate_candle_df(candles)
    closed = trim_to_closed(validated, timeframe)
    minimum_bars = (swing_length * 2) + 2
    if len(closed) < minimum_bars:
        return SFPResult()

    smc = _load_smc_module()
    smc_frame = _build_smc_frame(closed)
    swing_frame = smc.swing_highs_lows(smc_frame.copy(), swing_length=swing_length)

    last_bar = closed.iloc[-1]
    last_time = pd.Timestamp(last_bar["time"]).to_pydatetime()
    latest_high = _latest_swing(swing_frame, direction_value=1, before_index=len(closed) - 1)
    latest_low = _latest_swing(swing_frame, direction_value=-1, before_index=len(closed) - 1)

    bearish = False
    bullish = False
    bearish_level = None
    bullish_level = None
    bearish_time = None
    bullish_time = None

    if latest_high is not None:
        index, level = latest_high
        if float(last_bar["high"]) > level and float(last_bar["close"]) < level:
            bearish = True
            bearish_level = level
            bearish_time = pd.Timestamp(closed["time"].iloc[index]).to_pydatetime()

    if latest_low is not None:
        index, level = latest_low
        if float(last_bar["low"]) < level and float(last_bar["close"]) > level:
            bullish = True
            bullish_level = level
            bullish_time = pd.Timestamp(closed["time"].iloc[index]).to_pydatetime()

    if bullish == bearish:
        return SFPResult()
    if bullish:
        return SFPResult(
            detected=True,
            direction="BULLISH",
            reference_level=bullish_level,
            reference_time=bullish_time,
            sweep_price=float(last_bar["low"]),
            close_price=float(last_bar["close"]),
            occurred_at=last_time,
        )
    return SFPResult(
        detected=True,
        direction="BEARISH",
        reference_level=bearish_level,
        reference_time=bearish_time,
        sweep_price=float(last_bar["high"]),
        close_price=float(last_bar["close"]),
        occurred_at=last_time,
    )


def _build_smc_frame(closed: pd.DataFrame) -> pd.DataFrame:
    frame = closed.rename(columns={"tick_volume": "volume"}).copy(deep=True)
    return frame.loc[:, ["open", "high", "low", "close", "volume"]]


def _latest_swing(
    swing_frame: pd.DataFrame,
    *,
    direction_value: int,
    before_index: int,
) -> tuple[int, float] | None:
    matches = swing_frame.index[swing_frame["HighLow"] == direction_value].tolist()
    for index in reversed(matches):
        resolved_index = int(index)
        if resolved_index >= before_index:
            continue
        level = swing_frame["Level"].iloc[resolved_index]
        if pd.isna(level):
            continue
        return resolved_index, float(level)
    return None


__all__ = ["detect_sfp"]
