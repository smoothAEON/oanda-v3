from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from core.enums import CloseReason, TradeState
from core.models import RealizedPnLSummary, TradeRecord
from journal.trade_stats_service import TradeStatsService


BASE_TIME = datetime(2026, 4, 1, 0, 0, tzinfo=timezone.utc)


def build_trade(
    *,
    trade_id: str,
    instrument: str,
    account_pnl: float,
    instrument_pnl: float,
    pips: float | None,
    sl_price: float | None,
    opened_at: datetime,
    closed_at: datetime,
) -> TradeRecord:
    return TradeRecord(
        trade_id=trade_id,
        instrument=instrument,
        units=1.0,
        open_price=3000.0 if instrument == "SPX500_USD" else 1.1000,
        close_price=3010.0 if instrument == "SPX500_USD" else 1.1020,
        sl_price=sl_price,
        tp_price=None,
        gslo_price=None,
        state=TradeState.CLOSED,
        close_reason=CloseReason.MANUAL,
        pips=pips,
        instrument_pnl=instrument_pnl,
        instrument_pnl_currency="USD",
        account_pnl=account_pnl,
        account_currency="USD",
        opened_at=opened_at,
        closed_at=closed_at,
        notes=None,
    )


class StubTradeHistoryService:
    def __init__(self, summary: RealizedPnLSummary) -> None:
        self.summary = summary
        self.resolve_calls: list[tuple[str, object, object]] = []
        self.pnl_calls: list[tuple[str, str | None]] = []
        self.sync_calls = 0

    def resolve_period_selector(self, period: str, *, start_date=None, end_date=None) -> str:
        self.resolve_calls.append((period, start_date, end_date))
        if start_date is not None and end_date is not None:
            return f"custom:{start_date}:{end_date}"
        return period

    def _try_sync_for_read(self) -> None:
        self.sync_calls += 1

    def compute_realized_pnl(self, period: str, instrument: str | None = None):
        self.pnl_calls.append((period, instrument))
        return self.summary.model_copy(update={"period": period, "instrument": instrument})


class StubTradeRepository:
    def __init__(self, trades: list[TradeRecord]) -> None:
        self._trades = trades

    def list_closed(self) -> list[TradeRecord]:
        return list(self._trades)


class StubExcursionRepository:
    def __init__(self, summaries: dict[str, dict[str, float] | None]) -> None:
        self.summaries = summaries

    def get_mae_mfe(self, trade_id: str):
        return self.summaries.get(trade_id)


def test_trade_stats_service_builds_summary_and_per_instrument_breakdown() -> None:
    summary = RealizedPnLSummary(
        period="custom:2026-04-01:2026-04-01",
        instrument=None,
        start_utc=BASE_TIME,
        end_utc=BASE_TIME.replace(hour=23, minute=59),
        start_local=BASE_TIME,
        end_local=BASE_TIME.replace(hour=23, minute=59),
        gross_realized_pl=Decimal("8.00"),
        financing=Decimal("-1.00"),
        commission=Decimal("2.00"),
        net_realized_pl=Decimal("5.00"),
    )
    trades = [
        build_trade(
            trade_id="spx-win",
            instrument="SPX500_USD",
            account_pnl=10.0,
            instrument_pnl=12.0,
            pips=2.0,
            sl_price=2990.0,
            opened_at=BASE_TIME,
            closed_at=BASE_TIME.replace(hour=1),
        ),
        build_trade(
            trade_id="spx-loss",
            instrument="SPX500_USD",
            account_pnl=-5.0,
            instrument_pnl=-4.0,
            pips=-0.5,
            sl_price=2995.0,
            opened_at=BASE_TIME.replace(hour=2),
            closed_at=BASE_TIME.replace(hour=3),
        ),
        build_trade(
            trade_id="eur-flat",
            instrument="EUR_USD",
            account_pnl=0.0,
            instrument_pnl=0.0,
            pips=0.0,
            sl_price=None,
            opened_at=BASE_TIME.replace(hour=4),
            closed_at=BASE_TIME.replace(hour=5),
        ),
    ]
    service = TradeStatsService(
        trade_history_service=StubTradeHistoryService(summary),
        trade_repository=StubTradeRepository(trades),
        excursion_repository=StubExcursionRepository(
            {
                "spx-win": {"mae_pips": 8.0, "mfe_pips": 25.0},
                "spx-loss": {"mae_pips": 12.0, "mfe_pips": 3.0},
                "eur-flat": None,
            }
        ),
    )

    report = service.get_trade_stats(
        "day",
        start_date="2026-04-01",
        end_date="2026-04-01",
    )

    assert report.summary.period == "custom:2026-04-01:2026-04-01"
    assert report.summary.trade_count == 3
    assert report.summary.win_count == 1
    assert report.summary.loss_count == 1
    assert report.summary.breakeven_count == 1
    assert report.summary.win_rate == pytest.approx(1 / 3)
    assert report.summary.profit_factor == pytest.approx(2.0)
    assert report.summary.expectancy == Decimal("1.666666666666666666666666667")
    assert report.summary.average_win == Decimal("10.0")
    assert report.summary.average_loss == Decimal("-5.0")
    assert report.summary.largest_win == Decimal("10.0")
    assert report.summary.largest_loss == Decimal("-5.0")
    assert report.summary.avg_realized_r == pytest.approx(0.05)
    assert report.summary.rr_eligible_count == 2
    assert report.summary.mae_sampled_trade_count == 2
    assert report.summary.avg_mae_pips == pytest.approx(10.0)
    assert report.summary.max_drawdown == Decimal("5.0")
    assert [item.instrument for item in report.per_instrument] == ["EUR_USD", "SPX500_USD"]
    spx = next(item for item in report.per_instrument if item.instrument == "SPX500_USD")
    eur = next(item for item in report.per_instrument if item.instrument == "EUR_USD")
    assert spx.trade_count == 2
    assert spx.net_realized_pl == Decimal("5.0")
    assert spx.gross_realized_pl == Decimal("8.0")
    assert spx.avg_mae_pips == pytest.approx(10.0)
    assert eur.trade_count == 1
    assert eur.avg_mae_pips is None
    assert eur.rr_eligible_count == 0
