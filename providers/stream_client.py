"""Read-only OANDA price-stream boundary for the trade-helper path."""

from __future__ import annotations

import asyncio
import contextlib
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Iterable, Iterator

from config.settings import Settings, get_settings
from core.events import Heartbeat, PriceTick
from core.instrument_registry import normalize_instrument, validate_live_instrument
from core.logging_setup import get_logger, log_failure


class OandaStreamClient:
    """Read-only async wrapper around the OANDA pricing stream."""

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        api_client: object | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._api_client = api_client
        self.logger = get_logger(__name__)

    async def stream_prices(
        self,
        instruments: Iterable[str],
    ) -> AsyncGenerator[PriceTick | Heartbeat, None]:
        """Yield normalized price ticks and heartbeats for supported instruments."""

        resolved_instruments = self._normalize_instruments(instruments)
        endpoint: object | None = None
        iterator: Iterator[dict[str, Any]] | None = None
        pending_next: asyncio.Task[tuple[bool, dict[str, Any] | None]] | None = None

        try:
            endpoint, iterator = await asyncio.to_thread(
                self._open_stream,
                resolved_instruments,
            )
            self.logger.info(
                "price_stream_opened",
                instruments=resolved_instruments,
            )

            while True:
                pending_next = asyncio.create_task(asyncio.to_thread(self._next_payload, iterator))
                try:
                    finished, payload = await pending_next
                except asyncio.CancelledError:
                    finished, payload = await asyncio.shield(pending_next)
                    raise
                finally:
                    pending_next = None

                if finished:
                    self.logger.warning(
                        "price_stream_ended",
                        instruments=resolved_instruments,
                    )
                    return
                try:
                    event = self._normalize_stream_payload(payload)
                except Exception as exc:
                    log_failure(
                        self.logger,
                        "price_stream_payload_invalid",
                        exc,
                        instruments=resolved_instruments,
                        payload_type=type(payload).__name__,
                        payload_keys=tuple(sorted(payload.keys())) if isinstance(payload, dict) else None,
                    )
                    raise
                if event is None:
                    continue
                yield event
        except Exception as exc:
            log_failure(
                self.logger,
                "price_stream_iteration_failed",
                exc,
                instruments=resolved_instruments,
            )
            raise
        finally:
            if pending_next is not None:
                with contextlib.suppress(Exception):
                    await asyncio.shield(pending_next)
            if endpoint is not None and iterator is not None:
                await asyncio.to_thread(self._close_stream, endpoint, iterator)

    def _open_stream(
        self,
        instruments: tuple[str, ...],
    ) -> tuple[object, Iterator[dict[str, Any]]]:
        endpoint_class = self._import_pricing_stream_endpoint()
        endpoint = endpoint_class(
            accountID=self.settings.oanda_account_id.get_secret_value(),
            params={"instruments": ",".join(instruments)},
        )
        return endpoint, self._iter_stream_payloads(endpoint)

    def _iter_stream_payloads(self, endpoint: object) -> Iterator[dict[str, Any]]:
        response = self._get_api_client().request(endpoint)
        payloads = response if response is not None else getattr(endpoint, "response", None)
        return self._coerce_payload_iterator(payloads)

    def _get_api_client(self) -> object:
        if self._api_client is None:
            api_class = self._import_api_class()
            self._api_client = api_class(
                access_token=self.settings.oanda_api_key.get_secret_value(),
                environment=self.settings.oanda_environment,
            )
        return self._api_client

    @staticmethod
    def _next_payload(iterator: Iterator[dict[str, Any]]) -> tuple[bool, dict[str, Any] | None]:
        try:
            return False, next(iterator)
        except StopIteration:
            return True, None

    @staticmethod
    def _coerce_payload_iterator(payloads: object) -> Iterator[dict[str, Any]]:
        if payloads is None:
            return iter(())
        if isinstance(payloads, dict):
            return iter((payloads,))
        try:
            return iter(payloads)
        except TypeError as exc:
            raise RuntimeError("OANDA stream response is not iterable.") from exc

    def _close_stream(self, endpoint: object, iterator: Iterator[dict[str, Any]]) -> None:
        terminate = getattr(endpoint, "terminate", None)
        if callable(terminate):
            try:
                terminate("stream_prices closed")
            except Exception as exc:
                if not self._is_expected_close_exception(exc):
                    log_failure(self.logger, "price_stream_terminate_failed", exc, level="warning")

        close = getattr(iterator, "close", None)
        if callable(close):
            try:
                close()
            except Exception as exc:
                if not self._is_expected_close_exception(exc):
                    log_failure(
                        self.logger,
                        "price_stream_iterator_close_failed",
                        exc,
                        level="warning",
                    )

    @staticmethod
    def _is_expected_close_exception(exc: Exception) -> bool:
        if exc.__class__.__name__ == "StreamTerminated":
            return True
        if isinstance(exc, ValueError) and "generator already executing" in str(exc):
            return True
        return False

    @staticmethod
    def _normalize_instruments(instruments: Iterable[str]) -> tuple[str, ...]:
        normalized: list[str] = []
        seen: set[str] = set()
        for raw in instruments:
            instrument = validate_live_instrument(normalize_instrument(str(raw)))
            if instrument in seen:
                continue
            seen.add(instrument)
            normalized.append(instrument)
        if not normalized:
            raise ValueError("instruments must contain at least one supported instrument.")
        return tuple(normalized)

    @classmethod
    def _normalize_stream_payload(
        cls,
        payload: dict[str, Any] | None,
    ) -> PriceTick | Heartbeat | None:
        if payload is None:
            return None
        if not isinstance(payload, dict):
            raise RuntimeError("Streaming payload must be a JSON object.")

        payload_type = str(payload.get("type", "PRICE")).upper()
        if payload_type == "HEARTBEAT":
            return Heartbeat(time=cls._parse_oanda_time(payload.get("time")))

        instrument = cls._resolve_instrument(str(payload.get("instrument", "")))
        bid = cls._extract_price(payload, direct_key="closeoutBid", levels_key="bids")
        ask = cls._extract_price(payload, direct_key="closeoutAsk", levels_key="asks")
        return PriceTick(
            instrument=instrument,
            bid=bid,
            ask=ask,
            time=cls._parse_oanda_time(payload.get("time")),
        )

    @staticmethod
    def _resolve_instrument(instrument: str) -> str:
        resolved = normalize_instrument(instrument)
        return validate_live_instrument(resolved)

    @staticmethod
    def _extract_price(
        payload: dict[str, Any],
        *,
        direct_key: str,
        levels_key: str,
    ) -> float:
        levels = payload.get(levels_key, [])
        if levels:
            try:
                return float(levels[0]["price"])
            except (IndexError, KeyError, TypeError, ValueError):
                pass

        direct_value = payload.get(direct_key)
        if direct_value is not None:
            return float(direct_value)

        raise RuntimeError(f"Streaming payload is missing {direct_key}/{levels_key}.")

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
                "oandapyV20 is required to create a live OANDA stream client."
            ) from exc
        return API

    @staticmethod
    def _import_pricing_stream_endpoint() -> object:
        try:
            from oandapyV20.endpoints.pricing import PricingStream
        except ImportError as exc:
            raise RuntimeError(
                "oandapyV20 is required to stream OANDA pricing data."
            ) from exc
        return PricingStream


__all__ = ["OandaStreamClient"]
