"""Live price-tick consumer for MAE/MFE sample persistence."""

from __future__ import annotations

from datetime import datetime

from config.settings import Settings, get_settings
from core.events import PriceTick
from core.instrument_registry import get_pip_size
from core.models import ExcursionSample, TradeRecord
from journal.excursion_repository import ExcursionRepository
from journal.trade_repository import TradeRepository


class ExcursionTracker:
    """Persist bounded excursion samples for open trades."""

    def __init__(
        self,
        trade_repository: TradeRepository,
        excursion_repository: ExcursionRepository,
        *,
        settings: Settings | None = None,
    ) -> None:
        self.trade_repository = trade_repository
        self.excursion_repository = excursion_repository
        self.settings = settings or get_settings()
        self._last_written: dict[str, ExcursionSample] = {}

    def process_tick(self, tick: PriceTick) -> list[ExcursionSample]:
        """Persist excursion samples for open trades affected by a tick."""

        written: list[ExcursionSample] = []
        for trade in self.trade_repository.list_open():
            if trade.instrument != tick.instrument:
                continue

            sample = self._build_sample(trade, tick)
            if not self._should_write(sample):
                continue

            stored = self.excursion_repository.insert(sample)
            self._last_written[trade.trade_id] = stored
            written.append(stored)
        return written

    def _build_sample(self, trade: TradeRecord, tick: PriceTick) -> ExcursionSample:
        pip_size = get_pip_size(trade.instrument)
        if trade.units > 0:
            adverse_pips = max(0.0, (trade.open_price - tick.bid) / pip_size)
            favorable_pips = max(0.0, (tick.bid - trade.open_price) / pip_size)
        else:
            adverse_pips = max(0.0, (tick.ask - trade.open_price) / pip_size)
            favorable_pips = max(0.0, (trade.open_price - tick.ask) / pip_size)

        return ExcursionSample(
            trade_id=trade.trade_id,
            sampled_at=tick.time,
            bid=tick.bid,
            ask=tick.ask,
            adverse_pips=adverse_pips,
            favorable_pips=favorable_pips,
        )

    def _should_write(self, sample: ExcursionSample) -> bool:
        last = self._last_written.get(sample.trade_id)
        if last is None:
            return True

        min_move = self.settings.mae_mfe_min_pip_move
        adverse_delta = abs(sample.adverse_pips - last.adverse_pips)
        favorable_delta = abs(sample.favorable_pips - last.favorable_pips)
        return adverse_delta >= min_move or favorable_delta >= min_move


__all__ = ["ExcursionTracker"]
