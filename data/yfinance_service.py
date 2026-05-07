"""Bounded yfinance helpers for the MCP surface."""

from __future__ import annotations

from datetime import date, datetime
import math
import re
from typing import Any, Mapping, Protocol

import pandas as pd

from core.logging_setup import get_logger

_MAX_SEARCH_RESULTS = 25
_MAX_NEWS_ITEMS = 10
_MAX_HISTORY_ROWS = 1000
_MAX_OPTIONS_EXPIRATIONS = 24
_MAX_SUMMARY_CHARS = 600
_MAX_TABLE_ROWS = 20

_FAST_INFO_FIELDS: tuple[tuple[str, str], ...] = (
    ("currency", "currency"),
    ("quote_type", "quoteType"),
    ("exchange", "exchange"),
    ("timezone", "timezone"),
    ("last_price", "lastPrice"),
    ("previous_close", "previousClose"),
    ("regular_market_previous_close", "regularMarketPreviousClose"),
    ("open", "open"),
    ("day_high", "dayHigh"),
    ("day_low", "dayLow"),
    ("last_volume", "lastVolume"),
    ("market_cap", "marketCap"),
    ("fifty_day_average", "fiftyDayAverage"),
    ("two_hundred_day_average", "twoHundredDayAverage"),
    ("ten_day_average_volume", "tenDayAverageVolume"),
    ("three_month_average_volume", "threeMonthAverageVolume"),
    ("year_high", "yearHigh"),
    ("year_low", "yearLow"),
    ("year_change", "yearChange"),
)

_INFO_PROFILE_FIELDS: tuple[tuple[str, str], ...] = (
    ("short_name", "shortName"),
    ("long_name", "longName"),
    ("quote_type", "quoteType"),
    ("currency", "currency"),
    ("market", "market"),
    ("exchange", "exchange"),
    ("full_exchange_name", "fullExchangeName"),
    ("exchange_timezone_name", "exchangeTimezoneName"),
    ("exchange_timezone_short_name", "exchangeTimezoneShortName"),
    ("market_state", "marketState"),
    ("sector", "sector"),
    ("industry", "industry"),
    ("category", "category"),
    ("fund_family", "fundFamily"),
    ("legal_type", "legalType"),
    ("country", "country"),
    ("region", "region"),
    ("website", "website"),
)

_INFO_QUOTE_FALLBACKS: dict[str, tuple[str, ...]] = {
    "currency": ("currency",),
    "quote_type": ("quoteType",),
    "exchange": ("exchange", "fullExchangeName"),
    "timezone": ("exchangeTimezoneName",),
    "last_price": ("regularMarketPrice", "currentPrice", "navPrice"),
    "previous_close": ("previousClose",),
    "regular_market_previous_close": ("regularMarketPreviousClose",),
    "open": ("regularMarketOpen", "open"),
    "day_high": ("regularMarketDayHigh", "dayHigh"),
    "day_low": ("regularMarketDayLow", "dayLow"),
    "last_volume": ("regularMarketVolume", "volume"),
    "market_cap": ("marketCap", "netAssets"),
    "fifty_day_average": ("fiftyDayAverage",),
    "two_hundred_day_average": ("twoHundredDayAverage",),
    "ten_day_average_volume": ("averageDailyVolume10Day", "averageVolume10days"),
    "three_month_average_volume": ("averageDailyVolume3Month", "averageVolume"),
    "year_high": ("fiftyTwoWeekHigh",),
    "year_low": ("fiftyTwoWeekLow",),
    "year_change": ("fiftyTwoWeekChangePercent",),
}

_SEARCH_QUOTE_FIELDS: tuple[tuple[str, str], ...] = (
    ("symbol", "symbol"),
    ("short_name", "shortname"),
    ("long_name", "longname"),
    ("exchange", "exchange"),
    ("exchange_display", "exchDisp"),
    ("quote_type", "quoteType"),
    ("type_display", "typeDisp"),
    ("score", "score"),
    ("is_yahoo_finance", "isYahooFinance"),
)


