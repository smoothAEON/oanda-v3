from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest
from telegram.error import Conflict

import bot.runtime as runtime_module
import bot.bot as bot_module
from bot.bot import build_application, main, register_handlers
from config.settings import Settings, load_settings


def write_env_file(path: Path, **overrides: str) -> Path:
    values = {
        "OANDA_API_KEY": "api-key",
        "OANDA_ACCOUNT_ID": "account-id",
        "OANDA_ENVIRONMENT": "practice",
        "TELEGRAM_BOT_TOKEN": "telegram-token",
        "TELEGRAM_CHAT_ID": "123456789",
        "TELEGRAM_BOT_PASSWORD": "bot-password",
        "TELEGRAM_ADMIN_IDS": "111,222",
        "TINYDB_PATH": str(path.parent / "bot.json"),
    }
    values.update(overrides)
    path.write_text(
        "\n".join(f"{key}={value}" for key, value in values.items()),
        encoding="utf-8",
    )
    return path


def build_settings(tmp_path: Path, **overrides: str) -> Settings:
    return load_settings(env_file=write_env_file(tmp_path / ".env", **overrides))


@dataclass
class FakeHandler:
    command: str
    callback: object


class FakeApplication:
    def __init__(self) -> None:
        self.bot_data: dict[str, object] = {}
        self.handlers: list[object] = []
        self.error_handlers: list[object] = []

    def add_handler(self, handler: object) -> None:
        self.handlers.append(handler)

    def add_error_handler(self, handler: object) -> None:
        self.error_handlers.append(handler)


class FakeBuilder:
    def __init__(self) -> None:
        self.token_value: str | None = None
        self.post_init_fn = None
        self.post_shutdown_fn = None

    def token(self, value: str) -> "FakeBuilder":
        self.token_value = value
        return self

    def post_init(self, fn):
        self.post_init_fn = fn
        return self

    def post_shutdown(self, fn):
        self.post_shutdown_fn = fn
        return self

    def build(self) -> FakeApplication:
        return FakeApplication()


def test_register_handlers_registers_the_stage_13_command_surface(monkeypatch: pytest.MonkeyPatch) -> None:
    handlers: list[FakeHandler] = []

    def fake_command_handler(command: str, callback: object) -> FakeHandler:
        handler = FakeHandler(command=command, callback=callback)
        handlers.append(handler)
        return handler

    application = FakeApplication()
    monkeypatch.setattr(bot_module, "CommandHandler", fake_command_handler)

    register_handlers(application)

    assert len(application.handlers) == len(bot_module.COMMAND_REGISTRY)
    assert {handler.command for handler in handlers} == set(bot_module.COMMAND_REGISTRY)
    assert {handler.callback.__name__ for handler in handlers} >= {
        "start_command",
        "help_command",
        "logout_command",
        "status_command",
        "chart_command",
        "journal_command",
        "maemfe_command",
    }


def test_build_application_injects_bot_data_and_lifecycle_hooks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = build_settings(tmp_path)
    runtime = SimpleNamespace(bot_data=lambda: {"runtime": "injected"})
    builder = FakeBuilder()
    application = FakeApplication()

    monkeypatch.setattr(bot_module.Application, "builder", lambda: builder)
    monkeypatch.setattr(bot_module, "build_runtime", lambda settings=None: runtime)
    monkeypatch.setattr(builder, "build", lambda: application)
    monkeypatch.setattr(bot_module, "register_handlers", lambda app: app.add_handler("registered"))

    built = build_application(settings=settings)

    assert built is application
    assert builder.token_value == settings.telegram_bot_token.get_secret_value()
    assert builder.post_init_fn is bot_module._on_startup
    assert builder.post_shutdown_fn is bot_module._on_shutdown
    assert application.bot_data["runtime"] == "injected"
    assert application.handlers == ["registered"]
    assert application.error_handlers == [bot_module._handle_application_error]


