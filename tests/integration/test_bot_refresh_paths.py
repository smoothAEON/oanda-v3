from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from bot import bot as bot_module
from bot.security_manager import SecurityManager
from config.settings import load_settings
from core.candle_policy import get_timeframe_delta
from core.instrument_registry import get_instrument_spec
from core.models import (
    ActiveZoneSummary,
    CalendarEvent,
    CalendarRefreshStatus,
    IndicatorValueSummary,
    LiquidityPoolSummary,
    MacroContextStatus,
    MacroIndicatorStatus,
    MarketHoursOverview,
    MarketHoursStatus,
    PreviousHighLowSummary,
    SnapshotFreshness,
    SmcContextSummary,
    SpreadResult,
    StructureEventSummary,
    TimeframeSnapshot,
)
from data.persistence.trade_store import TradeStore
from orchestration.scan_orchestrator import ScanOrchestrator
from tests.integration.test_scan_orchestrator import (
    ClosedMarketHours,
    StubCalendarProvider as CacheOnlyCalendarProvider,
    StubMacroService,
    StubMarketDataProvider,
    StubSmcAdapter,
    build_indicator_summary,
)


BASE_TIME = datetime(2026, 3, 22, 8, 0, tzinfo=timezone.utc)


def write_env_file(path: Path, *, tinydb_path: Path) -> Path:
    path.write_text(
        "\n".join(
            (
                "OANDA_API_KEY=api-key",
                "OANDA_ACCOUNT_ID=account-id",
                "OANDA_ENVIRONMENT=practice",
                "TELEGRAM_BOT_TOKEN=telegram-token",
                "TELEGRAM_CHAT_ID=123456789",
                "TELEGRAM_BOT_PASSWORD=bot-password",
                "TELEGRAM_ADMIN_IDS=111,222",
                f"TINYDB_PATH={tinydb_path.as_posix()}",
            )
        ),
        encoding="utf-8",
    )
    return path


def build_settings_and_store(tmp_path: Path):
    env_file = write_env_file(tmp_path / ".env", tinydb_path=tmp_path / "bot.json")
    settings = load_settings(env_file=env_file)
    store = TradeStore(db_path=settings.tinydb_path, settings=settings)
    return settings, store


class RecorderMessage:
    def __init__(self) -> None:
        self.texts: list[str] = []

    async def reply_text(self, text: str, **kwargs) -> None:  # type: ignore[no-untyped-def]
        self.texts.append(text)


def build_update(user_id: int = 111, chat_id: int = 222) -> SimpleNamespace:
    return SimpleNamespace(
        effective_message=RecorderMessage(),
        effective_user=SimpleNamespace(id=user_id, username="tester", first_name="Test"),
        effective_chat=SimpleNamespace(id=chat_id),
    )


def authenticate(store: TradeStore, settings) -> SecurityManager:
    security = SecurityManager(store=store, settings=settings)
    security.authenticate(
        user_id=111,
        chat_id=222,
        password="bot-password",
        username="tester",
        first_name="Test",
    )
    return security


def build_spread(instrument: str = "EUR_USD") -> SpreadResult:
    spec = get_instrument_spec(instrument)
    bid = 1.1000
    ask = bid + spec.pip_size * 2.0
    return SpreadResult(
        instrument=instrument,
        bid=bid,
        ask=ask,
        raw_spread=ask - bid,
        spread_pips=2.0,
        pip_size=spec.pip_size,
        fetched_at=BASE_TIME,
    )


