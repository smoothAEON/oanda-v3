from __future__ import annotations

import pytest

from core.instrument_registry import INSTRUMENT_REGISTRY, SCAN_INSTRUMENTS, get_instrument_spec


def test_registry_contains_exactly_the_supported_scan_instruments() -> None:
    assert tuple(INSTRUMENT_REGISTRY) == SCAN_INSTRUMENTS
    assert len(INSTRUMENT_REGISTRY) == 16


@pytest.mark.parametrize(
    ("symbol", "pip_size", "pip_value_per_lot", "lot_size", "category"),
    [
        ("XAU_USD", 0.01, 1.0, 100, "metal"),
        ("SPX500_USD", 1.0, 1.0, 1, "index_cfd"),
        ("XAG_USD", 0.0001, 0.5, 5_000, "metal"),
        ("EUR_USD", 0.0001, 10.0, 100_000, "major_fx"),
        ("USD_JPY", 0.01, 1_000.0, 100_000, "major_fx"),
        ("GBP_JPY", 0.01, 1_000.0, 100_000, "minor_fx"),
        ("BCO_USD", 0.01, 0.01, 1, "energy_cfd"),
        ("WTICO_USD", 0.01, 0.01, 1, "energy_cfd"),
        ("JP225_USD", 1.0, 1.0, 1, "index_cfd"),
    ],
)
def test_registry_preserves_explicit_instrument_metadata(
    symbol: str,
    pip_size: float,
    pip_value_per_lot: float,
    lot_size: int,
    category: str,
) -> None:
    spec = get_instrument_spec(symbol)

    assert spec.symbol == symbol
    assert spec.pip_size == pip_size
    assert spec.pip_value_per_lot == pip_value_per_lot
    assert spec.lot_size == lot_size
    assert spec.category == category


def test_unknown_instrument_raises_loudly() -> None:
    with pytest.raises(KeyError) as excinfo:
        get_instrument_spec("BTC_USD")

    assert "Unsupported instrument" in str(excinfo.value)
