from __future__ import annotations

from decimal import Decimal
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
import re
from zoneinfo import ZoneInfo

import pytest

import bot.bot as bot_module
from bot.parsing import (
    normalize_command_instrument,
    normalize_command_timeframe,
    parse_calendar_args,
    parse_calendar_scope,
    parse_chart_args,
    parse_indicator_alert_args,
    parse_journal_args,
    parse_price_args,
    parse_time_alert_args,
)
from bot.runtime import SCHEDULER_KEY
from core.enums import ChartMode, ChartRenderStyle, IndicatorKind, RuntimeConfigKey, TimeAlertKind, TimeAlertStatus
from core.events import PriceTick
from core.models import RealizedPnLSummary, TimeAlert, TradeHistoryEvent, TradeHistoryPage
from data.persistence.trade_store import PersistenceWriteError


class DummyMessage:
    def __init__(self) -> None:
        self.text_replies: list[str] = []
        self.documents: list[tuple[object, str | None, dict[str, object]]] = []

    async def reply_text(self, text: str, **kwargs) -> None:
        self.text_replies.append(text)

    async def reply_document(self, document, filename: str | None = None, **kwargs) -> None:  # type: ignore[no-untyped-def]
        self.documents.append((document, filename, dict(kwargs)))


class DummyContext:
    def __init__(self, *, args: list[str] | None = None, bot_data: dict[str, object] | None = None) -> None:
        self.args = args or []
        self.bot_data = bot_data or {}


class DummyUpdate:
    def __init__(self, message: DummyMessage | None = None) -> None:
        self.effective_message = message or DummyMessage()
        self.effective_user = SimpleNamespace(id=1001, username="tester", first_name="Test")
        self.effective_chat = SimpleNamespace(id=2002)


class DummySession:
    def __init__(self) -> None:
        self.user_id = 1001
        self.chat_id = 2002
        self.is_admin = False
        self.authenticated_at = SimpleNamespace()
        self.last_activity_at = SimpleNamespace()


class FakeSecurityManager:
    def __init__(self, session: DummySession | None = None) -> None:
        self.session = session or DummySession()
        self.get_session_calls = 0
        self.touch_calls = 0
        self.logout_calls = 0

    def get_session(self, user_id: int):
        self.get_session_calls += 1
        return self.session

    def touch(self, user_id: int):
        self.touch_calls += 1
        return self.session

    def get_session_for_chat(self, user_id: int, chat_id: int):
        self.get_session_calls += 1
        if self.session is None or self.session.chat_id != chat_id:
            return None
        return self.session

    def touch_for_chat(self, user_id: int, chat_id: int):
        self.touch_calls += 1
        if self.session is None or self.session.chat_id != chat_id:
            return None
        return self.session

    def logout(self, user_id: int):
        self.logout_calls += 1
        return self.session

    def is_admin(self, user_id: int) -> bool:
        return False


class FakeRuntimeConfigManager:
    def __init__(self) -> None:
        self.set_value_calls: list[tuple[RuntimeConfigKey, object]] = []
        self.resolved_interval = 15

    def snapshot(self):
        return SimpleNamespace(
            tolerance=None,
            spread=None,
            chop=None,
            chart=ChartRenderStyle.CANDLESTICK,
            chart_mode=ChartMode.BALANCED,
            scan_interval=None,
            trade_push=True,
            session_alerts=True,
        )

    def set_value(self, key: RuntimeConfigKey, value: object):
        self.set_value_calls.append((key, value))
        return SimpleNamespace(key=key, value=value)

    def effective_scan_interval_minutes(self) -> int:
        return self.resolved_interval

    def effective_chart_style(self) -> ChartRenderStyle:
        return ChartRenderStyle.LINE

    def effective_chart_mode(self) -> ChartMode:
        return ChartMode.BALANCED

    def effective_sr_tolerance(self) -> float | None:
        return 1.5


class FakeRenderer:
    def __init__(self, artifact_path: Path) -> None:
        self.artifact_path = artifact_path
        self.calls: list[object] = []
        self.warning_text: str | None = None

    def render(self, request):
        self.calls.append(request)
        self.artifact_path.write_bytes(b"chart")
        return SimpleNamespace(
            artifact=SimpleNamespace(path=self.artifact_path),
            warning_text=self.warning_text,
            close=lambda: None,
        )


class FailingRenderer:
    def __init__(self) -> None:
        self.calls: list[object] = []

    def render(self, request):
        self.calls.append(request)
        raise RuntimeError("render exploded")


class FakeScheduler:
    def __init__(self) -> None:
        self.calls: list[int] = []

    def reschedule_auto_scan(self, interval: int):
        self.calls.append(interval)
        return interval