def build_snapshot(
    *,
    instrument: str = "EUR_USD",
    timeframe: str = "H1",
    is_fresh: bool = True,
) -> TimeframeSnapshot:
    delta = get_timeframe_delta(timeframe)
    return TimeframeSnapshot(
        instrument=instrument,
        timeframe=timeframe,
        last_completed_candle=BASE_TIME,
        computed_at=BASE_TIME + timedelta(minutes=1),
        candle_range_start=BASE_TIME - delta,
        candle_range_end=BASE_TIME,
        indicators=IndicatorValueSummary(),
        structure=StructureEventSummary(),
        zones=ActiveZoneSummary(),
        liquidity=LiquidityPoolSummary(),
        smc_context=SmcContextSummary(
            previous_high_low=PreviousHighLowSummary(
                previous_high=1.1060,
                previous_low=1.0940,
                broken_high=False,
                broken_low=False,
                as_of=BASE_TIME,
            )
        ),
        spread=build_spread(instrument),
        freshness=SnapshotFreshness(
            instrument=instrument,
            timeframe=timeframe,
            last_completed_candle=BASE_TIME,
            fetched_at=BASE_TIME + timedelta(minutes=1),
            source="test",
            candle_count=30,
            is_fresh=is_fresh,
            staleness_seconds=0.0 if is_fresh else 3600.0,
        ),
    )


class StubMarketState:
    def __init__(self, *, snapshot: TimeframeSnapshot | None = None) -> None:
        self.snapshot = snapshot
        self.snapshot_calls = 0

    def get_snapshot(self, instrument: str, timeframe: str):
        self.snapshot_calls += 1
        return self.snapshot


class StubCalendarProvider:
    def __init__(self) -> None:
        self.calls = 0

    def filter_events(
        self,
        *,
        impacts=None,
        window_start=None,
        window_end=None,
        currencies=None,
        countries=None,
    ):
        self.calls += 1
        return (
            CalendarEvent(
                event_time=BASE_TIME,
                currency="USD",
                title="CPI",
                impact="HIGH",
                forecast=None,
                previous=None,
                actual=None,
                is_blackout=True,
            ),
        )


class StubScanOrchestrator:
    def __init__(self, *, snapshots=None) -> None:  # type: ignore[no-untyped-def]
        self.snapshots = snapshots
        self.refresh_instrument_calls: list[str] = []
        self.refresh_force_calls: list[tuple[str, bool]] = []
        self.scan_calls = 0
        self.scan_force_calls: list[bool] = []
        self.last_scan_status = SimpleNamespace(
            run_kind="full",
            scanned_instruments=(),
            snapshots_published=0,
            errors=(),
        )
        self.market_hours_status = None
        self.macro_status = MacroContextStatus()
        self.calendar_status = CalendarRefreshStatus()
        self.calendar_provider = StubCalendarProvider()
        self.market_hours_service = SimpleNamespace(
            get_status=lambda: SimpleNamespace(
                is_market_open=True,
                reason="open",
                next_open_at=None,
                next_close_at=BASE_TIME,
            )
        )

    def refresh_instrument(self, instrument: str, *, force: bool = False):
        self.refresh_instrument_calls.append(instrument)
        self.refresh_force_calls.append((instrument, force))
        self.last_scan_status = SimpleNamespace(
            run_kind="instrument_refresh",
            scanned_instruments=(instrument,),
            snapshots_published=4 if self.snapshots is not None else 0,
            forced_market_fetch=force,
            errors=(),
        )
        return self.snapshots

    def scan_all(self, *, force: bool = False):
        self.scan_calls += 1
        self.scan_force_calls.append(force)
        self.last_scan_status = SimpleNamespace(
            run_kind="full",
            scanned_instruments=("EUR_USD", "XAU_USD"),
            snapshots_published=8,
            forced_market_fetch=force,
            errors=("EUR_USD: none",),
        )
        return self.last_scan_status

    def refresh_calendar(self, *, force: bool = True):
        self.calendar_status = CalendarRefreshStatus(
            last_attempted_at=BASE_TIME,
            last_refreshed_at=BASE_TIME,
            calendar_version=1,
            event_count=1,
            next_high_impact=BASE_TIME,
            used_cached=False,
            last_error=None,
        )
        return self.calendar_status

    def refresh_macro(self, *, force: bool = True):
        return self.macro_status


