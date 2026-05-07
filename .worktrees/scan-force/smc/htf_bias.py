"""Stage 09 HTF bias computation with pinned members and changepoints."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from functools import lru_cache
import importlib
import time
from typing import Any

import pandas as pd

from config.settings import Settings, get_settings
from core.candle_policy import validate_candle_df
from core.logging_setup import get_logger
from core.models import Direction, HTFBiasResult, RegimeChangepoint, StructureBreak, TimeframeSnapshot

HTF_TIMEFRAMES: tuple[str, str, str] = ("D", "H4", "H1")
_RUPTURES_METHOD = "ruptures_pelt_rbf"


def _direction_to_sign(direction: Direction) -> int:
    if direction == "BULLISH":
        return 1
    if direction == "BEARISH":
        return -1
    return 0


def _clamp_score(value: float) -> float:
    return max(-1.0, min(1.0, value))


def _to_utc_datetime(value: Any):
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize("UTC")
    else:
        timestamp = timestamp.tz_convert("UTC")
    return timestamp.to_pydatetime()


@lru_cache(maxsize=1)
def _load_ruptures_module() -> Any:
    try:
        return importlib.import_module("ruptures")
    except Exception as exc:  # pragma: no cover - depends on local environment
        raise RuntimeError("ruptures is required for HTF bias changepoint detection.") from exc


@dataclass(frozen=True)
class PinnedHTFMember:
    """Internal pinned HTF input for Stage 09 bias computation."""

    snapshot: TimeframeSnapshot
    candles: pd.DataFrame
    source_snapshot_version: int

    @property
    def instrument(self) -> str:
        return self.snapshot.instrument

    @property
    def timeframe(self) -> str:
        return self.snapshot.timeframe

    def normalized_candles(self) -> pd.DataFrame:
        if self.timeframe not in HTF_TIMEFRAMES:
            raise ValueError(f"Unsupported HTF member timeframe {self.timeframe!r}.")
        if self.source_snapshot_version <= 0:
            raise ValueError("Pinned HTF members require a positive source_snapshot_version.")
        if self.snapshot.version != self.source_snapshot_version:
            raise ValueError("Pinned HTF member version must match the snapshot version.")

        normalized = validate_candle_df(self.candles)
        if normalized.empty:
            raise ValueError(f"Pinned HTF member {self.timeframe} requires at least one closed candle.")

        if _to_utc_datetime(normalized["time"].iloc[-1]) != self.snapshot.last_completed_candle:
            raise ValueError(
                f"Pinned HTF candles for {self.instrument} {self.timeframe} must end at the snapshot boundary."
            )
        return normalized


@dataclass(frozen=True)
class HTFBiasTuning:
    """Internal tuning surface for the Stage 09 bias engine."""

    weights: Mapping[str, float]
    transition_windows: Mapping[str, int]
    neutral_band: float
    ruptures_penalty: float

    @classmethod
    def from_settings(cls, settings: Settings) -> "HTFBiasTuning":
        return cls(
            weights={
                "D": settings.htf_bias_weight_d,
                "H4": settings.htf_bias_weight_h4,
                "H1": settings.htf_bias_weight_h1,
            },
            transition_windows={
                "D": settings.htf_transition_window_d,
                "H4": settings.htf_transition_window_h4,
                "H1": settings.htf_transition_window_h1,
            },
            neutral_band=settings.htf_bias_neutral_band,
            ruptures_penalty=settings.ruptures_penalty,
        )

    def normalized_weights(self) -> dict[str, float]:
        total = float(sum(self.weights[timeframe] for timeframe in HTF_TIMEFRAMES))
        if total <= 0:
            raise ValueError("HTF bias weights must sum to a positive value.")
        return {
            timeframe: float(self.weights[timeframe]) / total
            for timeframe in HTF_TIMEFRAMES
        }


@dataclass(frozen=True)
class _TimeframeBiasEvidence:
    timeframe: str
    score: float
    vote: Direction
    latest_break: StructureBreak | None
    changepoint: RegimeChangepoint | None
    is_recent_transition: bool


@dataclass
class HTFBiasAnalyzer:
    """Pure Stage 09 HTF bias analyzer over pinned members."""

    tuning: HTFBiasTuning
    logger: Any

    def compute(self, members: Iterable[PinnedHTFMember]) -> HTFBiasResult:
        start = time.perf_counter()
        member_map = self._normalize_members(members)
        weights = self.tuning.normalized_weights()
        evidence = [
            self._score_timeframe(member_map[timeframe], neutral_band=self.tuning.neutral_band)
            for timeframe in HTF_TIMEFRAMES
        ]

        aggregate = sum(weights[item.timeframe] * item.score for item in evidence)
        base_direction = self._direction_from_score(aggregate)
        d_h4_conflict = self._is_material_d_h4_conflict(evidence)
        transition_conflict = self._has_transition_conflict(evidence, base_direction)
        is_transitioning = any(item.is_recent_transition for item in evidence)

        direction: Direction
        if (
            base_direction == "NEUTRAL"
            or abs(aggregate) < self.tuning.neutral_band
            or d_h4_conflict
            or transition_conflict
        ):
            direction = "NEUTRAL"
            alignment_score = 0.0
        else:
            direction = base_direction
            alignment_score = min(abs(aggregate), 1.0)

        if is_transitioning and direction != "NEUTRAL":
            non_neutral_votes = [item.vote for item in evidence if item.vote != "NEUTRAL"]
            if non_neutral_votes and len(set(non_neutral_votes)) == 1 and non_neutral_votes[0] == direction:
                alignment_score = min(alignment_score, 0.55)

        regime_changepoints = tuple(
            item.changepoint
            for item in evidence
            if item.changepoint is not None
        )
        for changepoint in regime_changepoints:
            self.logger.info(
                "changepoint_detected",
                instrument=member_map[changepoint.timeframe].instrument,
                timeframe=changepoint.timeframe,
                changepoint_index=changepoint.changepoint_index,
                changepoint_time=changepoint.changepoint_time,
                method=changepoint.method,
            )

        structure_breaks = tuple(
            sorted(
                (
                    item.latest_break
                    for item in evidence
                    if item.latest_break is not None
                ),
                key=lambda item: item.occurred_at,
                reverse=True,
            )
        )
        last_changepoint_bars_ago = min(
            (
                changepoint.bars_ago
                for changepoint in regime_changepoints
                if changepoint.bars_ago is not None
            ),
            default=None,
        )

        result = HTFBiasResult(
            direction=direction,
            alignment_score=alignment_score,
            timeframe_votes={item.timeframe: item.vote for item in evidence},
            structure_breaks=structure_breaks,
            regime_changepoints=regime_changepoints,
            is_transitioning=is_transitioning,
            last_changepoint_bars_ago=last_changepoint_bars_ago,
        )
        self.logger.info(
            "htf_bias_computed",
            instrument=next(iter(member_map.values())).instrument,
            direction=result.direction,
            alignment_score=result.alignment_score,
            timeframe_votes=result.timeframe_votes,
            duration_ms=round((time.perf_counter() - start) * 1000.0, 3),
        )
        return result

    def _normalize_members(
        self,
        members: Iterable[PinnedHTFMember],
    ) -> dict[str, PinnedHTFMember]:
        normalized: dict[str, PinnedHTFMember] = {}
        instrument: str | None = None

        for member in members:
            if member.timeframe in normalized:
                raise ValueError(f"Duplicate HTF member supplied for {member.timeframe}.")
            if instrument is None:
                instrument = member.instrument
            elif member.instrument != instrument:
                raise ValueError("All HTF members must belong to the same instrument.")
            member.normalized_candles()
            normalized[member.timeframe] = member

        if set(normalized) != set(HTF_TIMEFRAMES):
            raise ValueError(
                f"HTF bias requires exactly {HTF_TIMEFRAMES}, got {tuple(normalized)}."
            )
        return normalized

    def _score_timeframe(
        self,
        member: PinnedHTFMember,
        *,
        neutral_band: float,
    ) -> _TimeframeBiasEvidence:
        snapshot = member.snapshot
        closed = member.normalized_candles()
        latest_close = float(closed["close"].iloc[-1])
        score = 0.0

        latest_break = snapshot.structure.latest_break
        if latest_break is not None:
            break_weight = 0.45 if latest_break.kind == "BOS" else 0.35
            score += break_weight * _direction_to_sign(latest_break.direction)

        recent_breaks = snapshot.structure.recent_breaks
        if recent_breaks:
            break_balance = sum(_direction_to_sign(item.direction) for item in recent_breaks) / len(recent_breaks)
            score += 0.15 * break_balance

        previous_high_low = snapshot.smc_context.previous_high_low
        if previous_high_low is not None:
            if (
                previous_high_low.broken_high
                and not previous_high_low.broken_low
                and latest_close > previous_high_low.previous_high
            ):
                score += 0.20
            elif (
                previous_high_low.broken_low
                and not previous_high_low.broken_high
                and latest_close < previous_high_low.previous_low
            ):
                score -= 0.20

        retracement = snapshot.smc_context.retracement
        if retracement is not None and retracement.direction is not None:
            retracement_strength = 0.10
            current_retracement = retracement.current_retracement_pct or 0.0
            if current_retracement > 66.0:
                retracement_strength = 0.04
            elif current_retracement > 50.0:
                retracement_strength = 0.07
            score += retracement_strength * _direction_to_sign(retracement.direction)

        active_order_block = next(
            (
                item
                for item in snapshot.zones.order_blocks
                if item.is_mitigated is not True
            ),
            None,
        )
        if active_order_block is not None:
            score += 0.10 * _direction_to_sign(active_order_block.direction)

        metrics = {
            metric.name: metric.value
            for metric in snapshot.indicators.metrics
            if metric.value is not None
        }
        macd_hist = metrics.get("macd_hist")
        if macd_hist is not None:
            if macd_hist > 0:
                score += 0.10
            elif macd_hist < 0:
                score -= 0.10

        rsi = metrics.get("rsi")
        if rsi is not None:
            if rsi >= 55.0:
                score += 0.05
            elif rsi <= 45.0:
                score -= 0.05

        score *= self._trend_dampener(snapshot=snapshot, metrics=metrics)
        score = _clamp_score(score)
        vote = self._direction_from_score(score, neutral_band=neutral_band)
        changepoint = self._detect_changepoint(member)
        is_recent_transition = (
            changepoint is not None
            and changepoint.bars_ago is not None
            and changepoint.bars_ago <= self.tuning.transition_windows[member.timeframe]
        )
        return _TimeframeBiasEvidence(
            timeframe=member.timeframe,
            score=score,
            vote=vote,
            latest_break=latest_break,
            changepoint=changepoint,
            is_recent_transition=is_recent_transition,
        )

    def _trend_dampener(
        self,
        *,
        snapshot: TimeframeSnapshot,
        metrics: Mapping[str, float],
    ) -> float:
        dampener = 1.0
        if snapshot.chop.status == "CAUTION":
            dampener *= 0.80
        elif snapshot.chop.status == "REJECT":
            dampener *= 0.60

        adx = metrics.get("adx")
        if adx is None:
            dampener *= 0.85
        elif adx < 15.0:
            dampener *= 0.65
        elif adx < 20.0:
            dampener *= 0.80

        return dampener

    def _detect_changepoint(self, member: PinnedHTFMember) -> RegimeChangepoint | None:
        closed = member.normalized_candles()
        if len(closed) < 8:
            return None

        ruptures = _load_ruptures_module()
        close_values = closed["close"].astype(float).to_numpy()
        changepoints = ruptures.Pelt(model="rbf").fit(close_values).predict(
            pen=self.tuning.ruptures_penalty
        )
        if not changepoints or len(changepoints) < 2:
            return None

        changepoint_index = int(changepoints[-2])
        if changepoint_index >= len(closed):
            return None
        if not self._passes_regime_shift_filter(member=member, closed=closed, changepoint_index=changepoint_index):
            return None

        bars_ago = (len(closed) - 1) - changepoint_index
        return RegimeChangepoint(
            timeframe=member.timeframe,
            changepoint_index=changepoint_index,
            changepoint_time=_to_utc_datetime(closed["time"].iloc[changepoint_index]),
            method=_RUPTURES_METHOD,
            bars_ago=bars_ago,
        )

    def _passes_regime_shift_filter(
        self,
        *,
        member: PinnedHTFMember,
        closed: pd.DataFrame,
        changepoint_index: int,
    ) -> bool:
        returns = closed["close"].astype(float).diff().dropna().reset_index(drop=True)
        if returns.empty:
            return False

        window = max(4, self.tuning.transition_windows[member.timeframe])
        pre_returns = returns.iloc[max(0, changepoint_index - window) : changepoint_index]
        post_returns = returns.iloc[changepoint_index : changepoint_index + window]
        if pre_returns.empty or post_returns.empty:
            return False

        shift = abs(float(post_returns.mean()) - float(pre_returns.mean()))
        baseline = max(
            float(returns.abs().median()),
            float(returns.std(ddof=0)),
            1e-9,
        )
        return shift >= (baseline * 1.5)

    @staticmethod
    def _direction_from_score(score: float, *, neutral_band: float = 0.15) -> Direction:
        if score >= neutral_band:
            return "BULLISH"
        if score <= -neutral_band:
            return "BEARISH"
        return "NEUTRAL"

    @staticmethod
    def _is_material_d_h4_conflict(evidence: list[_TimeframeBiasEvidence]) -> bool:
        votes = {item.timeframe: item.vote for item in evidence}
        return (
            votes["D"] != "NEUTRAL"
            and votes["H4"] != "NEUTRAL"
            and votes["D"] != votes["H4"]
        )

    @staticmethod
    def _has_transition_conflict(
        evidence: list[_TimeframeBiasEvidence],
        base_direction: Direction,
    ) -> bool:
        if base_direction == "NEUTRAL":
            return False
        return any(
            item.is_recent_transition
            and item.vote not in {"NEUTRAL", base_direction}
            for item in evidence
        )


def summarize_htf_bias(
    members: Iterable[PinnedHTFMember],
    *,
    settings: Settings | None = None,
    logger: Any | None = None,
) -> HTFBiasResult:
    """Summarize pinned HTF members into a bundle-level HTF bias result."""

    resolved_settings = settings or get_settings()
    analyzer = HTFBiasAnalyzer(
        tuning=HTFBiasTuning.from_settings(resolved_settings),
        logger=logger or get_logger(__name__),
    )
    return analyzer.compute(members)


__all__ = [
    "HTFBiasAnalyzer",
    "HTFBiasTuning",
    "PinnedHTFMember",
    "summarize_htf_bias",
]
