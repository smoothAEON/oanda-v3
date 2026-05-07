"""Command-response formatting helpers."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from core.enums import TradeState
from core.instrument_registry import get_instrument_spec
from core.models import (
    CalendarEvent,
    CalendarRefreshStatus,
    ExcursionSample,
    FibSummary,
    PendingOrder,
    PreviousHighLowSummary,
    PriceAlert,
    SessionSummary,
    TimeAlert,
    TradePlanSummary,
    TradeHistoryPage,
    TradeRecord,
)
from bot.parsing import TRACKED_CALENDAR_CURRENCIES


def format_journal_list(
    trades: list[TradeRecord],
    *,
    filter_summary: str,
) -> str:
    """Return the canonical `/journal` list response."""

    lines = ["Trade Journal (last 10)", ""]
    if not trades:
        lines.append("No journaled trades found.")
    else:
        for trade in trades[:10]:
            pips = "—" if trade.state == TradeState.OPEN or trade.pips is None else _signed_pips(trade.pips)
            lines.append(
                f"#{trade.trade_id}  {trade.instrument}  {trade.direction:<5}  {trade.state.value:<6}  {pips:<11}  {trade.opened_at.date().isoformat()}"
            )
    lines.extend(("", f"Filters: {filter_summary}  |  /journal <id> for full detail"))
    return "\n".join(lines)


def format_journal_detail(
    trade: TradeRecord,
    *,
    account_currency: str,
    mae_samples: list[ExcursionSample],
) -> str:
    """Return the canonical `/journal <trade_id>` response."""

    lines = [f"Trade Detail — #{trade.trade_id}", ""]
    lines.append(f"Instrument:   {trade.instrument}")
    lines.append(f"Direction:    {trade.direction}")
    lines.append(f"Units:        {trade.units:.2f}")
    lines.append(f"Entry:        {trade.open_price:.2f}")
    if trade.close_price is not None:
        exit_suffix = " (hit)" if trade.close_reason == "TP_HIT" else ""
        lines.append(f"Exit:         {trade.close_price:.2f}{exit_suffix}")
    lines.append(f"SL:           {_format_optional_price(trade.sl_price)}")
    lines.append(f"TP:           {_format_optional_price(trade.tp_price)}")
    lines.append(f"GSLO:         {_format_optional_price(trade.gslo_price, none_label='None')}")
    lines.append("")

    if trade.state == TradeState.CLOSED:
        pnl_value = trade.account_pnl or 0.0
        lines.append(
            f"P&L:          {_signed_pips(trade.pips or 0.0)}  |  {_format_money(pnl_value, account_currency)}"
        )
        if trade.closed_at is not None:
            lines.append(f"Duration:     {_format_duration(trade.opened_at, trade.closed_at)}")
        lines.append(f"Opened:       {_format_timestamp(trade.opened_at)}")
        if trade.closed_at is not None:
            lines.append(f"Closed:       {_format_timestamp(trade.closed_at)}")
        if trade.close_reason is not None:
            lines.append(f"Reason:       {trade.close_reason.value.replace('_', ' ')}")
        lines.append("")
    else:
        lines.append(f"Opened:       {_format_timestamp(trade.opened_at)}")
        lines.append("")

    if mae_samples:
        mae_sample = max(mae_samples, key=lambda sample: sample.adverse_pips)
        mfe_sample = max(mae_samples, key=lambda sample: sample.favorable_pips)
        lines.append(f"MAE:          {_signed_pips(-mae_sample.adverse_pips)}")
        lines.append(f"MFE:          {_signed_pips(mfe_sample.favorable_pips)}")
        lines.append("")

    if trade.notes:
        lines.append(f'Note:         "{trade.notes}"')

    return "\n".join(lines).rstrip()


def format_trade_history_page(page: TradeHistoryPage) -> str:
    """Return the canonical `/tradehistory` response."""

    instrument_label = page.instrument or "ALL"
    end_local = page.window_end_local
    if page.period.lower().startswith("custom:"):
        end_local = end_local - timedelta(microseconds=1)

    lines = [
        f"Trade History - {page.period.upper()} - {page.view.upper()} - {instrument_label}",
        f"Window: {page.window_start_local.strftime('%Y-%m-%d %H:%M %Z')} -> {end_local.strftime('%Y-%m-%d %H:%M %Z')}",
        f"Trades: {page.total_rows}",
        f"Gross Realized PnL: {_format_decimal_money(page.summary.gross_realized_pl, signed=True)}",
        f"Financing: {_format_decimal_money(page.summary.financing, signed=True)}",
        f"Commission: {_format_decimal_money(page.summary.commission, signed=False)}",
        f"Net Realized PnL: {_format_decimal_money(page.summary.net_realized_pl, signed=True)}",
    ]

    if page.stale_warning:
        lines.extend(("", page.stale_warning))

    lines.append("")
    if not page.rows:
        lines.append("No trade events found.")
    else:
        row_number = (page.page - 1) * page.page_size + 1
        for event in page.rows:
            price_text = "-" if event.price is None else f"{event.price:f}"
            lines.append(
                f"{row_number}) {event.time_local.strftime('%H:%M %Z')} | {event.event_type} | {event.instrument} "
                f"| trade={event.trade_id} | units={_format_decimal_value(event.units)} | px={price_text} "
                f"| RPL={_format_decimal_money(event.realized_pl, signed=True)}"
            )
            row_number += 1

    lines.extend(("", f"Page {page.page}/{page.total_pages}"))
    return "\n".join(lines)


def format_orders_grouped(
    orders: list[PendingOrder],
    mid_prices: dict[str, float] | None = None,
) -> str:
    """Return grouped `/orders` response separating entry orders from risk orders."""

    _mids = mid_prices or {}
    entry_orders = [o for o in orders if not getattr(o, "is_risk_order", False)]
    risk_orders = [o for o in orders if getattr(o, "is_risk_order", False)]

    lines = ["Open Orders", ""]

    if entry_orders:
        lines.append("-- Entry Orders --")
        for order in entry_orders:
            direction = getattr(order, "direction", None)
            direction_str = direction.value if hasattr(direction, "value") else (direction or "N/A")
            order_type = getattr(order, "order_type", "N/A")
            order_type_str = order_type.value if hasattr(order_type, "value") else str(order_type)
            units = getattr(order, "units", None)
            units_str = f"{units:.2f} units" if units is not None else ""
            instrument = getattr(order, "instrument", None) or "N/A"
            mid = _mids.get(instrument) if instrument != "N/A" else None
            pip_size = get_instrument_spec(instrument).pip_size if instrument != "N/A" and mid is not None else 1.0
            dist_str = _pip_dist(order.price, mid, pip_size) if mid is not None else ""
            lines.append(
                f"#{order.order_id}  {instrument}  {order_type_str}  {direction_str}  {units_str}  @ {order.price:.5f}{dist_str}"
            )
        lines.append("")

    if risk_orders:
        lines.append("-- Trade-Attached Orders (SL/TP/GSLO) --")
        for order in risk_orders:
            order_type = getattr(order, "order_type", "N/A")
            order_type_str = order_type.value if hasattr(order_type, "value") else str(order_type)
            trade_ref = f"  (trade #{order.trade_id})" if getattr(order, "trade_id", None) else ""
            instrument = getattr(order, "instrument", None)
            mid = _mids.get(instrument) if instrument else None
            pip_size = get_instrument_spec(instrument).pip_size if instrument and mid is not None else 1.0
            dist_str = _pip_dist(order.price, mid, pip_size) if mid is not None else ""
            lines.append(
                f"#{order.order_id}  {order_type_str}  @ {order.price:.5f}{dist_str}{trade_ref}"
            )
        lines.append("")

    if not entry_orders and not risk_orders:
        lines.append("No open orders.")

    return "\n".join(lines).rstrip()


def format_sessions(
    instrument: str,
    timeframe: str,
    sessions: list[SessionSummary],
) -> str:
    """Return formatted `/session` response with session windows and high/low."""

    lines = [f"Trading Sessions — {instrument} {timeframe}", ""]
    if not sessions:
        lines.append("Session context unavailable.")
        return "\n".join(lines)

    for item in sessions:
        status = "ACTIVE" if item.is_active else "INACTIVE"
        start = _format_time_short(item.window_start)
        end = _format_time_short(item.window_end)
        time_range = f"  {start} - {end}" if item.window_start else ""
        lines.append(f"{item.name}: {status}{time_range}")
        if item.is_active and (item.session_high is not None or item.session_low is not None):
            high = f"{item.session_high:.5f}" if item.session_high is not None else "—"
            low = f"{item.session_low:.5f}" if item.session_low is not None else "—"
            lines.append(f"  High: {high}  Low: {low}")

    return "\n".join(lines)


def format_price_alert_list(alerts: list[PriceAlert]) -> str:
    """Return formatted `/listpricealerts` response."""

    lines = ["Pending Price Alerts", ""]
    if not alerts:
        lines.append("No pending price alerts.")
    else:
        for alert in alerts:
            note_suffix = f'  "{alert.notes}"' if alert.notes else ""
            lines.append(
                f"#{alert.id}  {alert.instrument}  {alert.direction}  {alert.target_price:.5f}{note_suffix}"
            )
    lines.extend(("", "Use /clearpricealert <id> to cancel."))
    return "\n".join(lines)


def format_time_alert_list(alerts: list[TimeAlert]) -> str:
    """Return formatted `/listtimealerts` response."""

    sgt = ZoneInfo("Asia/Singapore")
    lines = ["Active Time Alerts", ""]
    if not alerts:
        lines.append("No active time alerts.")
    else:
        for alert in alerts:
            if alert.session_name is not None:
                descriptor = f"session {alert.session_name}"
            else:
                descriptor = f"at {alert.local_time} {alert.schedule}"
            next_fire = (
                "unknown"
                if alert.next_fire_at is None
                else alert.next_fire_at.astimezone(sgt).strftime("%Y-%m-%d %H:%M SGT")
            )
            note_suffix = f'  "{alert.note}"' if alert.note else ""
            lines.append(f"#{alert.id}  {descriptor}  next={next_fire}{note_suffix}")
    lines.extend(("", "Use /cleartimealert <id> to cancel."))
    return "\n".join(lines)


def format_day_range(
    instrument: str,
    timeframe: str,
    summary: PreviousHighLowSummary | None,
) -> str:
    """Return formatted `/dayrange` output."""

    lines = [f"Day Range {instrument} {timeframe}", ""]
    if summary is None or summary.previous_high is None or summary.previous_low is None:
        lines.append("Previous-day range unavailable.")
        return "\n".join(lines)

    pip_size = get_instrument_spec(instrument).pip_size
    range_pips = (summary.previous_high - summary.previous_low) / pip_size
    lines.extend(
        [
            f"Previous high: {summary.previous_high:.5f}",
            f"Previous low: {summary.previous_low:.5f}",
            f"Range: {range_pips:.1f} pips",
            f"Swept high: {'yes' if summary.broken_high else 'no'}",
            f"Swept low: {'yes' if summary.broken_low else 'no'}",
        ]
    )
    return "\n".join(lines)


def format_previous_day_level(
    instrument: str,
    label: str,
    level: float | None,
    *,
    swept: bool,
) -> str:
    """Return formatted `/pdh` or `/pdl` output."""

    lines = [f"{label} {instrument}", ""]
    if level is None:
        lines.append(f"{label} unavailable.")
        return "\n".join(lines)
    lines.append(f"Level: {level:.5f}")
    lines.append(f"Swept: {'yes' if swept else 'no'}")
    return "\n".join(lines)


def format_fib_summary(summary: FibSummary | None) -> str:
    """Return formatted `/fib` output."""

    if summary is None:
        return "Fibonacci context unavailable from the published snapshot."
    lines = [f"Fib {summary.instrument} {summary.timeframe}", ""]
    lines.append(f"Direction: {summary.direction}")
    lines.append(f"Anchor high: {summary.anchor_high:.5f}")
    lines.append(f"Anchor low: {summary.anchor_low:.5f}")
    lines.append("")
    for level in summary.levels:
        lines.append(f"{level.label}: {level.price:.5f}")
    return "\n".join(lines)


def format_trade_plan(summary: TradePlanSummary) -> str:
    """Return formatted `/tradeplan` output."""

    lines = [f"Trade Plan {summary.instrument}", ""]
    if not summary.valid:
        lines.append("No valid trade setup right now.")
        lines.append("")
        for reason in summary.rejection_reasons:
            lines.append(f"- {reason}")
        return "\n".join(lines)

    lines.extend(
        [
            f"Direction: {summary.direction}",
            f"Setup: {summary.setup}",
            f"Trigger TF: {summary.trigger_timeframe}",
            f"Entry zone: {summary.entry_low:.5f} - {summary.entry_high:.5f}",
            f"Invalidation: {summary.invalidation_price:.5f}",
            f"Target: {summary.target_price:.5f}",
            f"RR: {summary.reward_risk:.2f}",
        ]
    )
    if summary.rationale:
        lines.append("")
        lines.append("Rationale:")
        for item in summary.rationale:
            lines.append(f"- {item}")
    return "\n".join(lines)


def format_maemfe_list(
    trades: list[TradeRecord],
    summaries: dict[str, dict[str, object] | None],
    *,
    current_prices: dict[str, float] | None = None,
) -> str:
    """Return the canonical `/maemfe` list response."""

    now = datetime.now(timezone.utc)
    lines = ["MAE / MFE — Open Trades", ""]
    if not trades:
        lines.append("No open trades tracked.")
    else:
        for trade in trades:
            summary = summaries.get(trade.trade_id) or {}
            mae = float(summary.get("mae_pips", 0.0))
            mfe = float(summary.get("mfe_pips", 0.0))
            duration = _format_duration(trade.opened_at, now)

            # Compute current P/L in pips if prices available
            pl_text = ""
            current_price = None
            if current_prices:
                current_price = current_prices.get(trade.trade_id)
                if current_price is None:
                    current_price = current_prices.get(trade.instrument)
            if current_price is not None:
                pl_pips = _compute_pl_pips(trade, current_price)
                pl_text = f"  P/L: {_signed_pips(pl_pips)}"

            lines.append(
                f"#{trade.trade_id}  {trade.instrument}  {trade.direction:<5}  entry={trade.open_price:.2f}{pl_text}"
            )
            lines.append(
                f"  MAE: {_signed_pips(-mae):>10}  MFE: {_signed_pips(mfe):>10}  ({duration})"
            )
    lines.extend(("", "Updated live from price stream. Use /maemfe <id> for full detail."))
    return "\n".join(lines)


def format_maemfe_detail(
    trade: TradeRecord,
    *,
    current_price: float,
    samples: list[ExcursionSample],
) -> str:
    """Return the canonical `/maemfe <trade_id>` response."""

    mae_sample = max(samples, key=lambda sample: sample.adverse_pips)
    mfe_sample = max(samples, key=lambda sample: sample.favorable_pips)

    pl_pips = _compute_pl_pips(trade, current_price)
    now = datetime.now(timezone.utc)
    duration = _format_duration(trade.opened_at, now) if trade.state == TradeState.OPEN else ""
    duration_text = f"  ({duration} open)" if duration else ""

    lines = [
        f"MAE/MFE — #{trade.trade_id}",
        "",
        f"Instrument:  {trade.instrument}",
        f"Direction:   {trade.direction}",
        f"Entry:       {trade.open_price:.2f}",
        f"Current:     {current_price:.2f}",
        f"P/L:         {_signed_pips(pl_pips)}{duration_text}",
    ]
    if trade.sl_price is not None:
        lines.append(f"SL:          {trade.sl_price:.2f}")
    if trade.tp_price is not None:
        lines.append(f"TP:          {trade.tp_price:.2f}")
    lines.extend([
        "",
        f"MAE (worst):  {_signed_pips(-mae_sample.adverse_pips)}  (at {mae_sample.sampled_at.strftime('%H:%M UTC')})",
        f"MFE (best):   {_signed_pips(mfe_sample.favorable_pips)}  (at {mfe_sample.sampled_at.strftime('%H:%M UTC')})",
        "",
        f"Samples:  {len(samples)}",
    ])
    return "\n".join(lines)


def _compute_pl_pips(trade: TradeRecord, current_price: float) -> float:
    """Compute P/L in pips from entry to current price."""

    try:
        spec = get_instrument_spec(trade.instrument)
        pip_size = spec.pip_size
    except (KeyError, AttributeError):
        pip_size = 0.0001
    if pip_size == 0:
        return 0.0
    raw = (current_price - trade.open_price) / pip_size
    if trade.direction == "SHORT":
        raw = -raw
    return raw


def _format_optional_price(value: float | None, *, none_label: str = "None") -> str:
    return none_label if value is None else f"{value:.2f}"


def _format_timestamp(value: datetime) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S UTC")


def _format_time_short(value: datetime | None) -> str:
    if value is None:
        return "—"
    return value.strftime("%H:%M UTC")


def _format_duration(started_at: datetime, completed_at: datetime) -> str:
    delta = completed_at - started_at
    total_minutes = max(int(delta.total_seconds() // 60), 0)
    hours, minutes = divmod(total_minutes, 60)
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def _signed_pips(value: float) -> str:
    return f"{value:+.1f} pips"


def _pip_dist(level: float | None, mid: float, pip_size: float) -> str:
    """Return signed pip distance annotation like '(+12.3p)' or '' when level is None."""
    if level is None:
        return ""
    dist = (level - mid) / pip_size
    return f"({dist:+.1f}p)"


def _format_money(value: float, currency: str) -> str:
    normalized = currency.upper()
    if normalized == "USD":
        sign = "+" if value >= 0 else "-"
        return f"{sign}${abs(value):.2f}"
    return f"{normalized} {value:+.2f}"


def _format_decimal_money(value: Decimal, *, signed: bool) -> str:
    quantized = value.quantize(Decimal("0.01"))
    if signed:
        return f"{quantized:+f}"
    return f"{quantized:f}"


def _format_decimal_value(value: Decimal) -> str:
    normalized = value.normalize()
    text = format(normalized, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def format_calendar_output(
    *,
    status: CalendarRefreshStatus,
    events: tuple[CalendarEvent, ...],
    scope: str,
    requested_currencies: tuple[str, ...] | None,
    sgt: ZoneInfo,
) -> str:
    """Return formatted /calendar response grouped by currency."""

    def _fmt_time(dt: datetime | None) -> str:
        if dt is None:
            return "never"
        return dt.astimezone(sgt).strftime("%Y-%m-%d %H:%M SGT")

    def _fmt_val(value: str | None) -> str:
        return value if value else "—"

    # Header
    version_str = f"v{status.calendar_version}" if status.calendar_version > 0 else "v0 (not yet fetched)"
    refreshed_str = _fmt_time(status.last_refreshed_at)
    next_high_str = _fmt_time(status.next_high_impact) if status.next_high_impact else "none"
    lines = [
        f"Calendar (SGT) | {scope}",
        f"Cached: {status.event_count}  {version_str}  Refreshed: {refreshed_str}",
        f"Next HIGH: {next_high_str}",
    ]

    if status.last_error and status.event_count > 0:
        lines.append(f"Warning: {status.last_error}")

    lines.append("")

    # Determine display currencies (sorted alphabetically)
    display_currencies = sorted(
        requested_currencies if requested_currencies is not None else TRACKED_CALENDAR_CURRENCIES
    )

    # Group events by currency (discard null-currency events)
    by_currency: dict[str, list[CalendarEvent]] = defaultdict(list)
    for event in events:
        if event.currency is not None:
            by_currency[event.currency].append(event)

    # Build currency sections
    currencies_with_events: list[str] = []
    currencies_without_events: list[str] = []
    section_lines: list[str] = []

    for currency in display_currencies:
        currency_events = by_currency.get(currency, [])
        if not currency_events:
            currencies_without_events.append(currency)
            continue
        currencies_with_events.append(currency)
        sorted_events = sorted(currency_events, key=lambda e: e.event_time)
        section_lines.append(f"— {currency} ({len(sorted_events)}) —")
        time_fmt = "%a %m/%d %H:%M" if scope == "week" else "%H:%M"
        for event in sorted_events:
            time_str = event.event_time.astimezone(sgt).strftime(time_fmt)
            badge = "[HIGH]" if event.impact == "HIGH" else "[MED]"
            section_lines.append(f"{time_str}  {event.title}  {badge}")
            data_line = f"  Prev: {_fmt_val(event.previous)}  Fcst: {_fmt_val(event.forecast)}"
            if event.actual:
                data_line += f"  Act: {event.actual}"
            section_lines.append(data_line)
        section_lines.append("")

    if not events:
        lines.append("No upcoming events in this window.")
    else:
        lines.extend(section_lines)
        if currencies_without_events:
            lines.append(f"No events: {' '.join(currencies_without_events)}")

    lines.append("")
    lines.append("/calendar week | /calendar USD EUR | /calendar force")

    output = "\n".join(lines)

    # Truncate if over Telegram's safe limit
    if len(output) > 3800:
        # Rebuild dropping trailing currency sections until it fits
        kept_sections: list[str] = []
        for currency in display_currencies:
            if currency not in currencies_with_events:
                continue
            currency_events = by_currency.get(currency, [])
            sorted_events = sorted(currency_events, key=lambda e: e.event_time)
            block: list[str] = [f"— {currency} ({len(sorted_events)}) —"]
            for event in sorted_events:
                time_str = event.event_time.astimezone(sgt).strftime(time_fmt)
                badge = "[HIGH]" if event.impact == "HIGH" else "[MED]"
                block.append(f"{time_str}  {event.title}  {badge}")
                data_line = f"  Prev: {_fmt_val(event.previous)}  Fcst: {_fmt_val(event.forecast)}"
                if event.actual:
                    data_line += f"  Act: {event.actual}"
                block.append(data_line)
            kept_sections.append("\n".join(block))

        truncation_notice = "[output truncated — use /calendar USD to filter by currency]"
        footer_parts = []
        if currencies_without_events:
            footer_parts.append(f"No events: {' '.join(currencies_without_events)}")
        footer_parts.append("")
        footer_parts.append("/calendar week | /calendar USD EUR | /calendar force")
        footer = "\n".join(footer_parts)

        # Drop sections from the end until we fit
        while kept_sections:
            body = "\n\n".join(kept_sections)
            candidate = "\n".join(lines[:4]) + "\n\n" + body + "\n" + truncation_notice + "\n" + footer
            if len(candidate) <= 3800:
                return candidate
            kept_sections.pop()

        # If even one section doesn't fit, return just the header + notice
        return "\n".join(lines[:4]) + "\n\n" + truncation_notice + "\n" + footer

    return output


__all__ = [
    "format_calendar_output",
    "format_day_range",
    "format_fib_summary",
    "format_journal_detail",
    "format_journal_list",
    "format_trade_history_page",
    "format_maemfe_detail",
    "format_maemfe_list",
    "format_orders_grouped",
    "format_price_alert_list",
    "format_previous_day_level",
    "format_sessions",
    "format_time_alert_list",
    "format_trade_plan",
]
