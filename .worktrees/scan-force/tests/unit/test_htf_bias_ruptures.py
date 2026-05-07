from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from core.candle_policy import get_timeframe_delta
from core.instrument_registry import get_instrument_spec
from core.models import (
    ActiveZoneSummary,
    ChopResult,
    IndicatorMetric,
    IndicatorValueSummary,
    LiquidityPoolSummary,
    OrderBlockSummary,
    PreviousHighLowSummary,
    RetracementSummary,
    SnapshotFreshness,
    SmcContextSummary,
    SpreadResult,
    StructureBreak,
    StructureEventSummary,
    TimeframeSnapshot,
)
from smc.htf_bias import HTFBiasAnalyzer, HTFBiasTuning, PinnedHTFMember

BASE_TIME = datetime(2026, 3, 20, 10, 0, tzinfo=timezone.utc)


class DummyLogger:
    def info(self, *args, **kwargs) -> None:
        return None


def build_tuning(*, penalty: float = 10.0) -> HTFBiasTuning:
    return HTFBiasTuning(
        weights={"D": 0.50, "H4": 0.30, "H1": 0.20},
        transition_windows={"D": 3, "H4": 4, "H1": 6},
        neutral_band=0.15,
        ruptures_penalty=penalty,
    )


def build_spread(instrument: str = "EUR_USD") -> SpreadResult:
    spec = get_instrument_spec(instrument)
    return SpreadResult(
        instrument=instrument,
        raw_spread=spec.typical_spread_pips * spec.pip_size,
        spread_pips=spec.typical_spread_pips,
        pip_size=spec.pip_size,
        typical_spread_pips=spec.typical_spread_pips,
        max_spread_pips=spec.max_spread_pips,
        is_acceptable=True,
        is_spiking=False,
        spread_ratio=1.0,
    )


def build_freshness(
    *,
    timeframe: str,
    last_completed_candle: datetime = BASE_TIME,
) -> SnapshotFreshness:
    return SnapshotFreshness(
        instrument="EUR_USD",
        timeframe=timeframe,
        last_completed_candle=last_completed_candle,
        fetched_at=last_completed_candle + timedelta(minutes=5),
        source="oanda_api",
        candle_count=500,
        is_fresh=True,
        staleness_seconds=0.0,
    )


def build_candles(
    *,
    timeframe: str,
    closes: list[float],
    end_time: datetime = BASE_TIME,
) -> pd.DataFrame:
    delta = get_timeframe_delta(timeframe)
    rows: list[dict[str, object]] = []

    for index, close in enumerate(closes):
        candle_time = end_time - delta * (len(closes) - 1 - index)
        open_price = closes[index - 1] if index > 0 else close - 0.10
        high_price = max(open_price, close) + 0.15
        low_price = min(open_price, close) - 0.15
        rows.append(
            {
                "time": candle_time,
                "open": open_price,
                "high": high_price,
                "low": low_price,
                "close": close,
                "tick_volume": 100 + index,
            }
        )

    return pd.DataFrame(rows)