class SearchResultProtocol(Protocol):
    @property
    def quotes(self) -> list[dict[str, Any]]:
        ...

    @property
    def news(self) -> list[dict[str, Any]]:
        ...


class TickerProtocol(Protocol):
    @property
    def info(self) -> Mapping[str, Any]:
        ...

    @property
    def fast_info(self) -> Any:
        ...

    @property
    def options(self) -> tuple[str, ...] | list[str]:
        ...

    @property
    def calendar(self) -> Any:
        ...

    @property
    def news(self) -> list[dict[str, Any]]:
        ...

    def history(self, **kwargs: Any) -> pd.DataFrame:
        ...


class YFinanceBackendProtocol(Protocol):
    def search(
        self,
        query: str,
        *,
        max_results: int,
        news_count: int,
        enable_fuzzy_query: bool,
    ) -> SearchResultProtocol:
        ...

    def ticker(self, symbol: str) -> TickerProtocol:
        ...


class _DefaultYFinanceBackend:
    def search(
        self,
        query: str,
        *,
        max_results: int,
        news_count: int,
        enable_fuzzy_query: bool,
    ) -> SearchResultProtocol:
        try:
            import yfinance as yf
        except ImportError as exc:  # pragma: no cover - import smoke covers availability
            raise RuntimeError("yfinance is required for MCP yfinance tools.") from exc

        return yf.Search(
            query,
            max_results=max_results,
            news_count=news_count,
            enable_fuzzy_query=enable_fuzzy_query,
            raise_errors=True,
        )

    def ticker(self, symbol: str) -> TickerProtocol:
        try:
            import yfinance as yf
        except ImportError as exc:  # pragma: no cover - import smoke covers availability
            raise RuntimeError("yfinance is required for MCP yfinance tools.") from exc

        return yf.Ticker(symbol)


