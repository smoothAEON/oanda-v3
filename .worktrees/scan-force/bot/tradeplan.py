"""Read-only Stage 16 trade-plan and Fibonacci helpers."""

from __future__ import annotations

from datetime import datetime, timezone

from core.models import FibLevel, FibSummary, InstrumentBundle, TimeframeSnapshot, TradePlanSummary

_FIB_RATIOS: tuple[tuple[str, float], ...] = (
    ("23.6%", 0.236),
    ("38.2%", 0.382),
    ("50.0%", 0.500),
    ("61.8%", 0.618),
    ("78.6%", 0.786),
)


def build_fib_summary(snapshot: TimeframeSnapshot) -> FibSummary | None:
    """Build a bounded Fibonacci ladder from published swing and retracement context."""

    retracement = snapshot.smc_context.retracement
    high = snapshot.structure.latest_swing_high
    low = snapshot.structure.latest_swing_low
    if retracement is None or retracement.direction is None or high is None or low is None:
        return None

    anchor_high = max(high.level, low.level)
    anchor_low = min(high.level, low.level)
    price_range = anchor_high - anchor_low
    if price_range <= 0:
        return None

    levels: list[FibLevel] = []
    for label, ratio in _FIB_RATIOS:
        if retracement.direction == "BULLISH":
            price = anchor_high - (price_range * ratio)
        else:
            price = anchor_low + (price_range * ratio)
        levels.append(FibLevel(label=label, ratio=ratio, price=price))

    return FibSummary(
        instrument=snapshot.instrument,
        timeframe=snapshot.timeframe,
        direction=retracement.direction,
        anchor_high=anchor_high,
        anchor_low=anchor_low,
        current_price=None,
        levels=tuple(levels),
        as_of=retracement.as_of or snapshot.computed_at,
    )


def build_trade_plan(
    *,
    instrument: str,
    bundle: InstrumentBundle,
    h1_snapshot: TimeframeSnapshot,
    m15_snapshot: TimeframeSnapshot,
) -> TradePlanSummary:
    """Build a deterministic read-only trade plan from published bundle and snapshots."""

    computed_at = max(bundle.created_at, h1_snapshot.computed_at, m15_snapshot.computed_at)
    rejection_reasons: list[str] = []
    rationale: list[str] = []

    if bundle.htf_bias.direction == "NEUTRAL":
        rejection_reasons.append("HTF bias is neutral.")
        direction = None
    else:
        direction = "LONG" if bundle.htf_bias.direction == "BULLISH" else "SHORT"
        rationale.append(f"HTF bias {bundle.htf_bias.direction}.")

    if not h1_snapshot.spread.is_acceptable or not m15_snapshot.spread.is_acceptable:
        rejection_reasons.append("Spread gate rejected the setup.")
    if h1_snapshot.chop.status == "REJECT" or m15_snapshot.chop.status == "REJECT":
        rejection_reasons.append("Chop filter rejected the setup.")

    trigger, trigger_timeframe, entry_low, entry_high, invalidation = _select_trigger(
        direction=direction,
        h1_snapshot=h1_snapshot,
        m15_snapshot=m15_snapshot,
    )
    if trigger is None:
        rejection_reasons.append("No qualifying trigger was found on H1 or M15.")

    if trigger is not None:
        rationale.append(trigger)

    target_price = _select_target_price(
        direction=direction,
        h1_snapshot=h1_snapshot,
        m15_snapshot=m15_snapshot,
        entry_low=entry_low,
        entry_high=entry_high,
    )
    if direction is not None and target_price is None:
        rejection_reasons.append("No read-only target level was available.")

    reward_risk = _compute_reward_risk(
        direction=direction,
        entry_low=entry_low,
        entry_high=entry_high,
        invalidation_price=invalidation,
        target_price=target_price,
    )
    if direction is not None and reward_risk is not None and reward_risk < 1.0:
        rejection_reasons.append("Reward/risk is below 1.0.")

    if rejection_reasons:
        return TradePlanSummary(
            instrument=instrument,
            valid=False,
            rejection_reasons=tuple(rejection_reasons),
            computed_at=computed_at,
        )

    assert direction is not None
    assert trigger is not None
    assert trigger_timeframe is not None
    assert entry_low is not None
    assert entry_high is not None
    assert invalidation is not None
    assert target_price is not None
    assert reward_risk is not None
    return TradePlanSummary(
        instrument=instrument,
        direction=direction,
        valid=True,
        setup=trigger,
        trigger_timeframe=trigger_timeframe,
        entry_low=entry_low,
        entry_high=entry_high,
        invalidation_price=invalidation,
        target_price=target_price,
        reward_risk=reward_risk,
        rationale=tuple(rationale),
        computed_at=computed_at,
    )