def build_snapshot(
    *,
    timeframe: str,
    version: int,
    direction: str,
    end_time: datetime = BASE_TIME,
    chop_status: str = "PASS",
    adx: float | None = 30.0,
) -> TimeframeSnapshot:
    delta = get_timeframe_delta(timeframe)
    if direction == "BULLISH":
        latest_break = StructureBreak(
            kind="BOS",
            direction="BULLISH",
            level=1.1050,
            occurred_at=end_time - delta,
        )
        structure = StructureEventSummary(
            latest_break=latest_break,
            recent_breaks=(
                latest_break,
                StructureBreak(
                    kind="CHOCH",
                    direction="BULLISH",
                    level=1.1030,
                    occurred_at=end_time - (delta * 2),
                ),
            ),
        )
        smc_context = SmcContextSummary(
            previous_high_low=PreviousHighLowSummary(
                previous_high=1.1060,
                previous_low=1.0940,
                broken_high=True,
                broken_low=False,
                as_of=end_time - delta,
            ),
            retracement=RetracementSummary(
                direction="BULLISH",
                current_retracement_pct=35.0,
                deepest_retracement_pct=55.0,
                as_of=end_time,
            ),
        )
        zones = ActiveZoneSummary(
            order_blocks=(
                OrderBlockSummary(
                    direction="BULLISH",
                    upper_price=1.1020,
                    lower_price=1.1000,
                    created_at=end_time - (delta * 2),
                    is_mitigated=False,
                ),
            )
        )
        metrics = [
            IndicatorMetric(name="macd_hist", value=0.8, source="talib"),
            IndicatorMetric(name="rsi", value=62.0, source="talib"),
        ]
    elif direction == "BEARISH":
        latest_break = StructureBreak(
            kind="BOS",
            direction="BEARISH",
            level=1.0950,
            occurred_at=end_time - delta,
        )
        structure = StructureEventSummary(
            latest_break=latest_break,
            recent_breaks=(
                latest_break,
                StructureBreak(
                    kind="CHOCH",
                    direction="BEARISH",
                    level=1.0970,
                    occurred_at=end_time - (delta * 2),
                ),
            ),
        )
        smc_context = SmcContextSummary(
            previous_high_low=PreviousHighLowSummary(
                previous_high=1.1060,
                previous_low=1.0940,
                broken_high=False,
                broken_low=True,
                as_of=end_time - delta,
            ),
            retracement=RetracementSummary(
                direction="BEARISH",
                current_retracement_pct=33.0,
                deepest_retracement_pct=52.0,
                as_of=end_time,
            ),
        )
        zones = ActiveZoneSummary(
            order_blocks=(
                OrderBlockSummary(
                    direction="BEARISH",
                    upper_price=1.0980,
                    lower_price=1.0960,
                    created_at=end_time - (delta * 2),
                    is_mitigated=False,
                ),
            )
        )
        metrics = [
            IndicatorMetric(name="macd_hist", value=-0.8, source="talib"),
            IndicatorMetric(name="rsi", value=39.0, source="talib"),
        ]
    else:
        structure = StructureEventSummary()
        smc_context = SmcContextSummary(
            previous_high_low=PreviousHighLowSummary(
                previous_high=1.1060,
                previous_low=1.0940,
                broken_high=False,
                broken_low=False,
                as_of=end_time - delta,
            ),
            retracement=None,
        )
        zones = ActiveZoneSummary()
        metrics = [
            IndicatorMetric(name="macd_hist", value=0.0, source="talib"),
            IndicatorMetric(name="rsi", value=50.0, source="talib"),
        ]

    if adx is not None:
        metrics.append(IndicatorMetric(name="adx", value=adx, source="talib"))

    indicators = IndicatorValueSummary(metrics=tuple(metrics))

    return TimeframeSnapshot(
        instrument="EUR_USD",
        timeframe=timeframe,
        version=version,
        last_completed_candle=end_time,
        computed_at=end_time + timedelta(minutes=1),
        candle_range_start=end_time - (delta * 79),
        candle_range_end=end_time,
        indicators=indicators,
        structure=structure,
        zones=zones,
        liquidity=LiquidityPoolSummary(),
        smc_context=smc_context,
        spread=build_spread(),
        chop=ChopResult(
            status=chop_status,
            reason="synthetic",
            metric_name="adx" if adx is not None else None,
            metric_value=adx,
            threshold=20.0 if adx is not None else None,
        ),
        freshness=build_freshness(timeframe=timeframe, last_completed_candle=end_time),
    )


def build_member(
    *,
    timeframe: str,
    version: int,
    direction: str,
    closes: list[float],
    end_time: datetime = BASE_TIME,
    chop_status: str = "PASS",
    adx: float | None = 30.0,
) -> PinnedHTFMember:
    return PinnedHTFMember(
        snapshot=build_snapshot(
            timeframe=timeframe,
            version=version,
            direction=direction,
            end_time=end_time,
            chop_status=chop_status,
            adx=adx,
        ),
        candles=build_candles(timeframe=timeframe, closes=closes, end_time=end_time),
        source_snapshot_version=version,
    )


def build_previous_high_low_member(
    *,
    timeframe: str,
    version: int,
    previous_high: float,
    previous_low: float,
    broken_high: bool,
    broken_low: bool,
    closes: list[float],
) -> PinnedHTFMember:
    snapshot = build_snapshot(
        timeframe=timeframe,
        version=version,
        direction="NEUTRAL",
    )
    snapshot = snapshot.model_copy(
        update={
            "smc_context": snapshot.smc_context.model_copy(
                update={
                    "previous_high_low": PreviousHighLowSummary(
                        previous_high=previous_high,
                        previous_low=previous_low,
                        broken_high=broken_high,
                        broken_low=broken_low,
                        as_of=BASE_TIME - get_timeframe_delta(timeframe),
                    )
                }
            )
        }
    )
    return PinnedHTFMember(
        snapshot=snapshot,
        candles=build_candles(timeframe=timeframe, closes=closes, end_time=BASE_TIME),
        source_snapshot_version=version,
    )


