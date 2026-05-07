"""Pure normalization helpers for OANDA transaction-backed trade history."""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from datetime import datetime, timezone
import json
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo

from core.models import FinancingEvent, TradeHistoryEvent


def normalize_transactions(
    transactions: Iterable[Mapping[str, Any]],
    *,
    journal_timezone: str,
) -> list[TradeHistoryEvent | FinancingEvent]:
    """Normalize multiple OANDA transactions into stored journal rows."""

    normalized: list[TradeHistoryEvent | FinancingEvent] = []
    for transaction in transactions:
        normalized.extend(normalize_transaction(transaction, journal_timezone=journal_timezone))
    return normalized


def normalize_transaction(
    transaction: Mapping[str, Any],
    *,
    journal_timezone: str,
) -> list[TradeHistoryEvent | FinancingEvent]:
    """Normalize one OANDA transaction into zero or more journal rows."""

    transaction_type = str(transaction.get("type") or "").strip().upper()
    if transaction_type == "ORDER_FILL":
        return _normalize_order_fill(transaction, journal_timezone=journal_timezone)
    if transaction_type == "DAILY_FINANCING":
        return _normalize_daily_financing(transaction, journal_timezone=journal_timezone)
    return []


def parse_oanda_timestamp(value: Any) -> datetime:
    """Parse an OANDA RFC3339 timestamp into UTC."""

    if value is None:
        raise ValueError("OANDA timestamp is missing.")
    text = str(value).replace("Z", "+00:00")
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def decimal_from(value: Any) -> Decimal:
    """Return a Decimal from OANDA string-or-number payloads."""

    if value is None:
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _normalize_order_fill(
    transaction: Mapping[str, Any],
    *,
    journal_timezone: str,
) -> list[TradeHistoryEvent]:
    time_utc = parse_oanda_timestamp(transaction.get("time"))
    time_local = time_utc.astimezone(ZoneInfo(journal_timezone))
    transaction_id = _required_string(transaction, "id")
    account_id = _required_string(transaction, "accountID")
    instrument = _required_string(transaction, "instrument")
    order_id = _optional_string(transaction.get("orderID"))
    batch_id = _optional_string(transaction.get("batchID"))
    reason = _optional_string(transaction.get("reason"))
    fallback_units = decimal_from(transaction.get("units"))
    fallback_price = _optional_decimal(transaction.get("price"))
    raw_json = _serialize_raw_json(transaction)

    legs: list[dict[str, Any]] = []
    trade_opened = transaction.get("tradeOpened")
    if isinstance(trade_opened, Mapping):
        opened_units = decimal_from(trade_opened.get("units", transaction.get("units")))
        legs.append(
            {
                "event_type": "OPEN",
                "trade_id": _required_string(trade_opened, "tradeID"),
                "units": opened_units,
                "price": _optional_decimal(trade_opened.get("price")) or fallback_price,
                "realized_pl": Decimal("0"),
                "financing": Decimal("0"),
            }
        )

    trades_closed = transaction.get("tradesClosed")
    if isinstance(trades_closed, list):
        for closed in trades_closed:
            if not isinstance(closed, Mapping):
                continue
            closed_units = decimal_from(closed.get("units"))
            legs.append(
                {
                    "event_type": "CLOSE",
                    "trade_id": _required_string(closed, "tradeID"),
                    "units": closed_units,
                    "price": _optional_decimal(closed.get("price")) or fallback_price,
                    "realized_pl": decimal_from(closed.get("realizedPL")),
                    "financing": decimal_from(closed.get("financing")),
                }
            )

    trade_reduced = transaction.get("tradeReduced")
    if isinstance(trade_reduced, Mapping):
        reduced_units = decimal_from(trade_reduced.get("units"))
        legs.append(
            {
                "event_type": "PARTIAL_CLOSE",
                "trade_id": _required_string(trade_reduced, "tradeID"),
                "units": reduced_units,
                "price": _optional_decimal(trade_reduced.get("price")) or fallback_price,
                "realized_pl": decimal_from(trade_reduced.get("realizedPL")),
                "financing": decimal_from(trade_reduced.get("financing")),
            }
        )

    commission_allocations = _allocate_commission(
        total_commission=decimal_from(transaction.get("commission")),
        units=[decimal_from(leg["units"]) for leg in legs],
    )

    normalized: list[TradeHistoryEvent] = []
    for index, leg in enumerate(legs):
        units = decimal_from(leg["units"])
        side = _resolve_side(units, fallback_units)
        commission = commission_allocations[index]
        realized_pl = decimal_from(leg["realized_pl"])
        financing = decimal_from(leg["financing"])
        normalized.append(
            TradeHistoryEvent(
                event_id=f"{transaction_id}:{leg['event_type']}:{leg['trade_id']}",
                transaction_id=transaction_id,
                batch_id=batch_id,
                event_type=leg["event_type"],
                account_id=account_id,
                instrument=instrument,
                trade_id=leg["trade_id"],
                order_id=order_id,
                units=units,
                abs_units=abs(units),
                side=side,
                price=leg["price"],
                realized_pl=realized_pl,
                financing=financing,
                commission=commission,
                net_realized_pl=realized_pl + financing - commission,
                time_utc=time_utc,
                time_local=time_local,
                reason=reason,
                raw_json=raw_json,
            )
        )
    return normalized


