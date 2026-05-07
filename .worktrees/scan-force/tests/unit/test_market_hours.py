from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from data.market_hours import MarketHoursService


class FakeCalendar:
    def __init__(self, schedule: pd.DataFrame) -> None:
        self._schedule = schedule

    def schedule(self, start_date, end_date) -> pd.DataFrame:
        return self._schedule


def build_schedule(windows: list[tuple[str, str]]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "market_open": pd.to_datetime([open_time for open_time, _ in windows], utc=True),
            "market_close": pd.to_datetime([close_time for _, close_time in windows], utc=True),
        }
    )


def test_market_hours_reports_open_status_for_active_window(monkeypatch) -> None:
    service = MarketHoursService(
        now_fn=lambda: datetime(2026, 3, 24, 12, 0, tzinfo=timezone.utc)
    )
    schedule = build_schedule(
        [
            ("2026-03-23T23:00:00Z", "2026-03-24T22:00:00Z"),
            ("2026-03-24T23:00:00Z", "2026-03-25T22:00:00Z"),
        ]
    )
    monkeypatch.setattr(service, "_load_calendar", lambda name: FakeCalendar(schedule))

    overview = service.get_status()

    assert overview.is_market_open is True
    assert overview.fx.reason == "open"
    assert overview.metals.reason == "open"
    assert overview.overall.reason == "open"
    assert overview.next_close_at == datetime(2026, 3, 24, 22, 0, tzinfo=timezone.utc)


def test_market_hours_reports_pre_open_when_next_session_starts_today(monkeypatch) -> None:
    service = MarketHoursService(
        now_fn=lambda: datetime(2026, 12, 29, 22, 30, tzinfo=timezone.utc)
    )
    schedule = build_schedule(
        [
            ("2026-12-23T23:00:00Z", "2026-12-24T22:00:00Z"),
            ("2026-12-29T23:00:00Z", "2026-12-30T22:00:00Z"),
        ]
    )
    monkeypatch.setattr(service, "_load_calendar", lambda name: FakeCalendar(schedule))

    status = service.get_category_status("fx")

    assert status.is_market_open is False
    assert status.reason == "pre_open"
    assert status.next_open_at == datetime(2026, 12, 29, 23, 0, tzinfo=timezone.utc)


def test_market_hours_reports_after_close_between_sessions(monkeypatch) -> None:
    service = MarketHoursService(
        now_fn=lambda: datetime(2026, 3, 24, 22, 30, tzinfo=timezone.utc)
    )
    schedule = build_schedule(
        [
            ("2026-03-23T23:00:00Z", "2026-03-24T22:00:00Z"),
            ("2026-03-24T23:00:00Z", "2026-03-25T22:00:00Z"),
        ]
    )
    monkeypatch.setattr(service, "_load_calendar", lambda name: FakeCalendar(schedule))

    status = service.get_category_status("fx")

    assert status.is_market_open is False
    assert status.reason == "after_close"
    assert status.next_open_at == datetime(2026, 3, 24, 23, 0, tzinfo=timezone.utc)


def test_market_hours_reports_weekend_closed(monkeypatch) -> None:
    service = MarketHoursService(
        now_fn=lambda: datetime(2026, 3, 28, 12, 0, tzinfo=timezone.utc)
    )
    schedule = build_schedule(
        [
            ("2026-03-26T23:00:00Z", "2026-03-27T22:00:00Z"),
            ("2026-03-29T23:00:00Z", "2026-03-30T22:00:00Z"),
        ]
    )
    monkeypatch.setattr(service, "_load_calendar", lambda name: FakeCalendar(schedule))

    status = service.get_category_status("fx")

    assert status.is_market_open is False
    assert status.reason == "weekend_closed"


def test_market_hours_reports_holiday_closed(monkeypatch) -> None:
    service = MarketHoursService(
        now_fn=lambda: datetime(2026, 12, 25, 12, 0, tzinfo=timezone.utc)
    )
    schedule = build_schedule(
        [
            ("2026-12-23T23:00:00Z", "2026-12-24T22:00:00Z"),
            ("2026-12-28T23:00:00Z", "2026-12-29T22:00:00Z"),
        ]
    )
    monkeypatch.setattr(service, "_load_calendar", lambda name: FakeCalendar(schedule))

    status = service.get_category_status("metals")

    assert status.is_market_open is False
    assert status.reason == "holiday_closed"
    assert status.next_open_at == datetime(2026, 12, 28, 23, 0, tzinfo=timezone.utc)


def test_market_hours_overview_supports_mixed_fx_and_metals_state(monkeypatch) -> None:
    service = MarketHoursService(
        now_fn=lambda: datetime(2026, 3, 24, 12, 0, tzinfo=timezone.utc)
    )
    schedules = {
        "CME_FX": build_schedule(
            [
                ("2026-03-23T23:00:00Z", "2026-03-24T22:00:00Z"),
                ("2026-03-24T23:00:00Z", "2026-03-25T22:00:00Z"),
            ]
        ),
        "CMEGlobex_PreciousMetals": build_schedule(
            [
                ("2026-03-20T23:00:00Z", "2026-03-21T22:00:00Z"),
                ("2026-03-24T23:00:00Z", "2026-03-25T22:00:00Z"),
            ]
        ),
    }
    monkeypatch.setattr(service, "_load_calendar", lambda name: FakeCalendar(schedules[name]))

    overview = service.get_status()

    assert overview.is_market_open is True
    assert overview.overall.reason == "partial_open"
    assert overview.fx.is_market_open is True
    assert overview.metals.is_market_open is False
    assert overview.metals.reason == "pre_open"
    assert overview.category_status("major_fx") == overview.fx
    assert overview.category_status("metal") == overview.metals