def test_bullish_consensus_surfaces_bullish_bias() -> None:
    analyzer = HTFBiasAnalyzer(tuning=build_tuning(), logger=DummyLogger())
    result = analyzer.compute(
        [
            build_member(timeframe="D", version=1, direction="BULLISH", closes=[100 + (i * 0.20) for i in range(80)]),
            build_member(timeframe="H4", version=1, direction="BULLISH", closes=[90 + (i * 0.15) for i in range(80)]),
            build_member(timeframe="H1", version=1, direction="BULLISH", closes=[80 + (i * 0.10) for i in range(80)]),
        ]
    )

    assert result.direction == "BULLISH"
    assert result.timeframe_votes == {"D": "BULLISH", "H4": "BULLISH", "H1": "BULLISH"}
    assert result.alignment_score == 1.0
    assert result.is_transitioning is False
    assert result.regime_changepoints == ()


def test_bearish_consensus_surfaces_bearish_bias() -> None:
    analyzer = HTFBiasAnalyzer(tuning=build_tuning(), logger=DummyLogger())
    result = analyzer.compute(
        [
            build_member(timeframe="D", version=1, direction="BEARISH", closes=[120 - (i * 0.20) for i in range(80)]),
            build_member(timeframe="H4", version=1, direction="BEARISH", closes=[110 - (i * 0.15) for i in range(80)]),
            build_member(timeframe="H1", version=1, direction="BEARISH", closes=[100 - (i * 0.10) for i in range(80)]),
        ]
    )

    assert result.direction == "BEARISH"
    assert result.timeframe_votes == {"D": "BEARISH", "H4": "BEARISH", "H1": "BEARISH"}
    assert result.alignment_score == pytest.approx(0.95)
    assert result.is_transitioning is False
    assert result.regime_changepoints == ()


def test_balanced_htf_conflict_resolves_to_neutral() -> None:
    analyzer = HTFBiasAnalyzer(tuning=build_tuning(), logger=DummyLogger())
    result = analyzer.compute(
        [
            build_member(timeframe="D", version=1, direction="BULLISH", closes=[100 + (i * 0.20) for i in range(80)]),
            build_member(timeframe="H4", version=1, direction="BEARISH", closes=[110 - (i * 0.18) for i in range(80)]),
            build_member(
                timeframe="H1",
                version=1,
                direction="NEUTRAL",
                closes=[100.0 + ((i % 4) * 0.02) for i in range(80)],
                chop_status="REJECT",
                adx=12.0,
            ),
        ]
    )

    assert result.direction == "NEUTRAL"
    assert result.timeframe_votes["D"] == "BULLISH"
    assert result.timeframe_votes["H4"] == "BEARISH"
    assert result.alignment_score == 0.0
    assert result.is_transitioning is False


def test_range_bound_low_conviction_members_stay_neutral() -> None:
    analyzer = HTFBiasAnalyzer(tuning=build_tuning(), logger=DummyLogger())
    result = analyzer.compute(
        [
            build_member(
                timeframe="D",
                version=1,
                direction="NEUTRAL",
                closes=[100.0 + ((i % 4) * 0.02) for i in range(80)],
                chop_status="REJECT",
                adx=12.0,
            ),
            build_member(
                timeframe="H4",
                version=1,
                direction="NEUTRAL",
                closes=[101.0 + ((i % 5) * 0.02) for i in range(80)],
                chop_status="CAUTION",
                adx=18.0,
            ),
            build_member(
                timeframe="H1",
                version=1,
                direction="NEUTRAL",
                closes=[99.8 + ((i % 3) * 0.02) for i in range(80)],
                chop_status="REJECT",
                adx=10.0,
            ),
        ]
    )

    assert result.direction == "NEUTRAL"
    assert set(result.timeframe_votes.values()) == {"NEUTRAL"}
    assert result.alignment_score == 0.0
    assert result.regime_changepoints == ()


def test_reclaimed_previous_low_does_not_count_as_bearish_acceptance() -> None:
    analyzer = HTFBiasAnalyzer(tuning=build_tuning(), logger=DummyLogger())
    reclaimed = build_previous_high_low_member(
        timeframe="H4",
        version=1,
        previous_high=1.1060,
        previous_low=1.0940,
        broken_high=False,
        broken_low=True,
        closes=[1.1000, 1.0998, 1.1002, 1.1005],
    )
    accepted = build_previous_high_low_member(
        timeframe="H4",
        version=1,
        previous_high=1.1060,
        previous_low=1.0940,
        broken_high=False,
        broken_low=True,
        closes=[1.0960, 1.0952, 1.0941, 1.0935],
    )

    reclaimed_evidence = analyzer._score_timeframe(reclaimed, neutral_band=build_tuning().neutral_band)
    accepted_evidence = analyzer._score_timeframe(accepted, neutral_band=build_tuning().neutral_band)

    assert reclaimed_evidence.score == pytest.approx(0.0)
    assert reclaimed_evidence.vote == "NEUTRAL"
    assert accepted_evidence.score == pytest.approx(-0.20)
    assert accepted_evidence.vote == "BEARISH"


