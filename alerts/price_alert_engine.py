"""Fire-once price alert evaluation for the Stage 11 runtime."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from threading import RLock

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
        self._state_lock = RLock()
        self._pending_by_instrument: dict[str, dict[int, PriceAlert]] = {}
        self.refresh_pending_alert_index()

    def refresh_pending_alert_index(self) -> tuple[str, ...]:
        """Reload the pending price-alert index from storage."""

        pending = self.alert_repository.list_pending_price_alerts()
        indexed: dict[str, dict[int, PriceAlert]] = defaultdict(dict)
        for alert in pending:
            indexed[alert.instrument][alert.id] = alert
        with self._state_lock:
            self._pending_by_instrument = dict(indexed)
            return tuple(sorted(self._pending_by_instrument))

    def active_instruments(self) -> tuple[str, ...]:
        """Return instruments with at least one pending price alert."""

        with self._state_lock:
            return tuple(sorted(self._pending_by_instrument))

    def evaluate_tick(self, tick: PriceTick) -> list[FiredPriceAlert]:
        """Evaluate one tick against the pending price-alert set."""

        fired: list[FiredPriceAlert] = []
        alerts = self._alerts_for_instrument(tick.instrument)
        if not alerts:
            self.refresh_pending_alert_index()
            alerts = self._alerts_for_instrument(tick.instrument)
        for alert in alerts:

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
                    updated = self.alert_repository.mark_price_alert_armed(alert.id)
                    if updated is not None:
                        self._store_pending_alert(updated)
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
            self._remove_pending_alert(updated.instrument, updated.id)

            self.logger.info(
                "alert_fired",
                alert_id=updated.id,
                alert_kind="price",
                instrument=updated.instrument,
                fire_value=fire_value,
                repeat_enabled=False,
            )
            try:
                self.alert_repository.insert_alert_history(
                    {
                        "id": None,
                        "alert_type": "price",
                        "alert_id": updated.id,
                        "chat_id": updated.chat_id,
                        "instrument": updated.instrument,
                        "granularity": None,
                        "indicator": None,
                        "triggered_at": updated.fired_at or tick.time,
                        "trigger_value": float(fire_value),
                        "alert_snapshot": updated.model_dump(mode="json"),
                        "trigger_context": {
                            "bid": float(tick.bid),
                            "ask": float(tick.ask),
                            "target_price": float(updated.target_price),
                            "direction": updated.direction,
                        },
                    }
                )
            except Exception as exc:
                self.logger.warning(
                    "alert_history_write_failed",
                    alert_id=updated.id,
                    alert_kind="price",
                    error=str(exc),
                )
            fired.append(FiredPriceAlert(alert=updated, fire_value=fire_value))
        return fired

    def _alerts_for_instrument(self, instrument: str) -> tuple[PriceAlert, ...]:
        with self._state_lock:
            pending = self._pending_by_instrument.get(instrument)
            if not pending:
                return ()
            return tuple(pending.values())

    def _store_pending_alert(self, alert: PriceAlert) -> None:
        with self._state_lock:
            pending = self._pending_by_instrument.setdefault(alert.instrument, {})
            pending[alert.id] = alert

    def _remove_pending_alert(self, instrument: str, alert_id: int) -> None:
        with self._state_lock:
            pending = self._pending_by_instrument.get(instrument)
            if pending is None:
                return
            pending.pop(alert_id, None)
            if not pending:
                self._pending_by_instrument.pop(instrument, None)


__all__ = ["FiredPriceAlert", "PriceAlertEngine"]
