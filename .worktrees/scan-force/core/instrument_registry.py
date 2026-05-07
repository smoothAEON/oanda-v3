"""Instrument registry and metadata for supported scan instruments."""

from __future__ import annotations

from dataclasses import dataclass

INSTRUMENT_ALIASES = {
    "gold": "XAU_USD",
    "silver": "XAG_USD",
    "oil": "WTICO_USD",
    "btc": "BTC_USD",
    "eth": "ETH_USD",
}

SCAN_INSTRUMENTS = (
    "XAU_USD",
    "XAG_USD",
    "EUR_USD",
    "GBP_USD",
    "USD_JPY",
    "AUD_USD",
    "USD_CAD",
    "USD_CHF",
    "NZD_USD",
    "EUR_GBP",
    "EUR_JPY",
    "GBP_JPY",
)


@dataclass(frozen=True)
class InstrumentSpec:
    """Explicit instrument metadata used across analysis-stage contracts."""

    symbol: str
    pip_size: float
    pip_value_per_lot: float
    typical_spread_pips: float
    max_spread_pips: float
    spike_multiplier: float
    lot_size: int
    category: str


INSTRUMENT_REGISTRY: dict[str, InstrumentSpec] = {
    "XAU_USD": InstrumentSpec(
        symbol="XAU_USD",
        # OANDA reports XAU_USD pipLocation = -2, so 1 pip = 0.01.
        pip_size=0.01,
        pip_value_per_lot=1.0,
        typical_spread_pips=25.0,
        max_spread_pips=80.0,
        spike_multiplier=3.0,
        lot_size=100,
        category="metal",
    ),
    "XAG_USD": InstrumentSpec(
        symbol="XAG_USD",
        # OANDA reports XAG_USD pipLocation = -4, so 1 pip = 0.0001.
        pip_size=0.0001,
        pip_value_per_lot=0.5,
        typical_spread_pips=200.0,
        max_spread_pips=600.0,
        spike_multiplier=3.0,
        lot_size=5_000,
        category="metal",
    ),
    "EUR_USD": InstrumentSpec(
        symbol="EUR_USD",
        pip_size=0.0001,
        pip_value_per_lot=10.0,
        typical_spread_pips=0.3,
        max_spread_pips=3.0,
        spike_multiplier=5.0,
        lot_size=100_000,
        category="major_fx",
    ),
    "GBP_USD": InstrumentSpec(
        symbol="GBP_USD",
        pip_size=0.0001,
        pip_value_per_lot=10.0,
        typical_spread_pips=0.5,
        max_spread_pips=4.0,
        spike_multiplier=4.0,
        lot_size=100_000,
        category="major_fx",
    ),
    "USD_JPY": InstrumentSpec(
        symbol="USD_JPY",
        pip_size=0.01,
        pip_value_per_lot=1_000.0,
        typical_spread_pips=0.5,
        max_spread_pips=3.0,
        spike_multiplier=5.0,
        lot_size=100_000,
        category="major_fx",
    ),
    "AUD_USD": InstrumentSpec(
        symbol="AUD_USD",
        pip_size=0.0001,
        pip_value_per_lot=10.0,
        typical_spread_pips=0.4,
        max_spread_pips=3.5,
        spike_multiplier=5.0,
        lot_size=100_000,
        category="major_fx",
    ),
    "USD_CAD": InstrumentSpec(
        symbol="USD_CAD",
        pip_size=0.0001,
        pip_value_per_lot=10.0,
        typical_spread_pips=0.5,
        max_spread_pips=4.0,
        spike_multiplier=4.0,
        lot_size=100_000,
        category="major_fx",
    ),
    "USD_CHF": InstrumentSpec(
        symbol="USD_CHF",
        pip_size=0.0001,
        pip_value_per_lot=10.0,
        typical_spread_pips=0.5,
        max_spread_pips=4.0,
        spike_multiplier=4.0,
        lot_size=100_000,
        category="major_fx",
    ),
    "NZD_USD": InstrumentSpec(
        symbol="NZD_USD",
        pip_size=0.0001,
        pip_value_per_lot=10.0,
        typical_spread_pips=0.6,
        max_spread_pips=4.0,
        spike_multiplier=4.0,
        lot_size=100_000,
        category="major_fx",
    ),
    "EUR_GBP": InstrumentSpec(
        symbol="EUR_GBP",
        pip_size=0.0001,
        pip_value_per_lot=10.0,
        typical_spread_pips=0.5,
        max_spread_pips=4.0,
        spike_multiplier=4.0,
        lot_size=100_000,
        category="minor_fx",
    ),
    "EUR_JPY": InstrumentSpec(
        symbol="EUR_JPY",
        pip_size=0.01,
        pip_value_per_lot=1_000.0,
        typical_spread_pips=0.7,
        max_spread_pips=5.0,
        spike_multiplier=4.0,
        lot_size=100_000,
        category="minor_fx",
    ),
    "GBP_JPY": InstrumentSpec(
        symbol="GBP_JPY",
        pip_size=0.01,
        pip_value_per_lot=1_000.0,
        typical_spread_pips=1.2,
        max_spread_pips=6.0,
        spike_multiplier=3.5,
        lot_size=100_000,
        category="minor_fx",
    ),
}


def get_instrument_spec(instrument: str) -> InstrumentSpec:
    """Return the registry entry for a supported instrument.

    Unknown instruments must fail loudly rather than inherit generic defaults.
    """

    try:
        return INSTRUMENT_REGISTRY[instrument]
    except KeyError as exc:
        supported = ", ".join(SCAN_INSTRUMENTS)
        raise KeyError(
            f"Unsupported instrument '{instrument}'. Supported instruments: {supported}."
        ) from exc


def normalize_instrument(instrument: str) -> str:
    """Normalize common instrument aliases and flexible input formats."""

    candidate = instrument.strip()
    if not candidate:
        raise ValueError("Instrument cannot be empty.")

    alias_match = INSTRUMENT_ALIASES.get(candidate.casefold())
    if alias_match is not None:
        return alias_match

    normalized = (
        candidate.upper()
        .replace("/", "_")
        .replace("-", "_")
        .replace(" ", "_")
    )
    normalized = "_".join(part for part in normalized.split("_") if part)

    if "_" not in normalized and normalized.isalpha() and len(normalized) == 6:
        normalized = f"{normalized[:3]}_{normalized[3:]}"

    return normalized


__all__ = [
    "INSTRUMENT_ALIASES",
    "INSTRUMENT_REGISTRY",
    "InstrumentSpec",
    "SCAN_INSTRUMENTS",
    "get_instrument_spec",
    "normalize_instrument",
]
