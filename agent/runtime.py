"""On-demand local runtime for the MCP stdio server."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import RLock
from typing import Any, Callable, TypeVar

from background.poller_task import TradePollerTask
from charting.renderer import ChartRenderer
from config.settings import Settings, get_settings
from core.market_state import MarketStateStore
from data.persistence.trade_store import TradeStore
from journal.excursion_repository import ExcursionRepository
from journal.journal_service import JournalService
from journal.trade_history_service import TradeHistoryService
from journal.trade_repository import TradeRepository
from orchestration.scan_orchestrator import ScanOrchestrator
from providers.account_client import OandaAccountClient
from providers.cache import CandleCache
from providers.oanda import OandaMarketDataProvider
from providers.oanda_history import OandaHistoryClient

T = TypeVar("T")


@dataclass
class AgentRuntime:
    """Concrete dependency graph for local, on-demand MCP calls."""

    settings: Settings
    started_at: datetime
    trade_store: TradeStore
    market_state: MarketStateStore
    market_data_provider: OandaMarketDataProvider
    account_client: OandaAccountClient
    trade_repository: TradeRepository
    excursion_repository: ExcursionRepository
    journal_service: JournalService
    trade_history_service: TradeHistoryService
    trade_poller: TradePollerTask
    scan_orchestrator: ScanOrchestrator
    chart_renderer: ChartRenderer
    _state_lock: RLock = field(default_factory=RLock, repr=False)

    async def run_blocking(
        self,
        fn: Callable[..., T],
        *args: Any,
        write: bool = False,
        **kwargs: Any,
    ) -> T:
        """Run blocking work off the event loop, optionally under the runtime write lock."""

        def call() -> T:
            if write:
                with self._state_lock:
                    return fn(*args, **kwargs)
            return fn(*args, **kwargs)

        return await asyncio.to_thread(call)

    async def sync_open_trades(self) -> tuple[object, ...]:
        """Refresh local open-trade journal state from OANDA once."""

        return await self.run_blocking(self.trade_poller.run_once, write=True)

    async def sync_trade_history(self) -> object:
        """Refresh transaction-backed trade history once."""

        return await self.run_blocking(self.trade_history_service.incremental_sync, write=True)

    async def sync_account_state(self) -> None:
        """Refresh local account-derived stores needed by journal/history views."""

        await self.sync_open_trades()
        await self.sync_trade_history()

    def close(self) -> None:
        self.trade_store.close()


def build_runtime(*, settings: Settings | None = None) -> AgentRuntime:
    """Build the local MCP runtime graph without background services."""

    resolved_settings = settings or get_settings()
    trade_store = TradeStore(settings=resolved_settings)
    candle_cache = CandleCache(trade_store=trade_store)
    market_state = MarketStateStore()
    market_data_provider = OandaMarketDataProvider(
        settings=resolved_settings,
        cache=candle_cache,
    )
    scan_orchestrator = ScanOrchestrator(
        settings=resolved_settings,
        market_data_provider=market_data_provider,
        market_state=market_state,
    )
    account_client = OandaAccountClient(settings=resolved_settings)
    history_client = OandaHistoryClient(settings=resolved_settings)
    trade_repository = TradeRepository(store=trade_store)
    excursion_repository = ExcursionRepository(store=trade_store)
    journal_service = JournalService(trade_repository, settings=resolved_settings)
    trade_history_service = TradeHistoryService(
        store=trade_store,
        trade_repository=trade_repository,
        history_client=history_client,
        settings=resolved_settings,
    )
    trade_poller = TradePollerTask(
        account_client,
        trade_repository,
        journal_service,
        settings=resolved_settings,
    )
    chart_renderer = ChartRenderer(
        settings=resolved_settings,
        market_state=market_state,
        market_data_provider=market_data_provider,
        scan_orchestrator=scan_orchestrator,
        trade_repository=trade_repository,
        account_client=account_client,
    )
    return AgentRuntime(
        settings=resolved_settings,
        started_at=datetime.now(timezone.utc),
        trade_store=trade_store,
        market_state=market_state,
        market_data_provider=market_data_provider,
        account_client=account_client,
        trade_repository=trade_repository,
        excursion_repository=excursion_repository,
        journal_service=journal_service,
        trade_history_service=trade_history_service,
        trade_poller=trade_poller,
        scan_orchestrator=scan_orchestrator,
        chart_renderer=chart_renderer,
    )


__all__ = ["AgentRuntime", "build_runtime"]