@pytest.mark.asyncio
async def test_startup_and_shutdown_delegate_to_runtime_lifecycle() -> None:
    calls: list[str] = []
    bot = object()

    class Runtime:
        def __init__(self) -> None:
            self.bot_data_called = False

        def configure_notifications(self, configured_bot: object) -> None:
            calls.append("configure")
            assert configured_bot is bot

        async def start(self) -> None:
            calls.append("start")

        async def stop(self) -> None:
            calls.append("stop")

    application = SimpleNamespace(bot_data={bot_module.BOT_RUNTIME_KEY: Runtime()}, bot=bot)

    await bot_module._on_startup(application)
    await bot_module._on_shutdown(application)

    assert calls == ["configure", "start", "stop"]


@pytest.mark.asyncio
async def test_handle_application_error_logs_polling_conflict_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    warnings: list[tuple[str, dict[str, object]]] = []
    failures: list[str] = []

    def fake_warning(event: str, **fields: object) -> None:
        warnings.append((event, fields))

    def fake_log_failure(*args, **kwargs) -> None:
        failures.append("called")

    monkeypatch.setattr(bot_module.LOGGER, "warning", fake_warning)
    monkeypatch.setattr(bot_module, "log_failure", fake_log_failure)

    application = SimpleNamespace(bot_data={})
    context = SimpleNamespace(
        error=Conflict("terminated by other getUpdates request"),
        application=application,
        bot_data=application.bot_data,
    )

    await bot_module._handle_application_error(update=None, context=context)
    await bot_module._handle_application_error(update=None, context=context)

    assert failures == []
    assert warnings == [
        (
            "telegram_polling_conflict_detected",
            {
                "error": "terminated by other getUpdates request",
                "action": "retrying_until_other_getupdates_client_releases_the_token",
            },
        )
    ]


def test_mark_polling_healthy_clears_conflict_flag_and_logs_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    info_events: list[str] = []

    monkeypatch.setattr(bot_module.LOGGER, "info", lambda event, **_: info_events.append(event))
    bot_data = {bot_module.POLLING_CONFLICT_ACTIVE_KEY: True}

    bot_module._mark_polling_healthy(bot_data)
    bot_module._mark_polling_healthy(bot_data)

    assert bot_data == {}
    assert info_events == ["telegram_polling_conflict_cleared"]


@pytest.mark.asyncio
async def test_runtime_start_fails_when_trade_poller_job_is_missing() -> None:
    async def start_all() -> None:
        return None

    runtime = runtime_module.BotRuntime(
        settings=object(),
        started_at=object(),
        trade_store=SimpleNamespace(close=lambda: None),
        security_manager=object(),
        runtime_config_manager=object(),
        market_state=object(),
        market_data_provider=object(),
        account_client=object(),
        stream_client=object(),
        trade_repository=object(),
        excursion_repository=object(),
        alert_repository=object(),
        journal_service=object(),
        trade_history_service=SimpleNamespace(incremental_sync=lambda: None),
        excursion_tracker=object(),
        price_alert_engine=SimpleNamespace(notifier=None, message_builder=None),
        indicator_alert_engine=SimpleNamespace(notifier=None, message_builder=None),
        time_alert_engine=SimpleNamespace(notifier=None),
        trade_poller=object(),
        stream_task=object(),
        task_supervisor=SimpleNamespace(start_all=start_all),
        scan_orchestrator=object(),
        scheduler=SimpleNamespace(
            start=lambda: None,
            status=lambda: SimpleNamespace(jobs=[]),
        ),
        chart_renderer=object(),
    )

    with pytest.raises(RuntimeError, match="Trade poller job is not registered"):
        await runtime.start()


