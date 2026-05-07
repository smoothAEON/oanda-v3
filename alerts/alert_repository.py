"""Alert repository wrappers for the Stage 11 runtime."""

from __future__ import annotations

from pathlib import Path

from alerts.constants import EVALUATED_INDICATOR_ALERT_TIMEFRAMES
from config.settings import Settings
from core.enums import AlertStatus, TimeAlertStatus
from core.models import AlertHistoryRecord, IndicatorAlert, IndicatorAlertEvaluationCursor, PriceAlert, TimeAlert
from data.persistence.trade_store import TradeStore


class AlertRepository:
    """Typed wrapper over the shared TinyDB alert collections."""

    def __init__(
        self,
        *,
        store: TradeStore | None = None,
        db_path: Path | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.store = store or TradeStore(db_path=db_path, settings=settings)

    def upsert_price_alert(self, alert: PriceAlert | dict[str, object]) -> PriceAlert:
        return self.store.upsert_price_alert(alert)

    def get_price_alert(self, alert_id: int) -> PriceAlert | None:
        return self.store.get_price_alert(alert_id)

    def list_pending_price_alerts(self) -> list[PriceAlert]:
        return self.store.list_pending_price_alerts()

    def list_pending_price_alerts_for_chat(self, chat_id: int) -> list[PriceAlert]:
        return self.store.list_pending_price_alerts_for_chat(chat_id)

    def mark_price_alert_fired(self, alert_id: int) -> PriceAlert | None:
        return self.store.mark_price_alert_fired(alert_id)

    def mark_price_alert_armed(self, alert_id: int) -> PriceAlert | None:
        return self.store.mark_price_alert_armed(alert_id)

    def upsert_indicator_alert(
        self,
        alert: IndicatorAlert | dict[str, object],
    ) -> IndicatorAlert:
        payload = alert.model_dump(mode="python") if isinstance(alert, IndicatorAlert) else dict(alert)
        if payload.get("id") is None:
            granularity = str(payload.get("granularity", "")).upper()
            if granularity not in EVALUATED_INDICATOR_ALERT_TIMEFRAMES:
                supported = ", ".join(EVALUATED_INDICATOR_ALERT_TIMEFRAMES)
                raise ValueError(
                    f"Automatic indicator alerts support {supported} only. Unsupported timeframe: {granularity}."
                )
        return self.store.upsert_indicator_alert(alert)

    def get_indicator_alert(self, alert_id: int) -> IndicatorAlert | None:
        return self.store.get_indicator_alert(alert_id)

    def list_pending_indicator_alerts(self) -> list[IndicatorAlert]:
        return self.store.list_pending_indicator_alerts()

    def list_active_indicator_alerts(self) -> list[IndicatorAlert]:
        return self.store.list_indicator_alerts(
            statuses=(AlertStatus.PENDING, AlertStatus.FIRED)
        )

    def list_active_indicator_alerts_for_chat(self, chat_id: int) -> list[IndicatorAlert]:
        return self.store.list_indicator_alerts(
            statuses=(AlertStatus.PENDING, AlertStatus.FIRED),
            chat_id=chat_id,
        )

    def mark_indicator_alert_fired(self, alert_id: int) -> IndicatorAlert | None:
        return self.store.mark_indicator_alert_fired(alert_id)

    def get_indicator_alert_evaluation_cursor(
        self,
        instrument: str,
        granularity: str,
    ) -> IndicatorAlertEvaluationCursor | None:
        return self.store.get_indicator_alert_evaluation_cursor(instrument, granularity)

    def upsert_indicator_alert_evaluation_cursor(
        self,
        record: IndicatorAlertEvaluationCursor | dict[str, object],
    ) -> IndicatorAlertEvaluationCursor:
        return self.store.upsert_indicator_alert_evaluation_cursor(record)

    def cancel_price_alert(self, alert_id: int) -> PriceAlert | None:
        return self.store.cancel_price_alert(alert_id)

    def cancel_price_alert_for_chat(self, alert_id: int, chat_id: int) -> PriceAlert | None:
        return self.store.cancel_price_alert_for_chat(alert_id, chat_id)

    def cancel_price_alerts_for_chat(
        self,
        chat_id: int,
        *,
        instrument: str | None = None,
        statuses: tuple[AlertStatus, ...] = (AlertStatus.PENDING,),
    ) -> list[PriceAlert]:
        return self.store.cancel_price_alerts_for_chat(
            chat_id,
            instrument=instrument,
            statuses=statuses,
        )

    def replace_price_alert_grid_for_chat(
        self,
        chat_id: int,
        *,
        instrument: str,
        alerts: list[PriceAlert | dict[str, object]],
    ) -> list[PriceAlert]:
        return self.store.replace_price_alert_grid(
            chat_id=chat_id,
            instrument=instrument,
            alerts=alerts,
        )

    def cancel_indicator_alert(self, alert_id: int) -> IndicatorAlert | None:
        return self.store.cancel_indicator_alert(alert_id)

    def cancel_indicator_alert_for_chat(self, alert_id: int, chat_id: int) -> IndicatorAlert | None:
        return self.store.cancel_indicator_alert_for_chat(alert_id, chat_id)

    def cancel_indicator_alerts_for_chat(
        self,
        chat_id: int,
        *,
        instrument: str | None = None,
        granularity: str | None = None,
        indicator: str | None = None,
        statuses: tuple[AlertStatus, ...] = (AlertStatus.PENDING, AlertStatus.FIRED),
    ) -> list[IndicatorAlert]:
        return self.store.cancel_indicator_alerts_for_chat(
            chat_id,
            instrument=instrument,
            granularity=granularity,
            indicator=indicator,
            statuses=statuses,
        )

    def upsert_time_alert(self, alert: TimeAlert | dict[str, object]) -> TimeAlert:
        return self.store.upsert_time_alert(alert)

    def create_time_alerts(self, alerts: list[TimeAlert | dict[str, object]]) -> list[TimeAlert]:
        return self.store.create_time_alerts(alerts)

    def get_time_alert(self, alert_id: int) -> TimeAlert | None:
        return self.store.get_time_alert(alert_id)

    def list_active_time_alerts(self) -> list[TimeAlert]:
        return self.store.list_time_alerts(statuses=(TimeAlertStatus.ACTIVE,))

    def list_active_time_alerts_for_chat(self, chat_id: int) -> list[TimeAlert]:
        return self.store.list_time_alerts(statuses=(TimeAlertStatus.ACTIVE,), chat_id=chat_id)

    def mark_time_alert_triggered(
        self,
        alert_id: int,
        *,
        next_fire_at=None,
        fired_at=None,
    ) -> TimeAlert | None:
        return self.store.mark_time_alert_triggered(
            alert_id,
            next_fire_at=next_fire_at,
            fired_at=fired_at,
        )

    def cancel_time_alert(self, alert_id: int) -> TimeAlert | None:
        return self.store.cancel_time_alert(alert_id)

    def cancel_time_alert_for_chat(self, alert_id: int, chat_id: int) -> TimeAlert | None:
        return self.store.cancel_time_alert_for_chat(alert_id, chat_id)

    def insert_alert_history(
        self,
        record: AlertHistoryRecord | dict[str, object],
    ) -> AlertHistoryRecord:
        return self.store.insert_alert_history(record)

    def list_alert_history(
        self,
        *,
        chat_id: int | None = None,
        alert_type: str | None = None,
        instrument: str | None = None,
        start_utc=None,
        end_utc=None,
        limit: int = 50,
    ) -> list[AlertHistoryRecord]:
        return self.store.list_alert_history(
            chat_id=chat_id,
            alert_type=alert_type,
            instrument=instrument,
            start_utc=start_utc,
            end_utc=end_utc,
            limit=limit,
        )

__all__ = ["AlertRepository", "EVALUATED_INDICATOR_ALERT_TIMEFRAMES"]
