from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import requests
import structlog.testing

from config.settings import Settings, load_settings
from core.logging_setup import configure_logging
from data.forex_calendar import ForexCalendarClient


def write_env_file(path: Path, **overrides: str) -> Path:
    values = {
        "OANDA_API_KEY": "api-key",
        "OANDA_ACCOUNT_ID": "account-id",
        "OANDA_ENVIRONMENT": "practice",
        "TELEGRAM_BOT_TOKEN": "telegram-token",
        "TELEGRAM_CHAT_ID": "123456789",
        "TELEGRAM_BOT_PASSWORD": "bot-password",
        "TELEGRAM_ADMIN_IDS": "111,222",
        "LOG_LEVEL": "INFO",
        "LOG_JSON": "false",
        "DEFAULT_CANDLE_COUNT": "500",
        "DEFAULT_SWING_LENGTH": "10",
        "RUPTURES_PENALTY": "10.0",
        "HTF_BIAS_WEIGHT_D": "0.50",
        "HTF_BIAS_WEIGHT_H4": "0.30",
        "HTF_BIAS_WEIGHT_H1": "0.20",
        "HTF_BIAS_NEUTRAL_BAND": "0.15",
        "HTF_TRANSITION_WINDOW_D": "3",
        "HTF_TRANSITION_WINDOW_H4": "4",
        "HTF_TRANSITION_WINDOW_H1": "6",
        "SCAN_INTERVAL_MINUTES": "5",
        "POLL_INTERVAL_SECONDS": "30",
        "STREAM_INSTRUMENTS": "XAU_USD,EUR_USD,GBP_USD,USD_JPY",
        "MAE_MFE_MIN_PIP_MOVE": "0.5",
        "ACCOUNT_CURRENCY": "USD",
        "CALENDAR_REFRESH_HOURS": "1",
        "TINYDB_PATH": str(path.parent / "bot.json"),
    }
    values.update(overrides)
    path.write_text(
        "\n".join(f"{key}={value}" for key, value in values.items()),
        encoding="utf-8",
    )
    return path


def build_settings(tmp_path: Path, **overrides: str) -> Settings:
    return load_settings(env_file=write_env_file(tmp_path / ".env", **overrides))


class FakeResponse:
    def __init__(self, payload, *, status_code: int = 200) -> None:
        self.payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        return self.payload


class SequencedSession:
    def __init__(self, *responses) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, object]] = []

    def get(self, url: str, *, headers: dict[str, str], timeout: int):
        self.calls.append({"url": url, "headers": headers, "timeout": timeout})
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def sample_payload() -> list[dict[str, object]]:
    return [
        {
            "title": "US CPI m/m",
            "date": "2026-03-21",
            "time": "08:30",
            "country": "United States",
            "impact": "High",
        },
        {
            "title": "ECB Rate Statement",
            "datetime": "2026-03-21T12:45:00Z",
            "currency": "EUR",
            "country": "Euro Zone",
            "impact": "Medium",
        },
        {
            "title": "BOJ Bank Holiday",
            "datetime": "2026-03-22T00:00:00Z",
            "country": "Japan",
            "impact": "Holiday",
        },
        {
            "title": "Ignored Event",
            "date": "2026-03-22",
            "time": "Tentative",
            "country": "United Kingdom",
            "impact": "High",
        },
    ]


def test_forex_calendar_parses_faireconomy_iso_date_in_date_field(tmp_path: Path) -> None:
    """FairEconomy returns a full ISO datetime in the 'date' field with no separate 'time' key."""
    payload = [
        {
            "title": "Fed Chair Powell Speaks",
            "date": "2026-03-21T13:30:00-04:00",
            "country": "USD",
            "impact": "High",
        },
    ]
    settings = build_settings(tmp_path)
    session = SequencedSession(FakeResponse(payload))
    client = ForexCalendarClient(
        settings=settings,
        session=session,
        now_fn=lambda: datetime(2026, 3, 21, 8, 15, tzinfo=timezone.utc),
    )

    events = client.get_events(force=True)

    assert len(events) == 1
    assert events[0].title == "Fed Chair Powell Speaks"
    assert events[0].event_time == datetime(2026, 3, 21, 17, 30, tzinfo=timezone.utc)
    assert events[0].impact == "HIGH"
    assert events[0].currency == "USD"


def test_forex_calendar_parses_groups_and_filters_events(tmp_path: Path) -> None:
    settings = build_settings(tmp_path)
    session = SequencedSession(FakeResponse(sample_payload()))
    client = ForexCalendarClient(
        settings=settings,
        session=session,
        now_fn=lambda: datetime(2026, 3, 21, 8, 15, tzinfo=timezone.utc),
    )

    events = client.get_events(force=True)

    assert len(events) == 3
    assert [event.title for event in events] == [
        "US CPI m/m",
        "ECB Rate Statement",
        "BOJ Bank Holiday",
    ]
    assert client.calendar_version == 1
    assert client.events_by_currency["USD"][0].title == "US CPI m/m"
    assert client.events_by_currency["EUR"][0].title == "ECB Rate Statement"
    assert client.events_by_country["Japan"][0].impact == "HOLIDAY"
    assert client.events_by_impact["HIGH"][0].title == "US CPI m/m"
    assert client.events_by_currency_and_impact["USD"]["HIGH"][0].title == "US CPI m/m"

    usd_only = client.filter_events(currencies="USD")
    euro_by_country = client.filter_events(countries="EURO ZONE", impacts="MEDIUM")
    upcoming = client.get_upcoming_high_impact(hours_ahead=2, currencies={"USD", "EUR"})

    assert [event.title for event in usd_only] == ["US CPI m/m"]
    assert [event.title for event in euro_by_country] == ["ECB Rate Statement"]
    assert [event.title for event in upcoming] == ["US CPI m/m"]
    assert len(session.calls) == 1