def test_main_loads_settings_and_invokes_application(monkeypatch: pytest.MonkeyPatch) -> None:
    called = {"settings": 0, "logging": 0, "build": 0, "run": 0}
    settings_obj = object()

    def fake_get_settings() -> object:
        called["settings"] += 1
        return settings_obj

    def fake_configure_logging(value: object) -> None:
        called["logging"] += 1
        assert value is settings_obj

    class FakeApp:
        def run_polling(self) -> None:
            called["run"] += 1

    def fake_build_application(*, settings=None, runtime=None):
        called["build"] += 1
        assert settings is settings_obj
        return FakeApp()

    monkeypatch.setattr(bot_module, "get_settings", fake_get_settings)
    monkeypatch.setattr(bot_module, "configure_logging", fake_configure_logging)
    monkeypatch.setattr(bot_module, "build_application", fake_build_application)

    assert main() == 0
    assert called == {"settings": 1, "logging": 1, "build": 1, "run": 1}


def test_build_runtime_shares_trade_store_with_market_data_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = build_settings(tmp_path)
    captured: dict[str, object] = {}

    class FakeMarketDataProvider:
        def __init__(self, *, settings=None, cache=None, api_client=None) -> None:
            captured["provider_cache"] = cache

    class FakeSecurityManager:
        def __init__(self, *, store=None, settings=None) -> None:
            pass

    class FakeRuntimeConfigManager:
        def __init__(self, *, store=None, settings=None) -> None:
            self._store = store

        def effective_spread_limit(self, value: float) -> float:
            return value

        def effective_chop_threshold(self) -> float:
            return 25.0

    class FakeAlertRepository:
        def __init__(self, store=None, settings=None, db_path=None) -> None:
            pass

    class FakePriceAlertEngine:
        def __init__(self, repository) -> None:
            pass

    class FakeIndicatorAlertEngine:
        def __init__(self, repository, market_data_provider, settings=None) -> None:
            pass

    class FakeScanOrchestrator:
        def __init__(self, **kwargs) -> None:
            self.market_hours_service = object()

    class FakeCacheWarmer:
        def __init__(self, **kwargs) -> None:
            pass

    class FakeAccountClient:
        def __init__(self, *, settings=None) -> None:
            pass

    class FakeStreamClient:
        def __init__(self, *, settings=None) -> None:
            pass

    class FakeHistoryClient:
        def __init__(self, *, settings=None) -> None:
            pass

    class FakeTradeRepository:
        def __init__(self, store=None, settings=None, db_path=None) -> None:
            pass

    class FakeExcursionRepository:
        def __init__(self, store=None, settings=None, db_path=None) -> None:
            pass

    class FakeJournalService:
        def __init__(self, trade_repository, settings=None) -> None:
            pass

    class FakeTradeHistoryService:
        def __init__(self, *, store=None, trade_repository=None, history_client=None, settings=None) -> None:
            pass

    class FakeExcursionTracker:
        def __init__(self, trade_repository, excursion_repository, settings=None) -> None:
            pass

    class FakeTradePollerTask:
        def __init__(
            self,
            account_client,
            trade_repository,
            journal_service,
            settings=None,
            runtime_config_manager=None,
        ) -> None:
            pass

    class FakePriceStreamTask:
        def __init__(self, stream_client, excursion_tracker, price_alert_engine, settings=None) -> None:
            pass

    class FakeTaskSupervisor:
        def __init__(self, **kwargs) -> None:
            pass

    class FakeSchedulerService:
        def __init__(self, **kwargs) -> None:
            pass

    class FakeChartRenderer:
        def __init__(self, **kwargs) -> None:
            pass

    monkeypatch.setattr(runtime_module, "SecurityManager", FakeSecurityManager)
    monkeypatch.setattr(runtime_module, "RuntimeConfigManager", FakeRuntimeConfigManager)
    monkeypatch.setattr(runtime_module, "OandaMarketDataProvider", FakeMarketDataProvider)
    monkeypatch.setattr(runtime_module, "AlertRepository", FakeAlertRepository)
    monkeypatch.setattr(runtime_module, "PriceAlertEngine", FakePriceAlertEngine)
    monkeypatch.setattr(runtime_module, "IndicatorAlertEngine", FakeIndicatorAlertEngine)
    monkeypatch.setattr(runtime_module, "ScanOrchestrator", FakeScanOrchestrator)
    monkeypatch.setattr(runtime_module, "CacheWarmer", FakeCacheWarmer)
    monkeypatch.setattr(runtime_module, "OandaAccountClient", FakeAccountClient)
    monkeypatch.setattr(runtime_module, "OandaHistoryClient", FakeHistoryClient)
    monkeypatch.setattr(runtime_module, "OandaStreamClient", FakeStreamClient)
    monkeypatch.setattr(runtime_module, "TradeRepository", FakeTradeRepository)
    monkeypatch.setattr(runtime_module, "ExcursionRepository", FakeExcursionRepository)
    monkeypatch.setattr(runtime_module, "JournalService", FakeJournalService)
    monkeypatch.setattr(runtime_module, "TradeHistoryService", FakeTradeHistoryService)
    monkeypatch.setattr(runtime_module, "ExcursionTracker", FakeExcursionTracker)
    monkeypatch.setattr(runtime_module, "TradePollerTask", FakeTradePollerTask)
    monkeypatch.setattr(runtime_module, "PriceStreamTask", FakePriceStreamTask)
    monkeypatch.setattr(runtime_module, "TaskSupervisor", FakeTaskSupervisor)
    monkeypatch.setattr(runtime_module, "SchedulerService", FakeSchedulerService)
    monkeypatch.setattr(runtime_module, "ChartRenderer", FakeChartRenderer)

    runtime = runtime_module.build_runtime(settings=settings)
    provider_cache = captured["provider_cache"]

    assert provider_cache is not None
    assert getattr(provider_cache, "trade_store") is runtime.trade_store
    runtime.trade_store.close()


