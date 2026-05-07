"""Trade-event consumer that writes the journal contract."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from config.settings import Settings, get_settings
from core.enums import CloseReason, TradeState
from core.events import TradeClosedEvent, TradeModifiedEvent, TradeOpenedEvent
from core.instrument_registry import get_pip_size
from core.logging_setup import get_logger
from core.models import TradeRecord
from journal.trade_repository import TradeRepository


TradeRecordHook = Callable[[TradeRecord, str], None]


@dataclass
class JournalService:
    """Translate typed trade events into persisted `TradeRecord` objects."""

    trade_repository: TradeRepository
    settings: Settings
    on_trade_recorded: TradeRecordHook | None = None

    def __init__(
        self,
        trade_repository: TradeRepository,
        *,
        settings: Settings | None = None,
        on_trade_recorded: TradeRecordHook | None = None,
    ) -> None:
        self.trade_repository = trade_repository
        self.settings = settings or get_settings()
        self.on_trade_recorded = on_trade_recorded
        self.logger = get_logger(__name__)

    def handle_trade_opened(self, event: TradeOpenedEvent) -> TradeRecord:
        record = TradeRecord(
            trade_id=event.trade_id,
            instrument=event.instrument,
            units=event.units,
            open_price=event.open_price,
            close_price=None,
            sl_price=event.sl,
            tp_price=event.tp,
            gslo_price=event.gslo,
            state=TradeState.OPEN,
            close_reason=None,
            pips=None,
            instrument_pnl=None,
            instrument_pnl_currency=None,
            account_pnl=None,
            account_currency=None,
            opened_at=event.opened_at,
            closed_at=None,
            notes=self.trade_repository.get(event.trade_id).notes
            if self.trade_repository.get(event.trade_id) is not None
            else None,
        )
        stored = self.trade_repository.upsert(record)
        self._log_record(stored, source_event="TradeOpenedEvent")
        return stored

    def handle_trade_modified(self, event: TradeModifiedEvent) -> TradeRecord | None:
        existing = self.trade_repository.get(event.trade_id)
        if existing is None:
            return None

        payload = existing.model_dump(mode="python")
        payload["sl_price"] = event.new_sl
        payload["tp_price"] = event.new_tp
        updated = TradeRecord.model_validate(payload)
        stored = self.trade_repository.upsert(updated)
        self._log_record(stored, source_event="TradeModifiedEvent")
        return stored

    def handle_trade_closed(self, event: TradeClosedEvent) -> TradeRecord | None:
        existing = self.trade_repository.get(event.trade_id)
        if existing is None:
            return None

        account_currency = event.account_currency or self.settings.account_currency
        stored = self.trade_repository.close_trade(
            event.trade_id,
            close_price=event.close_price,
            close_reason=event.close_reason,
            pips=self._compute_pips(existing, event),
            instrument_pnl=event.realized_pnl or 0.0,
            instrument_pnl_currency=account_currency,
            account_pnl=event.realized_pnl or 0.0,
            account_currency=account_currency,
            closed_at=event.closed_at,
        )
        if stored is None:
            return None
        self._log_record(stored, source_event="TradeClosedEvent")
        return stored

    def _compute_pips(self, existing: TradeRecord, event: TradeClosedEvent) -> float:
        move = event.close_price - existing.open_price
        signed_move = move if existing.units > 0 else -move
        return signed_move / get_pip_size(existing.instrument)

    def _log_record(self, trade: TradeRecord, *, source_event: str) -> None:
        self.logger.info(
            "journal_written",
            trade_id=trade.trade_id,
            state=trade.state,
            notes_present=bool(trade.notes),
            source_event=source_event,
        )
        if self.on_trade_recorded is not None:
            self.on_trade_recorded(trade, source_event)


__all__ = ["JournalService", "TradeRecordHook"]
