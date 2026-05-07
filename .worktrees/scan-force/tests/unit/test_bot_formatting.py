"""Unit tests for bot/formatting.py — format_calendar_output."""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from core.models import CalendarEvent, CalendarRefreshStatus
from bot.formatting import format_calendar_output

SGT = ZoneInfo("Asia/Singapore")
UTC = timezone.utc


def _make_status(
    *,
    event_count: int = 10,
    calendar_version: int = 3,
    last_refreshed_at: datetime | None = None,
    next_high_impact: datetime | None = None,
    last_error: str | None = None,
) -> CalendarRefreshStatus:
    refreshed = last_refreshed_at or datetime(2026, 3, 22, 0, 0, tzinfo=UTC)
    return CalendarRefreshStatus(
        event_count=event_count,
        calendar_version=calendar_version,
        last_refreshed_at=refreshed,
        next_high_impact=next_high_impact,
        last_error=last_error,
    )


def _make_event(
    *,
    title: str = "CPI m/m",
    event_time: datetime,
    impact: str = "HIGH",
    currency: str | None = "USD",
    forecast: str | None = None,
    previous: str | None = None,
    actual: str | None = None,
) -> CalendarEvent:
    return CalendarEvent(
        title=title,
        event_time=event_time,
        impact=impact,
        currency=currency,
        forecast=forecast,
        previous=previous,
        actual=actual,
        is_blackout=impact == "HIGH",
    )


def test_format_calendar_output_empty_events() -> None:
    status = _make_status()
    output = format_calendar_output(
        status=status,
        events=(),
        scope="today",
        requested_currencies=None,
        sgt=SGT,
    )
    assert "No upcoming events in this window." in output
    assert "Calendar (SGT) | today" in output


def test_format_calendar_output_no_events_line_absent_when_all_empty() -> None:
    """When events is empty, show fallback text — no 'No events:' line."""
    status = _make_status()
    output = format_calendar_output(
        status=status,
        events=(),
        scope="today",
        requested_currencies=None,
        sgt=SGT,
    )
    assert "No events:" not in output


def test_format_calendar_output_groups_by_currency_alphabetically() -> None:
    events = (
        _make_event(title="FOMC", event_time=datetime(2026, 3, 22, 18, 0, tzinfo=UTC), currency="USD"),
        _make_event(title="ECB Rate", event_time=datetime(2026, 3, 22, 11, 45, tzinfo=UTC), currency="EUR"),
    )
    status = _make_status()
    output = format_calendar_output(
        status=status,
        events=events,
        scope="today",
        requested_currencies=None,
        sgt=SGT,
    )
    eur_pos = output.index("— EUR")
    usd_pos = output.index("— USD")
    assert eur_pos < usd_pos  # EUR comes before USD alphabetically


def test_format_calendar_output_events_within_currency_sorted_chronologically() -> None:
    events = (
        _make_event(title="Late Event", event_time=datetime(2026, 3, 22, 20, 0, tzinfo=UTC), currency="USD"),
        _make_event(title="Early Event", event_time=datetime(2026, 3, 22, 13, 0, tzinfo=UTC), currency="USD"),
    )
    status = _make_status()
    output = format_calendar_output(
        status=status,
        events=events,
        scope="today",
        requested_currencies=None,
        sgt=SGT,
    )
    early_pos = output.index("Early Event")
    late_pos = output.index("Late Event")
    assert early_pos < late_pos


def test_format_calendar_output_high_badge() -> None:
    events = (
        _make_event(event_time=datetime(2026, 3, 22, 13, 0, tzinfo=UTC), impact="HIGH"),
    )
    output = format_calendar_output(
        status=_make_status(),
        events=events,
        scope="today",
        requested_currencies=("USD",),
        sgt=SGT,
    )
    assert "[HIGH]" in output


def test_format_calendar_output_med_badge_for_medium() -> None:
    events = (
        _make_event(event_time=datetime(2026, 3, 22, 13, 0, tzinfo=UTC), impact="MEDIUM"),
    )
    output = format_calendar_output(
        status=_make_status(),
        events=events,
        scope="today",
        requested_currencies=("USD",),
        sgt=SGT,
    )
    assert "[MED]" in output
    assert "[MEDIUM]" not in output


