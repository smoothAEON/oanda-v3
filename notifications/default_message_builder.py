"""Default background notification message formatting."""

from __future__ import annotations

from core.models import IndicatorAlert, PriceAlert, TradeRecord


class DefaultNotificationMessageBuilder:
    """Build concise Telegram-ready text for background notifications."""

    def build_trade_opened(
        self,
        trade: TradeRecord,
        *,
        account_currency: str | None = None,
    ) -> str:
        lines = [
            "TRADE OPENED",
            f"{trade.instrument} {trade.direction} {trade.units:.2f} units",
            f"Entry: {trade.open_price:.5f}",
            f"SL: {_format_optional_price(trade.sl_price)}",
            f"TP: {_format_optional_price(trade.tp_price)}",
            f"GSLO: {_format_optional_price(trade.gslo_price)}",
            f"Account Base: {account_currency or trade.account_currency or 'n/a'}",
            f"ID: {trade.trade_id}",
            f"Time: {_format_timestamp(trade.opened_at)}",
        ]
        return "\n".join(lines)

    def build_trade_closed(self, trade: TradeRecord) -> str:
        reason = trade.close_reason.value.replace("_", " ") if trade.close_reason is not None else "MANUAL"
        pnl_text = "n/a" if trade.pips is None else f"{trade.pips:+.1f} pips"
        money_text = (
            "n/a"
            if trade.account_pnl is None or trade.account_currency is None
            else f"{trade.account_pnl:+.2f} {trade.account_currency}"
        )
        lines = [
            "TRADE CLOSED",
            f"{trade.instrument} {trade.direction}",
            f"Entry -> Exit: {trade.open_price:.5f} -> {trade.close_price:.5f}",
            f"P&L: {pnl_text} | {money_text}",
            f"Duration: {_format_duration(trade.opened_at, trade.closed_at)}",
            f"Reason: {reason}",
            f"ID: {trade.trade_id}",
        ]
        return "\n".join(lines)

    def build_price_alert_fired(
        self,
        alert: PriceAlert,
        *,
        current_price: float,
    ) -> str:
        note = f"\nNote: {alert.notes}" if alert.notes else ""
        return (
            f"Price alert #{alert.id} fired\n"
            f"{alert.instrument} {alert.direction} {alert.target_price:.5f}\n"
            f"Current price: {current_price:.5f}{note}"
        )

    def build_indicator_alert_fired(
        self,
        alert: IndicatorAlert,
        *,
        current_value: float | str,
    ) -> str:
        threshold = (
            f" threshold={alert.threshold}"
            if alert.threshold is not None
            else ""
        )
        note = f"\nNote: {alert.notes}" if alert.notes else ""
        return (
            f"Indicator alert #{alert.id} fired\n"
            f"{alert.instrument} {alert.granularity} {alert.indicator.value} {alert.condition}{threshold}\n"
            f"Current value: {current_value}{note}"
        )


def _format_optional_price(value: float | None) -> str:
    if value is None:
        return "None"
    return f"{value:.5f}"


def _format_timestamp(value) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S UTC")


def _format_duration(started_at, completed_at) -> str:
    if completed_at is None:
        return "open"
    total_minutes = max(int((completed_at - started_at).total_seconds() // 60), 0)
    days, rem_minutes = divmod(total_minutes, 60 * 24)
    hours, minutes = divmod(rem_minutes, 60)
    if days:
        return f"{days}d {hours}h {minutes}m"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


__all__ = ["DefaultNotificationMessageBuilder"]
