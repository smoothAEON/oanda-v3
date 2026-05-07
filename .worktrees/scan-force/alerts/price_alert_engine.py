"""Fire-once price alert evaluation for the Stage 11 runtime."""

from __future__ import annotations

from dataclasses import dataclass

from core.events import PriceTick
from core.logging_setup import get_logger
from core.models import PriceAlert
from alerts.alert_repository import AlertRepository
from notifications.message_builder import NotificationMessageBuilder
from notifications.delivery import deliver_message_blocking
from notifications.notifier import Notifier


@dataclass
class FiredPriceAlert:
    """Typed fired-alert payload returned by the engine."""

    alert: PriceAlert
    fire_value: float


class PriceAlertEngine:
    """Evaluate pending price alerts against live ticks."""

    def __init__(
        self,
        alert_repository: AlertRepository,
        *,
        notifier: Notifier | None = None,
        message_builder: NotificationMessageBuilder | None = None,
    ) -> None:
        self.alert_repository = alert_repository
        self.notifier = notifier
        self.message_builder = message_builder
        self.logger = get_logger(__name__)

    def evaluate_tick(self, tick: PriceTick) -> list[FiredPriceAlert]:
        """Evaluate one tick against the pending price-alert set."""

        fired: list[FiredPriceAlert] = []
        for alert in self.alert_repository.list_pending_price_alerts():
            if alert.instrument != tick.instrument:
                continue

            crossed = False
            fire_value = tick.ask
            if alert.direction == "above":
                crossed = tick.ask >= alert.target_price
                fire_value = tick.ask
            else:
                crossed = tick.bid <= alert.target_price
                fire_value = tick.bid

            if not alert.armed:
                if not crossed:
                    self.alert_repository.mark_price_alert_armed(alert.id)
                continue

            if not crossed:
                continue

            if self.notifier is not None and self.message_builder is not None:
                text = self.message_builder.build_price_alert_fired(
                    alert,
                    current_price=fire_value,
                )
                error = deliver_message_blocking(
                    self.notifier,
                    chat_id=alert.chat_id,
                    text=text,
                    logger=self.logger,
                    failure_event="price_alert_notification_failed",
                    alert_id=alert.id,
                    instrument=alert.instrument,
                )
                if error is not None:
                    continue

            updated = self.alert_repository.mark_price_alert_fired(alert.id)
            if updated is None:
                continue

            self.logger.info(
                "alert_fired",
                alert_id=updated.id,
                alert_kind="price",
                instrument=updated.instrument,
                fire_value=fire_value,
                repeat_enabled=False,
            )
            fired.append(FiredPriceAlert(alert=updated, fire_value=fire_value))
        return fired


__all__ = ["FiredPriceAlert", "PriceAlertEngine"]