def test_reclaimed_previous_high_does_not_count_as_bullish_acceptance() -> None:
    analyzer = HTFBiasAnalyzer(tuning=build_tuning(), logger=DummyLogger())
    reclaimed = build_previous_high_low_member(
        timeframe="H4",
        version=1,
        previous_high=1.1060,
        previous_low=1.0940,
        broken_high=True,
        broken_low=False,
        closes=[1.1035, 1.1041, 1.1050, 1.1055],
    )
    accepted = build_previous_high_low_member(
        timeframe="H4",
        version=1,
        previous_high=1.1060,
        previous_low=1.0940,
        broken_high=True,
        broken_low=False,
        closes=[1.1045, 1.1058, 1.1061, 1.1068],
    )

    reclaimed_evidence = analyzer._score_timeframe(reclaimed, neutral_band=build_tuning().neutral_band)
    accepted_evidence = analyzer._score_timeframe(accepted, neutral_band=build_tuning().neutral_band)

    assert reclaimed_evidence.score == pytest.approx(0.0)
    assert reclaimed_evidence.vote == "NEUTRAL"
    assert accepted_evidence.score == pytest.approx(0.20)
    assert accepted_evidence.vote == "BULLISH"


def test_smooth_trends_do_not_surface_false_positive_changepoints() -> None:
    analyzer = HTFBiasAnalyzer(tuning=build_tuning(), logger=DummyLogger())
    result = analyzer.compute(
        [
            build_member(timeframe="D", version=1, direction="BULLISH", closes=[100 + (i * 0.20) for i in range(80)]),
            build_member(timeframe="H4", version=1, direction="BULLISH", closes=[90 + (i * 0.18) for i in range(80)]),
            build_member(timeframe="H1", version=1, direction="BULLISH", closes=[80 + (i * 0.16) for i in range(80)]),
        ]
    )

    assert result.regime_changepoints == ()
    assert result.last_changepoint_bars_ago is None
    assert result.is_transitioning is False


def test_recent_agreeing_changepoint_sets_transition_state_and_caps_alignment() -> None:
    analyzer = HTFBiasAnalyzer(tuning=build_tuning(penalty=2.0), logger=DummyLogger())
    result = analyzer.compute(
        [
            build_member(timeframe="D", version=1, direction="BULLISH", closes=[100 + (i * 0.20) for i in range(80)]),
            build_member(timeframe="H4", version=1, direction="BULLISH", closes=[90 + (i * 0.16) for i in range(80)]),
            build_member(
                timeframe="H1",
                version=1,
                direction="BULLISH",
                closes=[100 + (i * 0.05) for i in range(24)] + [101.2 + (i * 0.60) for i in range(6)],
            ),
        ]
    )

    assert result.direction == "BULLISH"
    assert result.is_transitioning is True
    assert result.alignment_score == 0.55
    assert len(result.regime_changepoints) == 1
    assert result.regime_changepoints[0].timeframe == "H1"
    assert result.regime_changepoints[0].bars_ago == 4
    assert result.last_changepoint_bars_ago == 4


def test_conflicting_recent_changepoint_forces_neutral() -> None:
    analyzer = HTFBiasAnalyzer(tuning=build_tuning(penalty=2.0), logger=DummyLogger())
    result = analyzer.compute(
        [
            build_member(timeframe="D", version=1, direction="BEARISH", closes=[120 - (i * 0.20) for i in range(80)]),
            build_member(timeframe="H4", version=1, direction="BEARISH", closes=[110 - (i * 0.16) for i in range(80)]),
            build_member(
                timeframe="H1",
                version=1,
                direction="BULLISH",
                closes=[100 + (i * 0.05) for i in range(24)] + [101.2 + (i * 0.60) for i in range(6)],
            ),
        ]
    )

    assert result.direction == "NEUTRAL"
    assert result.is_transitioning is True
    assert result.alignment_score == 0.0
    assert result.timeframe_votes["D"] == "BEARISH"
    assert result.timeframe_votes["H4"] == "BEARISH"
    assert result.timeframe_votes["H1"] == "BULLISH"
