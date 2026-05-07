"""FairEconomy economic calendar client for Stage 10."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from threading import RLock
from time import perf_counter
from typing import Any, Callable, Mapping

import requests

from config.settings import Settings, get_settings
from core.logging_setup import get_logger, log_failure
from core.models import CalendarEvent, ImpactLevel

FAIRECONOMY_CALENDAR_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
DEFAULT_TIMEOUT_SECONDS = 20
DEFAULT_BLACKOUT_IMPACTS: tuple[ImpactLevel, ...] = ("HIGH",)

_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/123.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json,text/plain,*/*",
    "Referer": "https://www.forexfactory.com/calendar",
}

_COUNTRY_TO_CURRENCY = {
    "AUSTRALIA": "AUD",
    "AUD": "AUD",
    "CANADA": "CAD",
    "CAD": "CAD",
    "SWITZERLAND": "CHF",
    "CHF": "CHF",
    "EU": "EUR",
    "EMU": "EUR",
    "EUR": "EUR",
    "EURO AREA": "EUR",
    "EURO ZONE": "EUR",
    "FRANCE": "EUR",
    "GERMANY": "EUR",
    "ITALY": "EUR",
    "SPAIN": "EUR",
    "UNITED KINGDOM": "GBP",
    "UK": "GBP",
    "GBP": "GBP",
    "JAPAN": "JPY",
    "JPY": "JPY",
    "NEW ZEALAND": "NZD",
    "NZD": "NZD",
    "UNITED STATES": "USD",
    "UNITED STATES OF AMERICA": "USD",
    "US": "USD",
    "USA": "USD",
    "USD": "USD",
}

_HIGH_IMPACT_MARKERS = {"HIGH", "RED", "3", "THREE", "HIGH IMPACT EXPECTED"}
_MEDIUM_IMPACT_MARKERS = {"MEDIUM", "ORANGE", "2", "TWO", "MODERATE VOLATILITY EXPECTED"}
_LOW_IMPACT_MARKERS = {"LOW", "YELLOW", "1", "ONE", "LOW IMPACT EXPECTED"}
_HOLIDAY_MARKERS = {"HOLIDAY", "BANK HOLIDAY", "NON-ECONOMIC", "GRAY", "GREY"}


class ForexCalendarClient:
    """Fetch, cache, and filter calendar events without blocking the runtime."""

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        session: requests.Session | None = None,
        calendar_url: str = FAIRECONOMY_CALENDAR_URL,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.session = session or requests.Session()
        self.calendar_url = calendar_url
        self._now_fn = now_fn or (lambda: datetime.now(timezone.utc))
        self._lock = RLock()
        self._events: tuple[CalendarEvent, ...] = ()
        self._events_by_currency: dict[str, tuple[CalendarEvent, ...]] = {}
        self._events_by_country: dict[str, tuple[CalendarEvent, ...]] = {}
        self._events_by_impact: dict[str, tuple[CalendarEvent, ...]] = {}
        self._events_by_currency_and_impact: dict[str, dict[str, tuple[CalendarEvent, ...]]] = {}
        self._last_attempted_at: datetime | None = None
        self._last_refreshed_at: datetime | None = None
        self._calendar_version = 0
        self._last_error: str | None = None
        self._last_request_used_cached = False
        self.logger = get_logger(__name__)

    @property
    def calendar_version(self) -> int:
        """Return the last successful refresh version."""

        self.get_events()
        with self._lock:
            return self._calendar_version

    @property
    def last_refreshed_at(self) -> datetime | None:
        """Return the last successful refresh timestamp."""

        self.get_events()
        with self._lock:
            return self._last_refreshed_at

    @property
    def last_attempted_at(self) -> datetime | None:
        """Return the last attempted refresh timestamp."""

        with self._lock:
            return self._last_attempted_at

    @property
    def last_error(self) -> str | None:
        """Return the latest refresh error, if any."""

        with self._lock:
            return self._last_error

    @property
    def last_request_used_cached(self) -> bool:
        """Return whether the last get_events() call served cached data."""

        with self._lock:
            return self._last_request_used_cached

    @property
    def events_by_currency(self) -> dict[str, tuple[CalendarEvent, ...]]:
        """Return cached events grouped by currency."""

        self.get_events()
        with self._lock:
            return dict(self._events_by_currency)

    @property
    def events_by_country(self) -> dict[str, tuple[CalendarEvent, ...]]:
        """Return cached events grouped by country."""

        self.get_events()
        with self._lock:
            return dict(self._events_by_country)

    @property
    def events_by_impact(self) -> dict[str, tuple[CalendarEvent, ...]]:
        """Return cached events grouped by impact."""

        self.get_events()
        with self._lock:
            return dict(self._events_by_impact)

    @property
    def events_by_currency_and_impact(self) -> dict[str, dict[str, tuple[CalendarEvent, ...]]]:
        """Return cached events grouped by currency and then impact."""

        self.get_events()
        with self._lock:
            return {
                currency: dict(impact_map)
                for currency, impact_map in self._events_by_currency_and_impact.items()
            }

    def get_events(self, *, force: bool = False) -> tuple[CalendarEvent, ...]:
        """Return cached events, refreshing when the cache is stale."""

        with self._lock:
            should_refresh = force or self._refresh_due_locked()
            if not should_refresh:
                self._last_request_used_cached = True
                return self._events

        return self._refresh(force=force)

    def filter_events(
        self,
        *,
        currencies: str | tuple[str, ...] | list[str] | set[str] | None = None,
        countries: str | tuple[str, ...] | list[str] | set[str] | None = None,
        impacts: str | tuple[str, ...] | list[str] | set[str] | None = None,
        window_start: datetime | None = None,
        window_end: datetime | None = None,
    ) -> tuple[CalendarEvent, ...]:
        """Filter cached events by currency, country, impact, and time window."""

        currency_set = self._normalize_value_set(currencies, mode="upper")
        country_set = self._normalize_value_set(countries, mode="upper")
        impact_set = self._normalize_value_set(impacts, mode="impact")
        start = self._ensure_utc(window_start)
        end = self._ensure_utc(window_end)

        filtered: list[CalendarEvent] = []
        for event in self.get_events():
            if currency_set is not None and (event.currency or "").upper() not in currency_set:
                continue
            if country_set is not None and (event.country or "").upper() not in country_set:
                continue
            if impact_set is not None and event.impact not in impact_set:
                continue
            if start is not None and event.event_time < start:
                continue
            if end is not None and event.event_time > end:
                continue
            filtered.append(event)
        return tuple(filtered)

    def get_upcoming_high_impact(
        self,
        hours_ahead: int = 4,
        *,
        currencies: str | tuple[str, ...] | list[str] | set[str] | None = None,
        now_utc: datetime | None = None,
    ) -> tuple[CalendarEvent, ...]:
        """Return upcoming high-impact events within the configured lookahead window."""

        if hours_ahead < 0:
            raise ValueError("hours_ahead must be greater than or equal to zero.")

        current_time = self._ensure_utc(now_utc) or self._now()
        return self.filter_events(
            currencies=currencies,
            impacts=("HIGH",),
            window_start=current_time,
            window_end=current_time + timedelta(hours=hours_ahead),
        )

    def is_event_blackout(
        self,
        minutes_before: int = 30,
        minutes_after: int = 15,
        *,
        currencies: str | tuple[str, ...] | list[str] | set[str] | None = None,
        impacts: str | tuple[str, ...] | list[str] | set[str] | None = DEFAULT_BLACKOUT_IMPACTS,
        now_utc: datetime | None = None,
    ) -> bool:
        """Return True when a matching event falls inside the blackout window."""

        if minutes_before < 0 or minutes_after < 0:
            raise ValueError("Blackout window minutes must be greater than or equal to zero.")

        current_time = self._ensure_utc(now_utc) or self._now()
        upcoming = self.filter_events(
            currencies=currencies,
            impacts=impacts,
            window_start=current_time - timedelta(minutes=minutes_before),
            window_end=current_time + timedelta(minutes=minutes_after),
        )
        return any(event.is_blackout for event in upcoming)

    def _refresh(self, *, force: bool) -> tuple[CalendarEvent, ...]:
        started_at = perf_counter()
        attempt_time = self._now()
        with self._lock:
            self._last_attempted_at = attempt_time
        try:
            rows = self._fetch_rows()
            events = self._parse_rows(rows)
        except Exception as exc:
            with self._lock:
                cached = self._events
                version = self._calendar_version
                self._last_error = str(exc)
                self._last_request_used_cached = bool(cached)
            log_failure(
                self.logger,
                "calendar_refresh_failed",
                exc,
                level="warning",
                using_cached=bool(cached),
                calendar_version=version,
                calendar_url=self.calendar_url,
            )
            return cached

        grouped = self._group_events(events)
        next_high_impact = next(
            (event.event_time for event in events if event.impact == "HIGH"),
            None,
        )
        duration_ms = (perf_counter() - started_at) * 1000.0

        with self._lock:
            if not force and not self._refresh_due_locked() and self._events:
                self._last_request_used_cached = True
                return self._events

            self._events = events
            self._events_by_currency = grouped["currency"]
            self._events_by_country = grouped["country"]
            self._events_by_impact = grouped["impact"]
            self._events_by_currency_and_impact = grouped["currency_impact"]
            self._last_refreshed_at = attempt_time
            self._calendar_version += 1
            self._last_error = None
            self._last_request_used_cached = False
            version = self._calendar_version

        self.logger.info(
            "calendar_fetched",
            event_count=len(events),
            high_impact_count=sum(1 for event in events if event.impact == "HIGH"),
            next_high_impact=next_high_impact,
            calendar_version=version,
            fetch_duration_ms=duration_ms,
        )
        return events

    def _fetch_rows(self) -> list[Mapping[str, Any]]:
        response = self.session.get(
            self.calendar_url,
            headers=_BROWSER_HEADERS,
            timeout=DEFAULT_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        payload = response.json()

        if isinstance(payload, list):
            return [row for row in payload if isinstance(row, Mapping)]
        if isinstance(payload, Mapping):
            for key in ("events", "calendar", "data"):
                candidate = payload.get(key)
                if isinstance(candidate, list):
                    return [row for row in candidate if isinstance(row, Mapping)]

        raise RuntimeError("FairEconomy calendar payload was not a list of events.")

    def _parse_rows(self, rows: list[Mapping[str, Any]]) -> tuple[CalendarEvent, ...]:
        parsed: list[CalendarEvent] = []
        for row in rows:
            event = self._parse_row(row)
            if event is not None:
                parsed.append(event)
        parsed.sort(key=lambda event: event.event_time)
        return tuple(parsed)

    def _parse_row(self, row: Mapping[str, Any]) -> CalendarEvent | None:
        title = self._first_text(row, "title", "event", "event_title", "name")
        if title is None:
            return None

        event_time = self._parse_event_time(row)
        if event_time is None:
            return None

        currency, country = self._parse_currency_and_country(row)
        impact = self._normalize_impact(
            self._first_text(row, "impact", "impact_name", "impactTitle", "impact_class")
        )

        forecast = self._first_text(row, "forecast", "f_cast", "expected")
        previous = self._first_text(row, "previous", "prev")
        actual = self._first_text(row, "actual", "act")

        return CalendarEvent(
            title=title,
            event_time=event_time,
            impact=impact,
            currency=currency,
            country=country,
            is_blackout=impact == "HIGH",
            forecast=forecast,
            previous=previous,
            actual=actual,
        )

    def _parse_event_time(self, row: Mapping[str, Any]) -> datetime | None:
        for key in ("event_time", "datetime", "timestamp", "date_utc", "time_utc"):
            parsed = self._parse_datetime_value(row.get(key))
            if parsed is not None:
                return parsed

        # FairEconomy format: "date" field holds a full ISO datetime string
        # (e.g. "2026-03-21T13:30:00-04:00"). Only treat it as a datetime when
        # a time separator is present; date-only values fall through to the
        # date+time branch below so "Tentative" / missing time can still filter them.
        for key in ("date", "event_date"):
            value = row.get(key)
            if isinstance(value, str) and "T" in value:
                parsed = self._parse_datetime_value(value)
                if parsed is not None:
                    return parsed

        date_text = self._first_text(row, "date", "event_date", "day")
        time_text = self._first_text(row, "time", "event_time_text")
        if date_text is None or time_text is None:
            return None
        return self._parse_date_and_time(date_text, time_text)

    def _parse_date_and_time(self, date_text: str, time_text: str) -> datetime | None:
        normalized_time = time_text.strip()
        if not normalized_time:
            return None

        lowered = normalized_time.casefold()
        if lowered in {"all day", "tentative", "day 1", "day 2", "day 3"}:
            return None

        combined = f"{date_text.strip()} {normalized_time}"
        for fmt in self._datetime_formats():
            try:
                parsed = datetime.strptime(combined, fmt)
                return parsed.replace(tzinfo=timezone.utc)
            except ValueError:
                continue

        with_year = f"{combined} {self._now().year}"
        for fmt in self._datetime_formats_with_injected_year():
            try:
                parsed = datetime.strptime(with_year, fmt)
                return parsed.replace(tzinfo=timezone.utc)
            except ValueError:
                continue
        return None

    @staticmethod
    def _datetime_formats() -> tuple[str, ...]:
        return (
            "%Y-%m-%d %H:%M",
            "%Y-%m-%d %I:%M%p",
            "%Y-%m-%d %I:%M %p",
            "%Y/%m/%d %H:%M",
            "%Y/%m/%d %I:%M%p",
            "%Y/%m/%d %I:%M %p",
            "%Y.%m.%d %H:%M",
            "%Y.%m.%d %I:%M%p",
            "%Y.%m.%d %I:%M %p",
            "%m-%d-%Y %H:%M",
            "%m-%d-%Y %I:%M%p",
            "%m-%d-%Y %I:%M %p",
            "%m/%d/%Y %H:%M",
            "%m/%d/%Y %I:%M%p",
            "%m/%d/%Y %I:%M %p",
            "%a %b %d %Y %H:%M",
            "%a %b %d %Y %I:%M%p",
            "%a %b %d %Y %I:%M %p",
            "%b %d %Y %H:%M",
            "%b %d %Y %I:%M%p",
            "%b %d %Y %I:%M %p",
        )

    @staticmethod
    def _datetime_formats_with_injected_year() -> tuple[str, ...]:
        return (
            "%a %b %d %H:%M %Y",
            "%a %b %d %I:%M%p %Y",
            "%a %b %d %I:%M %p %Y",
            "%b %d %H:%M %Y",
            "%b %d %I:%M%p %Y",
            "%b %d %I:%M %p %Y",
        )

    def _parse_datetime_value(self, value: Any) -> datetime | None:
        if value is None:
            return None
        if isinstance(value, datetime):
            return self._ensure_utc(value)
        if isinstance(value, (int, float)):
            timestamp = float(value)
            if timestamp > 10_000_000_000:
                timestamp /= 1000.0
            return datetime.fromtimestamp(timestamp, tz=timezone.utc)

        text = str(value).strip()
        if not text:
            return None

        normalized = text.replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError:
            parsed = None
        if parsed is not None:
            return self._ensure_utc(parsed)

        for fmt in (
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%Y/%m/%d %H:%M:%S",
            "%Y/%m/%d %H:%M",
            "%Y.%m.%d %H:%M:%S",
            "%Y.%m.%d %H:%M",
        ):
            try:
                return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
            except ValueError:
                continue
        return None

    def _parse_currency_and_country(self, row: Mapping[str, Any]) -> tuple[str | None, str | None]:
        raw_currency = self._first_text(row, "currency", "ccy", "code")
        raw_country = self._first_text(row, "country", "country_name", "nation")

        currency = self._normalize_currency(raw_currency)
        if currency is None and raw_country is not None:
            currency = _COUNTRY_TO_CURRENCY.get(raw_country.upper())

        country = raw_country.strip() if raw_country else None
        if country is None and raw_currency is not None and self._normalize_currency(raw_currency) is None:
            country = raw_currency.strip()

        return currency, country

    @staticmethod
    def _normalize_impact(value: str | None) -> ImpactLevel:
        if value is None:
            return "UNKNOWN"

        normalized = value.strip().upper()
        if normalized in _HIGH_IMPACT_MARKERS:
            return "HIGH"
        if normalized in _MEDIUM_IMPACT_MARKERS:
            return "MEDIUM"
        if normalized in _LOW_IMPACT_MARKERS:
            return "LOW"
        if normalized in _HOLIDAY_MARKERS:
            return "HOLIDAY"
        return "UNKNOWN"

    @staticmethod
    def _normalize_currency(value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().upper()
        if len(normalized) == 3 and normalized.isalpha():
            return normalized
        return _COUNTRY_TO_CURRENCY.get(normalized)

    def _group_events(
        self,
        events: tuple[CalendarEvent, ...],
    ) -> dict[str, Any]:
        by_currency: dict[str, list[CalendarEvent]] = defaultdict(list)
        by_country: dict[str, list[CalendarEvent]] = defaultdict(list)
        by_impact: dict[str, list[CalendarEvent]] = defaultdict(list)
        by_currency_impact: dict[str, dict[str, list[CalendarEvent]]] = defaultdict(
            lambda: defaultdict(list)
        )

        for event in events:
            by_impact[event.impact].append(event)
            if event.currency is not None:
                by_currency[event.currency].append(event)
                by_currency_impact[event.currency][event.impact].append(event)
            if event.country is not None:
                by_country[event.country].append(event)

        return {
            "currency": {
                key: tuple(value)
                for key, value in sorted(by_currency.items())
            },
            "country": {
                key: tuple(value)
                for key, value in sorted(by_country.items())
            },
            "impact": {
                key: tuple(value)
                for key, value in sorted(by_impact.items())
            },
            "currency_impact": {
                currency: {
                    impact: tuple(group)
                    for impact, group in sorted(impact_map.items())
                }
                for currency, impact_map in sorted(by_currency_impact.items())
            },
        }

    def _refresh_due_locked(self) -> bool:
        if self._last_refreshed_at is None:
            return True
        refresh_after = self._last_refreshed_at + timedelta(
            hours=self.settings.calendar_refresh_hours
        )
        return self._now() >= refresh_after

    @staticmethod
    def _first_text(row: Mapping[str, Any], *keys: str) -> str | None:
        for key in keys:
            value = row.get(key)
            if value is None:
                continue
            text = str(value).strip()
            if text:
                return text
        return None

    @staticmethod
    def _normalize_value_set(
        values: str | tuple[str, ...] | list[str] | set[str] | None,
        *,
        mode: str,
    ) -> set[str] | None:
        if values is None:
            return None
        if isinstance(values, str):
            raw_values = [values]
        else:
            raw_values = list(values)

        normalized: set[str] = set()
        for value in raw_values:
            text = str(value).strip()
            if not text:
                continue
            if mode == "impact":
                normalized.add(ForexCalendarClient._normalize_impact(text))
            else:
                normalized.add(text.upper())
        return normalized

    def _now(self) -> datetime:
        current = self._now_fn()
        return self._ensure_utc(current) or datetime.now(timezone.utc)

    @staticmethod
    def _ensure_utc(value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


ForexCalendarProvider = ForexCalendarClient

__all__ = [
    "DEFAULT_BLACKOUT_IMPACTS",
    "FAIRECONOMY_CALENDAR_URL",
    "ForexCalendarClient",
    "ForexCalendarProvider",
]
