from __future__ import annotations

import pytest

from core.instrument_registry import INSTRUMENT_REGISTRY, SCAN_INSTRUMENTS, get_instrument_spec


def test_registry_contains_exactly_the_supported_scan_instruments() -> None:
    assert tuple(INSTRUMENT_REGISTRY) == SCAN_INSTRUMENTS
    assert len(INSTRUMENT_REGISTRY) == 12


@pytest.mark.parametrize(
    ("symbol", "pip_size", "pip_value_per_lot", "typical", "maximum", "spike", "lot_size", "category"),
    [
        ("XAU_USD", 0.01, 1.0, 25.0, 80.0, 3.0, 100, "metal"),
        ("XAG_USD", 0.0001, 0.5, 200.0, 600.0, 3.0, 5_000, "metal"),
        ("EUR_USD", 0.0001, 10.0, 0.3, 3.0, 5.0, 100_000, "major_fx"),
        ("USD_JPY", 0.01, 1_000.0, 0.5, 3.0, 5.0, 100_000, "major_fx"),
        ("GBP_JPY", 0.01, 1_000.0, 1.2, 6.0, 3.5, 100_000, "minor_fx"),
    ],
)
def test_registry_preserves_explicit_pip_and_spread_metadata(
    symbol: str,
    pip_size: float,
    pip_value_per_lot: float,
    typical: float,
    maximum: float,
    spike: float,
    lot_size: int,
    category: str,
) -> None:
    spec = get_instrument_spec(symbol)

    assert spec.symbol == symbol
    assert spec.pip_size == pip_size
    assert spec.pip_value_per_lot == pip_value_per_lot
    assert spec.typical_spread_pips == typical
    assert spec.max_spread_pips == maximum
    assert spec.spike_multiplier == spike
    assert spec.lot_size == lot_size
    assert spec.category == category


def test_unknown_instrument_raises_loudly() -> None:
    with pytest.raises(KeyError) as excinfo:
        get_instrument_spec("BTC_USD")

    assert "Unsupported instrument" in str(excinfo.value)
