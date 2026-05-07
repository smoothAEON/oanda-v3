"""Instrument registry and metadata for scan and broker-data instruments."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from functools import lru_cache
import json
from typing import TYPE_CHECKING, Any
from urllib.error import URLError
from urllib.request import Request, urlopen

if TYPE_CHECKING:
    from config.settings import Settings

INSTRUMENT_ALIASES = {
    "spx500usd": "SPX500_USD",
    "spx500": "SPX500_USD",
    "spx": "SPX500_USD",
    "us500": "SPX500_USD",
    "us500usd": "SPX500_USD",
    "silver": "XAG_USD",
    "oil": "WTICO_USD",
    "btc": "BTC_USD",
    "eth": "ETH_USD",
}

SCAN_INSTRUMENTS = (
    "XAU_USD",
    "SPX500_USD",
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
    "BCO_USD",
    "WTICO_USD",
    "JP225_USD",
)


@dataclass(frozen=True)
class InstrumentSpec:
    """Explicit instrument metadata used across analysis-stage contracts."""

    symbol: str
    pip_size: float
    pip_value_per_lot: float
    lot_size: int
    category: str


@dataclass(frozen=True)
class OandaInstrumentDefinition:
    """Broker-data instrument metadata loaded from OANDA or static scan fallbacks."""

    symbol: str
    instrument_type: str
    display_precision: int
    pip_location: int
    pip_size: float
    trade_units_precision: int
    minimum_trade_size: float | None
    margin_rate: float | None


INSTRUMENT_REGISTRY: dict[str, InstrumentSpec] = {
    "XAU_USD": InstrumentSpec(
        symbol="XAU_USD",
        # OANDA reports XAU_USD pipLocation = -2, so 1 pip = 0.01.
        pip_size=0.01,
        pip_value_per_lot=1.0,
        lot_size=100,
        category="metal",
    ),
    "SPX500_USD": InstrumentSpec(
        symbol="SPX500_USD",
        pip_size=1.0,
        pip_value_per_lot=1.0,
        lot_size=1,
        category="index_cfd",
    ),
    "XAG_USD": InstrumentSpec(
        symbol="XAG_USD",
        # OANDA reports XAG_USD pipLocation = -4, so 1 pip = 0.0001.
        pip_size=0.0001,
        pip_value_per_lot=0.5,
        lot_size=5_000,
        category="metal",
    ),
    "EUR_USD": InstrumentSpec(
        symbol="EUR_USD",
        pip_size=0.0001,
        pip_value_per_lot=10.0,
        lot_size=100_000,
        category="major_fx",
    ),
    "GBP_USD": InstrumentSpec(
        symbol="GBP_USD",
        pip_size=0.0001,
        pip_value_per_lot=10.0,
        lot_size=100_000,
        category="major_fx",
    ),
    "USD_JPY": InstrumentSpec(
        symbol="USD_JPY",
        pip_size=0.01,
        pip_value_per_lot=1_000.0,
        lot_size=100_000,
        category="major_fx",
    ),
    "AUD_USD": InstrumentSpec(
        symbol="AUD_USD",
        pip_size=0.0001,
        pip_value_per_lot=10.0,
        lot_size=100_000,
        category="major_fx",
    ),
    "USD_CAD": InstrumentSpec(
        symbol="USD_CAD",
        pip_size=0.0001,
        pip_value_per_lot=10.0,
        lot_size=100_000,
        category="major_fx",
    ),
    "USD_CHF": InstrumentSpec(
        symbol="USD_CHF",
        pip_size=0.0001,
        pip_value_per_lot=10.0,
        lot_size=100_000,
        category="major_fx",
    ),
    "NZD_USD": InstrumentSpec(
        symbol="NZD_USD",
        pip_size=0.0001,
        pip_value_per_lot=10.0,
        lot_size=100_000,
        category="major_fx",
    ),
    "EUR_GBP": InstrumentSpec(
        symbol="EUR_GBP",
        pip_size=0.0001,
        pip_value_per_lot=10.0,
        lot_size=100_000,
        category="minor_fx",
    ),
    "EUR_JPY": InstrumentSpec(
        symbol="EUR_JPY",
        pip_size=0.01,
        pip_value_per_lot=1_000.0,
        lot_size=100_000,
        category="minor_fx",
    ),
    "GBP_JPY": InstrumentSpec(
        symbol="GBP_JPY",
        pip_size=0.01,
        pip_value_per_lot=1_000.0,
        lot_size=100_000,
        category="minor_fx",
    ),
    "BCO_USD": InstrumentSpec(
        symbol="BCO_USD",
        pip_size=0.01,
        pip_value_per_lot=0.01,
        lot_size=1,
        category="energy_cfd",
    ),
    "WTICO_USD": InstrumentSpec(
        symbol="WTICO_USD",
        pip_size=0.01,
        pip_value_per_lot=0.01,
        lot_size=1,
        category="energy_cfd",
    ),
    "JP225_USD": InstrumentSpec(
        symbol="JP225_USD",
        pip_size=1.0,
        pip_value_per_lot=1.0,
        lot_size=1,
        category="index_cfd",
    ),
}


def _pip_location_from_size(pip_size: float) -> int:
    try:
        normalized = Decimal(str(pip_size)).normalize()
    except InvalidOperation as exc:
        raise ValueError(f"Invalid pip_size {pip_size!r}.") from exc
    return int(normalized.as_tuple().exponent)


def _display_precision_from_pip_location(pip_location: int) -> int:
    if pip_location >= 0:
        return 1
    return abs(pip_location) + 1


def _scan_definition_from_spec(spec: InstrumentSpec) -> OandaInstrumentDefinition:
    pip_location = _pip_location_from_size(spec.pip_size)
    if spec.category in {"major_fx", "minor_fx"}:
        instrument_type = "CURRENCY"
        trade_units_precision = 0
        minimum_trade_size = 1.0
        margin_rate = 0.05
    elif spec.category == "metal":
        instrument_type = "METAL"
        trade_units_precision = 1
        minimum_trade_size = 0.1 if spec.symbol == "XAU_USD" else 1.0
        margin_rate = 0.2
    elif spec.category == "energy_cfd":
        instrument_type = "CFD"
        trade_units_precision = 0
        minimum_trade_size = 1.0
        margin_rate = 0.2
    elif spec.category == "index_cfd":
        instrument_type = "CFD"
        trade_units_precision = 2
        minimum_trade_size = 0.01
        margin_rate = 0.05
    else:
        instrument_type = "UNKNOWN"
        trade_units_precision = 0
        minimum_trade_size = None
        margin_rate = None
    return OandaInstrumentDefinition(
        symbol=spec.symbol,
        instrument_type=instrument_type,
        display_precision=_display_precision_from_pip_location(pip_location),
        pip_location=pip_location,
        pip_size=spec.pip_size,
        trade_units_precision=trade_units_precision,
        minimum_trade_size=minimum_trade_size,
        margin_rate=margin_rate,
    )


SCAN_INSTRUMENT_DEFINITIONS: dict[str, OandaInstrumentDefinition] = {
    symbol: _scan_definition_from_spec(spec)
    for symbol, spec in INSTRUMENT_REGISTRY.items()
}


def _normalize_catalog_instrument_payload(payload: dict[str, Any]) -> OandaInstrumentDefinition:
    symbol = normalize_instrument(str(payload["name"]))
    pip_location = int(payload["pipLocation"])
    pip_size = float(Decimal("10") ** pip_location)
    minimum_trade_size = payload.get("minimumTradeSize")
    margin_rate = payload.get("marginRate")
    return OandaInstrumentDefinition(
        symbol=symbol,
        instrument_type=str(payload["type"]).strip().upper(),
        display_precision=int(payload["displayPrecision"]),
        pip_location=pip_location,
        pip_size=pip_size,
        trade_units_precision=int(payload["tradeUnitsPrecision"]),
        minimum_trade_size=None if minimum_trade_size is None else float(minimum_trade_size),
        margin_rate=None if margin_rate is None else float(margin_rate),
    )


def _request_oanda_instrument_catalog(
    environment: str,
    account_id: str,
    api_key: str,
) -> dict[str, OandaInstrumentDefinition]:
    host = "api-fxtrade.oanda.com" if environment == "live" else "api-fxpractice.oanda.com"
    request = Request(
        f"https://{host}/v3/accounts/{account_id}/instruments",
        headers={"Authorization": f"Bearer {api_key}"},
    )
    try:
        with urlopen(request, timeout=30) as response:
            payload = json.load(response)
    except URLError as exc:
        raise RuntimeError(f"Unable to load OANDA instrument catalog: {exc}") from exc
    instruments = payload.get("instruments")
    if not isinstance(instruments, list):
        raise RuntimeError("OANDA instrument catalog response did not contain an instruments list.")
    return {
        definition.symbol: definition
        for definition in (
            _normalize_catalog_instrument_payload(item)
            for item in instruments
            if isinstance(item, dict) and item.get("name") is not None
        )
    }


@lru_cache(maxsize=8)
def _load_oanda_instrument_catalog_cached(
    environment: str,
    account_id: str,
    api_key: str,
) -> dict[str, OandaInstrumentDefinition]:
    return _request_oanda_instrument_catalog(environment, account_id, api_key)


def get_oanda_instrument_catalog(
    settings: Settings | None = None,
) -> dict[str, OandaInstrumentDefinition]:
    """Return the live-account OANDA instrument catalog."""

    from config.settings import get_settings

    resolved_settings = settings or get_settings()
    return _load_oanda_instrument_catalog_cached(
        resolved_settings.oanda_environment,
        resolved_settings.oanda_account_id.get_secret_value(),
        resolved_settings.oanda_api_key.get_secret_value(),
    )


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


def get_oanda_instrument_definition(
    instrument: str,
    *,
    settings: Settings | None = None,
) -> OandaInstrumentDefinition:
    """Return broker-data metadata for a live-account OANDA instrument."""

    resolved = normalize_instrument(instrument)
    definition = SCAN_INSTRUMENT_DEFINITIONS.get(resolved)
    if definition is not None:
        return definition

    catalog = get_oanda_instrument_catalog(settings=settings)
    try:
        return catalog[resolved]
    except KeyError as exc:
        raise KeyError(f"Unknown live OANDA instrument '{resolved}'.") from exc


def validate_live_instrument(
    instrument: str,
    *,
    settings: Settings | None = None,
) -> str:
    """Normalize and validate a broker-data instrument against the live OANDA catalog."""

    return get_oanda_instrument_definition(instrument, settings=settings).symbol


def get_pip_size(
    instrument: str,
    *,
    settings: Settings | None = None,
) -> float:
    """Return pip size for scan or broker-data instruments."""

    resolved = normalize_instrument(instrument)
    spec = INSTRUMENT_REGISTRY.get(resolved)
    if spec is not None:
        return spec.pip_size
    return get_oanda_instrument_definition(resolved, settings=settings).pip_size


def ensure_scan_instrument(
    instrument: str,
    *,
    settings: Settings | None = None,
) -> str:
    """Normalize one scan-backed instrument with explicit live-vs-scan errors."""

    resolved = normalize_instrument(instrument)
    if resolved in INSTRUMENT_REGISTRY:
        return resolved

    try:
        validate_live_instrument(resolved, settings=settings)
    except KeyError as exc:
        supported = ", ".join(SCAN_INSTRUMENTS)
        raise KeyError(
            f"Unknown live OANDA instrument '{resolved}'. Scan instruments: {supported}."
        ) from exc

    supported = ", ".join(SCAN_INSTRUMENTS)
    raise KeyError(
        f"Instrument '{resolved}' is a valid live OANDA instrument but is not in the scan universe. "
        f"Scan instruments: {supported}."
    )


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

    if "_" not in normalized:
        if normalized.isalpha() and len(normalized) == 6:
            normalized = f"{normalized[:3]}_{normalized[3:]}"
        elif (
            len(normalized) > 6
            and normalized[-3:].isalpha()
            and normalized[:-3].isalnum()
        ):
            normalized = f"{normalized[:-3]}_{normalized[-3:]}"

    return normalized


__all__ = [
    "INSTRUMENT_ALIASES",
    "INSTRUMENT_REGISTRY",
    "InstrumentSpec",
    "OandaInstrumentDefinition",
    "SCAN_INSTRUMENTS",
    "SCAN_INSTRUMENT_DEFINITIONS",
    "ensure_scan_instrument",
    "get_oanda_instrument_catalog",
    "get_oanda_instrument_definition",
    "get_pip_size",
    "get_instrument_spec",
    "normalize_instrument",
    "validate_live_instrument",
]
