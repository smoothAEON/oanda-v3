"""Excursion-sample repository wrappers for the Stage 11 runtime."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from config.settings import Settings
from core.models import ExcursionSample
from data.persistence.trade_store import TradeStore


class ExcursionRepository:
    """Typed wrapper over excursion persistence and aggregation helpers."""

    def __init__(
        self,
        *,
        store: TradeStore | None = None,
        db_path: Path | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.store = store or TradeStore(db_path=db_path, settings=settings)

    def insert(self, sample: ExcursionSample | dict[str, Any]) -> ExcursionSample:
        return self.store.insert_excursion_sample(sample)

    def list_for_trade(self, trade_id: str) -> list[ExcursionSample]:
        return self.store.list_excursion_samples(trade_id)

    def get_mae_mfe(self, trade_id: str) -> dict[str, Any] | None:
        return self.store.get_trade_mae_mfe(trade_id)


__all__ = ["ExcursionRepository"]
