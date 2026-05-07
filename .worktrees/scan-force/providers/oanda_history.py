"""OANDA transaction-history client wrappers for trade journal sync."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping
from urllib.parse import parse_qs, urlparse

from config.settings import Settings, get_settings
from core.instrument_registry import normalize_instrument
from core.logging_setup import get_logger, log_failure


class OandaHistoryClient:
    """Thin synchronous wrapper around OANDA transaction-history endpoints."""

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        api_client: object | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._api_client = api_client
        self.logger = get_logger(__name__)

    def get_account_details_sync(self) -> dict[str, Any]:
        """Return normalized account-details payload with string identifiers."""

        payload = self._request_account_details_payload()
        account = payload.get("account")
        if not isinstance(account, dict):
            self.logger.error(
                "trade_history_account_details_invalid",
                payload_keys=tuple(sorted(payload.keys())),
            )
            raise RuntimeError("OANDA account-details response did not contain an account object.")
        return self._normalize_account_payload(account)

    def get_account_summary_sync(self) -> dict[str, Any]:
        """Return normalized account-summary payload with string identifiers."""

        payload = self._request_account_summary_payload()
        account = payload.get("account")
        if not isinstance(account, dict):
            self.logger.error(
                "trade_history_account_summary_invalid",
                payload_keys=tuple(sorted(payload.keys())),
            )
            raise RuntimeError("OANDA account-summary response did not contain an account object.")
        return self._normalize_account_payload(account)

    def fetch_transaction_pages_sync(
        self,
        start_utc: datetime,
        end_utc: datetime,
        type_filter: str,
    ) -> list[tuple[str, str]]:
        """Return TransactionIDRange `(from_id, to_id)` pairs for one UTC window."""

        normalized_start = self._normalize_utc_datetime(start_utc)
        normalized_end = self._normalize_utc_datetime(end_utc)
        if normalized_end <= normalized_start:
            raise ValueError("end_utc must be greater than start_utc.")

        endpoint_class = self._import_transaction_list_endpoint()
        endpoint = endpoint_class(
            accountID=self.settings.oanda_account_id.get_secret_value(),
            params={
                "from": self._format_oanda_time(normalized_start),
                "to": self._format_oanda_time(normalized_end),
                "pageSize": 1000,
                "type": type_filter,
            },
        )
        payload = self._request_endpoint(
            endpoint,
            event="oanda_transaction_list_request_failed",
            start_utc=normalized_start.isoformat(),
            end_utc=normalized_end.isoformat(),
            type_filter=type_filter,
        )
        pages = payload.get("pages", [])
        if not isinstance(pages, list):
            self.logger.error(
                "trade_history_transaction_pages_invalid",
                payload_keys=tuple(sorted(payload.keys())),
            )
            raise RuntimeError("OANDA transaction-list response did not contain a page list.")
        ranges: list[tuple[str, str]] = []
        for page in pages:
            ranges.append(self._parse_transaction_page_url(str(page)))
        return ranges

    def fetch_transactions_for_window_sync(
        self,
        start_utc: datetime,
        end_utc: datetime,
        type_filter: str,
    ) -> list[dict[str, Any]]:
        """Fetch all matching transactions for one UTC window."""

        transactions_by_id: dict[str, dict[str, Any]] = {}
        for from_id, to_id in self.fetch_transaction_pages_sync(start_utc, end_utc, type_filter):
            endpoint_class = self._import_transaction_id_range_endpoint()
            endpoint = endpoint_class(
                accountID=self.settings.oanda_account_id.get_secret_value(),
                params={
                    "from": from_id,
                    "to": to_id,
                    "type": type_filter,
                },
            )
            payload = self._request_endpoint(
                endpoint,
                event="oanda_transaction_idrange_request_failed",
                from_id=from_id,
                to_id=to_id,
                type_filter=type_filter,
            )
            transactions = payload.get("transactions", [])
            if not isinstance(transactions, list):
                self.logger.error(
                    "trade_history_transaction_idrange_invalid",
                    payload_keys=tuple(sorted(payload.keys())),
                )
                raise RuntimeError("OANDA transaction id-range response did not contain transactions.")
            for transaction in transactions:
                normalized = self._normalize_transaction_payload(transaction)
                transactions_by_id[normalized["id"]] = normalized

        return sorted(
            transactions_by_id.values(),
            key=lambda item: self._transaction_sort_key(item.get("id")),
        )

    def fetch_transactions_since_sync(
        self,
        last_transaction_id: str,
        type_filter: str,
    ) -> tuple[list[dict[str, Any]], str]:
        """Fetch transactions newer than `last_transaction_id`."""

        if not str(last_transaction_id).strip():
            raise ValueError("last_transaction_id must be a non-empty string.")

        endpoint_class = self._import_transactions_since_id_endpoint()
        endpoint = endpoint_class(
            accountID=self.settings.oanda_account_id.get_secret_value(),
            params={
                "id": str(last_transaction_id).strip(),
                "type": type_filter,
            },
        )
        payload = self._request_endpoint(
            endpoint,
            event="oanda_transactions_sinceid_request_failed",
            last_transaction_id=str(last_transaction_id),
            type_filter=type_filter,
        )
        transactions = payload.get("transactions", [])
        if not isinstance(transactions, list):
            self.logger.error(
                "trade_history_sinceid_invalid",
                payload_keys=tuple(sorted(payload.keys())),
            )
            raise RuntimeError("OANDA transactions-since-id response did not contain transactions.")
        last_seen = payload.get("lastTransactionID")
        if last_seen is None:
            raise RuntimeError("OANDA transactions-since-id response did not contain lastTransactionID.")
        normalized_transactions = [
            self._normalize_transaction_payload(transaction)
            for transaction in transactions
        ]
        normalized_transactions.sort(key=lambda item: self._transaction_sort_key(item.get("id")))
        return normalized_transactions, str(last_seen)

    def _request_account_details_payload(self) -> dict[str, Any]:
        endpoint_class = self._import_account_details_endpoint()
        endpoint = endpoint_class(accountID=self.settings.oanda_account_id.get_secret_value())
        return self._request_endpoint(endpoint, event="oanda_account_details_request_failed")

    def _request_account_summary_payload(self) -> dict[str, Any]:
        endpoint_class = self._import_account_summary_endpoint()
        endpoint = endpoint_class(accountID=self.settings.oanda_account_id.get_secret_value())
        return self._request_endpoint(endpoint, event="oanda_account_summary_request_failed")

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
    def _normalize_account_payload(account: Mapping[str, Any]) -> dict[str, Any]:
        normalized = dict(account)
        if normalized.get("id") is not None:
            normalized["id"] = str(normalized["id"])
        if normalized.get("lastTransactionID") is not None:
            normalized["lastTransactionID"] = str(normalized["lastTransactionID"])
        return normalized

    @classmethod
    def _normalize_transaction_payload(cls, transaction: Mapping[str, Any]) -> dict[str, Any]:
        normalized = dict(transaction)
        for key in ("id", "batchID", "orderID", "accountID", "lastTransactionID"):
            if key in normalized and normalized[key] is not None:
                normalized[key] = str(normalized[key])
        if normalized.get("instrument") is not None:
            normalized["instrument"] = normalize_instrument(str(normalized["instrument"]))

        for nested_key in ("tradeOpened", "tradeReduced"):
            nested_value = normalized.get(nested_key)
            if isinstance(nested_value, Mapping):
                normalized[nested_key] = cls._normalize_trade_link_payload(nested_value)

        if isinstance(normalized.get("tradesClosed"), list):
            normalized["tradesClosed"] = [
                cls._normalize_trade_link_payload(item)
                for item in normalized["tradesClosed"]
                if isinstance(item, Mapping)
            ]

        if isinstance(normalized.get("positionFinancings"), list):
            normalized["positionFinancings"] = [
                cls._normalize_position_financing_payload(item)
                for item in normalized["positionFinancings"]
                if isinstance(item, Mapping)
            ]

        return normalized

    @staticmethod
    def _normalize_trade_link_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
        normalized = dict(payload)
        if normalized.get("tradeID") is not None:
            normalized["tradeID"] = str(normalized["tradeID"])
        return normalized

    @staticmethod
    def _normalize_position_financing_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
        normalized = dict(payload)
        if normalized.get("instrument") is not None:
            normalized["instrument"] = normalize_instrument(str(normalized["instrument"]))
        return normalized

    @staticmethod
    def _parse_transaction_page_url(page_url: str) -> tuple[str, str]:
        parsed = urlparse(page_url)
        params = parse_qs(parsed.query)
        from_id = params.get("from", [])
        to_id = params.get("to", [])
        if not from_id or not to_id:
            raise RuntimeError(f"Could not parse transaction page URL: {page_url!r}.")
        return str(from_id[0]), str(to_id[0])

    @staticmethod
    def _normalize_utc_datetime(value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("Datetime arguments must be timezone-aware.")
        return value.astimezone(timezone.utc)

    @staticmethod
    def _format_oanda_time(value: datetime) -> str:
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    @staticmethod
    def _transaction_sort_key(value: Any) -> tuple[int, str]:
        try:
            return (0, f"{int(str(value)):020d}")
        except (TypeError, ValueError):
            return (1, str(value))

    @staticmethod
    def _import_api_class() -> object:
        try:
            from oandapyV20 import API
        except ImportError as exc:
            raise RuntimeError("oandapyV20 is required to create a live OANDA history client.") from exc
        return API

    @staticmethod
    def _import_account_details_endpoint() -> object:
        try:
            from oandapyV20.endpoints.accounts import AccountDetails
        except ImportError as exc:
            raise RuntimeError("oandapyV20 is required to fetch OANDA account details.") from exc
        return AccountDetails

    @staticmethod
    def _import_account_summary_endpoint() -> object:
        try:
            from oandapyV20.endpoints.accounts import AccountSummary
        except ImportError as exc:
            raise RuntimeError("oandapyV20 is required to fetch OANDA account summary.") from exc
        return AccountSummary

    @staticmethod
    def _import_transaction_list_endpoint() -> object:
        try:
            from oandapyV20.endpoints.transactions import TransactionList
        except ImportError as exc:
            raise RuntimeError("oandapyV20 is required to list OANDA transactions.") from exc
        return TransactionList

    @staticmethod
    def _import_transaction_id_range_endpoint() -> object:
        try:
            from oandapyV20.endpoints.transactions import TransactionIDRange
        except ImportError as exc:
            raise RuntimeError("oandapyV20 is required to fetch OANDA transaction id ranges.") from exc
        return TransactionIDRange

    @staticmethod
    def _import_transactions_since_id_endpoint() -> object:
        try:
            from oandapyV20.endpoints.transactions import TransactionsSinceID
        except ImportError as exc:
            raise RuntimeError("oandapyV20 is required to fetch OANDA transactions since id.") from exc
        return TransactionsSinceID


__all__ = ["OandaHistoryClient"]
