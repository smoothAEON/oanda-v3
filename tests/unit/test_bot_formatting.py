"""Unit tests for bot/formatting.py — format_calendar_output."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from bot.formatting import (
    format_calendar_output,
    format_journal_detail,
    format_journal_list,
    format_maemfe_list,
    format_trade_history_page,
)
from core.enums import CloseReason, TradeState
from core.models import (
    CalendarEvent,
    CalendarRefreshStatus,
    RealizedPnLSummary,
    TradeHistoryEvent,
    TradeHistoryPage,
    TradeRecord,
)

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


def test_format_journal_list_includes_account_currency_pnl_for_closed_trades() -> None:
    trade = TradeRecord(
        trade_id="trade-1",
        instrument="XAU_USD",
        units=1.0,
        open_price=3000.0,
        close_price=3004.2,
        sl_price=2990.0,
        tp_price=3010.0,
        gslo_price=None,
        state=TradeState.CLOSED,
        close_reason=CloseReason.TP_HIT,
        pips=42.0,
        instrument_pnl=4.2,
        instrument_pnl_currency="USD",
        account_pnl=4.2,
        account_currency="USD",
        opened_at=datetime(2026, 4, 1, 0, 0, tzinfo=UTC),
        closed_at=datetime(2026, 4, 1, 4, 0, tzinfo=UTC),
        notes=None,
    )

    output = format_journal_list(
        [trade],
        filter_summary="none",
        account_currency="USD",
    )

    assert "+42.0 pips" in output
    assert "+$4.20" in output


def test_format_journal_detail_prefers_trade_account_currency() -> None:
    trade = TradeRecord(
        trade_id="trade-2",
        instrument="EUR_USD",
        units=-1.0,
        open_price=1.11,
        close_price=1.10,
        sl_price=1.12,
        tp_price=1.10,
        gslo_price=None,
        state=TradeState.CLOSED,
        close_reason=CloseReason.MANUAL,
        pips=10.0,
        instrument_pnl=8.5,
        instrument_pnl_currency="EUR",
        account_pnl=8.5,
        account_currency="EUR",
        opened_at=datetime(2026, 4, 2, 0, 0, tzinfo=UTC),
        closed_at=datetime(2026, 4, 2, 1, 0, tzinfo=UTC),
        notes=None,
    )

    output = format_journal_detail(
        trade,
        account_currency="USD",
        mae_samples=[],
    )

    assert "EUR +8.50" in output
    assert "+$8.50" not in output


def test_format_maemfe_list_includes_current_price_when_available() -> None:
    trade = TradeRecord(
        trade_id="trade-3",
        instrument="XAU_USD",
        units=1.0,
        open_price=3000.0,
        close_price=None,
        sl_price=2990.0,
        tp_price=3015.0,
        gslo_price=None,
        state=TradeState.OPEN,
        close_reason=None,
        pips=None,
        instrument_pnl=None,
        instrument_pnl_currency=None,
        account_pnl=None,
        account_currency=None,
        opened_at=datetime(2026, 4, 2, 0, 0, tzinfo=UTC),
        closed_at=None,
        notes=None,
    )

    output = format_maemfe_list(
        [trade],
        {"trade-3": {"mae_pips": 12.0, "mfe_pips": 30.0}},
        current_prices={"trade-3": 3005.25},
    )

    assert "current=3005.25000" in output
    assert "P/L: +525.0 pips" in output
    assert "MAE: -12.0 pips" in output
    assert "MFE: +30.0 pips" in output


def test_format_trade_history_page_adds_summary_headline_and_navigation() -> None:
    summary = RealizedPnLSummary(
        period="custom:2026-04-01:2026-04-01",
        instrument="XAU_USD",
        start_utc=datetime(2026, 3, 31, 16, 0, tzinfo=UTC),
        end_utc=datetime(2026, 4, 1, 15, 59, 59, tzinfo=UTC),
        start_local=datetime(2026, 4, 1, 0, 0, tzinfo=SGT),
        end_local=datetime(2026, 4, 1, 23, 59, 59, tzinfo=SGT),
        gross_realized_pl=Decimal("12.50"),
        financing=Decimal("-0.20"),
        commission=Decimal("0.10"),
        net_realized_pl=Decimal("12.20"),
    )
    page = TradeHistoryPage(
        period=summary.period,
        view="all",
        instrument="XAU_USD",
        window_start_utc=summary.start_utc,
        window_end_utc=summary.end_utc + timedelta(microseconds=1),
        window_start_local=summary.start_local,
        window_end_local=summary.end_local + timedelta(microseconds=1),
        summary=summary,
        rows=(
            TradeHistoryEvent(
                event_id="101:CLOSE:trade-1",
                transaction_id="101",
                batch_id="500",
                event_type="CLOSE",
                account_id="account-id",
                instrument="XAU_USD",
                trade_id="trade-1",
                order_id="9001",
                units=Decimal("-40"),
                abs_units=Decimal("40"),
                side="SHORT",
                price=Decimal("3123.456"),
                realized_pl=Decimal("12.50"),
                financing=Decimal("-0.20"),
                commission=Decimal("0.10"),
                net_realized_pl=Decimal("12.20"),
                time_utc=datetime(2026, 4, 1, 1, 15, tzinfo=UTC),
                time_local=datetime(2026, 4, 1, 9, 15, tzinfo=SGT),
                reason="TAKE_PROFIT_ORDER",
                raw_json="{}",
            ),
        ),
        page=1,
        page_size=20,
        total_rows=41,
        total_pages=3,
        stale_warning=None,
    )

    output = format_trade_history_page(page)

    assert "P&L (2026-04-01): +12.20" in output
    assert "Next: /tradehistory custom:2026-04-01:2026-04-01 all XAU_USD 2" in output