class FakeTradeHistoryService:
    def __init__(self) -> None:
        self.trade_history_calls: list[tuple[str, str, str | None, int]] = []
        self.backfill_calls: list[tuple[object, object, object]] = []

    def get_trade_history(self, period: str, view: str, instrument: str | None, page: int) -> TradeHistoryPage:
        self.trade_history_calls.append((period, view, instrument, page))
        summary = RealizedPnLSummary(
            period=period,
            instrument=instrument,
            start_utc=datetime(2026, 4, 1, 0, 0, tzinfo=timezone.utc),
            end_utc=datetime(2026, 4, 1, 6, 0, tzinfo=timezone.utc),
            start_local=datetime(2026, 4, 1, 8, 0, tzinfo=ZoneInfo("Asia/Singapore")),
            end_local=datetime(2026, 4, 1, 14, 0, tzinfo=ZoneInfo("Asia/Singapore")),
            gross_realized_pl=Decimal("12.50"),
            financing=Decimal("-0.20"),
            commission=Decimal("0.10"),
            net_realized_pl=Decimal("12.20"),
        )
        return TradeHistoryPage(
            period=period,
            view=view,
            instrument=instrument,
            window_start_utc=summary.start_utc,
            window_end_utc=summary.end_utc,
            window_start_local=summary.start_local,
            window_end_local=summary.end_local,
            summary=summary,
            rows=(
                TradeHistoryEvent(
                    event_id="101:CLOSE:trade-1",
                    transaction_id="101",
                    batch_id="500",
                    event_type="CLOSE",
                    account_id="account-id",
                    instrument=instrument or "XAU_USD",
                    trade_id="trade-1",
                    order_id="9001",
                    units=Decimal("-40"),
                    abs_units=Decimal("40"),
                    side="SHORT",
                    price=Decimal("3123.456"),
                    realized_pl=Decimal("12.50"),
                    financing=Decimal("-0.20"),
                    commission=Decimal("0.10"),
                    net_realized_pl=Decimal("12.20"),
                    time_utc=datetime(2026, 4, 1, 1, 15, tzinfo=timezone.utc),
                    time_local=datetime(2026, 4, 1, 9, 15, tzinfo=ZoneInfo("Asia/Singapore")),
                    reason="TAKE_PROFIT_ORDER",
                    raw_json="{}",
                ),
            ),
            page=1,
            page_size=20,
            total_rows=1,
            total_pages=1,
            stale_warning=None,
        )

    def backfill_history(self, start_date, end_date, tz_name):
        self.backfill_calls.append((start_date, end_date, tz_name))
        return {
            "start": "2025-01-01",
            "end": "2026-04-01",
            "timezone_name": "Asia/Singapore",
            "chunks": 2,
            "raw_seen": 20,
            "raw_inserted": 10,
            "raw_updated": 0,
            "seen": 12,
            "inserted": 12,
            "updated": 0,
            "projected_trades": 3,
        }


class RecordingApplication:
    def __init__(self) -> None:
        self.handlers: list[object] = []

    def add_handler(self, handler) -> None:  # type: ignore[no-untyped-def]
        self.handlers.append(handler)


def make_persistence_error(action: str = "write") -> PersistenceWriteError:
    return PersistenceWriteError(action, Path("test.json"), "disk unavailable")


def registered_command_handlers() -> dict[str, object]:
    application = RecordingApplication()
    bot_module.register_handlers(application)
    mapping: dict[str, object] = {}
    for handler in application.handlers:
        commands = tuple(sorted(handler.commands))
        assert len(commands) == 1
        mapping[commands[0]] = handler.callback
    return mapping


def test_trade_poller_health_is_failed_when_scheduler_job_is_missing() -> None:
    status = bot_module._trade_poller_status_from_scheduler(
        SimpleNamespace(
            state="RUNNING",
            started_at=datetime(2026, 3, 29, 0, 0, tzinfo=timezone.utc),
            jobs=(),
        )
    )

    assert status.name == "trade_poller"
    assert status.state == "FAILED"
    assert "not registered" in (status.last_error or "")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("gold", "XAU_USD"),
        ("EUR/USD", "EUR_USD"),
        ("usd jpy", "USD_JPY"),
    ],
)
def test_normalize_command_instrument_accepts_aliases(value: str, expected: str) -> None:
    assert normalize_command_instrument(value) == expected


@pytest.mark.parametrize("value", ["oil", "btc", "eth"])
def test_normalize_command_instrument_rejects_glossary_only_values(value: str) -> None:
    with pytest.raises(ValueError, match="Unsupported instrument"):
        normalize_command_instrument(value)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("1h", "H1"),
        ("30m", "M30"),
        ("daily", "D"),
    ],
)
def test_normalize_command_timeframe_accepts_aliases(value: str, expected: str) -> None:
    assert normalize_command_timeframe(value) == expected


@pytest.mark.parametrize("value", ["w", "weekly", "1w"])
def test_normalize_command_timeframe_rejects_weekly_aliases(value: str) -> None:
    with pytest.raises(ValueError, match="Unsupported timeframe 'W'"):
        normalize_command_timeframe(value)


def test_parse_chart_args_builds_request_shape_with_default_style() -> None:
    request = parse_chart_args(
        [
            "gold",
            "1h",
            "--count",
            "250",
            "--smc",
            "orderblocks|structure",
            "--trade",
            "positions,sl",
            "--alert",
            "pricealerts",
        ],
        default_style=ChartRenderStyle.LINE,
    )

    assert request.instrument == "XAU_USD"
    assert request.timeframe == "H1"
    assert request.count == 250
    assert request.style == ChartRenderStyle.LINE
    assert request.selection.keys == (
        "orderblocks",
        "structure",
        "positions",
        "sl",
        "pricealerts",
    )
    assert request.selection.smc == ("orderblocks", "structure")
    assert request.selection.trade == ("positions", "sl")
    assert request.selection.alert == ("pricealerts",)


def test_parse_chart_args_accepts_flag_first_and_defaults_timeframe() -> None:
    request = parse_chart_args(
        ["gold", "--count", "250", "--alert", "pricealerts"],
        default_style=ChartRenderStyle.CANDLESTICK,
    )

    assert request.instrument == "XAU_USD"
    assert request.timeframe == "H1"
    assert request.count == 250
    assert request.selection.alert == ("pricealerts",)


def test_parse_chart_args_accepts_mode_flag_and_runtime_default_mode() -> None:
    full_request = parse_chart_args(
        ["gold", "1h", "--mode", "full"],
        default_style=ChartRenderStyle.CANDLESTICK,
        default_mode=ChartMode.COMPACT,
    )
    compact_request = parse_chart_args(
        ["gold"],
        default_style=ChartRenderStyle.CANDLESTICK,
        default_mode=ChartMode.COMPACT,
    )

    assert full_request.mode == ChartMode.FULL
    assert compact_request.mode == ChartMode.COMPACT