def test_forex_calendar_blackout_window_detects_matching_event(tmp_path: Path) -> None:
    settings = build_settings(tmp_path)
    session = SequencedSession(FakeResponse(sample_payload()))
    client = ForexCalendarClient(
        settings=settings,
        session=session,
        now_fn=lambda: datetime(2026, 3, 21, 8, 15, tzinfo=timezone.utc),
    )

    assert client.is_event_blackout(currencies="USD") is True
    assert client.is_event_blackout(currencies="EUR") is False


def test_forex_calendar_retains_stale_snapshot_on_refresh_failure(tmp_path: Path) -> None:
    settings = build_settings(tmp_path)
    configure_logging(settings)

    now = {"value": datetime(2026, 3, 21, 8, 15, tzinfo=timezone.utc)}
    session = SequencedSession(
        FakeResponse(sample_payload()),
        requests.RequestException("rate limited"),
    )
    client = ForexCalendarClient(
        settings=settings,
        session=session,
        now_fn=lambda: now["value"],
    )

    first = client.get_events(force=True)
    now["value"] = datetime(2026, 3, 21, 10, 30, tzinfo=timezone.utc)

    with structlog.testing.capture_logs() as logs:
        second = client.get_events(force=True)

    assert second == first
    assert client.calendar_version == 1
    assert client.last_request_used_cached is True
    assert any(entry["event"] == "calendar_refresh_failed" for entry in logs)
    assert any(entry["using_cached"] is True for entry in logs if entry["event"] == "calendar_refresh_failed")


def test_forex_calendar_returns_empty_when_first_refresh_fails(tmp_path: Path) -> None:
    settings = build_settings(tmp_path)
    configure_logging(settings)

    session = SequencedSession(requests.RequestException("forbidden"))
    client = ForexCalendarClient(
        settings=settings,
        session=session,
        now_fn=lambda: datetime(2026, 3, 21, 8, 15, tzinfo=timezone.utc),
    )

    with structlog.testing.capture_logs() as logs:
        events = client.get_events(force=True)

    assert events == ()
    assert client.calendar_version == 0
    assert client.last_request_used_cached is False
    assert any(entry["event"] == "calendar_refresh_failed" for entry in logs)
    assert any(entry["using_cached"] is False for entry in logs if entry["event"] == "calendar_refresh_failed")


def test_forex_calendar_marks_cached_reads_without_refresh(tmp_path: Path) -> None:
    settings = build_settings(tmp_path)
    session = SequencedSession(FakeResponse(sample_payload()))
    client = ForexCalendarClient(
        settings=settings,
        session=session,
        now_fn=lambda: datetime(2026, 3, 21, 8, 15, tzinfo=timezone.utc),
    )

    first = client.get_events(force=True)
    second = client.get_events(force=False)

    assert second == first
    assert client.last_request_used_cached is True
    assert len(session.calls) == 1


def test_forex_calendar_parses_forecast_previous_actual_fields(tmp_path: Path) -> None:
    """forecast/previous/actual are stored when present; empty string becomes None."""
    payload = [
        {
            "title": "US CPI m/m",
            "date": "2026-03-21T13:30:00Z",
            "country": "United States",
            "impact": "High",
            "forecast": "0.3%",
            "previous": "0.5%",
            "actual": "",
        },
        {
            "title": "FOMC Minutes",
            "date": "2026-03-21T18:00:00Z",
            "country": "United States",
            "impact": "High",
            "forecast": "",
            "previous": "",
            "actual": "hawkish",
        },
    ]
    settings = build_settings(tmp_path)
    session = SequencedSession(FakeResponse(payload))
    client = ForexCalendarClient(
        settings=settings,
        session=session,
        now_fn=lambda: datetime(2026, 3, 21, 8, 0, tzinfo=timezone.utc),
    )

    events = client.get_events(force=True)

    assert len(events) == 2
    cpi = events[0]
    assert cpi.title == "US CPI m/m"
    assert cpi.forecast == "0.3%"
    assert cpi.previous == "0.5%"
    assert cpi.actual is None  # empty string -> None via _first_text

    fomc = events[1]
    assert fomc.title == "FOMC Minutes"
    assert fomc.forecast is None
    assert fomc.previous is None
    assert fomc.actual == "hawkish"


def test_forex_calendar_forecast_none_when_field_absent(tmp_path: Path) -> None:
    """Events without forecast/previous/actual fields default to None."""
    payload = [
        {
            "title": "GDP q/q",
            "date": "2026-03-21T13:30:00Z",
            "country": "United States",
            "impact": "High",
        },
    ]
    settings = build_settings(tmp_path)
    session = SequencedSession(FakeResponse(payload))
    client = ForexCalendarClient(
        settings=settings,
        session=session,
        now_fn=lambda: datetime(2026, 3, 21, 8, 0, tzinfo=timezone.utc),
    )

    events = client.get_events(force=True)

    assert len(events) == 1
    assert events[0].forecast is None
    assert events[0].previous is None
    assert events[0].actual is None