class YFinanceService:
    """Expose a bounded, MCP-friendly subset of yfinance."""

    def __init__(self, *, backend: YFinanceBackendProtocol | None = None) -> None:
        self._backend = backend or _DefaultYFinanceBackend()
        self.logger = get_logger(__name__)

    def search_tickers(
        self,
        query: str,
        *,
        limit: int = 8,
        news_count: int = 0,
        enable_fuzzy: bool = False,
    ) -> dict[str, Any]:
        resolved_query = self._normalize_query(query)
        resolved_limit = self._bounded_int(limit, name="limit", minimum=1, maximum=_MAX_SEARCH_RESULTS)
        resolved_news_count = self._bounded_int(
            news_count,
            name="news_count",
            minimum=0,
            maximum=_MAX_NEWS_ITEMS,
        )
        search = self._backend.search(
            resolved_query,
            max_results=resolved_limit,
            news_count=resolved_news_count,
            enable_fuzzy_query=enable_fuzzy,
        )
        quotes = [self._sanitize_search_quote(quote) for quote in search.quotes[:resolved_limit]]
        news = [self._sanitize_news_item(item) for item in search.news[:resolved_news_count]]
        return {
            "provider": "yfinance",
            "query": resolved_query,
            "limit": resolved_limit,
            "news_count": resolved_news_count,
            "enable_fuzzy": bool(enable_fuzzy),
            "returned_count": len(quotes),
            "quotes": quotes,
            "news": news,
        }

    def get_ticker(
        self,
        symbol: str,
        *,
        include_news: bool = False,
        news_limit: int = 5,
    ) -> dict[str, Any]:
        resolved_symbol = self._normalize_symbol(symbol)
        resolved_news_limit = self._bounded_int(
            news_limit,
            name="news_limit",
            minimum=0,
            maximum=_MAX_NEWS_ITEMS,
        )
        ticker = self._backend.ticker(resolved_symbol)
        warnings: list[str] = []
        info = self._safe_fetch(
            lambda: dict(getattr(ticker, "info", {}) or {}),
            warnings=warnings,
            label="info",
            default={},
        )
        fast_info = self._safe_fetch(
            lambda: getattr(ticker, "fast_info", {}) or {},
            warnings=warnings,
            label="fast_info",
            default={},
        )
        options = self._safe_fetch(
            lambda: list(getattr(ticker, "options", ()) or ()),
            warnings=warnings,
            label="options",
            default=[],
        )
        calendar = self._safe_fetch(
            lambda: getattr(ticker, "calendar", None),
            warnings=warnings,
            label="calendar",
            default=None,
        )
        news: list[dict[str, Any]] = []
        if include_news and resolved_news_limit > 0:
            raw_news = self._safe_fetch(
                lambda: list(getattr(ticker, "news", []) or []),
                warnings=warnings,
                label="news",
                default=[],
            )
            news = [self._sanitize_news_item(item) for item in raw_news[:resolved_news_limit]]

        quote = self._build_quote(info=info, fast_info=fast_info, warnings=warnings)
        profile = self._build_profile(info=info)
        option_expirations = [self._sanitize_value(value) for value in options[:_MAX_OPTIONS_EXPIRATIONS]]
        calendar_payload = self._sanitize_calendar(calendar)
        if not any((quote, profile, calendar_payload, option_expirations, news)):
            raise RuntimeError(f"yfinance returned no readable data for {resolved_symbol}.")

        return {
            "provider": "yfinance",
            "symbol": resolved_symbol,
            "quote": quote,
            "profile": profile,
            "calendar": calendar_payload,
            "available_option_expiration_count": len(options),
            "options_expirations": option_expirations,
            "options_expirations_truncated": len(options) > len(option_expirations),
            "news": news,
            "warnings": warnings,
        }

    def get_history(
        self,
        symbol: str,
        *,
        period: str | None = "1mo",
        interval: str = "1d",
        start: str | None = None,
        end: str | None = None,
        prepost: bool = False,
        actions: bool = False,
        auto_adjust: bool = True,
        max_rows: int = 250,
    ) -> dict[str, Any]:
        resolved_symbol = self._normalize_symbol(symbol)
        resolved_interval = self._normalize_non_empty(interval, field_name="interval")
        resolved_period = self._normalize_optional_text(period)
        resolved_start = self._normalize_optional_text(start)
        resolved_end = self._normalize_optional_text(end)
        resolved_max_rows = self._bounded_int(
            max_rows,
            name="max_rows",
            minimum=1,
            maximum=_MAX_HISTORY_ROWS,
        )
        ticker = self._backend.ticker(resolved_symbol)
        history_kwargs: dict[str, Any] = {
            "interval": resolved_interval,
            "prepost": bool(prepost),
            "actions": bool(actions),
            "auto_adjust": bool(auto_adjust),
        }
        if resolved_period is not None:
            history_kwargs["period"] = resolved_period
        if resolved_start is not None:
            history_kwargs["start"] = resolved_start
        if resolved_end is not None:
            history_kwargs["end"] = resolved_end

        frame = ticker.history(**history_kwargs)
        records = self._history_records(frame, max_rows=resolved_max_rows)
        return {
            "provider": "yfinance",
            "symbol": resolved_symbol,
            "period": resolved_period,
            "interval": resolved_interval,
            "start": resolved_start,
            "end": resolved_end,
            "prepost": bool(prepost),
            "actions": bool(actions),
            "auto_adjust": bool(auto_adjust),
            "requested_max_rows": resolved_max_rows,
            "available_count": 0 if frame.empty else len(frame),
            "returned_count": len(records),
            "truncated": len(records) < len(frame),
            "history": records,
        }

    def get_news(self, symbol: str, *, limit: int = 8) -> dict[str, Any]:
        resolved_symbol = self._normalize_symbol(symbol)
        resolved_limit = self._bounded_int(limit, name="limit", minimum=1, maximum=_MAX_NEWS_ITEMS)
        ticker = self._backend.ticker(resolved_symbol)
        raw_news = list(getattr(ticker, "news", []) or [])
        news = [self._sanitize_news_item(item) for item in raw_news[:resolved_limit]]
        return {
            "provider": "yfinance",
            "symbol": resolved_symbol,
            "limit": resolved_limit,
            "returned_count": len(news),
            "news": news,
        }

    def _build_quote(
        self,
        *,
        info: Mapping[str, Any],
        fast_info: Any,
        warnings: list[str],
    ) -> dict[str, Any]:
        quote: dict[str, Any] = {}
        for output_key, fast_key in _FAST_INFO_FIELDS:
            value = None
            try:
                value = self._mapping_get(fast_info, fast_key)
            except Exception as exc:
                warnings.append(f"fast_info.{fast_key}: {exc}")
                self.logger.warning("yfinance_fast_info_field_failed", field=fast_key, error=str(exc))
            if value is None:
                for info_key in _INFO_QUOTE_FALLBACKS.get(output_key, ()):
                    value = info.get(info_key)
                    if value is not None:
                        break
            sanitized = self._sanitize_value(value)
            if sanitized is not None:
                quote[output_key] = sanitized

        last_price = quote.get("last_price")
        previous_close = quote.get("previous_close")
        if isinstance(last_price, (int, float)) and isinstance(previous_close, (int, float)) and previous_close != 0:
            day_change = float(last_price - previous_close)
            quote["day_change"] = day_change
            quote["day_change_percent"] = day_change / previous_close
        return quote

    def _build_profile(self, *, info: Mapping[str, Any]) -> dict[str, Any]:
        profile: dict[str, Any] = {}
        for output_key, info_key in _INFO_PROFILE_FIELDS:
            value = self._sanitize_value(info.get(info_key))
            if value is not None:
                profile[output_key] = value
        summary = self._truncate_text(info.get("longBusinessSummary"))
        if summary is not None:
            profile["business_summary_excerpt"] = summary
        return profile

    def _sanitize_calendar(self, calendar: Any) -> Any:
        if calendar is None:
            return None
        if isinstance(calendar, pd.DataFrame):
            frame = calendar.copy()
            if frame.empty:
                return {"rows": [], "returned_count": 0, "truncated": False}
            rows = self._frame_records(frame.reset_index(), max_rows=_MAX_TABLE_ROWS)
            return {
                "rows": rows,
                "returned_count": len(rows),
                "truncated": len(frame) > len(rows),
            }
        return self._sanitize_value(calendar)

    def _history_records(self, frame: pd.DataFrame, *, max_rows: int) -> list[dict[str, Any]]:
        if frame.empty:
            return []
        return self._frame_records(frame.reset_index(), max_rows=max_rows)

    def _frame_records(self, frame: pd.DataFrame, *, max_rows: int) -> list[dict[str, Any]]:
        bounded = frame.tail(max_rows).copy()
        if bounded.empty:
            return []
        records: list[dict[str, Any]] = []
        for row in bounded.to_dict(orient="records"):
            record = {
                self._snake_case(str(key)): self._sanitize_value(value)
                for key, value in row.items()
            }
            if "date" in record and "time" not in record:
                record["time"] = record.pop("date")
            if "datetime" in record and "time" not in record:
                record["time"] = record.pop("datetime")
            records.append(record)
        return records

    def _sanitize_search_quote(self, quote: Mapping[str, Any]) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        for output_key, input_key in _SEARCH_QUOTE_FIELDS:
            value = self._sanitize_value(quote.get(input_key))
            if value is not None:
                payload[output_key] = value
        return payload

    def _sanitize_news_item(self, item: Mapping[str, Any]) -> dict[str, Any]:
        content = item.get("content") if isinstance(item.get("content"), Mapping) else item
        provider = content.get("provider") if isinstance(content.get("provider"), Mapping) else None
        canonical_url = (
            content.get("canonicalUrl")
            if isinstance(content.get("canonicalUrl"), Mapping)
            else None
        )
        click_through_url = (
            content.get("clickThroughUrl")
            if isinstance(content.get("clickThroughUrl"), Mapping)
            else None
        )
        payload = {
            "id": self._sanitize_value(content.get("id") or item.get("id") or item.get("uuid")),
            "title": self._sanitize_value(content.get("title") or item.get("title")),
            "publisher": self._sanitize_value(
                item.get("publisher")
                or (None if provider is None else provider.get("displayName"))
            ),
            "link": self._sanitize_value(
                item.get("link")
                or (None if canonical_url is None else canonical_url.get("url"))
                or (None if click_through_url is None else click_through_url.get("url"))
            ),
            "published_at": self._sanitize_timestamp(
                item.get("providerPublishTime")
                or content.get("providerPublishTime")
                or content.get("pubDate")
                or content.get("displayTime")
            ),
            "type": self._sanitize_value(item.get("type") or content.get("contentType")),
            "summary": self._truncate_text(
                content.get("summary") or content.get("description") or item.get("summary")
            ),
        }
        return {key: value for key, value in payload.items() if value is not None}

    def _safe_fetch(
        self,
        loader,
        *,
        warnings: list[str],
        label: str,
        default: Any,
    ) -> Any:
        try:
            return loader()
        except Exception as exc:
            warnings.append(f"{label}: {exc}")
            self.logger.warning("yfinance_partial_fetch_failed", section=label, error=str(exc))
            return default

    @staticmethod
    def _mapping_get(container: Any, key: str) -> Any:
        if container is None:
            return None
        getter = getattr(container, "get", None)
        if callable(getter):
            return getter(key)
        if isinstance(container, Mapping):
            return container.get(key)
        try:
            return container[key]
        except Exception:
            return None

    @staticmethod
    def _normalize_query(value: str) -> str:
        query = str(value).strip()
        if not query:
            raise ValueError("query must be a non-empty string.")
        return query

    @staticmethod
    def _normalize_symbol(value: str) -> str:
        symbol = str(value).strip().upper()
        if not symbol:
            raise ValueError("symbol must be a non-empty string.")
        return symbol

    @staticmethod
    def _normalize_non_empty(value: str, *, field_name: str) -> str:
        normalized = str(value).strip()
        if not normalized:
            raise ValueError(f"{field_name} must be a non-empty string.")
        return normalized

    @staticmethod
    def _normalize_optional_text(value: str | None) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip()
        return normalized or None

    @staticmethod
    def _bounded_int(value: int, *, name: str, minimum: int, maximum: int) -> int:
        resolved = int(value)
        if resolved < minimum or resolved > maximum:
            raise ValueError(f"{name} must be between {minimum} and {maximum}.")
        return resolved

    @classmethod
    def _sanitize_value(cls, value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, pd.Timestamp):
            return cls._sanitize_timestamp(value)
        if isinstance(value, datetime):
            return cls._sanitize_timestamp(value)
        if isinstance(value, date):
            return value
        if isinstance(value, str):
            return value
        if isinstance(value, Mapping):
            payload: dict[str, Any] = {}
            for key, item in value.items():
                sanitized = cls._sanitize_value(item)
                if sanitized is not None:
                    payload[cls._snake_case(str(key))] = sanitized
            return payload
        if isinstance(value, (list, tuple, set)):
            return [cls._sanitize_value(item) for item in value]
        if hasattr(value, "item") and not isinstance(value, (str, bytes, bytearray)):
            try:
                value = value.item()
            except Exception:
                pass
        if isinstance(value, float) and math.isnan(value):
            return None
        return value

    @staticmethod
    def _sanitize_timestamp(value: Any) -> datetime | None:
        if value is None:
            return None
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if math.isnan(value):
                return None
            timestamp = pd.to_datetime(value, unit="s", utc=True)
        else:
            timestamp = pd.Timestamp(value)
            if pd.isna(timestamp):
                return None
            if timestamp.tzinfo is None:
                timestamp = timestamp.tz_localize("UTC")
            else:
                timestamp = timestamp.tz_convert("UTC")
        return timestamp.to_pydatetime()

    @staticmethod
    def _truncate_text(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        if not text:
            return None
        if len(text) <= _MAX_SUMMARY_CHARS:
            return text
        return f"{text[:_MAX_SUMMARY_CHARS - 3].rstrip()}..."

    @staticmethod
    def _snake_case(value: str) -> str:
        normalized = re.sub(r"[^0-9A-Za-z]+", "_", value).strip("_").lower()
        return normalized or "value"


__all__ = ["YFinanceService"]
