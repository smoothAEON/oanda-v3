from __future__ import annotations

from datetime import datetime, timedelta, timezone

from bot.tradeplan import build_fib_summary, build_trade_plan
from core.models import (
    ActiveZoneSummary,
    ChopResult,
    HTFBiasResult,
    IndicatorValueSummary,
    InstrumentBundle,
    LiquidityLevelSummary,
    LiquidityPoolSummary,
    OrderBlockSummary,
    PreviousHighLowSummary,
    RetracementSummary,
    SnapshotFreshness,
    SmcContextSummary,
    SpreadResult,
    StructureEventSummary,
    SwingPointSummary,
    TimeframeSnapshot,
)


BASE_TIME = datetime(2026, 3, 29, 0, 0, tzinfo=timezone.utc)


def build_freshness(*, timeframe: str) -> SnapshotFreshness:
    return SnapshotFreshness(
        instrument="EUR_USD",
        timeframe=timeframe,
        last_completed_candle=BASE_TIME,
        fetched_at=BASE_TIME + timedelta(minutes=5),
        source="oanda_api",
        candle_count=500,
        is_fresh=True,
        staleness_seconds=0.0,
    )


def build_snapshot(
    *,
    timeframe: str,
    order_block_direction: str = "BULLISH",
    spread_acceptable: bool = True,
    chop_status: str = "PASS",
) -> TimeframeSnapshot:
    return TimeframeSnapshot(
        instrument="EUR_USD",
        timeframe=timeframe,
        last_completed_candle=BASE_TIME,
        computed_at=BASE_TIME + timedelta(minutes=1),
        candle_range_start=BASE_TIME - (timedelta(hours=1) if timeframe == "H1" else timedelta(minutes=15)),
        candle_range_end=BASE_TIME,
        indicators=IndicatorValueSummary(),
        structure=StructureEventSummary(
            latest_swing_high=SwingPointSummary(kind="HIGH", level=1.1200, occurred_at=BASE_TIME - timedelta(hours=3)),
            latest_swing_low=SwingPointSummary(kind="LOW", level=1.1000, occurred_at=BASE_TIME - timedelta(hours=4)),
        ),
        zones=ActiveZoneSummary(
            order_blocks=(
                OrderBlockSummary(
                    direction=order_block_direction,
                    upper_price=1.1050,
                    lower_price=1.1040,
                    created_at=BASE_TIME - timedelta(hours=1),
                    distance_pips=5.0,
                    is_mitigated=False,
                ),
            )
        ),
        liquidity=LiquidityPoolSummary(
            levels=(
                LiquidityLevelSummary(
                    side="SELL_SIDE",
                    price=1.1160,
                    occurred_at=BASE_TIME - timedelta(hours=2),
                ),
            )
        ),
        smc_context=SmcContextSummary(
            previous_high_low=PreviousHighLowSummary(
                previous_high=1.1170,
                previous_low=1.0980,
                broken_high=False,
                broken_low=False,
                as_of=BASE_TIME,
            ),
            retracement=RetracementSummary(
                direction="BULLISH",
                current_retracement_pct=50.0,
                deepest_retracement_pct=61.8,
                as_of=BASE_TIME,
            ),
        ),
        spread=SpreadResult(
            instrument="EUR_USD",
            raw_spread=0.0002,
            spread_pips=2.0,
            pip_size=0.0001,
            typical_spread_pips=1.0,
            max_spread_pips=3.0,
            is_acceptable=spread_acceptable,
            is_spiking=False,
            spread_ratio=1.0,
        ),
        chop=ChopResult(status=chop_status, reason="placeholder", metric_name="adx", metric_value=30.0, threshold=20.0),
        freshness=build_freshness(timeframe=timeframe),
    )


def build_bundle(*, direction: str) -> InstrumentBundle:
    h1 = build_freshness(timeframe="H1")
    m15 = build_freshness(timeframe="M15")
    return InstrumentBundle(
        instrument="EUR_USD",
        created_at=BASE_TIME + timedelta(minutes=2),
        members={"H1": 1, "M15": 1},
        htf_bias=HTFBiasResult(
            direction=direction,
            alignment_score=1.0 if direction != "NEUTRAL" else 0.0,
            timeframe_votes={"H1": direction if direction != "NEUTRAL" else "NEUTRAL"},
        ),
        calendar=(),
        calendar_version=0,
        mixed_freshness=False,
        stalest_timeframe=None,
        stalest_age_seconds=0.0,
        member_freshness={"H1": h1, "M15": m15},
    )


def test_build_fib_summary_returns_levels_from_published_snapshot() -> None:
    summary = build_fib_summary(build_snapshot(timeframe="H1"))

    assert summary is not None
    assert summary.direction == "BULLISH"
    assert [level.label for level in summary.levels] == ["23.6%", "38.2%", "50.0%", "61.8%", "78.6%"]
    assert summary.anchor_high == 1.1200
    assert summary.anchor_low == 1.1000


def test_build_trade_plan_accepts_matching_order_block_setup() -> None:
    summary = build_trade_plan(
        instrument="EUR_USD",
        bundle=build_bundle(direction="BULLISH"),
        h1_snapshot=build_snapshot(timeframe="H1"),
        m15_snapshot=build_snapshot(timeframe="M15"),
    )

    assert summary.valid is True
    assert summary.direction == "LONG"
    assert summary.setup == "Order-block retest"
    assert summary.trigger_timeframe == "M15"
    assert summary.reward_risk is not None
    assert summary.reward_risk > 1.0


def test_build_trade_plan_rejects_neutral_bias_and_filter_failures() -> None:
    summary = build_trade_plan(
        instrument="EUR_USD",
        bundle=build_bundle(direction="NEUTRAL"),
        h1_snapshot=build_snapshot(timeframe="H1", spread_acceptable=False, chop_status="REJECT"),
        m15_snapshot=build_snapshot(timeframe="M15", order_block_direction="BEARISH", spread_acceptable=False, chop_status="REJECT"),
    )

    assert summary.valid is False
    assert "HTF bias is neutral." in summary.rejection_reasons
    assert "Spread gate rejected the setup." in summary.rejection_reasons
    assert "Chop filter rejected the setup." in summary.rejection_reasons
    assert "No qualifying trigger was found on H1 or M15." in summary.rejection_reasons