def test_runtime_configure_notifications_binds_alert_engines(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: dict[str, object] = {}

    class FakeNotifier:
        def __init__(self, bot) -> None:
            created["bot"] = bot

    class FakeMessageBuilder:
        pass

    monkeypatch.setattr(runtime_module, "TelegramNotifier", FakeNotifier)
    monkeypatch.setattr(runtime_module, "DefaultNotificationMessageBuilder", FakeMessageBuilder)

    runtime = runtime_module.BotRuntime(
        settings=object(),
        started_at=object(),
        trade_store=SimpleNamespace(close=lambda: None),
        security_manager=object(),
        runtime_config_manager=object(),
        market_state=object(),
        market_data_provider=object(),
        account_client=object(),
        stream_client=object(),
        trade_repository=object(),
        excursion_repository=object(),
        alert_repository=object(),
        journal_service=object(),
        trade_history_service=object(),
        excursion_tracker=object(),
        price_alert_engine=SimpleNamespace(notifier=None, message_builder=None),
        indicator_alert_engine=SimpleNamespace(notifier=None, message_builder=None),
        time_alert_engine=SimpleNamespace(notifier=None),
        trade_poller=SimpleNamespace(notifier=None, message_builder=None),
        stream_task=object(),
        task_supervisor=object(),
        scan_orchestrator=object(),
        scheduler=object(),
        chart_renderer=object(),
    )
    bot = object()

    runtime.configure_notifications(bot)

    assert created["bot"] is bot
    assert isinstance(runtime.price_alert_engine.notifier, FakeNotifier)
    assert isinstance(runtime.indicator_alert_engine.notifier, FakeNotifier)
    assert isinstance(runtime.price_alert_engine.message_builder, FakeMessageBuilder)
    assert isinstance(runtime.indicator_alert_engine.message_builder, FakeMessageBuilder)
    assert isinstance(runtime.trade_poller.notifier, FakeNotifier)
    assert isinstance(runtime.trade_poller.message_builder, FakeMessageBuilder)
    assert isinstance(runtime.time_alert_engine.notifier, FakeNotifier)
