"""Shared Stage 13 runtime assembly for the Telegram bot."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from alerts.alert_repository import AlertRepository
from alerts.indicator_alert_engine import IndicatorAlertEngine
from alerts.price_alert_engine import PriceAlertEngine
from alerts.time_alert_engine import TimeAlertEngine
from background.poller_task import TradePollerTask
from background.stream_task import PriceStreamTask
from background.task_supervisor import TaskSupervisor
from bot.runtime_config import RuntimeConfigManager
from bot.security_manager import SecurityManager
from charting.renderer import ChartRenderer
from config.settings import Settings, get_settings
from core.logging_setup import get_logger, log_failure
from core.market_state import MarketStateStore
from data.persistence.trade_store import TradeStore
from journal.excursion_repository import ExcursionRepository
from journal.journal_service import JournalService
from journal.trade_history_service import TradeHistoryService
from journal.trade_repository import TradeRepository
from notifications.default_message_builder import DefaultNotificationMessageBuilder
from notifications.telegram_notifier import TelegramNotifier
from orchestration.cache_warmer import CacheWarmer
from orchestration.scan_orchestrator import ScanOrchestrator
from orchestration.scheduler import SchedulerService, TRADE_POLLER_JOB_ID
from providers.account_client import OandaAccountClient
from providers.cache import CandleCache
from providers.oanda import OandaMarketDataProvider
from providers.oanda_history import OandaHistoryClient
from providers.stream_client import OandaStreamClient
from tracking.excursion_tracker import ExcursionTracker

if TYPE_CHECKING:
    from telegram import Bot

BOT_RUNTIME_KEY = "bot_runtime"
SETTINGS_KEY = "settings"
SECURITY_MANAGER_KEY = "security_manager"
RUNTIME_CONFIG_MANAGER_KEY = "runtime_config_manager"
TRADE_STORE_KEY = "trade_store"
TRADE_REPOSITORY_KEY = "trade_repository"
EXCURSION_REPOSITORY_KEY = "excursion_repository"
ALERT_REPOSITORY_KEY = "alert_repository"
ACCOUNT_CLIENT_KEY = "account_client"
TRADE_HISTORY_SERVICE_KEY = "trade_history_service"
MARKET_DATA_PROVIDER_KEY = "market_data_provider"
MARKET_STATE_KEY = "market_state"
SCAN_ORCHESTRATOR_KEY = "scan_orchestrator"
SCHEDULER_KEY = "scheduler"
TASK_SUPERVISOR_KEY = "task_supervisor"
CHART_RENDERER_KEY = "chart_renderer"
STARTED_AT_KEY = "started_at"
LOGGER = get_logger(__name__)


@dataclass
class BotRuntime:
    """Concrete Stage 13 runtime dependency graph."""

    settings: Settings
    started_at: datetime
    trade_store: TradeStore
    security_manager: SecurityManager
    runtime_config_manager: RuntimeConfigManager
    market_state: MarketStateStore
    market_data_provider: OandaMarketDataProvider
    account_client: OandaAccountClient
    stream_client: OandaStreamClient
    trade_repository: TradeRepository
    excursion_repository: ExcursionRepository
    alert_repository: AlertRepository
    journal_service: JournalService
    trade_history_service: TradeHistoryService
    excursion_tracker: ExcursionTracker
    price_alert_engine: PriceAlertEngine
    indicator_alert_engine: IndicatorAlertEngine
    time_alert_engine: TimeAlertEngine
    trade_poller: TradePollerTask
    stream_task: PriceStreamTask
    task_supervisor: TaskSupervisor
    scan_orchestrator: ScanOrchestrator
    scheduler: SchedulerService
    chart_renderer: ChartRenderer

    def configure_notifications(self, bot: "Bot") -> None:
        """Bind the live Telegram notifier into alert engines at startup."""

        notifier = TelegramNotifier(bot)
        message_builder = DefaultNotificationMessageBuilder()
        self.price_alert_engine.notifier = notifier
        self.price_alert_engine.message_builder = message_builder
        self.indicator_alert_engine.notifier = notifier
        self.indicator_alert_engine.message_builder = message_builder
        self.trade_poller.notifier = notifier
        self.trade_poller.message_builder = message_builder
        self.time_alert_engine.notifier = notifier

    async def start(self) -> None:
        """Start supervised background work and the scheduler."""

        try:
            try:
                await asyncio.to_thread(self.trade_history_service.incremental_sync)
            except Exception as exc:
                log_failure(LOGGER, "trade_history_startup_sync_failed", exc, level="warning")
            refresh_price_alert_instruments = getattr(self.stream_task, "refresh_price_alert_instruments", None)
            if callable(refresh_price_alert_instruments):
                await asyncio.to_thread(refresh_price_alert_instruments)
            update_open_trade_instruments = getattr(self.stream_task, "update_open_trade_instruments", None)
            if callable(update_open_trade_instruments):
                await asyncio.to_thread(
                    update_open_trade_instruments,
                    {trade.instrument for trade in self.trade_repository.list_open()},
                )
            await self.task_supervisor.start_all()
            self.scheduler.start()
            self._ensure_required_scheduler_jobs()
        except Exception as exc:
            log_failure(LOGGER, "bot_runtime_start_failed", exc)
            raise
        LOGGER.info("bot_runtime_started")

    async def stop(self) -> None:
        """Stop scheduler-owned and supervised runtime work."""

        try:
            self.scheduler.shutdown(wait=False)
            await self.task_supervisor.stop_all()
            self.trade_store.close()
        except Exception as exc:
            log_failure(LOGGER, "bot_runtime_stop_failed", exc)
            raise
        LOGGER.info("bot_runtime_stopped")

    def _ensure_required_scheduler_jobs(self) -> None:
        job_ids = {job.job_id for job in self.scheduler.status().jobs}
        if TRADE_POLLER_JOB_ID not in job_ids:
            raise RuntimeError("Trade poller job is not registered.")

    def bot_data(self) -> dict[str, object]:
        """Return the shared bot-data dependency map."""

        return {
            BOT_RUNTIME_KEY: self,
            SETTINGS_KEY: self.settings,
            SECURITY_MANAGER_KEY: self.security_manager,
            RUNTIME_CONFIG_MANAGER_KEY: self.runtime_config_manager,
            TRADE_STORE_KEY: self.trade_store,
            TRADE_REPOSITORY_KEY: self.trade_repository,
            EXCURSION_REPOSITORY_KEY: self.excursion_repository,
            ALERT_REPOSITORY_KEY: self.alert_repository,
            ACCOUNT_CLIENT_KEY: self.account_client,
            TRADE_HISTORY_SERVICE_KEY: self.trade_history_service,
            MARKET_DATA_PROVIDER_KEY: self.market_data_provider,
            MARKET_STATE_KEY: self.market_state,
            SCAN_ORCHESTRATOR_KEY: self.scan_orchestrator,
            SCHEDULER_KEY: self.scheduler,
            TASK_SUPERVISOR_KEY: self.task_supervisor,
            CHART_RENDERER_KEY: self.chart_renderer,
            STARTED_AT_KEY: self.started_at,
        }


def build_runtime(*, settings: Settings | None = None) -> BotRuntime:
    """Build the full Stage 13 runtime graph."""

    resolved_settings = settings or get_settings()
    trade_store = TradeStore(settings=resolved_settings)
    candle_cache = CandleCache(trade_store=trade_store)
    security_manager = SecurityManager(store=trade_store, settings=resolved_settings)
    runtime_config_manager = RuntimeConfigManager(
        store=trade_store,
        settings=resolved_settings,
    )
    market_state = MarketStateStore()
    market_data_provider = OandaMarketDataProvider(
        settings=resolved_settings,
        cache=candle_cache,
    )

    alert_repository = AlertRepository(store=trade_store)
    price_alert_engine = PriceAlertEngine(alert_repository)
    indicator_alert_engine = IndicatorAlertEngine(
        alert_repository,
        market_data_provider,
        settings=resolved_settings,
    )
    time_alert_engine = TimeAlertEngine(
        alert_repository,
        runtime_config_manager=runtime_config_manager,
    )
    scan_orchestrator = ScanOrchestrator(
        settings=resolved_settings,
        market_data_provider=market_data_provider,
        market_state=market_state,
        indicator_alert_engine=indicator_alert_engine,
    )
    cache_warmer = CacheWarmer(
        market_data_provider=market_data_provider,
        settings=resolved_settings,
        market_hours_service=scan_orchestrator.market_hours_service,
    )
    account_client = OandaAccountClient(settings=resolved_settings)
    history_client = OandaHistoryClient(settings=resolved_settings)
    stream_client = OandaStreamClient(settings=resolved_settings)
    trade_repository = TradeRepository(store=trade_store)
    excursion_repository = ExcursionRepository(store=trade_store)
    journal_service = JournalService(trade_repository, settings=resolved_settings)
    trade_history_service = TradeHistoryService(
        store=trade_store,
        trade_repository=trade_repository,
        history_client=history_client,
        settings=resolved_settings,
    )
    excursion_tracker = ExcursionTracker(
        trade_repository,
        excursion_repository,
        settings=resolved_settings,
    )
    stream_task = PriceStreamTask(
        stream_client,
        excursion_tracker,
        price_alert_engine,
        settings=resolved_settings,
    )
    trade_poller = TradePollerTask(
        account_client,
        trade_repository,
        journal_service,
        settings=resolved_settings,
        runtime_config_manager=runtime_config_manager,
    )
    if hasattr(trade_poller, "open_trade_instruments_handler"):
        trade_poller.open_trade_instruments_handler = (
            stream_task.update_open_trade_instruments
            if hasattr(stream_task, "update_open_trade_instruments")
            else None
        )
    task_supervisor = TaskSupervisor(
        stream_task=stream_task,
    )
    scheduler = SchedulerService(
        settings=resolved_settings,
        scan_orchestrator=scan_orchestrator,
        cache_warmer=cache_warmer,
        trade_poller=trade_poller,
        trade_history_service=trade_history_service,
        time_alert_engine=time_alert_engine,
    )
    chart_renderer = ChartRenderer(
        settings=resolved_settings,
        market_state=market_state,
        market_data_provider=market_data_provider,
        scan_orchestrator=scan_orchestrator,
        trade_repository=trade_repository,
        alert_repository=alert_repository,
        account_client=account_client,
    )
    return BotRuntime(
        settings=resolved_settings,
        started_at=datetime.now(timezone.utc),
        trade_store=trade_store,
        security_manager=security_manager,
        runtime_config_manager=runtime_config_manager,
        market_state=market_state,
        market_data_provider=market_data_provider,
        account_client=account_client,
        stream_client=stream_client,
        trade_repository=trade_repository,
        excursion_repository=excursion_repository,
        alert_repository=alert_repository,
        journal_service=journal_service,
        trade_history_service=trade_history_service,
        excursion_tracker=excursion_tracker,
        price_alert_engine=price_alert_engine,
        indicator_alert_engine=indicator_alert_engine,
        time_alert_engine=time_alert_engine,
        trade_poller=trade_poller,
        stream_task=stream_task,
        task_supervisor=task_supervisor,
        scan_orchestrator=scan_orchestrator,
        scheduler=scheduler,
        chart_renderer=chart_renderer,
    )


__all__ = [
    "ACCOUNT_CLIENT_KEY",
    "ALERT_REPOSITORY_KEY",
    "BOT_RUNTIME_KEY",
    "BotRuntime",
    "CHART_RENDERER_KEY",
    "EXCURSION_REPOSITORY_KEY",
    "MARKET_DATA_PROVIDER_KEY",
    "MARKET_STATE_KEY",
    "RUNTIME_CONFIG_MANAGER_KEY",
    "SCAN_ORCHESTRATOR_KEY",
    "SCHEDULER_KEY",
    "SECURITY_MANAGER_KEY",
    "SETTINGS_KEY",
    "STARTED_AT_KEY",
    "TASK_SUPERVISOR_KEY",
    "TRADE_HISTORY_SERVICE_KEY",
    "TRADE_REPOSITORY_KEY",
    "TRADE_STORE_KEY",
    "build_runtime",
]
