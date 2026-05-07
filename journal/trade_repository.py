"""Trade journal repository wrappers for the Stage 11 runtime."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from config.settings import Settings
from core.enums import CloseReason
from core.models import TradeRecord
from data.persistence.trade_store import TradeStore


class TradeRepository:
    """Thin typed repository over the shared TinyDB trade store."""

    def __init__(
        self,
        *,
        store: TradeStore | None = None,
        db_path: Path | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.store = store or TradeStore(db_path=db_path, settings=settings)

    def upsert(self, trade: TradeRecord) -> TradeRecord:
        return self.store.upsert_trade(trade)

    def get(self, trade_id: str) -> TradeRecord | None:
        return self.store.get_trade(trade_id)

    def list_open(self) -> list[TradeRecord]:
        return self.store.list_open_trades()

    def list_closed(self) -> list[TradeRecord]:
        return self.store.list_closed_trades()

    def close_trade(
        self,
        trade_id: str,
        *,
        close_price: float,
        close_reason: CloseReason,
        pips: float,
        instrument_pnl: float,
        instrument_pnl_currency: str,
        account_pnl: float,
        account_currency: str,
        closed_at: datetime,
    ) -> TradeRecord | None:
        return self.store.close_trade(
            trade_id,
            close_price=close_price,
            close_reason=close_reason,
            pips=pips,
            instrument_pnl=instrument_pnl,
            instrument_pnl_currency=instrument_pnl_currency,
            account_pnl=account_pnl,
            account_currency=account_currency,
            closed_at=closed_at,
        )

    def set_notes(self, trade_id: str, notes: str | None) -> TradeRecord | None:
        return self.store.set_trade_notes(trade_id, notes)


__all__ = ["TradeRepository"]