def test_format_calendar_output_act_line_present_when_actual_nonempty() -> None:
    events = (
        _make_event(
            event_time=datetime(2026, 3, 22, 13, 0, tzinfo=UTC),
            forecast="0.3%",
            previous="0.5%",
            actual="0.4%",
        ),
    )
    output = format_calendar_output(
        status=_make_status(),
        events=events,
        scope="today",
        requested_currencies=("USD",),
        sgt=SGT,
    )
    assert "Act: 0.4%" in output
    assert "Fcst: 0.3%" in output
    assert "Prev: 0.5%" in output


def test_format_calendar_output_act_line_absent_when_actual_is_none() -> None:
    events = (
        _make_event(
            event_time=datetime(2026, 3, 22, 13, 0, tzinfo=UTC),
            forecast="0.3%",
            previous="0.5%",
            actual=None,
        ),
    )
    output = format_calendar_output(
        status=_make_status(),
        events=events,
        scope="today",
        requested_currencies=("USD",),
        sgt=SGT,
    )
    assert "Act:" not in output


def test_format_calendar_output_dash_for_none_forecast_previous() -> None:
    events = (
        _make_event(
            event_time=datetime(2026, 3, 22, 13, 0, tzinfo=UTC),
            forecast=None,
            previous=None,
        ),
    )
    output = format_calendar_output(
        status=_make_status(),
        events=events,
        scope="today",
        requested_currencies=("USD",),
        sgt=SGT,
    )
    assert "Prev: —" in output
    assert "Fcst: —" in output


def test_format_calendar_output_no_events_line_for_zero_event_currencies() -> None:
    events = (
        _make_event(event_time=datetime(2026, 3, 22, 13, 0, tzinfo=UTC), currency="USD"),
    )
    output = format_calendar_output(
        status=_make_status(),
        events=events,
        scope="today",
        requested_currencies=("USD", "EUR", "GBP"),
        sgt=SGT,
    )
    assert "No events:" in output
    assert "EUR" in output.split("No events:")[1]
    assert "GBP" in output.split("No events:")[1]


def test_format_calendar_output_v0_label() -> None:
    status = _make_status(calendar_version=0)
    output = format_calendar_output(
        status=status,
        events=(),
        scope="today",
        requested_currencies=None,
        sgt=SGT,
    )
    assert "v0 (not yet fetched)" in output


def test_format_calendar_output_refreshed_never_when_none() -> None:
    status = CalendarRefreshStatus(
        event_count=0,
        calendar_version=0,
        last_refreshed_at=None,
        next_high_impact=None,
    )
    output = format_calendar_output(
        status=status,
        events=(),
        scope="today",
        requested_currencies=None,
        sgt=SGT,
    )
    assert "Refreshed: never" in output


def test_format_calendar_output_footer_present() -> None:
    output = format_calendar_output(
        status=_make_status(),
        events=(),
        scope="today",
        requested_currencies=None,
        sgt=SGT,
    )
    assert "/calendar week" in output
    assert "/calendar force" in output


def test_format_calendar_output_null_currency_events_discarded() -> None:
    events = (
        _make_event(event_time=datetime(2026, 3, 22, 13, 0, tzinfo=UTC), currency=None, title="Unknown Currency"),
        _make_event(event_time=datetime(2026, 3, 22, 14, 0, tzinfo=UTC), currency="USD", title="USD Event"),
    )
    output = format_calendar_output(
        status=_make_status(),
        events=events,
        scope="today",
        requested_currencies=("USD",),
        sgt=SGT,
    )
    assert "Unknown Currency" not in output
    assert "USD Event" in output


def test_format_calendar_output_next_high_none_shows_none_label() -> None:
    status = _make_status(next_high_impact=None)
    output = format_calendar_output(
        status=status,
        events=(),
        scope="today",
        requested_currencies=None,
        sgt=SGT,
    )
    assert "Next HIGH: none" in output


def test_format_calendar_output_truncation_adds_notice_when_long() -> None:
    """When output exceeds 3800 chars, truncation notice is appended."""
    long_events = tuple(
        _make_event(
            title=f"Very Long Event Title Number {i} That Takes Space",
            event_time=datetime(2026, 3, 22, i % 24, 0, tzinfo=UTC),
            currency=curr,
            forecast="0.3%",
            previous="0.5%",
        )
        for i, curr in enumerate(["AUD"] * 20 + ["CAD"] * 20 + ["CHF"] * 20 + ["EUR"] * 20)
    )
    status = _make_status(event_count=80)
    output = format_calendar_output(
        status=status,
        events=long_events,
        scope="week",
        requested_currencies=None,
        sgt=SGT,
    )
    assert len(output) <= 3800 or "output truncated" in output
