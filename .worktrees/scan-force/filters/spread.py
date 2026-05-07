"""Spread gate placeholder reserved for Stage 08."""

from __future__ import annotations

from core.instrument_registry import get_instrument_spec
from core.logging_setup import get_logger
from core.models import SpreadResult
from providers.base import PriceSnapshot

logger = get_logger(__name__)


def evaluate_spread(price: PriceSnapshot) -> SpreadResult:
    """Evaluate current spread using the registry-backed instrument contract."""

    spec = get_instrument_spec(price.instrument)
    if price.ask < price.bid:
        raise ValueError("ask must be greater than or equal to bid.")

    raw_spread = price.ask - price.bid
    spread_pips = raw_spread / spec.pip_size
    threshold_pips = spec.max_spread_pips
    is_acceptable = spread_pips <= threshold_pips
    is_spiking = spread_pips > (spec.typical_spread_pips * spec.spike_multiplier)
    spread_ratio = spread_pips / spec.typical_spread_pips

    result = SpreadResult(
        instrument=spec.symbol,
        raw_spread=raw_spread,
        spread_pips=spread_pips,
        pip_size=spec.pip_size,
        typical_spread_pips=spec.typical_spread_pips,
        max_spread_pips=spec.max_spread_pips,
        is_acceptable=is_acceptable,
        is_spiking=is_spiking,
        spread_ratio=spread_ratio,
    )
    logger.info(
        "spread_checked",
        instrument=result.instrument,
        spread_pips=result.spread_pips,
        threshold_pips=threshold_pips,
        is_acceptable=result.is_acceptable,
        is_spiking=result.is_spiking,
        spread_ratio=result.spread_ratio,
    )
    return result


__all__ = ["evaluate_spread"]