class StubAccountClient:
    def __init__(self) -> None:
        self.summary_calls = 0
        self.position_calls = 0
        self.order_calls = 0

    async def get_account_summary(self):
        self.summary_calls += 1
        return SimpleNamespace(
            account_id="001-001",
            environment="practice",
            currency="USD",
            balance=1000.0,
            nav=1010.0,
            unrealized_pl=10.0,
            realized_pl=5.0,
            margin_used=12.0,
            margin_available=998.0,
            open_trade_count=1,
            open_position_count=1,
            pending_order_count=2,
        )

    async def get_open_positions(self):
        self.position_calls += 1
        return [
            SimpleNamespace(
                trade_id="trade-1",
                instrument="EUR_USD",
                units=1.0,
                open_price=1.1000,
                unrealized_pl=2.5,
                realized_pl=0.0,
                account_currency="USD",
                stop_loss_price=1.0900,
                take_profit_price=1.1200,
                gslo_price=None,
                opened_at=BASE_TIME,
                direction="LONG",
            )
        ]

    async def get_open_orders(self):
        self.order_calls += 1
        return [
            SimpleNamespace(
                order_id="order-1",
                instrument="EUR_USD",
                order_type="LIMIT",
                direction="LONG",
                price=1.1050,
                state="PENDING",
            )
        ]

    async def get_pricing(self, instrument: str):
        return SimpleNamespace(
            instrument=instrument,
            bid=1.1000,
            ask=1.1002,
            spread_pips=2.0,
            fetched_at=BASE_TIME,
        )


class StubRuntime:
    def __init__(self, scan_orchestrator) -> None:  # type: ignore[no-untyped-def]
        self.scan_orchestrator = scan_orchestrator
        self.stream_task = SimpleNamespace(
            stream_status=lambda: SimpleNamespace(
                state="RUNNING",
                reconnect_count=0,
                last_tick_at=BASE_TIME,
            )
        )


@pytest.mark.asyncio
async def test_smc_uses_snapshot_before_refresh_and_reports_raw_spread(tmp_path: Path) -> None:
    settings, store = build_settings_and_store(tmp_path)
    security = authenticate(store, settings)
    snapshot = build_snapshot()
    market_state = StubMarketState(snapshot=snapshot)
    orchestrator = StubScanOrchestrator()
    bot_data = {
        bot_module.BOT_RUNTIME_KEY: StubRuntime(orchestrator),
        bot_module.SECURITY_MANAGER_KEY: security,
        bot_module.MARKET_STATE_KEY: market_state,
        bot_module.SCAN_ORCHESTRATOR_KEY: orchestrator,
    }
    update = build_update()

    try:
        await bot_module.smc_command(
            update,
            SimpleNamespace(bot_data=bot_data, args=["EUR_USD", "H1"]),
        )
    finally:
        store.close()

    assert market_state.snapshot_calls == 1
    assert "SMC EUR_USD H1" in update.effective_message.texts[-1]
    assert "Spread: 2.0 pips" in update.effective_message.texts[-1]
    assert "acceptable" not in update.effective_message.texts[-1].lower()


@pytest.mark.asyncio
async def test_smc_surfaces_stale_snapshot_warning_without_refresh(tmp_path: Path) -> None:
    settings, store = build_settings_and_store(tmp_path)
    security = authenticate(store, settings)
    stale_snapshot = build_snapshot(is_fresh=False)
    market_state = StubMarketState(snapshot=stale_snapshot)
    orchestrator = StubScanOrchestrator()
    bot_data = {
        bot_module.BOT_RUNTIME_KEY: StubRuntime(orchestrator),
        bot_module.SECURITY_MANAGER_KEY: security,
        bot_module.MARKET_STATE_KEY: market_state,
        bot_module.SCAN_ORCHESTRATOR_KEY: orchestrator,
    }
    update = build_update()

    try:
        await bot_module.smc_command(
            update,
            SimpleNamespace(bot_data=bot_data, args=["EUR_USD", "H1"]),
        )
    finally:
        store.close()

    assert "Warning: snapshot is stale" in update.effective_message.texts[-1]


