"""Scheduled indicator-alert evaluation for the Stage 11 runtime."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Callable

import pandas as pd

from alerts.alert_repository import AlertRepository
from config.settings import Settings, get_settings
from core.enums import AlertStatus, IndicatorKind
from core.logging_setup import get_logger
from core.models import IndicatorAlert, IndicatorMetric
from indicators import build_indicator_summary
from notifications.delivery import deliver_message_blocking
from notifications.message_builder import NotificationMessageBuilder
from notifications.notifier import Notifier
from providers.base import MarketDataProvider


class IndicatorAlertEngine:
    """Evaluate indicator alerts on the scheduled Stage 11 cadence."""

    def __init__(
        self,
        alert_repository: AlertRepository,
        market_data_provider: MarketDataProvider,
        *,
        settings: Settings | None = None,
        indicator_builder: Callable = build_indicator_summary,
        notifier: Notifier | None = None,
        message_builder: NotificationMessageBuilder | None = None,
    ) -> None:
        self.alert_repository = alert_repository
        self.market_data_provider = market_data_provider
        self.settings = settings or get_settings()
        self.indicator_builder = indicator_builder
        self.notifier = notifier
        self.message_builder = message_builder
        self.logger = get_logger(__name__)

    def evaluate_for_snapshot(
        self,
        instrument: str,
        granularity: str,
        candles: pd.DataFrame,
        current_summary,
    ) -> list[IndicatorAlert]:
        """Evaluate indicator alerts for a newly computed snapshot.

        Skips evaluation when the last closed candle was already processed
        for this (instrument, granularity) pair, including across restarts.
        """
        if len(candles) < 2:
            return []

        last_candle: datetime = candles["time"].iloc[-1].to_pydatetime()
        cursor = self.alert_repository.get_indicator_alert_evaluation_cursor(
            instrument,
            granularity,
        )
        if cursor is not None and cursor.last_evaluated_candle == last_candle:
            return []

        active = [
            alert
            for alert in self.alert_repository.list_active_indicator_alerts()
            if alert.instrument == instrument and alert.granularity == granularity
        ]
        eligible = [
            alert for alert in active
            if not (alert.status == AlertStatus.FIRED and not alert.repeat)
            and not (
                alert.status == AlertStatus.FIRED
                and alert.repeat
                and not self._cooloff_elapsed(alert)
            )
        ]

        fired: list[IndicatorAlert] = []
        delivery_failed = False
        if eligible:
            previous_summary = self.indicator_builder(
                candles.iloc[:-1].reset_index(drop=True), granularity
            )
            for alert in eligible:
                current_value, previous_value = self._resolve_values(
                    alert=alert,
                    current_summary=current_summary,
                    previous_summary=previous_summary,
                )
                if not self._is_triggered(alert, current_value=current_value, previous_value=previous_value):
                    continue

                if self.notifier is not None and self.message_builder is not None:
                    text = self.message_builder.build_indicator_alert_fired(
                        alert,
                        current_value=current_value,
                    )
                    error = deliver_message_blocking(
                        self.notifier,
                        chat_id=alert.chat_id,
                        text=text,
                        logger=self.logger,
                        failure_event="indicator_alert_notification_failed",
                        alert_id=alert.id,
                        instrument=alert.instrument,
                        granularity=alert.granularity,
                    )
                    if error is not None:
                        delivery_failed = True
                        continue

                updated = self.alert_repository.mark_indicator_alert_fired(alert.id)
                if updated is None:
                    continue

                self.logger.info(
                    "alert_fired",
                    alert_id=updated.id,
                    alert_kind="indicator",
                    instrument=updated.instrument,
                    fire_value=current_value,
                    repeat_enabled=updated.repeat,
                )
                fired.append(updated)

        if not delivery_failed:
            self.alert_repository.upsert_indicator_alert_evaluation_cursor(
                {
                    "instrument": instrument,
                    "granularity": granularity,
                    "last_evaluated_candle": last_candle,
                    "updated_at": datetime.now(timezone.utc),
                }
            )
        return fired

    def _cooloff_elapsed(self, alert: IndicatorAlert) -> bool:
        if alert.fired_at is None or alert.cooloff_minutes is None:
            return True
        return datetime.now(timezone.utc) >= (alert.fired_at + timedelta(minutes=alert.cooloff_minutes))

    def _resolve_values(
        self,
        *,
        alert: IndicatorAlert,
        current_summary,
        previous_summary,
    ) -> tuple[float, float | None]:
        current_metrics = {metric.name: metric for metric in current_summary.metrics}
        previous_metrics = {metric.name: metric for metric in previous_summary.metrics}

        if alert.indicator == IndicatorKind.RSI:
            return (
                self._required_metric_value(current_metrics, "rsi"),
                self._optional_metric_value(previous_metrics, "rsi"),
            )
        if alert.indicator == IndicatorKind.STOCH:
            return (
                self._required_metric_value(current_metrics, "stoch_k"),
                self._optional_metric_value(previous_metrics, "stoch_k"),
            )
        if alert.indicator == IndicatorKind.SMA_CROSS:
            sma_50 = self._required_metric_value(current_metrics, "sma_50")
            sma_200 = self._required_metric_value(current_metrics, "sma_200")
            prev_sma_50 = self._optional_metric_value(previous_metrics, "sma_50")
            prev_sma_200 = self._optional_metric_value(previous_metrics, "sma_200")
            previous_value = None
            if prev_sma_50 is not None and prev_sma_200 is not None:
                previous_value = prev_sma_50 - prev_sma_200
            return (sma_50 - sma_200, previous_value)

        current_macd = self._required_metric_value(current_metrics, "macd")
        previous_macd = self._optional_metric_value(previous_metrics, "macd")
        current_signal = self._required_metric_value(current_metrics, "macd_signal")
        previous_signal = self._optional_metric_value(previous_metrics, "macd_signal")
        if current_signal is None:
            raise RuntimeError("MACD signal metric is required for MACD alerts.")
        current_value = current_macd - current_signal
        previous_value = None
        if previous_macd is not None and previous_signal is not None:
            previous_value = previous_macd - previous_signal
        return current_value, previous_value

    @staticmethod
    def _required_metric_value(metrics: dict[str, IndicatorMetric], name: str) -> float:
        metric = metrics.get(name)
        if metric is None or metric.value is None:
            raise RuntimeError(f"Indicator metric {name!r} is unavailable for alert evaluation.")
        return metric.value

    @staticmethod
    def _optional_metric_value(metrics: dict[str, IndicatorMetric], name: str) -> float | None:
        metric = metrics.get(name)
        if metric is None:
            return None
        return metric.value

    def _is_triggered(
        self,
        alert: IndicatorAlert,
        *,
        current_value: float,
        previous_value: float | None,
    ) -> bool:
        if alert.condition == "above":
            return current_value > float(alert.threshold)
        if alert.condition == "below":
            return current_value < float(alert.threshold)

        baseline = 0.0 if alert.indicator in (IndicatorKind.MACD, IndicatorKind.SMA_CROSS) else 50.0
        previous = baseline if previous_value is None else previous_value
        if alert.condition == "cross_up":
            return previous <= baseline < current_value
        return previous >= baseline > current_value

__all__ = ["IndicatorAlertEngine"]
