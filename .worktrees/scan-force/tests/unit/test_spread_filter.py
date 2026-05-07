from __future__ import annotations

from datetime import datetime, timezone

import pytest

from filters.spread import evaluate_spread
from providers.base import PriceSnapshot


BASE_TIME = datetime(2026, 3, 20, 10, 0, tzinfo=timezone.utc)


def build_price(*, instrument: str, bid: float, ask: float) -> PriceSnapshot:
    return PriceSnapshot(
        instrument=instrument,
        bid=bid,
        ask=ask,
        spread_price=ask - bid,
        spread_pips=0.0,
        fetched_at=BASE_TIME,
    )


def test_spread_filter_uses_registry_thresholds_for_fx_pair() -> None:
    result = evaluate_spread(build_price(instrument="EUR_USD", bid=1.1000, ask=1.10003))

    assert result.instrument == "EUR_USD"
    assert result.spread_pips == pytest.approx(0.3)
    assert result.is_acceptable is True
    assert result.is_spiking is False
    assert result.spread_ratio == pytest.approx(1.0)


def test_spread_filter_uses_gold_specific_pip_handling() -> None:
    result = evaluate_spread(build_price(instrument="XAU_USD", bid=3000.0, ask=3000.5))

    assert result.instrument == "XAU_USD"
    assert result.raw_spread == pytest.approx(0.5)
    assert result.spread_pips == pytest.approx(50.0)
    assert result.max_spread_pips == 80.0
    assert result.is_acceptable is True
    assert result.is_spiking is False


def test_spread_filter_uses_silver_specific_pip_handling() -> None:
    result = evaluate_spread(build_price(instrument="XAG_USD", bid=30.0000, ask=30.0200))

    assert result.instrument == "XAG_USD"
    assert result.raw_spread == pytest.approx(0.0200)
    assert result.spread_pips == pytest.approx(200.0)
    assert result.max_spread_pips == 600.0
    assert result.is_acceptable is True
    assert result.is_spiking is False


def test_spread_filter_detects_spike_against_typical_spread() -> None:
    result = evaluate_spread(build_price(instrument="EUR_USD", bid=1.1000, ask=1.1002))

    assert result.spread_pips == pytest.approx(2.0)
    assert result.is_acceptable is True
    assert result.is_spiking is True


def test_spread_filter_fails_loudly_for_unknown_instruments() -> None:
    with pytest.raises(KeyError):
        evaluate_spread(build_price(instrument="BTC_USD", bid=1.0, ask=1.1))


def test_spread_filter_rejects_inverted_quotes() -> None:
    with pytest.raises(ValueError):
        evaluate_spread(build_price(instrument="EUR_USD", bid=1.1002, ask=1.1000))
