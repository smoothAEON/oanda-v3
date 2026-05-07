"""Read-only OANDA account-runtime boundary for the trade-helper path."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from config.settings import Settings, get_settings
from core.candle_policy import get_timeframe_delta, trim_to_closed, validate_candle_df
from core.instrument_registry import get_instrument_spec, normalize_instrument
from core.logging_setup import get_logger, log_failure
from core.models import AccountSummary, OpenTradePosition, PendingOrder
from providers.base import PriceSnapshot


class OandaAccountClient:
    """Read-only OANDA REST wrapper for trade-helper runtime calls."""

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        api_client: object | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._api_client = api_client
        self.logger = get_logger(__name__)

    async def get_open_trades(self) -> list[dict[str, Any]]:
        """Return normalized open-trade payloads."""

        payload = await asyncio.to_thread(self._request_open_trades_payload)
        return self._normalize_open_trades_payload(payload)

    def get_open_trades_sync(self) -> list[dict[str, Any]]:
        """Return normalized open-trade payloads without an async boundary."""

        payload = self._request_open_trades_payload()
        return self._normalize_open_trades_payload(payload)

    def _normalize_open_trades_payload(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        trades = payload.get("trades", [])
        if not isinstance(trades, list):
            self.logger.error(
                "account_open_trades_invalid",
                payload_keys=tuple(sorted(payload.keys())),
            )
            raise RuntimeError("OANDA open-trades response did not contain a trade list.")
        try:
            return [self._normalize_trade(trade) for trade in trades]
        except Exception as exc:
            log_failure(
                self.logger,
                "account_open_trades_normalization_failed",
                exc,
                trade_count=len(trades),
            )
            raise

    async def get_open_orders(self) -> list[PendingOrder]:
        """Return normalized pending-order payloads."""

        payload = await asyncio.to_thread(self._request_open_orders_payload)
        orders = payload.get("orders", [])
        if not isinstance(orders, list):
            self.logger.error(
                "account_open_orders_invalid",
                payload_keys=tuple(sorted(payload.keys())),
            )
            raise RuntimeError("OANDA open-orders response did not contain an order list.")
        try:
            return [self._normalize_order(order) for order in orders]
        except Exception as exc:
            log_failure(
                self.logger,
                "account_open_orders_normalization_failed",
                exc,
                order_count=len(orders),
            )
            raise

    async def get_open_positions(self) -> list[OpenTradePosition]:
        """Return individual live trades as typed open-position rows."""

        trades = await self.get_open_trades()
        return [self._normalize_position(trade) for trade in trades]

    async def get_account_summary(self) -> AccountSummary:
        """Return a typed OANDA account summary."""

        payload = await asyncio.to_thread(self._request_account_summary_payload)
        account = payload.get("account")
        if not isinstance(account, dict):
            self.logger.error(
                "account_summary_invalid",
                payload_keys=tuple(sorted(payload.keys())),
            )
            raise RuntimeError("OANDA account-summary response did not contain an account object.")
        try:
            return self._normalize_account_summary(account)
        except Exception as exc:
            log_failure(
                self.logger,
                "account_summary_normalization_failed",
                exc,
                payload_keys=tuple(sorted(account.keys())),
            )
            raise

    async def get_trade_detail(self, trade_id: str) -> dict[str, Any]:
        """Return one normalized trade-detail payload."""

        resolved_trade_id = trade_id.strip()
        if not resolved_trade_id:
            self.logger.warning("trade_detail_request_invalid", trade_id=trade_id)
            raise ValueError("trade_id must be a non-empty string.")
        payload = await asyncio.to_thread(self._request_trade_detail_payload, resolved_trade_id)
        return self._normalize_trade_detail_payload(payload, resolved_trade_id)

    def get_trade_detail_sync(self, trade_id: str) -> dict[str, Any]:
        """Return one normalized trade-detail payload without an async boundary."""

        resolved_trade_id = trade_id.strip()
        if not resolved_trade_id:
            self.logger.warning("trade_detail_request_invalid", trade_id=trade_id)
            raise ValueError("trade_id must be a non-empty string.")

        payload = self._request_trade_detail_payload(resolved_trade_id)
        return self._normalize_trade_detail_payload(payload, resolved_trade_id)

    def _normalize_trade_detail_payload(
        self,
        payload: dict[str, Any],
        resolved_trade_id: str,
    ) -> dict[str, Any]:
        trade = payload.get("trade")
        if not isinstance(trade, dict):
            self.logger.error(
                "trade_detail_invalid",
                trade_id=resolved_trade_id,
                payload_keys=tuple(sorted(payload.keys())),
            )
            raise RuntimeError("OANDA trade-detail response did not contain a trade object.")
        try:
            return self._normalize_trade(trade)
        except Exception as exc:
            log_failure(
                self.logger,
                "trade_detail_normalization_failed",
                exc,
                trade_id=resolved_trade_id,
                payload_keys=tuple(sorted(trade.keys())),
            )
            raise

    async def get_transaction_detail(self, transaction_id: str) -> dict[str, Any]:
        """Return one normalized transaction-detail payload."""

        resolved_transaction_id = transaction_id.strip()
        if not resolved_transaction_id:
            self.logger.warning("transaction_detail_request_invalid", transaction_id=transaction_id)
            raise ValueError("transaction_id must be a non-empty string.")
        payload = await asyncio.to_thread(
            self._request_transaction_detail_payload,
            resolved_transaction_id,
        )
        return self._normalize_transaction_detail_payload(payload, resolved_transaction_id)

    def get_transaction_detail_sync(self, transaction_id: str) -> dict[str, Any]:
        """Return one normalized transaction-detail payload without an async boundary."""

        resolved_transaction_id = transaction_id.strip()
        if not resolved_transaction_id:
            self.logger.warning("transaction_detail_request_invalid", transaction_id=transaction_id)
            raise ValueError("transaction_id must be a non-empty string.")

        payload = self._request_transaction_detail_payload(resolved_transaction_id)
        return self._normalize_transaction_detail_payload(payload, resolved_transaction_id)

    def _normalize_transaction_detail_payload(
        self,
        payload: dict[str, Any],
        resolved_transaction_id: str,
    ) -> dict[str, Any]:
        transaction = payload.get("transaction")
        if not isinstance(transaction, dict):
            self.logger.error(
                "transaction_detail_invalid",
                transaction_id=resolved_transaction_id,
                payload_keys=tuple(sorted(payload.keys())),
            )
            raise RuntimeError(
                "OANDA transaction-detail response did not contain a transaction object."
            )
        return self._normalize_transaction(transaction)

    async def get_trade_transactions(self, trade_id: str) -> list[dict[str, Any]]:
        """Return normalized closing-related transactions for one trade when available."""

        return await asyncio.to_thread(self.get_trade_transactions_sync, trade_id)

    def get_trade_transactions_sync(self, trade_id: str) -> list[dict[str, Any]]:
        """Return normalized closing-related transactions without an async boundary."""

        detail = self.get_trade_detail_sync(trade_id)
        raw_ids = detail.get("closingTransactionIDs") or detail.get("relatedTransactionIDs") or ()
        if not isinstance(raw_ids, (list, tuple)):
            return []

        transactions: list[dict[str, Any]] = []
        for raw_id in raw_ids:
            try:
                transactions.append(self.get_transaction_detail_sync(str(raw_id)))
            except Exception as exc:
                log_failure(
                    self.logger,
                    "trade_transaction_lookup_failed",
                    exc,
                    trade_id=trade_id,
                    transaction_id=str(raw_id),
                    level="warning",
                )
        return transactions

    async def get_pricing(self, instrument: str) -> PriceSnapshot:
        """Return current bid/ask prices for a supported instrument."""

        resolved_instrument = self._resolve_instrument(instrument)
        spec = get_instrument_spec(resolved_instrument)
        payload = await asyncio.to_thread(self._request_pricing_payload, resolved_instrument)
        prices = payload.get("prices", [])
        if not prices:
            self.logger.error(
                "account_pricing_empty",
                instrument=resolved_instrument,
                payload_keys=tuple(sorted(payload.keys())),
            )
            raise RuntimeError(
                f"OANDA pricing response for {resolved_instrument} contained no prices."
            )

        price = prices[0]
        try:
            bid = float(price["bids"][0]["price"])
            ask = float(price["asks"][0]["price"])
        except (IndexError, KeyError, TypeError, ValueError) as exc:
            log_failure(
                self.logger,
                "account_pricing_invalid",
                exc,
                instrument=resolved_instrument,
                payload_keys=tuple(sorted(price.keys())),
            )
            raise RuntimeError(
                f"OANDA pricing response for {resolved_instrument} is missing bid/ask quotes."
            ) from exc

        spread_price = ask - bid
        return PriceSnapshot(
            instrument=resolved_instrument,
            bid=bid,
            ask=ask,
            spread_price=spread_price,
            spread_pips=spread_price / spec.pip_size,
            fetched_at=self._parse_oanda_time(price.get("time")),
        )

    async def get_candles(
        self,
        instrument: str,
        granularity: str,
        count: int,
    ) -> pd.DataFrame:
        """Return canonical closed candles for a supported instrument and timeframe."""

        resolved_instrument = self._resolve_instrument(instrument)
        get_timeframe_delta(granularity)
        if count <= 0:
            self.logger.warning(
                "account_candles_request_invalid",
                instrument=resolved_instrument,
                granularity=granularity,
                requested_count=count,
            )
            raise ValueError("count must be a positive integer.")

        payload = await asyncio.to_thread(
            self._request_candles_payload,
            resolved_instrument,
            granularity,
            count,
        )
        raw_candles = payload.get("candles", [])
        if not raw_candles:
            self.logger.error(
                "account_candles_empty",
                instrument=resolved_instrument,
                granularity=granularity,
                requested_count=count,
                payload_keys=tuple(sorted(payload.keys())),
            )
            raise RuntimeError(
                f"OANDA candle response for {resolved_instrument} {granularity} contained no candles."
            )

        rows: list[dict[str, object]] = []
        for candle in raw_candles:
            if not candle.get("complete", False):
                continue
            mid = candle.get("mid")
            if not isinstance(mid, dict):
                continue
            try:
                rows.append(
                    {
                        "time": candle["time"],
                        "open": float(mid["o"]),
                        "high": float(mid["h"]),
                        "low": float(mid["l"]),
                        "close": float(mid["c"]),
                        "tick_volume": int(candle["volume"]),
                    }
                )
            except (KeyError, TypeError, ValueError) as exc:
                log_failure(
                    self.logger,
                    "account_candles_invalid",
                    exc,
                    instrument=resolved_instrument,
                    granularity=granularity,
                    candle_time=candle.get("time"),
                    payload_keys=tuple(sorted(candle.keys())),
                )
                raise RuntimeError(
                    f"OANDA candle response for {resolved_instrument} {granularity} contained invalid candle data."
                ) from exc

        if not rows:
            self.logger.error(
                "account_candles_no_complete_rows",
                instrument=resolved_instrument,
                granularity=granularity,
                raw_candle_count=len(raw_candles),
            )
            raise RuntimeError(
                f"OANDA candle response for {resolved_instrument} {granularity} contained no complete candles."
            )

        normalized = validate_candle_df(pd.DataFrame(rows))
        trimmed = trim_to_closed(normalized, granularity)
        if trimmed.empty:
            self.logger.error(
                "account_candles_trimmed_empty",
                instrument=resolved_instrument,
                granularity=granularity,
                normalized_count=len(normalized),
            )
            raise RuntimeError(
                f"OANDA candle response for {resolved_instrument} {granularity} yielded no closed candles after trimming."
            )
        return trimmed

    async def get_bid_ask_candles(
        self,
        instrument: str,
        granularity: str,
        count: int,
    ) -> pd.DataFrame:
        """Return closed bid/ask OHLC candles for extractor CSV exports."""

        resolved_instrument = self._resolve_instrument(instrument)
        get_timeframe_delta(granularity)
        if count <= 0:
            self.logger.warning(
                "account_bid_ask_request_invalid",
                instrument=resolved_instrument,
                granularity=granularity,
                requested_count=count,
            )
            raise ValueError("count must be a positive integer.")

        bid_payload = await asyncio.to_thread(
            self._request_candles_payload,
            resolved_instrument,
            granularity,
            count,
            "B",
        )
        ask_payload = await asyncio.to_thread(
            self._request_candles_payload,
            resolved_instrument,
            granularity,
            count,
            "A",
        )
        try:
            return self._merge_bid_ask_candles(
                instrument=resolved_instrument,
                granularity=granularity,
                bid_payload=bid_payload,
                ask_payload=ask_payload,
            )
        except Exception as exc:
            log_failure(
                self.logger,
                "account_bid_ask_merge_failed",
                exc,
                instrument=resolved_instrument,
                granularity=granularity,
                requested_count=count,
            )
            raise

    def _request_open_trades_payload(self) -> dict[str, Any]:
        endpoint_class = self._import_open_trades_endpoint()
        endpoint = endpoint_class(accountID=self.settings.oanda_account_id.get_secret_value())
        return self._request_endpoint(
            endpoint,
            event="oanda_open_trades_request_failed",
        )

    def _request_open_orders_payload(self) -> dict[str, Any]:
        endpoint_class = self._import_open_orders_endpoint()
        endpoint = endpoint_class(accountID=self.settings.oanda_account_id.get_secret_value())
        return self._request_endpoint(
            endpoint,
            event="oanda_open_orders_request_failed",
        )

    def _request_account_summary_payload(self) -> dict[str, Any]:
        endpoint_class = self._import_account_summary_endpoint()
        endpoint = endpoint_class(accountID=self.settings.oanda_account_id.get_secret_value())
        return self._request_endpoint(
            endpoint,
            event="oanda_account_summary_request_failed",
        )

    def _request_trade_detail_payload(self, trade_id: str) -> dict[str, Any]:
        endpoint_class = self._import_trade_detail_endpoint()
        endpoint = endpoint_class(
            accountID=self.settings.oanda_account_id.get_secret_value(),
            tradeID=trade_id,
        )
        return self._request_endpoint(
            endpoint,
            event="oanda_trade_detail_request_failed",
            trade_id=trade_id,
        )

    def _request_transaction_detail_payload(self, transaction_id: str) -> dict[str, Any]:
        endpoint_class = self._import_transaction_detail_endpoint()
        endpoint = endpoint_class(
            accountID=self.settings.oanda_account_id.get_secret_value(),
            transactionID=transaction_id,
        )
        return self._request_endpoint(
            endpoint,
            event="oanda_transaction_detail_request_failed",
            transaction_id=transaction_id,
        )

    def _request_pricing_payload(self, instrument: str) -> dict[str, Any]:
        endpoint_class = self._import_pricing_info_endpoint()
        endpoint = endpoint_class(
            accountID=self.settings.oanda_account_id.get_secret_value(),
            params={"instruments": instrument},
        )
        return self._request_endpoint(
            endpoint,
            event="oanda_account_pricing_request_failed",
            instrument=instrument,
        )

    def _request_candles_payload(
        self,
        instrument: str,
        granularity: str,
        count: int,
        price_component: str = "M",
    ) -> dict[str, Any]:
        endpoint_class = self._import_instruments_candles_endpoint()
        endpoint = endpoint_class(
            instrument=instrument,
            params={"granularity": granularity, "price": price_component, "count": count + 1},
        )
        return self._request_endpoint(
            endpoint,
            event="oanda_account_candles_request_failed",
            instrument=instrument,
            granularity=granularity,
            requested_count=count,
            price_component=price_component,
        )

    def _request_endpoint(
        self,
        endpoint: object,
        *,
        event: str,
        **fields: object,
    ) -> dict[str, Any]:
        try:
            self._get_api_client().request(endpoint)
        except Exception as exc:
            log_failure(self.logger, event, exc, **fields)
            raise
        return dict(getattr(endpoint, "response", {}))

    def _get_api_client(self) -> object:
        if self._api_client is None:
            api_class = self._import_api_class()
            self._api_client = api_class(
                access_token=self.settings.oanda_api_key.get_secret_value(),
                environment=self.settings.oanda_environment,
            )
        return self._api_client

    @staticmethod
    def _resolve_instrument(instrument: str) -> str:
        resolved = normalize_instrument(instrument)
        get_instrument_spec(resolved)
        return resolved

    @classmethod
    def _normalize_trade(cls, trade: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(trade)

        instrument = normalized.get("instrument")
        if instrument is not None:
            resolved = cls._resolve_instrument(str(instrument))
            normalized["instrument"] = resolved

        if "id" in normalized:
            normalized["id"] = str(normalized["id"])

        for key in ("price", "currentUnits", "initialUnits", "realizedPL", "unrealizedPL"):
            if key in normalized and normalized[key] is not None:
                normalized[key] = float(normalized[key])

        if "openTime" in normalized:
            normalized["openTime"] = cls._parse_oanda_time(normalized["openTime"])

        for order_key, field_name in (
            ("stopLossOrder", "stop_loss_price"),
            ("takeProfitOrder", "take_profit_price"),
            ("guaranteedStopLossOrder", "gslo_price"),
        ):
            price = cls._extract_nested_price(normalized.get(order_key))
            if price is not None:
                normalized[field_name] = price

        return normalized

    @classmethod
    def _normalize_order(cls, order: dict[str, Any]) -> PendingOrder:
        normalized = dict(order)

        instrument = normalized.get("instrument")
        if instrument is not None:
            resolved = cls._resolve_instrument(str(instrument))
        else:
            resolved = None

        created_at = (
            normalized.get("createTime")
            or normalized.get("createdTime")
            or normalized.get("timeCreated")
        )
        payload: dict[str, Any] = {
            "order_id": str(normalized["id"]) if normalized.get("id") is not None else None,
            "instrument": resolved,
            "units": float(normalized["units"]) if normalized.get("units") is not None else None,
            "price": float(normalized["price"]) if normalized.get("price") is not None else None,
            "order_type": (
                str(normalized["type"]).upper() if normalized.get("type") is not None else None
            ),
            "state": (
                str(normalized["state"]).upper() if normalized.get("state") is not None else "PENDING"
            ),
            "time_in_force": (
                str(normalized["timeInForce"]) if normalized.get("timeInForce") is not None else None
            ),
            "position_fill": (
                str(normalized["positionFill"]) if normalized.get("positionFill") is not None else None
            ),
            "trigger_condition": (
                str(normalized["triggerCondition"])
                if normalized.get("triggerCondition") is not None
                else None
            ),
            "trade_id": (
                str(normalized["tradeID"]) if normalized.get("tradeID") is not None else None
            ),
            "stop_loss_price": cls._extract_nested_price(normalized.get("stopLossOnFill")),
            "take_profit_price": cls._extract_nested_price(normalized.get("takeProfitOnFill")),
            "gslo_price": cls._extract_nested_price(normalized.get("guaranteedStopLossOnFill")),
            "created_at": cls._parse_oanda_time(created_at) if created_at is not None else None,
        }

        clean_payload = {key: value for key, value in payload.items() if value is not None}
        return PendingOrder.model_validate(clean_payload)

    def _normalize_position(self, trade: dict[str, Any]) -> OpenTradePosition:
        return OpenTradePosition(
            trade_id=str(trade["id"]),
            instrument=str(trade["instrument"]),
            units=float(trade["currentUnits"]),
            open_price=float(trade["price"]),
            unrealized_pl=self._optional_float(trade.get("unrealizedPL")),
            realized_pl=self._optional_float(trade.get("realizedPL")),
            account_currency=self.settings.account_currency,
            stop_loss_price=self._optional_float(trade.get("stop_loss_price")),
            take_profit_price=self._optional_float(trade.get("take_profit_price")),
            gslo_price=self._optional_float(trade.get("gslo_price")),
            opened_at=self._parse_oanda_time(trade.get("openTime")),
        )

    def _normalize_account_summary(self, account: dict[str, Any]) -> AccountSummary:
        return AccountSummary(
            account_id=str(account.get("id") or self.settings.oanda_account_id.get_secret_value()),
            alias=str(account.get("alias")).strip() if account.get("alias") is not None else None,
            environment=self.settings.oanda_environment,
            currency=str(account.get("currency") or self.settings.account_currency),
            balance=float(account.get("balance", 0.0)),
            nav=float(account.get("NAV", account.get("nav", 0.0))),
            unrealized_pl=float(account.get("unrealizedPL", 0.0)),
            realized_pl=float(account.get("pl", account.get("realizedPL", 0.0))),
            margin_used=float(account.get("marginUsed", 0.0)),
            margin_available=float(account.get("marginAvailable", 0.0)),
            open_trade_count=int(account.get("openTradeCount", 0)),
            open_position_count=int(account.get("openPositionCount", 0)),
            pending_order_count=int(account.get("pendingOrderCount", 0)),
            hedging_enabled=(
                bool(account.get("hedgingEnabled"))
                if account.get("hedgingEnabled") is not None
                else None
            ),
            fetched_at=datetime.now(timezone.utc),
        )

    @classmethod
    def _normalize_transaction(cls, transaction: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(transaction)
        if "id" in normalized:
            normalized["id"] = str(normalized["id"])
        if "tradeID" in normalized and normalized["tradeID"] is not None:
            normalized["tradeID"] = str(normalized["tradeID"])
        if "batchID" in normalized and normalized["batchID"] is not None:
            normalized["batchID"] = str(normalized["batchID"])
        if "instrument" in normalized and normalized["instrument"] is not None:
            normalized["instrument"] = cls._resolve_instrument(str(normalized["instrument"]))
        for key in ("pl", "financing", "accountBalance", "price", "units"):
            if key in normalized and normalized[key] is not None:
                normalized[key] = float(normalized[key])
        if "time" in normalized and normalized["time"] is not None:
            normalized["time"] = cls._parse_oanda_time(normalized["time"])
        return normalized

    @classmethod
    def _merge_bid_ask_candles(
        cls,
        *,
        instrument: str,
        granularity: str,
        bid_payload: dict[str, Any],
        ask_payload: dict[str, Any],
    ) -> pd.DataFrame:
        bid_rows = cls._extract_export_rows(
            payload=bid_payload,
            price_key="bid",
            instrument=instrument,
            granularity=granularity,
        )
        ask_rows = cls._extract_export_rows(
            payload=ask_payload,
            price_key="ask",
            instrument=instrument,
            granularity=granularity,
        )

        merged: list[dict[str, object]] = []
        for timestamp in sorted(set(bid_rows) & set(ask_rows)):
            bid_row = bid_rows[timestamp]
            ask_row = ask_rows[timestamp]
            merged.append(
                {
                    "time": timestamp,
                    "bid_open": bid_row["open"],
                    "bid_high": bid_row["high"],
                    "bid_low": bid_row["low"],
                    "bid_close": bid_row["close"],
                    "ask_open": ask_row["open"],
                    "ask_high": ask_row["high"],
                    "ask_low": ask_row["low"],
                    "ask_close": ask_row["close"],
                    "tick_volume": bid_row["tick_volume"],
                }
            )

        if not merged:
            raise RuntimeError(
                f"OANDA candle response for {instrument} {granularity} contained no complete bid/ask candles."
            )

        frame = pd.DataFrame(merged)
        frame["time"] = pd.to_datetime(frame["time"], utc=True)
        frame = frame.sort_values("time", kind="mergesort").reset_index(drop=True)
        return frame

    @classmethod
    def _extract_export_rows(
        cls,
        *,
        payload: dict[str, Any],
        price_key: str,
        instrument: str,
        granularity: str,
    ) -> dict[datetime, dict[str, object]]:
        raw_candles = payload.get("candles", [])
        if not raw_candles:
            raise RuntimeError(
                f"OANDA candle response for {instrument} {granularity} contained no candles."
            )

        rows: dict[datetime, dict[str, object]] = {}
        for candle in raw_candles:
            if not candle.get("complete", False):
                continue
            price = candle.get(price_key)
            if not isinstance(price, dict):
                continue
            timestamp = cls._parse_oanda_time(candle.get("time"))
            rows[timestamp] = {
                "open": float(price["o"]),
                "high": float(price["h"]),
                "low": float(price["l"]),
                "close": float(price["c"]),
                "tick_volume": int(candle["volume"]),
            }

        if not rows:
            raise RuntimeError(
                f"OANDA candle response for {instrument} {granularity} contained no complete {price_key} candles."
            )
        return rows

    @staticmethod
    def _extract_nested_price(payload: Any) -> float | None:
        if not isinstance(payload, dict):
            return None
        price = payload.get("price")
        if price is None:
            return None
        return float(price)

    @staticmethod
    def _optional_float(value: object) -> float | None:
        if value is None:
            return None
        return float(value)

    @staticmethod
    def _parse_oanda_time(value: object) -> datetime:
        if value is None:
            raise ValueError("OANDA timestamp is missing.")
        text = str(value).replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError as exc:
            raise ValueError(f"Invalid OANDA timestamp: {value!r}.") from exc
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    @staticmethod
    def _import_api_class() -> object:
        try:
            from oandapyV20 import API
        except ImportError as exc:
            raise RuntimeError(
                "oandapyV20 is required to create a live OANDA account client."
            ) from exc
        return API

    @staticmethod
    def _import_open_trades_endpoint() -> object:
        try:
            from oandapyV20.endpoints.trades import OpenTrades
        except ImportError as exc:
            raise RuntimeError(
                "oandapyV20 is required to fetch OANDA open trades."
            ) from exc
        return OpenTrades

    @staticmethod
    def _import_open_orders_endpoint() -> object:
        try:
            from oandapyV20.endpoints.orders import OrdersPending
        except ImportError as exc:
            raise RuntimeError(
                "oandapyV20 is required to fetch OANDA pending orders."
            ) from exc
        return OrdersPending

    @staticmethod
    def _import_account_summary_endpoint() -> object:
        try:
            from oandapyV20.endpoints.accounts import AccountSummary as OandaAccountSummary
        except ImportError as exc:
            raise RuntimeError(
                "oandapyV20 is required to fetch OANDA account summary."
            ) from exc
        return OandaAccountSummary

    @staticmethod
    def _import_trade_detail_endpoint() -> object:
        try:
            from oandapyV20.endpoints.trades import TradeDetails
        except ImportError as exc:
            raise RuntimeError(
                "oandapyV20 is required to fetch OANDA trade details."
            ) from exc
        return TradeDetails

    @staticmethod
    def _import_transaction_detail_endpoint() -> object:
        try:
            from oandapyV20.endpoints.transactions import TransactionDetails
        except ImportError as exc:
            raise RuntimeError(
                "oandapyV20 is required to fetch OANDA transaction details."
            ) from exc
        return TransactionDetails

    @staticmethod
    def _import_pricing_info_endpoint() -> object:
        try:
            from oandapyV20.endpoints.pricing import PricingInfo
        except ImportError as exc:
            raise RuntimeError(
                "oandapyV20 is required to fetch OANDA pricing data."
            ) from exc
        return PricingInfo

    @staticmethod
    def _import_instruments_candles_endpoint() -> object:
        try:
            from oandapyV20.endpoints.instruments import InstrumentsCandles
        except ImportError as exc:
            raise RuntimeError(
                "oandapyV20 is required to fetch OANDA candle data."
            ) from exc
        return InstrumentsCandles


__all__ = ["OandaAccountClient"]