@pytest.mark.asyncio
async def test_async_account_commands_and_scan_paths_report_snapshots(tmp_path: Path) -> None:
    settings, store = build_settings_and_store(tmp_path)
    security = authenticate(store, settings)
    account_client = StubAccountClient()
    snapshots = {"H1": build_snapshot()}
    orchestrator = StubScanOrchestrator(snapshots=snapshots)
    bot_data = {
        bot_module.BOT_RUNTIME_KEY: StubRuntime(orchestrator),
        bot_module.SECURITY_MANAGER_KEY: security,
        bot_module.ACCOUNT_CLIENT_KEY: account_client,
        bot_module.SETTINGS_KEY: settings,
        bot_module.SCAN_ORCHESTRATOR_KEY: orchestrator,
        bot_module.MARKET_STATE_KEY: StubMarketState(snapshot=build_snapshot()),
    }

    account_update = build_update()
    positions_update = build_update()
    orders_update = build_update()
    scan_update = build_update()
    scan_one_update = build_update()

    try:
        await bot_module.account_command(account_update, SimpleNamespace(bot_data=bot_data, args=[]))
        await bot_module.positions_command(positions_update, SimpleNamespace(bot_data=bot_data, args=[]))
        await bot_module.orders_command(orders_update, SimpleNamespace(bot_data=bot_data, args=[]))
        await bot_module.scan_command(scan_update, SimpleNamespace(bot_data=bot_data, args=[]))
        await bot_module.scan_command(
            scan_one_update,
            SimpleNamespace(bot_data=bot_data, args=["EUR_USD"]),
        )
    finally:
        store.close()

    assert account_client.summary_calls == 1
    assert account_client.position_calls == 1
    assert account_client.order_calls == 1
    assert orchestrator.scan_calls == 1
    assert orchestrator.refresh_instrument_calls == ["EUR_USD"]
    assert "Account 001-001 (practice)" in account_update.effective_message.texts[-1]
    assert "Open Trades (1)" in positions_update.effective_message.texts[-1]
    assert "Open Orders" in orders_update.effective_message.texts[-1]
    assert "Full scan complete" in scan_update.effective_message.texts[-1]
    assert "Snapshots: 8" in scan_update.effective_message.texts[-1]
    assert "Scan complete for EUR_USD" in scan_one_update.effective_message.texts[-1]
    assert "Snapshots ready: yes" in scan_one_update.effective_message.texts[-1]


@pytest.mark.asyncio
async def test_scan_command_force_flag_is_parsed_and_forwarded(tmp_path: Path) -> None:
    settings, store = build_settings_and_store(tmp_path)
    security = authenticate(store, settings)
    orchestrator = StubScanOrchestrator(snapshots={"H1": build_snapshot()})
    bot_data = {
        bot_module.BOT_RUNTIME_KEY: StubRuntime(orchestrator),
        bot_module.SECURITY_MANAGER_KEY: security,
        bot_module.SCAN_ORCHESTRATOR_KEY: orchestrator,
        bot_module.SETTINGS_KEY: settings,
    }

    full_force_update = build_update()
    single_force_update = build_update()
    single_default_update = build_update()

    try:
        await bot_module.scan_command(
            full_force_update,
            SimpleNamespace(bot_data=bot_data, args=["force"]),
        )
        await bot_module.scan_command(
            single_force_update,
            SimpleNamespace(bot_data=bot_data, args=["EUR_USD", "force"]),
        )
        await bot_module.scan_command(
            single_default_update,
            SimpleNamespace(bot_data=bot_data, args=["EUR_USD"]),
        )
    finally:
        store.close()

    assert orchestrator.scan_force_calls == [True]
    assert orchestrator.refresh_force_calls == [("EUR_USD", True), ("EUR_USD", False)]
    assert "Note: forced scan used live fetch" in full_force_update.effective_message.texts[-1]
    assert "Note: forced scan used live fetch" in single_force_update.effective_message.texts[-1]
    assert "Note: forced scan used live fetch" not in single_default_update.effective_message.texts[-1]


