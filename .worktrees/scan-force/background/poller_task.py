"""Scheduled open-trade polling and diff emission for Stage 11."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone

from config.settings import Settings, get_settings
from core.enums import CloseReason
from core.events import TradeClosedEvent, TradeModifiedEvent, TradeOpenedEvent
from core.instrument_registry import get_instrument_spec
from core.logging_setup import get_logger, log_failure
from core.models import BackgroundTaskStatus
from notifications.message_builder import NotificationMessageBuilder
from notifications.notifier import Notifier
from journal.journal_service import JournalService
from journal.trade_repository import TradeRepository
from notifications.delivery import deliver_message_blocking
from providers.account_client import OandaAccountClient


class TradePollerTask:
    """Diff open trades into typed events and journal writes."""

    def __init__(
        self,
        account_client: OandaAccountClient,
        trade_repository: TradeRepository,
        journal_service: JournalService,
        *,
        settings: Settings | None = None,
        runtime_config_manager=None,
        notifier: Notifier | None = None,
        message_builder: NotificationMessageBuilder | None = None,
    ) -> None:
        self.account_client = account_client
        self.trade_repository = trade_repository
        self.journal_service = journal_service
        self.settings = settings or get_settings()
        self.runtime_config_manager = runtime_config_manager
        self.notifier = notifier
        self.message_builder = message_builder
        self.logger = get_logger(__name__)
        self._started_at: datetime | None = None
        self._last_heartbeat_at: datetime | None = None
        self._last_error_at: datetime | None = None
        self._last_error: str | None = None

    def run_once(self) -> tuple[object, ...]:
        """Run one polling cycle from a scheduler thread."""

        return tuple(self._poll_once_sync())

    async def poll_once(self) -> list[object]:
        """Run one async polling cycle and emit journal events."""

        return await asyncio.to_thread(self._poll_once_sync)

    def _poll_once_sync(self) -> list[object]:
        """Run one polling cycle and emit journal events."""

        now = datetime.now(timezone.utc)
        self._started_at = self._started_at or now
        cycle_errors: list[str] = []
        try:
            current = self.account_client.get_open_trades_sync()
        except Exception as exc:
            self._last_error_at = now
            self._last_error = str(exc)
            log_failure(self.logger, "trade_poller_poll_failed", exc)
            raise

        current_by_id = {str(item["id"]): item for item in current}
        existing_open = {trade.trade_id: trade for trade in self.trade_repository.list_open()}
        emitted: list[object] = []

        for trade_id, payload in current_by_id.items():
            if trade_id not in existing_open:
                try:
                    event = TradeOpenedEvent(
                        trade_id=trade_id,
                        instrument=str(payload["instrument"]),
                        units=float(payload["currentUnits"]),
                        open_price=float(payload["price"]),
                        sl=self._optional_float(payload.get("stop_loss_price")),
                        tp=self._optional_float(payload.get("take_profit_price")),
                        gslo=self._optional_float(payload.get("gslo_price")),
                        opened_at=self._parse_datetime(payload.get("openTime")) or now,
                    )
                except Exception as exc:
                    log_failure(
                        self.logger,
                        "trade_open_event_build_failed",
                        exc,
                        trade_id=trade_id,
                        payload_keys=tuple(sorted(payload.keys())),
                    )
                    raise
                self.logger.info(
                    "trade_event_emitted",
                    trade_id=event.trade_id,
                    instrument=event.instrument,
                    event_type="OPEN",
                    close_reason=None,
                    gslo_present=event.gslo is not None,
                )
                stored_trade = self.journal_service.handle_trade_opened(event)
                self._dispatch_trade_opened(stored_trade, cycle_errors=cycle_errors)
                emitted.append(event)
                continue

            existing = existing_open[trade_id]
            new_sl = self._optional_float(payload.get("stop_loss_price"))
            new_tp = self._optional_float(payload.get("take_profit_price"))
            if existing.sl_price != new_sl or existing.tp_price != new_tp:
                try:
                    event = TradeModifiedEvent(
                        trade_id=trade_id,
                        new_sl=new_sl,
                        new_tp=new_tp,
                        modified_at=now,
                    )
                except Exception as exc:
                    log_failure(
                        self.logger,
                        "trade_modify_event_build_failed",
                        exc,
                        trade_id=trade_id,
                        payload_keys=tuple(sorted(payload.keys())),
                    )
                    raise
                self.logger.info(
                    "trade_event_emitted",
                    trade_id=existing.trade_id,
                    instrument=existing.instrument,
                    event_type="MODIFY",
                    close_reason=None,
                    gslo_present=existing.gslo_price is not None,
                )
                self.journal_service.handle_trade_modified(event)
                emitted.append(event)

        for trade_id, existing in existing_open.items():
            if trade_id in current_by_id:
                continue
            detail = self._safe_trade_detail(trade_id)
            if detail is None:
                cycle_errors.append(f"trade_detail_unavailable:{trade_id}")
                self.logger.warning(
                    "trade_close_deferred",
                    trade_id=trade_id,
                    instrument=existing.instrument,
                    reason="trade_detail_unavailable",
                )
                continue
            close_price = self._resolve_close_price(detail, existing.open_price)
            transactions = self._safe_trade_transactions(trade_id)
            close_reason = self._infer_close_reason(
                existing.sl_price,
                existing.tp_price,
                close_price,
                detail.get("closeReason") if detail else None,
                transactions,
            )
            try:
                event = TradeClosedEvent(
                    trade_id=trade_id,
                    instrument=existing.instrument,
                    units=existing.units,
                    open_price=existing.open_price,
                    close_price=close_price,
                    realized_pnl=self._optional_float(
                        None if detail is None else detail.get("realizedPL")
                    ),
                    close_reason=close_reason,
                    closed_at=self._parse_datetime(
                        None if detail is None else detail.get("closeTime")
                    )
                    or now,
                )
            except Exception as exc:
                log_failure(
                    self.logger,
                    "trade_close_event_build_failed",
                    exc,
                    trade_id=trade_id,
                    detail_keys=()
                    if detail is None
                    else tuple(sorted(detail.keys())),
                )
                raise
            self.logger.info(
                "trade_event_emitted",
                trade_id=event.trade_id,
                instrument=event.instrument,
                event_type="CLOSE",
                close_reason=event.close_reason,
                gslo_present=existing.gslo_price is not None,
            )
            stored_trade = self.journal_service.handle_trade_closed(event)
            if stored_trade is not None:
                self._dispatch_trade_closed(stored_trade, cycle_errors=cycle_errors)
            emitted.append(event)

        self._last_heartbeat_at = datetime.now(timezone.utc)
        if cycle_errors:
            self._last_error_at = self._last_heartbeat_at
            self._last_error = "; ".join(cycle_errors)
        else:
            self._last_error = None
            self._last_error_at = None
        return emitted

    def status(self) -> BackgroundTaskStatus:
        state = "DEGRADED" if self._last_error is not None else "RUNNING"
        if self._started_at is None:
            state = "STOPPED"
        return BackgroundTaskStatus(
            name="trade_poller",
            state=state,
            restart_count=0,
            started_at=self._started_at,
            last_heartbeat_at=self._last_heartbeat_at,
            last_error_at=self._last_error_at,
            last_error=self._last_error,
        )

    def _safe_trade_detail(self, trade_id: str) -> dict[str, object] | None:
        try:
            detail = self.account_client.get_trade_detail_sync(trade_id)
        except Exception as exc:
            log_failure(
                self.logger,
                "trade_detail_unavailable",
                exc,
                level="warning",
                trade_id=trade_id,
            )
            return None
        return detail

    def _safe_trade_transactions(self, trade_id: str) -> list[dict[str, object]]:
        if not hasattr(self.account_client, "get_trade_transactions_sync"):
            return []
        try:
            transactions = self.account_client.get_trade_transactions_sync(trade_id)
        except Exception as exc:
            log_failure(
                self.logger,
                "trade_transactions_unavailable",
                exc,
                level="warning",
                trade_id=trade_id,
            )
            return []
        return [dict(transaction) for transaction in transactions]

    @staticmethod
    def _resolve_close_price(detail: dict[str, object] | None, fallback: float) -> float:
        if detail is None:
            return fallback
        for key in ("averageClosePrice", "closePrice", "price"):
            value = detail.get(key)
            if value is None:
                continue
            try:
                return float(value)
            except (TypeError, ValueError):
                continue
        return fallback

    @staticmethod
    def _infer_close_reason(
        sl_price: float | None,
        tp_price: float | None,
        close_price: float,
        raw_reason: object,
        transactions: list[dict[str, object]] | None = None,
    ) -> CloseReason:
        reason_text = str(raw_reason or "").strip().upper()
        for transaction in transactions or ():
            reason_text = " ".join(
                value
                for value in (
                    reason_text,
                    str(transaction.get("reason", "")).strip().upper(),
                    str(transaction.get("type", "")).strip().upper(),
                )
                if value
            )
        if "STOP" in reason_text:
            return CloseReason.SL_HIT
        if "TAKE" in reason_text or "TP" in reason_text:
            return CloseReason.TP_HIT

        tolerance = abs(close_price) * 1e-6
        if sl_price is not None and abs(close_price - sl_price) <= max(tolerance, 1e-6):
            return CloseReason.SL_HIT
        if tp_price is not None and abs(close_price - tp_price) <= max(tolerance, 1e-6):
            return CloseReason.TP_HIT
        return CloseReason.MANUAL

    @staticmethod
    def _parse_datetime(value: object) -> datetime | None:
        if value is None:
            return None
        if isinstance(value, datetime):
            if value.tzinfo is None:
                return value.replace(tzinfo=timezone.utc)
            return value.astimezone(timezone.utc)
        text = str(value).replace("Z", "+00:00")
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    @staticmethod
    def _optional_float(value: object) -> float | None:
        if value is None:
            return None
        return float(value)

    def _trade_push_enabled(self) -> bool:
        if self.runtime_config_manager is None:
            return True
        return bool(self.runtime_config_manager.trade_push_enabled())

    def _dispatch_trade_opened(self, trade, *, cycle_errors: list[str] | None = None) -> None:
        if not self._trade_push_enabled() or self.notifier is None or self.message_builder is None:
            return
        self._dispatch_notification(
            text=self.message_builder.build_trade_opened(trade),
            chat_id=self.settings.telegram_chat_id,
            event="trade_open_notification_failed",
            cycle_errors=cycle_errors,
            trade_id=trade.trade_id,
            instrument=trade.instrument,
        )

    def _dispatch_trade_closed(self, trade, *, cycle_errors: list[str] | None = None) -> None:
        if not self._trade_push_enabled() or self.notifier is None or self.message_builder is None:
            return
        self._dispatch_notification(
            text=self.message_builder.build_trade_closed(trade),
            chat_id=self.settings.telegram_chat_id,
            event="trade_close_notification_failed",
            cycle_errors=cycle_errors,
            trade_id=trade.trade_id,
            instrument=trade.instrument,
        )

    def _dispatch_notification(
        self,
        *,
        text: str,
        chat_id: int,
        event: str,
        cycle_errors: list[str] | None = None,
        **fields: object,
    ) -> None:
        error = deliver_message_blocking(
            self.notifier,
            chat_id=chat_id,
            text=text,
            logger=self.logger,
            failure_event=event,
            **fields,
        )
        if error is None:
            return
        self._last_error = str(error)
        self._last_error_at = datetime.now(timezone.utc)
        if cycle_errors is not None:
            cycle_errors.append(f"{event}:{error}")


__all__ = ["TradePollerTask"]
