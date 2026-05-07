"""OANDA-backed analysis-layer market data provider."""

from __future__ import annotations

from datetime import datetime, timezone
from time import perf_counter

import pandas as pd

from config.settings import Settings, get_settings
from core.candle_policy import (
    OANDA_CANDLE_ALIGNMENT_PARAMS,
    OANDA_MAX_CANDLE_COUNT,
    get_timeframe_delta,
    trim_to_closed,
    validate_candle_df,
)
from core.instrument_registry import get_pip_size, validate_live_instrument
from core.logging_setup import get_logger, log_failure
from providers.base import CandleFreshness, PriceSnapshot
from providers.cache import CandleCache


class OandaMarketDataProvider:
    """Market-data-only OANDA provider composed with the Stage 04 cache."""

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        cache: CandleCache | None = None,
        api_client: object | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.cache = cache or CandleCache()
        self._api_client = api_client
        self.logger = get_logger(__name__)

    def get_candles(
        self,
        instrument: str,
        timeframe: str,
        count: int | None = None,
    ) -> pd.DataFrame:
        """Return canonical closed candles through the cache layer."""

        resolved_instrument = validate_live_instrument(instrument)
        get_timeframe_delta(timeframe)
        resolved_count = self.settings.default_candle_count if count is None else count
        if resolved_count <= 0:
            self.logger.warning(
                "market_data_request_invalid",
                instrument=resolved_instrument,
                timeframe=timeframe,
                requested_count=resolved_count,
            )
            raise ValueError("count must be a positive integer.")
        if resolved_count > OANDA_MAX_CANDLE_COUNT:
            raise ValueError(f"count must be less than or equal to {OANDA_MAX_CANDLE_COUNT}.")
        return self.cache.get_candles(
            resolved_instrument,
            timeframe,
            resolved_count,
            self._fetch_candles_from_api,
        )

    def get_current_price(self, instrument: str) -> PriceSnapshot:
        """Return current bid/ask prices and derived spread in pips."""

        resolved_instrument = validate_live_instrument(instrument)
        pip_size = get_pip_size(resolved_instrument)
        payload = self._request_pricing_payload(resolved_instrument)
        prices = payload.get("prices", [])
        if not prices:
            self.logger.error(
                "pricing_payload_empty",
                instrument=resolved_instrument,
                payload_keys=tuple(sorted(payload.keys())),
            )
            raise RuntimeError(f"OANDA pricing response for {resolved_instrument} contained no prices.")

        price = prices[0]
        try:
            bid = float(price["bids"][0]["price"])
            ask = float(price["asks"][0]["price"])
        except (IndexError, KeyError, TypeError, ValueError) as exc:
            log_failure(
                self.logger,
                "pricing_payload_invalid",
                exc,
                instrument=resolved_instrument,
                payload_keys=tuple(sorted(price.keys())),
            )
            raise RuntimeError(
                f"OANDA pricing response for {resolved_instrument} is missing bid/ask quotes."
            ) from exc

        fetched_at = self._parse_oanda_time(price.get("time"))
        spread_price = ask - bid
        return PriceSnapshot(
            instrument=resolved_instrument,
            bid=bid,
            ask=ask,
            spread_price=spread_price,
            spread_pips=spread_price / pip_size,
            fetched_at=fetched_at,
        )

    def get_candle_freshness(self, instrument: str, timeframe: str) -> CandleFreshness:
        """Return cache freshness metadata for a key."""

        resolved_instrument = validate_live_instrument(instrument)
        get_timeframe_delta(timeframe)
        return self.cache.get_candle_freshness(resolved_instrument, timeframe)

    def get_cached_candles(
        self,
        instrument: str,
        timeframe: str,
        count: int | None = None,
    ) -> pd.DataFrame | None:
        """Return cached candles only, without reaching OANDA."""

        resolved_instrument = validate_live_instrument(instrument)
        get_timeframe_delta(timeframe)
        resolved_count = self.settings.default_candle_count if count is None else count
        if resolved_count <= 0:
            self.logger.warning(
                "market_data_request_invalid",
                instrument=resolved_instrument,
                timeframe=timeframe,
                requested_count=resolved_count,
            )
            raise ValueError("count must be a positive integer.")
        return self.cache.get_cached_candles(resolved_instrument, timeframe, resolved_count)

    def _fetch_candles_from_api(
        self,
        instrument: str,
        timeframe: str,
        count: int,
        since: pd.Timestamp | None,
    ) -> pd.DataFrame:
        started_at = perf_counter()
        payload = self._request_candles_payload(instrument, timeframe, count, since)
        raw_candles = payload.get("candles", [])
        if not raw_candles:
            self.logger.error(
                "candle_payload_empty",
                instrument=instrument,
                timeframe=timeframe,
                requested_count=count,
                since=None if since is None else since.to_pydatetime(),
                payload_keys=tuple(sorted(payload.keys())),
            )
            raise RuntimeError(
                f"OANDA candle response for {instrument} {timeframe} contained no candles."
            )

        canonical_rows: list[dict[str, object]] = []
        incomplete_present = False
        last_raw_time = None

        for candle in raw_candles:
            last_raw_time = candle.get("time", last_raw_time)
            if not candle.get("complete", False):
                incomplete_present = True
                continue

            mid = candle.get("mid")
            if not isinstance(mid, dict):
                continue

            try:
                canonical_rows.append(
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
                    "candle_payload_invalid",
                    exc,
                    instrument=instrument,
                    timeframe=timeframe,
                    candle_time=candle.get("time"),
                    payload_keys=tuple(sorted(candle.keys())),
                )
                raise RuntimeError(
                    f"OANDA candle response for {instrument} {timeframe} contained invalid candle data."
                ) from exc

        if not canonical_rows:
            if since is not None:
                # Weekend/holiday gap: no new complete candles in the incremental window.
                # Return empty so the cache layer can fall back to existing data.
                return pd.DataFrame()
            self.logger.error(
                "candle_payload_no_complete_rows",
                instrument=instrument,
                timeframe=timeframe,
                requested_count=count,
                raw_candle_count=len(raw_candles),
            )
            raise RuntimeError(
                f"OANDA candle response for {instrument} {timeframe} contained no complete candles."
            )

        normalized = validate_candle_df(pd.DataFrame(canonical_rows))
        trimmed = trim_to_closed(normalized, timeframe)
        excluded = incomplete_present or len(trimmed) != len(normalized)
        reason = "complete_flag_false" if incomplete_present else "none"
        if not incomplete_present and len(trimmed) != len(normalized):
            reason = "forming_bar_trimmed"

        self.logger.info(
            "current_bar_excluded",
            instrument=instrument,
            timeframe=timeframe,
            excluded=excluded,
            last_bar_time=self._parse_oanda_time(last_raw_time) if last_raw_time is not None else None,
            reason=reason,
        )

        if trimmed.empty:
            self.logger.error(
                "candle_payload_trimmed_empty",
                instrument=instrument,
                timeframe=timeframe,
                normalized_count=len(normalized),
                raw_candle_count=len(raw_candles),
            )
            raise RuntimeError(
                f"OANDA candle response for {instrument} {timeframe} yielded no closed candles after trimming."
            )

        duration_ms = (perf_counter() - started_at) * 1000.0
        self.logger.info(
            "candles_fetched",
            instrument=instrument,
            timeframe=timeframe,
            source="oanda_api",
            candle_count=len(trimmed),
            last_completed_candle=trimmed["time"].iloc[-1].to_pydatetime(),
            fetch_duration_ms=duration_ms,
        )
        return trimmed

    def _request_candles_payload(
        self,
        instrument: str,
        timeframe: str,
        count: int,
        since: pd.Timestamp | None,
    ) -> dict[str, object]:
        endpoint_class = self._import_instruments_candles_endpoint()
        params: dict[str, object] = {
            "granularity": timeframe,
            "price": "M",
        }
        if since is None:
            params["count"] = min(count + 1, OANDA_MAX_CANDLE_COUNT)
        else:
            params["from"] = since.to_pydatetime().isoformat()
        params.update(OANDA_CANDLE_ALIGNMENT_PARAMS)

        endpoint = endpoint_class(instrument=instrument, params=params)
        try:
            self._get_api_client().request(endpoint)
        except Exception as exc:
            log_failure(
                self.logger,
                "oanda_candles_request_failed",
                exc,
                instrument=instrument,
                timeframe=timeframe,
                requested_count=count,
                since=None if since is None else since.to_pydatetime(),
                params=dict(params),
            )
            raise
        return dict(getattr(endpoint, "response", {}))

    def _request_pricing_payload(self, instrument: str) -> dict[str, object]:
        endpoint_class = self._import_pricing_info_endpoint()
        endpoint = endpoint_class(
            accountID=self.settings.oanda_account_id.get_secret_value(),
            params={"instruments": instrument},
        )
        try:
            self._get_api_client().request(endpoint)
        except Exception as exc:
            log_failure(
                self.logger,
                "oanda_pricing_request_failed",
                exc,
                instrument=instrument,
            )
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
                "oandapyV20 is required to create a live OANDA market-data client."
            ) from exc
        return API

    @staticmethod
    def _import_instruments_candles_endpoint() -> object:
        try:
            from oandapyV20.endpoints.instruments import InstrumentsCandles
        except ImportError as exc:
            raise RuntimeError(
                "oandapyV20 is required to fetch OANDA candle data."
            ) from exc
        return InstrumentsCandles

    @staticmethod
    def _import_pricing_info_endpoint() -> object:
        try:
            from oandapyV20.endpoints.pricing import PricingInfo
        except ImportError as exc:
            raise RuntimeError(
                "oandapyV20 is required to fetch OANDA pricing data."
            ) from exc
        return PricingInfo


__all__ = ["OandaMarketDataProvider"]