@pytest.mark.asyncio
async def test_scan_then_pdl_uses_closed_market_cache_without_seed_snapshot(tmp_path: Path) -> None:
    settings, store = build_settings_and_store(tmp_path)
    security = authenticate(store, settings)
    orchestrator = ScanOrchestrator(
        settings=settings,
        market_data_provider=StubMarketDataProvider(cached_available=True),
        calendar_provider=CacheOnlyCalendarProvider(),
        market_hours_service=ClosedMarketHours(),
        macro_context_service=StubMacroService(),
        smc_adapter=StubSmcAdapter(),
        indicator_builder=build_indicator_summary,
    )
    bot_data = {
        bot_module.SECURITY_MANAGER_KEY: security,
        bot_module.SCAN_ORCHESTRATOR_KEY: orchestrator,
        bot_module.MARKET_STATE_KEY: orchestrator.market_state,
    }
    scan_update = build_update()
    pdl_update = build_update()

    try:
        await bot_module.scan_command(
            scan_update,
            SimpleNamespace(bot_data=bot_data, args=["XAU_USD"]),
        )
        await bot_module.pdl_command(
            pdl_update,
            SimpleNamespace(bot_data=bot_data, args=["XAU_USD"]),
        )
    finally:
        store.close()

    assert "Scan complete for XAU_USD" in scan_update.effective_message.texts[-1]
    assert "Data unavailable for XAU_USD H1. Try /scan first." not in pdl_update.effective_message.texts[-1]
    assert "PDL XAU_USD" in pdl_update.effective_message.texts[-1]


@pytest.mark.asyncio
async def test_calendar_prefers_cached_status_then_refreshes_force_false(tmp_path: Path) -> None:
    settings, store = build_settings_and_store(tmp_path)
    security = authenticate(store, settings)
    orchestrator = StubScanOrchestrator()
    runtime = StubRuntime(orchestrator)
    bot_data = {
        bot_module.BOT_RUNTIME_KEY: runtime,
        bot_module.SECURITY_MANAGER_KEY: security,
    }
    update = build_update()

    try:
        await bot_module.calendar_command(update, SimpleNamespace(bot_data=bot_data, args=["today"]))
    finally:
        store.close()

    assert orchestrator.calendar_provider.calls == 1
    assert orchestrator.calendar_status.calendar_version == 1
    assert "Calendar (SGT)" in update.effective_message.texts[-1]
    assert "USD (1)" in update.effective_message.texts[-1]
    assert "CPI" in update.effective_message.texts[-1]


@pytest.mark.asyncio
async def test_marketstatus_renders_category_and_macro_lines(tmp_path: Path) -> None:
    settings, store = build_settings_and_store(tmp_path)
    security = authenticate(store, settings)
    orchestrator = StubScanOrchestrator()
    orchestrator.market_hours_status = MarketHoursOverview(
        overall=MarketHoursStatus(
            checked_at=BASE_TIME,
            is_market_open=True,
            source="test",
            category="overall",
            reason="partial_open",
            next_open_at=None,
            next_close_at=BASE_TIME + timedelta(hours=2),
        ),
        fx=MarketHoursStatus(
            checked_at=BASE_TIME,
            is_market_open=True,
            source="test",
            category="fx",
            reason="open",
            next_open_at=None,
            next_close_at=BASE_TIME + timedelta(hours=2),
        ),
        metals=MarketHoursStatus(
            checked_at=BASE_TIME,
            is_market_open=False,
            source="test",
            category="metals",
            reason="holiday_closed",
            next_open_at=BASE_TIME + timedelta(days=1),
            next_close_at=None,
        ),
    )
    orchestrator.macro_status = MacroContextStatus(
        last_attempted_at=BASE_TIME,
        used_cached=True,
        vix=MacroIndicatorStatus(name="VIX", symbol="^VIX", value=18.5, as_of=BASE_TIME),
        dxy=MacroIndicatorStatus(name="DXY", symbol="DX-Y.NYB", value=104.2, as_of=BASE_TIME),
    )
    runtime = StubRuntime(orchestrator)
    bot_data = {
        bot_module.BOT_RUNTIME_KEY: runtime,
        bot_module.SECURITY_MANAGER_KEY: security,
    }
    update = build_update()

    try:
        await bot_module.marketstatus_command(update, SimpleNamespace(bot_data=bot_data, args=[]))
    finally:
        store.close()

    text = update.effective_message.texts[-1]
    assert "Market Status" in text
    assert "Overall: open" in text
    assert "FX: open" in text
    assert "Metals: closed" in text
    assert "VIX: 18.50000" in text
    assert "DXY: 104.20000" in text