def _normalize_daily_financing(
    transaction: Mapping[str, Any],
    *,
    journal_timezone: str,
) -> list[FinancingEvent]:
    time_utc = parse_oanda_timestamp(transaction.get("time"))
    time_local = time_utc.astimezone(ZoneInfo(journal_timezone))
    transaction_id = _required_string(transaction, "id")
    account_id = _required_string(transaction, "accountID")
    raw_json = _serialize_raw_json(transaction)

    position_financings = transaction.get("positionFinancings")
    if isinstance(position_financings, list) and position_financings:
        events: list[FinancingEvent] = []
        for item in position_financings:
            if not isinstance(item, Mapping):
                continue
            instrument = _optional_string(item.get("instrument"))
            events.append(
                FinancingEvent(
                    event_id=f"{transaction_id}:DAILY_FINANCING:{instrument or 'ACCOUNT'}",
                    transaction_id=transaction_id,
                    account_id=account_id,
                    instrument=instrument,
                    financing=decimal_from(item.get("financing")),
                    time_utc=time_utc,
                    time_local=time_local,
                    raw_json=raw_json,
                )
            )
        if events:
            return events

    return [
        FinancingEvent(
            event_id=f"{transaction_id}:DAILY_FINANCING:ACCOUNT",
            transaction_id=transaction_id,
            account_id=account_id,
            instrument=None,
            financing=decimal_from(transaction.get("financing")),
            time_utc=time_utc,
            time_local=time_local,
            raw_json=raw_json,
        )
    ]


def _allocate_commission(
    *,
    total_commission: Decimal,
    units: list[Decimal],
) -> list[Decimal]:
    if not units:
        return []
    total_abs_units = sum(abs(unit) for unit in units)
    if total_abs_units == 0:
        return [Decimal("0") for _ in units]

    quantum = _decimal_quantum(total_commission)
    allocations: list[Decimal] = []
    running_total = Decimal("0")
    for index, unit in enumerate(units):
        if index == len(units) - 1:
            allocation = total_commission - running_total
        else:
            raw_share = (total_commission * abs(unit)) / total_abs_units
            allocation = raw_share.quantize(quantum, rounding=ROUND_HALF_UP)
            running_total += allocation
        allocations.append(allocation)
    return allocations


def _decimal_quantum(value: Decimal) -> Decimal:
    exponent = value.as_tuple().exponent
    if exponent >= 0:
        return Decimal("1")
    return Decimal("1").scaleb(exponent)


def _resolve_side(units: Decimal, fallback_units: Decimal) -> str:
    signed_units = units if units != 0 else fallback_units
    return "LONG" if signed_units > 0 else "SHORT"


def _serialize_raw_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=_json_default)


def _json_default(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _required_string(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if value is None or not str(value).strip():
        raise ValueError(f"Missing required transaction field {key!r}.")
    return str(value)


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _optional_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value))


__all__ = [
    "decimal_from",
    "normalize_transaction",
    "normalize_transactions",
    "parse_oanda_timestamp",
]