def test_parse_chart_args_rejects_undocumented_style_flag() -> None:
    with pytest.raises(ValueError, match="Unsupported chart flag '--style'"):
        parse_chart_args(
            ["gold", "1h", "--style", "line"],
            default_style=ChartRenderStyle.CANDLESTICK,
        )


def test_parse_price_args_accepts_live_flag_and_rejects_multiple_symbols() -> None:
    assert parse_price_args(["gold"]) == ("XAU_USD", False)
    assert parse_price_args(["--live", "gold"]) == ("XAU_USD", True)

    with pytest.raises(ValueError, match="Usage: /price <symbol> \\[--live\\]"):
        parse_price_args(["gold", "eurusd"])


def test_parse_time_alert_args_supports_fixed_and_session_modes() -> None:
    assert parse_time_alert_args(["at", "09:30", "daily", "London", "prep"]) == (
        "at",
        "daily",
        "09:30",
        None,
        "London prep",
    )
    assert parse_time_alert_args(["session", "newyork", "open"]) == (
        "session",
        "session",
        None,
        "newyork",
        "open",
    )

    with pytest.raises(ValueError, match="Session alerts support"):
        parse_time_alert_args(["session", "tokyo"])


@pytest.mark.asyncio
async def test_require_session_rejects_chat_mismatch_without_touching_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    message = DummyMessage()
    update = DummyUpdate(message)
    update.effective_chat.id = 9999
    security = FakeSecurityManager()
    context = DummyContext(bot_data={bot_module.SECURITY_MANAGER_KEY: security})

    async def fake_to_thread(func, /, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(bot_module.asyncio, "to_thread", fake_to_thread)
    monkeypatch.setattr(bot_module, "_security_manager", lambda _context: security)

    session = await bot_module._require_session(update, context)

    assert session is None
    assert security.touch_calls == 0
    assert message.text_replies[-1] == bot_module.CHAT_SCOPE_TEXT


def test_parse_calendar_scope_defaults_and_validates() -> None:
    assert parse_calendar_scope([]) == "today"
    assert parse_calendar_scope(["week"]) == "week"
    with pytest.raises(ValueError, match=r"Usage: /calendar \[today\|week\]"):
        parse_calendar_scope(["month"])


def test_parse_calendar_args_defaults() -> None:
    assert parse_calendar_args([]) == ("today", None, False)


def test_parse_calendar_args_scope_only() -> None:
    assert parse_calendar_args(["week"]) == ("week", None, False)


def test_parse_calendar_args_currency_filter() -> None:
    assert parse_calendar_args(["USD", "EUR"]) == ("today", ("USD", "EUR"), False)


def test_parse_calendar_args_scope_and_currency() -> None:
    assert parse_calendar_args(["week", "USD"]) == ("week", ("USD",), False)


def test_parse_calendar_args_force_only() -> None:
    assert parse_calendar_args(["force"]) == ("today", None, True)


def test_parse_calendar_args_refresh_alias() -> None:
    assert parse_calendar_args(["refresh"]) == ("today", None, True)


def test_parse_calendar_args_week_force() -> None:
    assert parse_calendar_args(["week", "force"]) == ("week", None, True)


def test_parse_calendar_args_currencies_and_force() -> None:
    assert parse_calendar_args(["USD", "EUR", "force"]) == ("today", ("USD", "EUR"), True)


def test_parse_calendar_args_lowercase_currencies() -> None:
    assert parse_calendar_args(["usd"]) == ("today", ("USD",), False)


def test_parse_calendar_args_mixed_case_currency() -> None:
    assert parse_calendar_args(["Gbp"]) == ("today", ("GBP",), False)


def test_parse_calendar_args_invalid_token_raises() -> None:
    with pytest.raises(ValueError, match=r"Usage: /calendar"):
        parse_calendar_args(["INVALID_LONG"])


def test_parse_calendar_args_unknown_3letter_accepted_silently() -> None:
    # Unknown 3-letter codes pass through; filter_events returns zero events for them
    scope, currencies, force = parse_calendar_args(["FOO"])
    assert currencies == ("FOO",)


def test_parse_indicator_alert_args_parses_threshold_and_note() -> None:
    instrument, timeframe, indicator, condition, threshold, note = parse_indicator_alert_args(
        ["gold", "1h", "rsi", "above", "30", "trend", "watch"]
    )

    assert instrument == "XAU_USD"
    assert timeframe == "H1"
    assert indicator == IndicatorKind.RSI
    assert condition == "above"
    assert threshold == pytest.approx(30.0)
    assert note == "trend watch"


@pytest.mark.parametrize(
    "args",
    [
        ["gold", "1h", "--count"],
        ["gold", "1h", "--smc"],
        ["gold", "1h", "--overlays"],
        ["gold", "1h", "--trade"],
        ["gold", "1h", "--alert"],
        ["gold", "1h", "--indicator"],
    ],
)
def test_parse_chart_args_rejects_missing_flag_value(args: list[str]) -> None:
    with pytest.raises(ValueError, match="requires a value"):
        parse_chart_args(args, default_style=ChartRenderStyle.CANDLESTICK)


@pytest.mark.parametrize(
    ("args", "expected_match"),
    [
        (["--instrument"], "requires a value"),
        (["--from"], "requires a date"),
        (["--to"], "requires a date"),
    ],
)
def test_parse_journal_args_rejects_missing_flag_value(args: list[str], expected_match: str) -> None:
    with pytest.raises(ValueError, match=expected_match):
        parse_journal_args(args)


@pytest.mark.asyncio
async def test_config_command_rejects_unknown_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    message = DummyMessage()
    update = DummyUpdate(message)
    context = DummyContext(
        args=["bogus", "1"],
        bot_data={
            bot_module.SECURITY_MANAGER_KEY: FakeSecurityManager(),
            bot_module.RUNTIME_CONFIG_MANAGER_KEY: FakeRuntimeConfigManager(),
        },
    )

    await bot_module.config_command(update, context)

    assert message.text_replies
    assert "bogus" in message.text_replies[-1]
    assert "set_value" not in message.text_replies[-1]


@pytest.mark.asyncio
async def test_start_command_reports_persistence_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    message = DummyMessage()
    update = DummyUpdate(message)

    class FailingSecurityManager:
        def authenticate(self, **kwargs):
            raise make_persistence_error("upsert_session")

    context = DummyContext(
        args=["bot-password"],
        bot_data={bot_module.SECURITY_MANAGER_KEY: FailingSecurityManager()},
    )

    async def fake_to_thread(func, /, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(bot_module.asyncio, "to_thread", fake_to_thread)
    monkeypatch.setattr(bot_module, "_security_manager", lambda _context: context.bot_data[bot_module.SECURITY_MANAGER_KEY])

    await bot_module.start_command(update, context)

    assert message.text_replies[-1] == "Authentication failed because the session could not be saved."


@pytest.mark.asyncio
async def test_config_command_uses_to_thread_for_scan_interval_reschedule(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    message = DummyMessage()
    update = DummyUpdate(message)
    runtime_config = FakeRuntimeConfigManager()
    scheduler = FakeScheduler()
    context = DummyContext(
        args=["scan_interval", "17"],
        bot_data={
            bot_module.SECURITY_MANAGER_KEY: FakeSecurityManager(),
            bot_module.RUNTIME_CONFIG_MANAGER_KEY: runtime_config,
            SCHEDULER_KEY: scheduler,
        },
    )

    calls: list[str] = []

    async def fake_to_thread(func, /, *args, **kwargs):
        calls.append(func.__name__)
        return func(*args, **kwargs)

    monkeypatch.setattr(bot_module.asyncio, "to_thread", fake_to_thread)
    monkeypatch.setattr(bot_module, "_security_manager", lambda _context: context.bot_data[bot_module.SECURITY_MANAGER_KEY])

    await bot_module.config_command(update, context)

    assert calls == [
        "get_session",
        "touch_for_chat",
        "set_value",
        "effective_scan_interval_minutes",
        "reschedule_auto_scan",
    ]
    assert runtime_config.set_value_calls == [(RuntimeConfigKey.SCAN_INTERVAL, "17")]
    assert scheduler.calls == [15]
    assert message.text_replies[-1] == "Config updated: scan_interval=17"


@pytest.mark.asyncio
async def test_config_command_reports_persistence_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    message = DummyMessage()
    update = DummyUpdate(message)

    class FailingRuntimeConfigManager(FakeRuntimeConfigManager):
        def set_value(self, key: RuntimeConfigKey, value: object):
            raise make_persistence_error("upsert_runtime_config")

    context = DummyContext(
        args=["scan_interval", "17"],
        bot_data={
            bot_module.SECURITY_MANAGER_KEY: FakeSecurityManager(),
            bot_module.RUNTIME_CONFIG_MANAGER_KEY: FailingRuntimeConfigManager(),
            SCHEDULER_KEY: FakeScheduler(),
        },
    )

    async def fake_to_thread(func, /, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(bot_module.asyncio, "to_thread", fake_to_thread)
    monkeypatch.setattr(bot_module, "_security_manager", lambda _context: context.bot_data[bot_module.SECURITY_MANAGER_KEY])

    await bot_module.config_command(update, context)

    assert message.text_replies[-1] == "Config update failed because the change could not be saved."


@pytest.mark.asyncio
async def test_config_command_lists_stage16_runtime_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    message = DummyMessage()
    update = DummyUpdate(message)
    runtime_config = FakeRuntimeConfigManager()
    security = FakeSecurityManager()
    context = DummyContext(
        bot_data={
            bot_module.SECURITY_MANAGER_KEY: security,
            bot_module.RUNTIME_CONFIG_MANAGER_KEY: runtime_config,
        },
    )

    async def fake_to_thread(func, /, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(bot_module.asyncio, "to_thread", fake_to_thread)
    monkeypatch.setattr(bot_module, "_security_manager", lambda _context: security)

    await bot_module.config_command(update, context)

    output = message.text_replies[-1]
    assert "chart_mode: balanced" in output
    assert "trade_push: on" in output
    assert "session_alerts: on" in output


@pytest.mark.asyncio
async def test_chart_command_uses_to_thread_and_sends_document(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    message = DummyMessage()
    update = DummyUpdate(message)
    artifact_path = tmp_path / "chart.png"
    renderer = FakeRenderer(artifact_path)
    request = SimpleNamespace(instrument="XAU_USD", timeframe="H1", style=ChartRenderStyle.CANDLESTICK)
    runtime_config = FakeRuntimeConfigManager()
    security = FakeSecurityManager()
    context = DummyContext(
        args=["gold", "1h"],
        bot_data={
            bot_module.SECURITY_MANAGER_KEY: security,
            bot_module.CHART_RENDERER_KEY: renderer,
            bot_module.RUNTIME_CONFIG_MANAGER_KEY: runtime_config,
        },
    )

    calls: list[str] = []

    async def fake_to_thread(func, /, *args, **kwargs):
        calls.append(func.__name__)
        return func(*args, **kwargs)

    monkeypatch.setattr(bot_module.asyncio, "to_thread", fake_to_thread)
    monkeypatch.setattr(bot_module, "_security_manager", lambda _context: security)
    monkeypatch.setattr(bot_module, "_runtime_config_manager", lambda _context: runtime_config)
    monkeypatch.setattr(
        bot_module,
        "parse_chart_args",
        lambda args, default_style, default_mode: request,
    )

    await bot_module.chart_command(update, context)

    assert calls == [
        "get_session",
        "touch_for_chat",
        "effective_chart_style",
        "effective_chart_mode",
        "render",
    ]
    assert renderer.calls == [request]
    assert len(message.documents) == 1
    sent_document, sent_filename, sent_kwargs = message.documents[0]
    assert Path(sent_document.name).name == "chart.png"
    assert sent_filename == "chart.png"
    assert sent_kwargs == {}


@pytest.mark.asyncio
async def test_chart_command_includes_warning_caption_when_present(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    message = DummyMessage()
    update = DummyUpdate(message)
    artifact_path = tmp_path / "chart.png"
    renderer = FakeRenderer(artifact_path)
    renderer.warning_text = "Warning: chart candles use cached fallback data after live fetch failed."
    request = SimpleNamespace(instrument="XAU_USD", timeframe="H1", style=ChartRenderStyle.CANDLESTICK)
    runtime_config = FakeRuntimeConfigManager()
    security = FakeSecurityManager()
    context = DummyContext(
        args=["gold", "1h"],
        bot_data={
            bot_module.SECURITY_MANAGER_KEY: security,
            bot_module.CHART_RENDERER_KEY: renderer,
            bot_module.RUNTIME_CONFIG_MANAGER_KEY: runtime_config,
        },
    )

    async def fake_to_thread(func, /, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(bot_module.asyncio, "to_thread", fake_to_thread)
    monkeypatch.setattr(bot_module, "_security_manager", lambda _context: security)
    monkeypatch.setattr(bot_module, "_runtime_config_manager", lambda _context: runtime_config)
    monkeypatch.setattr(
        bot_module,
        "parse_chart_args",
        lambda args, default_style, default_mode: request,
    )

    await bot_module.chart_command(update, context)

    assert len(message.documents) == 1
    _, _, sent_kwargs = message.documents[0]
    assert sent_kwargs == {"caption": renderer.warning_text}


@pytest.mark.asyncio
async def test_chart_command_reports_render_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    message = DummyMessage()
    update = DummyUpdate(message)
    renderer = FailingRenderer()
    request = SimpleNamespace(instrument="XAU_USD", timeframe="H1", style=ChartRenderStyle.CANDLESTICK)
    runtime_config = FakeRuntimeConfigManager()
    security = FakeSecurityManager()
    context = DummyContext(
        args=["gold", "1h"],
        bot_data={
            bot_module.SECURITY_MANAGER_KEY: security,
            bot_module.CHART_RENDERER_KEY: renderer,
            bot_module.RUNTIME_CONFIG_MANAGER_KEY: runtime_config,
        },
    )

    async def fake_to_thread(func, /, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(bot_module.asyncio, "to_thread", fake_to_thread)
    monkeypatch.setattr(bot_module, "_security_manager", lambda _context: security)
    monkeypatch.setattr(bot_module, "_runtime_config_manager", lambda _context: runtime_config)
    monkeypatch.setattr(
        bot_module,
        "parse_chart_args",
        lambda args, default_style, default_mode: request,
    )

    await bot_module.chart_command(update, context)

    assert renderer.calls == [request]
    assert message.text_replies[-1] == "Chart render failed: render exploded"


@pytest.mark.asyncio
async def test_price_command_prefers_live_stream_quote(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    message = DummyMessage()
    update = DummyUpdate(message)
    security = FakeSecurityManager()
    live_tick = PriceTick(
        instrument="XAU_USD",
        bid=3050.10,
        ask=3050.40,
        time=datetime(2026, 3, 29, 2, 0, tzinfo=timezone.utc),
    )

    class FailingAccountClient:
        async def get_pricing(self, instrument: str):
            raise AssertionError("REST pricing should not be used when a fresh live quote exists.")

    context = DummyContext(
        args=["gold", "--live"],
        bot_data={
            bot_module.SECURITY_MANAGER_KEY: security,
            bot_module.BOT_RUNTIME_KEY: SimpleNamespace(
                stream_task=SimpleNamespace(latest_quote=lambda instrument, max_age_seconds=None: live_tick)
            ),
            bot_module.ACCOUNT_CLIENT_KEY: FailingAccountClient(),
        },
    )

    calls: list[str] = []

    async def fake_to_thread(func, /, *args, **kwargs):
        calls.append(func.__name__)
        return func(*args, **kwargs)

    monkeypatch.setattr(bot_module.asyncio, "to_thread", fake_to_thread)
    monkeypatch.setattr(bot_module, "_security_manager", lambda _context: security)

    await bot_module.price_command(update, context)

    assert calls == ["get_session", "touch_for_chat"]
    assert "Source: live stream" in message.text_replies[-1]
    assert "Source: REST pricing" not in message.text_replies[-1]


@pytest.mark.asyncio
async def test_price_command_falls_back_to_rest_when_live_quote_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    message = DummyMessage()
    update = DummyUpdate(message)
    security = FakeSecurityManager()

    class RecordingAccountClient:
        def __init__(self) -> None:
            self.calls: list[str] = []

        async def get_pricing(self, instrument: str):
            self.calls.append(instrument)
            return SimpleNamespace(
                bid=3049.90,
                ask=3050.20,
                spread_pips=30.0,
                fetched_at=datetime(2026, 3, 29, 2, 1, tzinfo=timezone.utc),
            )

    account_client = RecordingAccountClient()
    context = DummyContext(
        args=["gold", "--live"],
        bot_data={
            bot_module.SECURITY_MANAGER_KEY: security,
            bot_module.BOT_RUNTIME_KEY: SimpleNamespace(
                stream_task=SimpleNamespace(latest_quote=lambda instrument, max_age_seconds=None: None)
            ),
            bot_module.ACCOUNT_CLIENT_KEY: account_client,
        },
    )

    async def fake_to_thread(func, /, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(bot_module.asyncio, "to_thread", fake_to_thread)
    monkeypatch.setattr(bot_module, "_security_manager", lambda _context: security)

    await bot_module.price_command(update, context)

    assert account_client.calls == ["XAU_USD"]
    assert "Source: live stream unavailable or stale, falling back to REST." in message.text_replies[-1]
    assert "Source: REST pricing" in message.text_replies[-1]


@pytest.mark.asyncio
async def test_positions_command_includes_account_currency_pnl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    message = DummyMessage()
    update = DummyUpdate(message)
    security = FakeSecurityManager()

    class RecordingAccountClient:
        async def get_open_positions(self):
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
                    opened_at=datetime(2026, 3, 29, 2, 0, tzinfo=timezone.utc),
                    direction="LONG",
                )
            ]

        async def get_pricing(self, instrument: str):
            assert instrument == "EUR_USD"
            return SimpleNamespace(
                bid=1.1025,
                ask=1.1027,
                fetched_at=datetime(2026, 3, 29, 2, 1, tzinfo=timezone.utc),
            )

    context = DummyContext(
        args=[],
        bot_data={
            bot_module.SECURITY_MANAGER_KEY: security,
            bot_module.ACCOUNT_CLIENT_KEY: RecordingAccountClient(),
            bot_module.SETTINGS_KEY: SimpleNamespace(account_currency="USD"),
        },
    )

    async def fake_to_thread(func, /, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(bot_module.asyncio, "to_thread", fake_to_thread)
    monkeypatch.setattr(bot_module, "_security_manager", lambda _context: security)

    await bot_module.positions_command(update, context)

    assert "Open Trades" in message.text_replies[-1]
    assert "pnl=+2.50 USD" in message.text_replies[-1]


@pytest.mark.asyncio
async def test_tradeplan_command_uses_published_bundle_and_snapshots(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    message = DummyMessage()
    update = DummyUpdate(message)
    security = FakeSecurityManager()
    context = DummyContext(
        args=["gold"],
        bot_data={bot_module.SECURITY_MANAGER_KEY: security},
    )
    calls: list[tuple[str, str]] = []
    bundle = SimpleNamespace(
        mixed_freshness=False,
        instrument="XAU_USD",
        members={"H1": 1, "M15": 1},
    )
    h1_snapshot = SimpleNamespace(timeframe="H1", freshness=SimpleNamespace(is_fresh=True))
    m15_snapshot = SimpleNamespace(timeframe="M15", freshness=SimpleNamespace(is_fresh=True))

    async def fake_to_thread(func, /, *args, **kwargs):
        return func(*args, **kwargs)

    async def fake_bundle_first(update, context, instrument):
        calls.append(("bundle", instrument))
        return bundle

    def fake_bundle_snapshot(context, resolved_bundle, timeframe):
        calls.append(("snapshot", timeframe))
        assert resolved_bundle is bundle
        return h1_snapshot if timeframe == "H1" else m15_snapshot

    monkeypatch.setattr(bot_module.asyncio, "to_thread", fake_to_thread)
    monkeypatch.setattr(bot_module, "_security_manager", lambda _context: security)
    monkeypatch.setattr(bot_module, "_bundle_first", fake_bundle_first)
    monkeypatch.setattr(bot_module, "_bundle_snapshot", fake_bundle_snapshot)
    monkeypatch.setattr(
        bot_module,
        "build_trade_plan",
        lambda **kwargs: SimpleNamespace(summary="ok", kwargs=kwargs),
    )
    monkeypatch.setattr(bot_module, "format_trade_plan", lambda summary: "tradeplan output")

    await bot_module.tradeplan_command(update, context)

    assert calls == [("bundle", "XAU_USD"), ("snapshot", "H1"), ("snapshot", "M15")]
    assert message.text_replies[-1] == "tradeplan output"


@pytest.mark.asyncio
async def test_fib_command_uses_snapshot_first_and_formatter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    message = DummyMessage()
    update = DummyUpdate(message)
    security = FakeSecurityManager()
    context = DummyContext(
        args=["gold", "1h"],
        bot_data={bot_module.SECURITY_MANAGER_KEY: security},
    )

    async def fake_to_thread(func, /, *args, **kwargs):
        return func(*args, **kwargs)

    async def fake_snapshot_first(update, context, instrument, timeframe):
        assert instrument == "XAU_USD"
        assert timeframe == "H1"
        return SimpleNamespace(freshness=SimpleNamespace(is_fresh=True))

    monkeypatch.setattr(bot_module.asyncio, "to_thread", fake_to_thread)
    monkeypatch.setattr(bot_module, "_security_manager", lambda _context: security)
    monkeypatch.setattr(bot_module, "_snapshot_first", fake_snapshot_first)
    monkeypatch.setattr(bot_module, "build_fib_summary", lambda snapshot: SimpleNamespace(fib="ok"))
    monkeypatch.setattr(bot_module, "format_fib_summary", lambda summary: "fib output")

    await bot_module.fib_command(update, context)

    assert message.text_replies[-1] == "fib output"


@pytest.mark.asyncio
async def test_time_alert_command_saves_chat_scoped_alert(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    message = DummyMessage()
    update = DummyUpdate(message)
    security = FakeSecurityManager()

    class RecordingAlertRepository:
        def __init__(self) -> None:
            self.payloads: list[dict[str, object]] = []

        def upsert_time_alert(self, payload):
            self.payloads.append(dict(payload))
            return SimpleNamespace(id=7, session_name=None, local_time=payload["local_time"])

    repository = RecordingAlertRepository()
    context = DummyContext(
        args=["at", "09:30", "daily", "london", "prep"],
        bot_data={
            bot_module.SECURITY_MANAGER_KEY: security,
            bot_module.ALERT_REPOSITORY_KEY: repository,
        },
    )

    async def fake_to_thread(func, /, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(bot_module.asyncio, "to_thread", fake_to_thread)
    monkeypatch.setattr(bot_module, "_security_manager", lambda _context: security)

    await bot_module.time_alert_command(update, context)

    created = repository.payloads[-1]
    assert created["chat_id"] == 2002
    assert created["kind"] == TimeAlertKind.FIXED_TIME
    assert created["schedule"] == "daily"
    assert created["local_time"] == "09:30"
    assert created["status"] == "ACTIVE"
    assert created["timezone_name"] == "Asia/Singapore"
    assert created["next_fire_at"] is not None
    assert message.text_replies[-1] == "Time alert #7 created for 09:30."


@pytest.mark.asyncio
async def test_list_and_clear_time_alert_commands_use_chat_scoped_repository(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    message = DummyMessage()
    update = DummyUpdate(message)
    security = FakeSecurityManager()
    active_alert = TimeAlert(
        id=5,
        chat_id=2002,
        kind=TimeAlertKind.FIXED_TIME,
        status=TimeAlertStatus.ACTIVE,
        schedule="daily",
        timezone_name="Asia/Singapore",
        local_time="09:30",
        session_name=None,
        note="london prep",
        created_at=datetime(2026, 3, 29, 0, 0, tzinfo=timezone.utc),
        next_fire_at=datetime(2026, 3, 29, 1, 30, tzinfo=timezone.utc),
        last_fired_at=None,
    )

    class RecordingAlertRepository:
        def __init__(self) -> None:
            self.list_chat_ids: list[int] = []
            self.clear_calls: list[tuple[int, int]] = []

        def list_active_time_alerts_for_chat(self, chat_id: int):
            self.list_chat_ids.append(chat_id)
            return [active_alert]

        def cancel_time_alert_for_chat(self, alert_id: int, chat_id: int):
            self.clear_calls.append((alert_id, chat_id))
            return active_alert

    repository = RecordingAlertRepository()
    context = DummyContext(
        args=[],
        bot_data={
            bot_module.SECURITY_MANAGER_KEY: security,
            bot_module.ALERT_REPOSITORY_KEY: repository,
        },
    )

    async def fake_to_thread(func, /, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(bot_module.asyncio, "to_thread", fake_to_thread)
    monkeypatch.setattr(bot_module, "_security_manager", lambda _context: security)

    await bot_module.list_time_alerts_command(update, context)
    assert repository.list_chat_ids == [2002]
    assert "Active Time Alerts" in message.text_replies[-1]
    assert "#5" in message.text_replies[-1]

    clear_context = DummyContext(
        args=["5"],
        bot_data=context.bot_data,
    )
    await bot_module.clear_time_alert_command(update, clear_context)

    assert repository.clear_calls == [(5, 2002)]
    assert message.text_replies[-1] == "Time alert 5 cleared."


@pytest.mark.asyncio
async def test_tradehistory_command_uses_service_and_formats_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    message = DummyMessage()
    update = DummyUpdate(message)
    security = FakeSecurityManager()
    trade_history_service = FakeTradeHistoryService()
    context = DummyContext(
        args=["month", "closed", "XAU_USD", "2"],
        bot_data={
            bot_module.SECURITY_MANAGER_KEY: security,
            bot_module.TRADE_HISTORY_SERVICE_KEY: trade_history_service,
        },
    )

    async def fake_to_thread(func, /, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(bot_module.asyncio, "to_thread", fake_to_thread)
    monkeypatch.setattr(bot_module, "_security_manager", lambda _context: security)

    await bot_module.tradehistory_command(update, context)

    assert trade_history_service.trade_history_calls == [("month", "closed", "XAU_USD", 2)]
    assert "Trade History - MONTH - CLOSED - XAU_USD" in message.text_replies[-1]
    assert "Gross Realized PnL: +12.50" in message.text_replies[-1]
    assert "Page 1/1" in message.text_replies[-1]


@pytest.mark.asyncio
async def test_tradehistory_backfill_command_requires_admin_and_uses_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    message = DummyMessage()
    update = DummyUpdate(message)
    trade_history_service = FakeTradeHistoryService()

    class AdminSecurityManager(FakeSecurityManager):
        def is_admin(self, user_id: int) -> bool:
            return True

    security = AdminSecurityManager()
    security.session.is_admin = True
    context = DummyContext(
        args=["2025-01-01", "2026-04-01"],
        bot_data={
            bot_module.SECURITY_MANAGER_KEY: security,
            bot_module.TRADE_HISTORY_SERVICE_KEY: trade_history_service,
        },
    )

    async def fake_to_thread(func, /, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(bot_module.asyncio, "to_thread", fake_to_thread)
    monkeypatch.setattr(bot_module, "_security_manager", lambda _context: security)

    await bot_module.tradehistory_backfill_command(update, context)

    assert trade_history_service.backfill_calls
    assert "Trade history backfill complete." in message.text_replies[-1]
    assert "Projected TradeRecord rows: 3" in message.text_replies[-1]


@pytest.mark.asyncio
async def test_tradehistory_backfill_command_rejects_non_admin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    message = DummyMessage()
    update = DummyUpdate(message)
    security = FakeSecurityManager()
    context = DummyContext(
        args=["2025-01-01", "2026-04-01"],
        bot_data={
            bot_module.SECURITY_MANAGER_KEY: security,
            bot_module.TRADE_HISTORY_SERVICE_KEY: FakeTradeHistoryService(),
        },
    )

    async def fake_to_thread(func, /, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(bot_module.asyncio, "to_thread", fake_to_thread)
    monkeypatch.setattr(bot_module, "_security_manager", lambda _context: security)

    await bot_module.tradehistory_backfill_command(update, context)

    assert message.text_replies[-1] == "Admin access required."


@pytest.mark.asyncio
async def test_label_command_reports_persistence_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    message = DummyMessage()
    update = DummyUpdate(message)

    class FailingTradeRepository:
        def set_notes(self, trade_id: str, note: str):
            raise make_persistence_error("upsert_trade")

    context = DummyContext(
        args=["trade-1", "runner"],
        bot_data={
            bot_module.SECURITY_MANAGER_KEY: FakeSecurityManager(),
            bot_module.TRADE_REPOSITORY_KEY: FailingTradeRepository(),
        },
    )

    async def fake_to_thread(func, /, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(bot_module.asyncio, "to_thread", fake_to_thread)
    monkeypatch.setattr(bot_module, "_security_manager", lambda _context: context.bot_data[bot_module.SECURITY_MANAGER_KEY])

    await bot_module.label_command(update, context)

    assert message.text_replies[-1] == "Label update failed because the trade note could not be saved."


@pytest.mark.asyncio
async def test_price_alert_command_reports_persistence_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    message = DummyMessage()
    update = DummyUpdate(message)

    class FailingAlertRepository:
        def upsert_price_alert(self, payload):
            raise make_persistence_error("upsert_price_alert")

    context = DummyContext(
        args=["gold", "3050", "above"],
        bot_data={
            bot_module.SECURITY_MANAGER_KEY: FakeSecurityManager(),
            bot_module.ALERT_REPOSITORY_KEY: FailingAlertRepository(),
        },
    )

    async def fake_to_thread(func, /, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(bot_module.asyncio, "to_thread", fake_to_thread)
    monkeypatch.setattr(bot_module, "_security_manager", lambda _context: context.bot_data[bot_module.SECURITY_MANAGER_KEY])

    await bot_module.price_alert_command(update, context)

    assert message.text_replies[-1] == "Price alert could not be saved."


@pytest.mark.asyncio
async def test_clear_price_alert_command_reports_persistence_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    message = DummyMessage()
    update = DummyUpdate(message)

    class FailingAlertRepository:
        def cancel_price_alert_for_chat(self, alert_id: int, chat_id: int):
            raise make_persistence_error("cancel_price_alert")

    context = DummyContext(
        args=["7"],
        bot_data={
            bot_module.SECURITY_MANAGER_KEY: FakeSecurityManager(),
            bot_module.ALERT_REPOSITORY_KEY: FailingAlertRepository(),
        },
    )

    async def fake_to_thread(func, /, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(bot_module.asyncio, "to_thread", fake_to_thread)
    monkeypatch.setattr(bot_module, "_security_manager", lambda _context: context.bot_data[bot_module.SECURITY_MANAGER_KEY])

    await bot_module.clear_price_alert_command(update, context)

    assert (
        message.text_replies[-1]
        == "Price alert 7 could not be cleared because the change could not be saved."
    )


AUTH_GATED_COMMANDS = tuple(command for command in bot_module.COMMAND_REGISTRY if command != "start")


@pytest.mark.asyncio
@pytest.mark.parametrize("command_name", AUTH_GATED_COMMANDS)
async def test_all_registered_commands_reject_when_no_session(
    monkeypatch: pytest.MonkeyPatch,
    command_name: str,
) -> None:
    handlers = registered_command_handlers()
    message = DummyMessage()
    update = DummyUpdate(message)
    security = FakeSecurityManager()
    security.session = None
    context = DummyContext(
        args=[],
        bot_data={bot_module.SECURITY_MANAGER_KEY: security},
    )

    async def fake_to_thread(func, /, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(bot_module.asyncio, "to_thread", fake_to_thread)
    monkeypatch.setattr(bot_module, "_security_manager", lambda _context: security)

    await handlers[command_name](update, context)

    assert message.text_replies[-1] == bot_module.AUTH_REQUIRED_TEXT


@pytest.mark.asyncio
@pytest.mark.parametrize("command_name", AUTH_GATED_COMMANDS)
async def test_all_registered_commands_reject_when_chat_scope_mismatches(
    monkeypatch: pytest.MonkeyPatch,
    command_name: str,
) -> None:
    handlers = registered_command_handlers()
    message = DummyMessage()
    update = DummyUpdate(message)
    session = DummySession()
    session.chat_id = 9999
    security = FakeSecurityManager(session=session)
    context = DummyContext(
        args=[],
        bot_data={bot_module.SECURITY_MANAGER_KEY: security},
    )

    async def fake_to_thread(func, /, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(bot_module.asyncio, "to_thread", fake_to_thread)
    monkeypatch.setattr(bot_module, "_security_manager", lambda _context: security)

    await handlers[command_name](update, context)

    assert message.text_replies[-1] == bot_module.CHAT_SCOPE_TEXT


def test_help_text_lists_every_registered_command() -> None:
    help_commands = {
        match.group(1)
        for line in (
            bot_module._build_help_text(is_admin=False).splitlines()
            + bot_module._build_help_text(is_admin=True).splitlines()
        )
        if (match := re.match(r"^\s*/([a-z_]+)\b", line))
    }

    missing = sorted(set(registered_command_handlers()) - help_commands)

    assert not missing, f"/help is missing registered commands: {', '.join(missing)}"
