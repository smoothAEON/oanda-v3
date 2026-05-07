"""ORB detector placeholder reserved for Stage 08."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from core.candle_policy import trim_to_closed, validate_candle_df
from core.models import ORBResult, SessionName
from smc.provider import SESSION_NAMES, _load_smc_module


@dataclass(frozen=True)
class _SessionWindow:
    session: SessionName
    start_index: int
    end_index: int
    start_time: pd.Timestamp


def detect_orb(
    candles: pd.DataFrame,
    timeframe: str,
    *,
    opening_range_bars: int = 1,
) -> ORBResult | None:
    """Detect a latest-session opening-range breakout on M15 only."""

    if timeframe != "M15":
        return None
    if opening_range_bars <= 0:
        raise ValueError("opening_range_bars must be positive.")

    validated = validate_candle_df(candles)
    closed = trim_to_closed(validated, timeframe)
    if len(closed) < opening_range_bars + 1:
        return ORBResult()

    indexed_frame = _build_indexed_smc_frame(closed)
    smc = _load_smc_module()
    latest_window = _latest_session_window(
        smc,
        indexed_frame,
        opening_range_bars=opening_range_bars,
    )
    if latest_window is None:
        return ORBResult()

    opening_range = closed.iloc[
        latest_window.start_index : latest_window.start_index + opening_range_bars
    ]
    range_high = float(opening_range["high"].max())
    range_low = float(opening_range["low"].min())

    for index in range(
        latest_window.start_index + opening_range_bars,
        latest_window.end_index + 1,
    ):
        close_price = float(closed["close"].iloc[index])
        if close_price > range_high:
            return ORBResult(
                detected=True,
                direction="BULLISH",
                session=latest_window.session,
                range_high=range_high,
                range_low=range_low,
                breakout_price=close_price,
                occurred_at=pd.Timestamp(closed["time"].iloc[index]).to_pydatetime(),
            )
        if close_price < range_low:
            return ORBResult(
                detected=True,
                direction="BEARISH",
                session=latest_window.session,
                range_high=range_high,
                range_low=range_low,
                breakout_price=close_price,
                occurred_at=pd.Timestamp(closed["time"].iloc[index]).to_pydatetime(),
            )

    return ORBResult()


def _build_indexed_smc_frame(closed: pd.DataFrame) -> pd.DataFrame:
    frame = closed.rename(columns={"tick_volume": "volume"}).copy(deep=True)
    indexed = frame.loc[:, ["open", "high", "low", "close", "volume"]]
    indexed.index = pd.DatetimeIndex(closed["time"], name="time")
    return indexed


def _latest_session_window(
    smc,
    indexed_frame: pd.DataFrame,
    *,
    opening_range_bars: int,
) -> _SessionWindow | None:
    candidates: list[_SessionWindow] = []
    for public_name, smc_name in SESSION_NAMES:
        session_frame = smc.sessions(indexed_frame.copy(), smc_name)
        active_mask = _build_active_mask(session_frame)
        for start_index, end_index in _extract_active_windows(active_mask):
            if (end_index - start_index + 1) < opening_range_bars + 1:
                continue
            candidates.append(
                _SessionWindow(
                    session=public_name,
                    start_index=start_index,
                    end_index=end_index,
                    start_time=pd.Timestamp(indexed_frame.index[start_index]),
                )
            )

    if not candidates:
        return None
    return max(
        candidates,
        key=lambda item: (item.start_time.value, item.end_index, item.session),
    )


def _build_active_mask(session_frame: pd.DataFrame) -> list[bool]:
    active = pd.to_numeric(session_frame["Active"], errors="coerce").fillna(0).astype(int)
    return [value == 1 for value in active.tolist()]


def _extract_active_windows(active_mask: list[bool]) -> list[tuple[int, int]]:
    windows: list[tuple[int, int]] = []
    start_index: int | None = None
    for index, is_active in enumerate(active_mask):
        if is_active and start_index is None:
            start_index = index
            continue
        if not is_active and start_index is not None:
            windows.append((start_index, index - 1))
            start_index = None

    if start_index is not None:
        windows.append((start_index, len(active_mask) - 1))
    return windows


__all__ = ["detect_orb"]
