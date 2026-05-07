"""Protocol-only notification message builder contracts."""

from __future__ import annotations

from typing import Protocol

from core.models import IndicatorAlert, PriceAlert, TradeRecord


class NotificationMessageBuilder(Protocol):
    """Builds background notification text from typed runtime contracts."""

    def build_trade_opened(
        self,
        trade: TradeRecord,
        *,
        account_currency: str | None = None,
    ) -> str:
        """Build a trade-open background notification."""

    def build_trade_closed(self, trade: TradeRecord) -> str:
        """Build a trade-close background notification."""

    def build_price_alert_fired(
        self,
        alert: PriceAlert,
        *,
        current_price: float,
    ) -> str:
        """Build a price-alert-fired background notification."""

    def build_indicator_alert_fired(
        self,
        alert: IndicatorAlert,
        *,
        current_value: float | str,
    ) -> str:
        """Build an indicator-alert-fired background notification."""


__all__ = ["NotificationMessageBuilder"]
