"""Stage 06 SMC adapter and typed summary mapping."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import hashlib
import importlib
import os
from typing import Any

import pandas as pd

from core.candle_policy import trim_to_closed, validate_candle_df
from core.instrument_registry import get_instrument_spec
from core.models import (
    ActiveZoneSummary,
    BinaryDirection,
    LiquidityLevelSummary,
    LiquidityPoolSummary,
    MAX_ACTIVE_ORDER_BLOCKS,
    OrderBlockStatus,
    OrderBlockSummary,
    PreviousHighLowSummary,
    RetracementSummary,
    SessionContextSummary,
    SessionSummary,
    SmcContextSummary,
    StructureBreak,
    StructureEventSummary,
    SwingPointSummary,
)

SESSION_NAMES: tuple[tuple[str, str], ...] = (
    ("SYDNEY", "Sydney"),
    ("TOKYO", "Tokyo"),
    ("LONDON", "London"),
    ("NEW_YORK", "New York"),
)


@dataclass(frozen=True)
class OrderBlockCandidate:
    """Internal order-block candidate for tracker publication."""

    id: str
    instrument: str
    timeframe: str
    direction: BinaryDirection
    upper_price: float
    lower_price: float
    created_at: pd.Timestamp
    status: OrderBlockStatus
    mitigated_at: pd.Timestamp | None
    last_analyzed_close: float


@dataclass(frozen=True)
class SmcAnalysisResult:
    """Typed Stage 06 adapter output."""

    structure: StructureEventSummary
    zones: ActiveZoneSummary
    liquidity: LiquidityPoolSummary
    smc_context: SmcContextSummary
    order_block_candidates: tuple[OrderBlockCandidate, ...]


@lru_cache(maxsize=1)
def _load_smc_module() -> Any:
    os.environ["SMC_CREDIT"] = "0"

    try:
        package = importlib.import_module("smartmoneyconcepts")
    except Exception as exc:  # pragma: no cover - exercised through tests
        raise RuntimeError(
            "Failed to import smartmoneyconcepts after suppressing package credit output."
        ) from exc

    smc_module = getattr(package, "smc", None)
    if smc_module is None:
        raise RuntimeError("smartmoneyconcepts did not expose the expected 'smc' object.")
    return smc_module


@dataclass(frozen=True)
class SmcAdapter:
    """Thin deterministic adapter around smartmoneyconcepts."""

    swing_length: int = 50
    liquidity_range_percent: float = 0.01
    close_break: bool = True
    close_mitigation: bool = False

    def analyze(
        self,
        instrument: str,
        timeframe: str,
        candles: pd.DataFrame,
    ) -> SmcAnalysisResult:
        get_instrument_spec(instrument)

        validated = validate_candle_df(candles)
        closed = trim_to_closed(validated, timeframe)
        if closed.empty:
            return SmcAnalysisResult(
                structure=StructureEventSummary(),
                zones=ActiveZoneSummary(),
                liquidity=LiquidityPoolSummary(),
                smc_context=SmcContextSummary(),
                order_block_candidates=(),
            )

        smc = _load_smc_module()
        smc_frame = self._build_smc_frame(closed)
        indexed_smc_frame = self._build_indexed_smc_frame(closed)
        last_time = pd.Timestamp(closed["time"].iloc[-1])

        sessions = self._build_session_context(smc, indexed_smc_frame, last_time)
        previous_high_low = self._build_previous_high_low(smc, indexed_smc_frame, last_time)

        minimum_swing_bars = (self.swing_length * 2) + 1
        if len(closed) < minimum_swing_bars:
            return SmcAnalysisResult(
                structure=StructureEventSummary(),
                zones=ActiveZoneSummary(),
                liquidity=LiquidityPoolSummary(),
                smc_context=SmcContextSummary(
                    sessions=sessions,
                    previous_high_low=previous_high_low,
                    retracement=None,
                ),
                order_block_candidates=(),
            )

        swing_frame = smc.swing_highs_lows(smc_frame.copy(), swing_length=self.swing_length)
        bos_choch_frame = smc.bos_choch(
            smc_frame.copy(),
            swing_frame.copy(),
            close_break=self.close_break,
        )
        order_block_frame = smc.ob(
            smc_frame.copy(),
            swing_frame.copy(),
            close_mitigation=self.close_mitigation,
        )
        liquidity_frame = smc.liquidity(
            smc_frame.copy(),
            swing_frame.copy(),
            range_percent=self.liquidity_range_percent,
        )
        retracement_frame = smc.retracements(smc_frame.copy(), swing_frame.copy())

        structure = self._build_structure_summary(closed, swing_frame, bos_choch_frame)
        order_block_candidates = self._build_order_block_candidates(
            instrument=instrument,
            timeframe=timeframe,
            closed=closed,
            order_block_frame=order_block_frame,
        )
        zones = self._build_zone_summary(order_block_candidates)
        liquidity = self._build_liquidity_summary(
            instrument=instrument,
            closed=closed,
            liquidity_frame=liquidity_frame,
        )
        retracement = self._build_retracement_summary(closed, retracement_frame)

        return SmcAnalysisResult(
            structure=structure,
            zones=zones,
            liquidity=liquidity,
            smc_context=SmcContextSummary(
                sessions=sessions,
                previous_high_low=previous_high_low,
                retracement=retracement,
            ),
            order_block_candidates=order_block_candidates,
        )

    @staticmethod
    def build_order_block_record_id(
        instrument: str,
        timeframe: str,
        direction: BinaryDirection,
        created_at: pd.Timestamp,
        upper_price: float,
        lower_price: float,
    ) -> str:
        payload = "|".join(
            (
                instrument,
                timeframe,
                direction,
                created_at.isoformat(),
                f"{upper_price:.10f}",
                f"{lower_price:.10f}",
            )
        )
        return hashlib.sha1(payload.encode("utf-8")).hexdigest()

    @staticmethod
    def _build_smc_frame(closed: pd.DataFrame) -> pd.DataFrame:
        frame = closed.rename(columns={"tick_volume": "volume"}).copy(deep=True)
        return frame.loc[:, ["open", "high", "low", "close", "volume"]]

    @staticmethod
    def _build_indexed_smc_frame(closed: pd.DataFrame) -> pd.DataFrame:
        indexed = SmcAdapter._build_smc_frame(closed)
        indexed.index = pd.DatetimeIndex(closed["time"], name="time")
        return indexed

    def _build_structure_summary(
        self,
        closed: pd.DataFrame,
        swing_frame: pd.DataFrame,
        bos_choch_frame: pd.DataFrame,
    ) -> StructureEventSummary:
        breaks: list[StructureBreak] = []
        for index, row in bos_choch_frame.iterrows():
            level = row.get("Level")
            broken_index = row.get("BrokenIndex")
            for kind_name in ("BOS", "CHOCH"):
                value = row.get(kind_name)
                if pd.isna(value) or pd.isna(broken_index):
                    continue
                resolved_index = int(broken_index)
                if resolved_index < 0 or resolved_index >= len(closed):
                    resolved_index = int(index)
                occurred_at = pd.Timestamp(closed["time"].iloc[resolved_index]).to_pydatetime()
                origin_time = pd.Timestamp(closed["time"].iloc[int(index)]).isoformat()
                breaks.append(
                    StructureBreak(
                        kind=kind_name,
                        direction="BULLISH" if value > 0 else "BEARISH",
                        level=None if pd.isna(level) else float(level),
                        occurred_at=occurred_at,
                        context=f"origin_time={origin_time}",
                    )
                )

        breaks.sort(key=lambda item: item.occurred_at, reverse=True)
        latest_break = breaks[0] if breaks else None

        latest_swing_high = self._build_latest_swing_point(closed, swing_frame, 1, "HIGH")
        latest_swing_low = self._build_latest_swing_point(closed, swing_frame, -1, "LOW")

        return StructureEventSummary(
            latest_break=latest_break,
            recent_breaks=tuple(breaks[:3]),
            latest_swing_high=latest_swing_high,
            latest_swing_low=latest_swing_low,
        )

    @staticmethod
    def _build_latest_swing_point(
        closed: pd.DataFrame,
        swing_frame: pd.DataFrame,
        direction_value: int,
        kind: str,
    ) -> SwingPointSummary | None:
        matches = swing_frame.index[swing_frame["HighLow"] == direction_value].tolist()
        if not matches:
            return None

        index = int(matches[-1])
        level = swing_frame["Level"].iloc[index]
        if pd.isna(level):
            return None
        return SwingPointSummary(
            kind=kind,
            level=float(level),
            occurred_at=pd.Timestamp(closed["time"].iloc[index]).to_pydatetime(),
        )

    def _build_order_block_candidates(
        self,
        *,
        instrument: str,
        timeframe: str,
        closed: pd.DataFrame,
        order_block_frame: pd.DataFrame,
    ) -> tuple[OrderBlockCandidate, ...]:
        pip_size = get_instrument_spec(instrument).pip_size
        last_close = float(closed["close"].iloc[-1])
        candidates: list[tuple[float, OrderBlockCandidate]] = []

        for index, row in order_block_frame.iterrows():
            value = row.get("OB")
            top = row.get("Top")
            bottom = row.get("Bottom")
            if pd.isna(value) or pd.isna(top) or pd.isna(bottom):
                continue

            created_at = pd.Timestamp(closed["time"].iloc[int(index)])
            mitigated_index = row.get("MitigatedIndex")
            mitigated_at: pd.Timestamp | None = None
            status: OrderBlockStatus = "ACTIVE"
            if pd.notna(mitigated_index):
                resolved_index = int(mitigated_index)
                if 0 < resolved_index < len(closed):
                    mitigated_at = pd.Timestamp(closed["time"].iloc[resolved_index])
                    status = "MITIGATED"

            upper_price = float(max(top, bottom))
            lower_price = float(min(top, bottom))
            distance_pips = self._zone_distance_pips(
                last_close=last_close,
                upper_price=upper_price,
                lower_price=lower_price,
                pip_size=pip_size,
            )
            direction: BinaryDirection = "BULLISH" if value > 0 else "BEARISH"
            candidate = OrderBlockCandidate(
                id=self.build_order_block_record_id(
                    instrument=instrument,
                    timeframe=timeframe,
                    direction=direction,
                    created_at=created_at,
                    upper_price=upper_price,
                    lower_price=lower_price,
                ),
                instrument=instrument,
                timeframe=timeframe,
                direction=direction,
                upper_price=upper_price,
                lower_price=lower_price,
                created_at=created_at,
                status=status,
                mitigated_at=mitigated_at,
                last_analyzed_close=last_close,
            )
            candidates.append((distance_pips, candidate))

        candidates.sort(
            key=lambda item: (
                item[0],
                0 if item[1].status == "ACTIVE" else 1,
                -item[1].created_at.timestamp(),
                item[1].id,
            ),
            reverse=False,
        )
        return tuple(candidate for _, candidate in candidates)

    def _build_zone_summary(
        self,
        candidates: tuple[OrderBlockCandidate, ...],
    ) -> ActiveZoneSummary:
        order_blocks = tuple(
            OrderBlockSummary(
                direction=candidate.direction,
                upper_price=candidate.upper_price,
                lower_price=candidate.lower_price,
                created_at=candidate.created_at.to_pydatetime(),
                distance_pips=self._zone_distance_pips(
                    last_close=candidate.last_analyzed_close,
                    upper_price=candidate.upper_price,
                    lower_price=candidate.lower_price,
                    pip_size=get_instrument_spec(candidate.instrument).pip_size,
                ),
                is_mitigated=candidate.status == "MITIGATED",
            )
            for candidate in candidates[:MAX_ACTIVE_ORDER_BLOCKS]
        )
        return ActiveZoneSummary(order_blocks=order_blocks)

    def _build_liquidity_summary(
        self,
        *,
        instrument: str,
        closed: pd.DataFrame,
        liquidity_frame: pd.DataFrame,
    ) -> LiquidityPoolSummary:
        pip_size = get_instrument_spec(instrument).pip_size
        last_close = float(closed["close"].iloc[-1])
        levels: list[tuple[float, LiquidityLevelSummary]] = []

        for index, row in liquidity_frame.iterrows():
            value = row.get("Liquidity")
            level = row.get("Level")
            if pd.isna(value) or pd.isna(level):
                continue

            occurred_at_index = int(index)
            end_index = row.get("End")
            if pd.notna(end_index):
                resolved_end = int(end_index)
                if 0 <= resolved_end < len(closed):
                    occurred_at_index = resolved_end

            swept_index = row.get("Swept")
            was_swept = False
            if pd.notna(swept_index):
                resolved_swept = int(swept_index)
                was_swept = 0 < resolved_swept < len(closed)

            price = float(level)
            levels.append(
                (
                    abs(last_close - price) / pip_size,
                    LiquidityLevelSummary(
                        side="BUY_SIDE" if value > 0 else "SELL_SIDE",
                        price=price,
                        occurred_at=pd.Timestamp(
                            closed["time"].iloc[occurred_at_index]
                        ).to_pydatetime(),
                        distance_pips=abs(last_close - price) / pip_size,
                        was_swept=was_swept,
                    ),
                )
            )

        levels.sort(
            key=lambda item: (
                item[0],
                -(
                    item[1].occurred_at.timestamp()
                    if item[1].occurred_at is not None
                    else 0.0
                ),
                item[1].side,
            )
        )
        return LiquidityPoolSummary(levels=tuple(level for _, level in levels[:3]))

    def _build_session_context(
        self,
        smc: Any,
        indexed_frame: pd.DataFrame,
        last_time: pd.Timestamp,
    ) -> SessionContextSummary:
        summaries = []
        for public_name, smc_name in SESSION_NAMES:
            session_frame = smc.sessions(indexed_frame.copy(), smc_name)
            summaries.append(
                self._build_session_summary(
                    name=public_name,
                    session_frame=session_frame,
                    indexed_frame=indexed_frame,
                    last_time=last_time,
                )
            )
        return SessionContextSummary(sessions=tuple(summaries))

    @staticmethod
    def _build_session_summary(
        *,
        name: str,
        session_frame: pd.DataFrame,
        indexed_frame: pd.DataFrame,
        last_time: pd.Timestamp,
    ) -> SessionSummary:
        active_series = session_frame["Active"]
        active_indices = [int(index) for index in session_frame.index[active_series == 1]]
        if not active_indices:
            return SessionSummary(name=name, last_evaluated_at=last_time.to_pydatetime())

        end_index = active_indices[-1]
        start_index = end_index
        while start_index > 0 and active_series.iloc[start_index - 1] == 1:
            start_index -= 1

        return SessionSummary(
            name=name,
            is_active=end_index == len(indexed_frame) - 1,
            window_start=pd.Timestamp(indexed_frame.index[start_index]).to_pydatetime(),
            window_end=pd.Timestamp(indexed_frame.index[end_index]).to_pydatetime(),
            session_high=float(session_frame["High"].iloc[end_index]),
            session_low=float(session_frame["Low"].iloc[end_index]),
            last_evaluated_at=last_time.to_pydatetime(),
        )

    @staticmethod
    def _build_previous_high_low(
        smc: Any,
        indexed_frame: pd.DataFrame,
        last_time: pd.Timestamp,
    ) -> PreviousHighLowSummary | None:
        previous_frame = smc.previous_high_low(indexed_frame.copy(), time_frame="1D")
        last_row = previous_frame.iloc[-1]
        previous_high = last_row.get("PreviousHigh")
        previous_low = last_row.get("PreviousLow")
        if pd.isna(previous_high) or pd.isna(previous_low):
            return None

        return PreviousHighLowSummary(
            previous_high=float(previous_high),
            previous_low=float(previous_low),
            broken_high=bool(last_row.get("BrokenHigh", 0)),
            broken_low=bool(last_row.get("BrokenLow", 0)),
            as_of=last_time.to_pydatetime(),
        )

    @staticmethod
    def _build_retracement_summary(
        closed: pd.DataFrame,
        retracement_frame: pd.DataFrame,
    ) -> RetracementSummary | None:
        last_row = retracement_frame.iloc[-1]
        direction = int(last_row.get("Direction", 0))
        if direction == 0:
            return None

        current = abs(float(last_row.get("CurrentRetracement%", 0.0)))
        deepest = abs(float(last_row.get("DeepestRetracement%", 0.0)))
        if deepest < current:
            deepest = current
        return RetracementSummary(
            direction="BULLISH" if direction > 0 else "BEARISH",
            current_retracement_pct=current,
            deepest_retracement_pct=deepest,
            as_of=pd.Timestamp(closed["time"].iloc[-1]).to_pydatetime(),
        )

    @staticmethod
    def _zone_distance_pips(
        *,
        last_close: float,
        upper_price: float,
        lower_price: float,
        pip_size: float,
    ) -> float:
        if lower_price <= last_close <= upper_price:
            return 0.0
        if last_close > upper_price:
            return (last_close - upper_price) / pip_size
        return (lower_price - last_close) / pip_size


__all__ = [
    "OrderBlockCandidate",
    "SmcAdapter",
    "SmcAnalysisResult",
    "_load_smc_module",
]