def _select_trigger(
    *,
    direction: str | None,
    h1_snapshot: TimeframeSnapshot,
    m15_snapshot: TimeframeSnapshot,
) -> tuple[str | None, str | None, float | None, float | None, float | None]:
    if direction is None:
        return None, None, None, None, None

    target_direction = "BULLISH" if direction == "LONG" else "BEARISH"

    for timeframe, snapshot in (("M15", m15_snapshot), ("H1", h1_snapshot)):
        order_block = next(
            (item for item in snapshot.zones.order_blocks if item.direction == target_direction),
            None,
        )
        if order_block is not None:
            invalidation = order_block.lower_price if direction == "LONG" else order_block.upper_price
            return (
                "Order-block retest",
                timeframe,
                min(order_block.lower_price, order_block.upper_price),
                max(order_block.lower_price, order_block.upper_price),
                invalidation,
            )

        sfp = snapshot.sfp
        if sfp.detected and _direction_matches_pattern(direction, sfp.direction):
            entry_low = min(sfp.reference_level or sfp.close_price or sfp.sweep_price, sfp.close_price or sfp.reference_level or sfp.sweep_price)
            entry_high = max(sfp.reference_level or sfp.close_price or sfp.sweep_price, sfp.close_price or sfp.reference_level or sfp.sweep_price)
            invalidation = min(entry_low, sfp.sweep_price or entry_low) if direction == "LONG" else max(entry_high, sfp.sweep_price or entry_high)
            return ("SFP confirmation", timeframe, entry_low, entry_high, invalidation)

        turtle = snapshot.turtle_soup
        if turtle.detected and _direction_matches_pattern(direction, turtle.direction):
            entry_low = min(
                turtle.reference_level or turtle.close_price or turtle.sweep_price,
                turtle.close_price or turtle.reference_level or turtle.sweep_price,
            )
            entry_high = max(
                turtle.reference_level or turtle.close_price or turtle.sweep_price,
                turtle.close_price or turtle.reference_level or turtle.sweep_price,
            )
            invalidation = min(entry_low, turtle.sweep_price or entry_low) if direction == "LONG" else max(entry_high, turtle.sweep_price or entry_high)
            return ("Turtle Soup confirmation", timeframe, entry_low, entry_high, invalidation)

    return None, None, None, None, None


def _select_target_price(
    *,
    direction: str | None,
    h1_snapshot: TimeframeSnapshot,
    m15_snapshot: TimeframeSnapshot,
    entry_low: float | None,
    entry_high: float | None,
) -> float | None:
    if direction is None or entry_low is None or entry_high is None:
        return None

    candidates: list[float] = []
    reference = entry_high if direction == "LONG" else entry_low

    for snapshot in (m15_snapshot, h1_snapshot):
        previous = snapshot.smc_context.previous_high_low
        if previous is not None:
            if direction == "LONG" and previous.previous_high is not None and previous.previous_high > reference:
                candidates.append(previous.previous_high)
            if direction == "SHORT" and previous.previous_low is not None and previous.previous_low < reference:
                candidates.append(previous.previous_low)

        for liquidity in snapshot.liquidity.levels:
            if direction == "LONG" and liquidity.side == "SELL_SIDE" and liquidity.price > reference:
                candidates.append(liquidity.price)
            if direction == "SHORT" and liquidity.side == "BUY_SIDE" and liquidity.price < reference:
                candidates.append(liquidity.price)

        if direction == "LONG" and snapshot.structure.latest_swing_high is not None:
            price = snapshot.structure.latest_swing_high.level
            if price > reference:
                candidates.append(price)
        if direction == "SHORT" and snapshot.structure.latest_swing_low is not None:
            price = snapshot.structure.latest_swing_low.level
            if price < reference:
                candidates.append(price)

    if not candidates:
        return None
    return min(candidates) if direction == "LONG" else max(candidates)


def _compute_reward_risk(
    *,
    direction: str | None,
    entry_low: float | None,
    entry_high: float | None,
    invalidation_price: float | None,
    target_price: float | None,
) -> float | None:
    if (
        direction is None
        or entry_low is None
        or entry_high is None
        or invalidation_price is None
        or target_price is None
    ):
        return None

    entry_mid = (entry_low + entry_high) / 2.0
    if direction == "LONG":
        risk = entry_mid - invalidation_price
        reward = target_price - entry_mid
    else:
        risk = invalidation_price - entry_mid
        reward = entry_mid - target_price
    if risk <= 0 or reward <= 0:
        return None
    return reward / risk


def _direction_matches_pattern(direction: str, pattern_direction: str | None) -> bool:
    if pattern_direction is None:
        return False
    return (direction == "LONG" and pattern_direction == "BULLISH") or (
        direction == "SHORT" and pattern_direction == "BEARISH"
    )


def empty_trade_plan(instrument: str, *reasons: str) -> TradePlanSummary:
    """Return an invalid trade plan with explicit rejection reasons."""

    return TradePlanSummary(
        instrument=instrument,
        valid=False,
        rejection_reasons=tuple(reason for reason in reasons if reason),
        computed_at=datetime.now(timezone.utc),
    )


__all__ = [
    "build_fib_summary",
    "build_trade_plan",
    "empty_trade_plan",
]
