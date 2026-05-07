"""Aggregated realized trade statistics for MCP review workflows."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Iterable

from config.settings import Settings, get_settings
from core.instrument_registry import get_pip_size
from core.models import InstrumentTradeStats, TradeRecord, TradeStatsReport, TradeStatsSummary
from journal.excursion_repository import ExcursionRepository
from journal.trade_history_service import TradeHistoryService
from journal.trade_repository import TradeRepository


@dataclass
class TradeStatsService:
    """Build realized trade statistics from persisted history and projected trades."""

    trade_history_service: TradeHistoryService
    trade_repository: TradeRepository
    excursion_repository: ExcursionRepository
    settings: Settings

    def __init__(
        self,
        *,
        trade_history_service: TradeHistoryService,
        trade_repository: TradeRepository,
        excursion_repository: ExcursionRepository,
        settings: Settings | None = None,
    ) -> None:
        self.trade_history_service = trade_history_service
        self.trade_repository = trade_repository
        self.excursion_repository = excursion_repository
        self.settings = settings or get_settings()

    def get_trade_stats(
        self,
        period: str = "day",
        *,
        start_date: date | str | None = None,
        end_date: date | str | None = None,
        instrument: str | None = None,
    ) -> TradeStatsReport:
        """Return aggregate and per-instrument realized trade statistics."""

        resolved_period = self.trade_history_service.resolve_period_selector(
            period,
            start_date=start_date,
            end_date=end_date,
        )
        self.trade_history_service._try_sync_for_read()
        pnl_summary = self.trade_history_service.compute_realized_pnl(
            resolved_period,
            instrument=instrument,
        )
        trades = self._closed_trades_for_window(
            start_utc=pnl_summary.start_utc,
            end_utc=pnl_summary.end_utc,
            instrument=instrument,
        )

        summary = self._build_summary(
            period=resolved_period,
            instrument=instrument,
            pnl_summary=pnl_summary,
            trades=trades,
        )
        grouped: dict[str, list[TradeRecord]] = defaultdict(list)
        for trade in trades:
            grouped[trade.instrument].append(trade)
        per_instrument = tuple(
            self._build_instrument_summary(inst, grouped[inst])
            for inst in sorted(grouped)
        )
        return TradeStatsReport(summary=summary, per_instrument=per_instrument)

    def _closed_trades_for_window(
        self,
        *,
        start_utc,
        end_utc,
        instrument: str | None,
    ) -> list[TradeRecord]:
        trades = self.trade_repository.list_closed()
        filtered = [
            trade
            for trade in trades
            if trade.closed_at is not None
            and trade.closed_at >= start_utc
            and trade.closed_at < end_utc
            and (instrument is None or trade.instrument == instrument)
        ]
        filtered.sort(key=lambda trade: trade.closed_at)
        return filtered

    def _build_summary(
        self,
        *,
        period: str,
        instrument: str | None,
        pnl_summary,
        trades: list[TradeRecord],
    ) -> TradeStatsSummary:
        net_values = [self._net_pnl(trade) for trade in trades]
        wins = [value for value in net_values if value > 0]
        losses = [value for value in net_values if value < 0]
        breakevens = [value for value in net_values if value == 0]
        rr_values = self._realized_r_values(trades)
        mae_values = self._mae_values(trades)
        return TradeStatsSummary(
            period=period,
            instrument=instrument,
            start_utc=pnl_summary.start_utc,
            end_utc=pnl_summary.end_utc,
            start_local=pnl_summary.start_local,
            end_local=pnl_summary.end_local,
            trade_count=len(trades),
            win_count=len(wins),
            loss_count=len(losses),
            breakeven_count=len(breakevens),
            win_rate=None if not trades else len(wins) / len(trades),
            gross_realized_pl=pnl_summary.gross_realized_pl,
            financing=pnl_summary.financing,
            commission=pnl_summary.commission,
            net_realized_pl=pnl_summary.net_realized_pl,
            profit_factor=None if not losses else float(sum(wins) / abs(sum(losses))),
            expectancy=None if not trades else (sum(net_values, Decimal("0")) / Decimal(len(trades))),
            average_win=self._average_decimal(wins),
            average_loss=self._average_decimal(losses),
            largest_win=max(wins) if wins else None,
            largest_loss=min(losses) if losses else None,
            avg_realized_r=None if not rr_values else (sum(rr_values) / len(rr_values)),
            rr_eligible_count=len(rr_values),
            mae_sampled_trade_count=len(mae_values),
            avg_mae_pips=None if not mae_values else (sum(mae_values) / len(mae_values)),
            max_drawdown=self._max_drawdown(trades),
        )

    def _build_instrument_summary(
        self,
        instrument: str,
        trades: list[TradeRecord],
    ) -> InstrumentTradeStats:
        net_values = [self._net_pnl(trade) for trade in trades]
        wins = [value for value in net_values if value > 0]
        losses = [value for value in net_values if value < 0]
        breakevens = [value for value in net_values if value == 0]
        rr_values = self._realized_r_values(trades)
        mae_values = self._mae_values(trades)
        return InstrumentTradeStats(
            instrument=instrument,
            trade_count=len(trades),
            win_count=len(wins),
            loss_count=len(losses),
            breakeven_count=len(breakevens),
            win_rate=None if not trades else len(wins) / len(trades),
            gross_realized_pl=sum(
                (Decimal(str(trade.instrument_pnl)) for trade in trades if trade.instrument_pnl is not None),
                Decimal("0"),
            ),
            net_realized_pl=sum(net_values, Decimal("0")),
            avg_mae_pips=None if not mae_values else (sum(mae_values) / len(mae_values)),
            mae_sampled_trade_count=len(mae_values),
            avg_realized_r=None if not rr_values else (sum(rr_values) / len(rr_values)),
            rr_eligible_count=len(rr_values),
        )

    @staticmethod
    def _average_decimal(values: list[Decimal]) -> Decimal | None:
        if not values:
            return None
        return sum(values, Decimal("0")) / Decimal(len(values))

    @staticmethod
    def _net_pnl(trade: TradeRecord) -> Decimal:
        if trade.account_pnl is None:
            return Decimal("0")
        return Decimal(str(trade.account_pnl))

    def _mae_values(self, trades: Iterable[TradeRecord]) -> list[float]:
        values: list[float] = []
        for trade in trades:
            summary = self.excursion_repository.get_mae_mfe(trade.trade_id)
            if summary is None:
                continue
            mae_pips = summary.get("mae_pips")
            if mae_pips is None:
                continue
            values.append(float(mae_pips))
        return values

    def _realized_r_values(self, trades: Iterable[TradeRecord]) -> list[float]:
        values: list[float] = []
        for trade in trades:
            if trade.sl_price is None or trade.pips is None:
                continue
            pip_size = get_pip_size(trade.instrument)
            risk_pips = abs((trade.open_price - trade.sl_price) / pip_size)
            if risk_pips <= 0:
                continue
            values.append(float(trade.pips) / risk_pips)
        return values

    def _max_drawdown(self, trades: list[TradeRecord]) -> Decimal | None:
        if not trades:
            return None
        cumulative = Decimal("0")
        peak = Decimal("0")
        max_drawdown = Decimal("0")
        for trade in trades:
            cumulative += self._net_pnl(trade)
            if cumulative > peak:
                peak = cumulative
            drawdown = peak - cumulative
            if drawdown > max_drawdown:
                max_drawdown = drawdown
        return max_drawdown


__all__ = ["TradeStatsService"]
