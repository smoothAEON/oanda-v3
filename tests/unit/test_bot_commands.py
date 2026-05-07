from __future__ import annotations

import json
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
    parse_order_block_args,
    parse_price_args,
    parse_time_alert_args,
    parse_vwap_args,
)
from bot.runtime import MARKET_DATA_PROVIDER_KEY, SCHEDULER_KEY
from core.enums import ChartMode, ChartRenderStyle, IndicatorKind, RuntimeConfigKey, TimeAlertKind, TimeAlertStatus
from core.events import PriceTick
from core.models import (
    ActiveZoneSummary,
    OrderBlockSummary,
    RealizedPnLSummary,
    TimeAlert,
    TradeHistoryEvent,
    TradeHistoryPage,
)
from data.persistence.trade_store import PersistenceWriteError
import pandas as pd


class DummyMessage:
    def __init__(self) -> None:
        self.text_replies: list[str] = []
        self.documents: list[tuple[object, str | None, dict[str, object]]] = []
        self.reply_to_message = None

    async def reply_text(self, text: str, **kwargs) -> None:
        self.text_replies.append(text)

    async def reply_document(self, document, filename: str | None = None, **kwargs) -> None:  # type: ignore[no-untyped-def]
        self.documents.append((document, filename, dict(kwargs)))


class DummyDownloadedFile:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload

    async def download_as_bytearray(self) -> bytearray:
        return bytearray(self.payload)


class DummyDocument:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload

    async def get_file(self) -> DummyDownloadedFile:
        return DummyDownloadedFile(self.payload)


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


class FakeMarketState:
    def __init__(self, snapshot: object | None) -> None:
        self.snapshot = snapshot
        self.calls: list[tuple[str, str]] = []

    def get_snapshot(self, instrument: str, timeframe: str):
        self.calls.append((instrument, timeframe))
        return self.snapshot


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
                    instrument=instrument or "SPX500_USD",
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
            page=page,
            page_size=20,
            total_rows=42,
            total_pages=3,
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


def test_removed_analysis_commands_are_not_registered_or_listed_in_help() -> None:
    removed = {"bias", "tradeplan", "sfp", "turtlesoup", "sr", "fib"}
    handlers = registered_command_handlers()
    help_text = bot_module._build_help_text(is_admin=True)

    assert removed.isdisjoint(handlers)
    assert all(f"/{command}" not in help_text for command in removed)


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
        ("spx500usd", "SPX500_USD"),
        ("EUR/USD", "EUR_USD"),
        ("usd jpy", "USD_JPY"),
    ],
)
def test_normalize_command_instrument_accepts_aliases(value: str, expected: str) -> None:
    assert normalize_command_instrument(value) == expected


def test_normalize_command_instrument_accepts_oil_alias() -> None:
    assert normalize_command_instrument("oil") == "WTICO_USD"


@pytest.mark.parametrize("value", ["ZZZ_YYY", "not_an_instrument"])
def test_normalize_command_instrument_rejects_unknown_values(value: str) -> None:
    with pytest.raises(ValueError, match="instrument"):
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


@pytest.mark.parametrize(
    ("args", "default_mode", "expected_timeframe", "expected_mode", "expected_count"),
    [
        (["spx500usd"], ChartMode.COMPACT, "H1", ChartMode.COMPACT, 500),
        (["spx500usd", "4h"], ChartMode.BALANCED, "H4", ChartMode.BALANCED, 500),
        (["spx500usd", "full"], ChartMode.COMPACT, "H1", ChartMode.FULL, 500),
        (["spx500usd", "4h", "full"], ChartMode.COMPACT, "H4", ChartMode.FULL, 500),
        (["spx500usd", "4h", "250"], ChartMode.BALANCED, "H4", ChartMode.BALANCED, 250),
        (["spx500usd", "4h", "full", "250"], ChartMode.COMPACT, "H4", ChartMode.FULL, 250),
        (["spx500usd", "4h", "250", "full"], ChartMode.COMPACT, "H4", ChartMode.FULL, 250),
    ],
)
def test_parse_chart_args_accepts_positional_mode_and_count(
    args: list[str],
    default_mode: ChartMode,
    expected_timeframe: str,
    expected_mode: ChartMode,
    expected_count: int,
) -> None:
    request = parse_chart_args(
        args,
        default_style=ChartRenderStyle.LINE,
        default_mode=default_mode,
    )

    assert request.instrument == "SPX500_USD"
    assert request.timeframe == expected_timeframe
    assert request.mode == expected_mode
    assert request.count == expected_count
    assert request.style == ChartRenderStyle.LINE


def test_parse_chart_args_full_mode_keeps_full_overlay_bundle() -> None:
    request = parse_chart_args(
        ["spx500usd", "1h", "full", "250"],
        default_style=ChartRenderStyle.CANDLESTICK,
    )

    assert request.selection.smc == ("orderblocks", "structure", "liquidity")
    assert request.selection.trade == ("positions", "orders", "sl", "tp", "gslo")
    assert request.selection.alert == ("pricealerts",)
    assert request.selection.indicator == ("ema", "bollinger", "vwap", "rsi", "macd")


def test_parse_price_args_accepts_live_flag_and_rejects_multiple_symbols() -> None:
    assert parse_price_args(["spx500usd"]) == ("SPX500_USD", False)
    assert parse_price_args(["--live", "spx500usd"]) == ("SPX500_USD", True)

    with pytest.raises(ValueError, match="Usage: /price <symbol> \\[--live\\]"):
        parse_price_args(["spx500usd", "eurusd"])


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
    assert parse_time_alert_args(["at", "2026-04-10", "09:30", "once", "CPI", "prep"]) == (
        "at",
        "once",
        "2026-04-10 09:30",
        None,
        "CPI prep",
    )

    with pytest.raises(ValueError, match="Session alerts support"):
        parse_time_alert_args(["session", "tokyo"])
    with pytest.raises(ValueError, match="once only"):
        parse_time_alert_args(["at", "2026-04-10", "09:30", "daily"])


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
        ["spx500usd", "1h", "rsi", "above", "30", "trend", "watch"]
    )

    assert instrument == "SPX500_USD"
    assert timeframe == "H1"
    assert indicator == IndicatorKind.RSI
    assert condition == "above"
    assert threshold == pytest.approx(30.0)
    assert note == "trend watch"


def test_parse_vwap_args_parses_defaults_and_flags() -> None:
    instrument, timeframe, anchor, bands = parse_vwap_args(
        ["spx500usd", "4h", "--anchor", "weekly", "--bands", "2,1"]
    )

    assert instrument == "SPX500_USD"
    assert timeframe == "H4"
    assert anchor == "W"
    assert bands == (1.0, 2.0)


def test_parse_vwap_args_rejects_unsupported_lower_timeframe() -> None:
    with pytest.raises(ValueError, match="VWAP timeframe"):
        parse_vwap_args(["spx500usd", "15m"])


def test_parse_vwap_args_rejects_invalid_anchor() -> None:
    with pytest.raises(ValueError, match="anchor must be"):
        parse_vwap_args(["spx500usd", "--anchor", "quarterly"])


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (["spx500usd"], ("SPX500_USD", "H1", "all")),
        (["spx500usd", "4h"], ("SPX500_USD", "H4", "all")),
        (["spx500usd", "mitigated"], ("SPX500_USD", "H1", "mitigated")),
        (["spx500usd", "unmitigated"], ("SPX500_USD", "H1", "unmitigated")),
        (["spx500usd", "miltigated"], ("SPX500_USD", "H1", "mitigated")),
        (["spx500usd", "unmiltigated"], ("SPX500_USD", "H1", "unmitigated")),
        (["spx500usd", "4h", "unmitigated"], ("SPX500_USD", "H4", "unmitigated")),
        (["spx500usd", "mitigated", "4h"], ("SPX500_USD", "H4", "mitigated")),
    ],
)
def test_parse_order_block_args_accepts_status_filter_and_timeframe(
    args: list[str],
    expected: tuple[str, str, str],
) -> None:
    assert parse_order_block_args(args) == expected


@pytest.mark.parametrize(
    ("args", "expected_match"),
    [
        ([], "Usage: /ob"),
        (["spx500usd", "mitigated", "unmitigated"], "may only be provided once"),
        (["spx500usd", "H1", "H4"], "timeframe may only be provided once"),
        (["spx500usd", "inactive"], "Usage: /ob"),
    ],
)
def test_parse_order_block_args_rejects_invalid_optional_tokens(
    args: list[str],
    expected_match: str,
) -> None:
    with pytest.raises(ValueError, match=expected_match):
        parse_order_block_args(args)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("filter_arg", "expected_status", "unexpected_status"),
    [
        ("mitigated", "MITIGATED BULLISH", "UNMITIGATED BEARISH"),
        ("unmitigated", "UNMITIGATED BEARISH", "MITIGATED BULLISH"),
    ],
)
async def test_order_blocks_command_filters_by_mitigation_status(
    monkeypatch: pytest.MonkeyPatch,
    filter_arg: str,
    expected_status: str,
    unexpected_status: str,
) -> None:
    message = DummyMessage()
    update = DummyUpdate(message)
    security = FakeSecurityManager()
    snapshot = SimpleNamespace(
        zones=ActiveZoneSummary(
            order_blocks=(
                OrderBlockSummary(
                    direction="BULLISH",
                    upper_price=1.1050,
                    lower_price=1.1000,
                    is_mitigated=True,
                ),
                OrderBlockSummary(
                    direction="BEARISH",
                    upper_price=1.0950,
                    lower_price=1.0900,
                    is_mitigated=False,
                ),
            )
        ),
        freshness=SimpleNamespace(is_fresh=True),
    )
    market_state = FakeMarketState(snapshot)
    context = DummyContext(
        args=["spx500usd", filter_arg],
        bot_data={
            bot_module.SECURITY_MANAGER_KEY: security,
            bot_module.MARKET_STATE_KEY: market_state,
        },
    )

    async def fake_to_thread(func, /, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(bot_module.asyncio, "to_thread", fake_to_thread)
    monkeypatch.setattr(bot_module, "_security_manager", lambda _context: security)

    await bot_module.order_blocks_command(update, context)

    output = message.text_replies[-1]
    assert market_state.calls == [("SPX500_USD", "H1")]
    assert f"Filter: {filter_arg} (all=2, mitigated=1, unmitigated=1)" in output
    assert expected_status in output
    assert unexpected_status not in output
    assert "mitigated=True" not in output


@pytest.mark.parametrize(
    ("args", "expected_match"),
    [
        (["spx500usd", "1h", "--mode", "full"], "Unsupported chart option '--mode'"),
        (["spx500usd", "1h", "--count", "250"], "Unsupported chart option '--count'"),
        (["spx500usd", "1h", "--smc", "orderblocks"], "Unsupported chart option '--smc'"),
        (["spx500usd", "1h", "full", "balanced"], "Chart mode may only be provided once"),
        (["spx500usd", "1h", "250", "300"], "Chart count may only be provided once"),
        (["spx500usd", "1h", "full", "notes"], "Unsupported chart option 'notes'"),
        (["spx500usd", "1h", "1"], "Chart count must be between 2 and 5000"),
        (["spx500usd", "1h", "5001"], "Chart count must be between 2 and 5000"),
    ],
)
def test_parse_chart_args_rejects_legacy_flags_and_invalid_tokens(
    args: list[str],
    expected_match: str,
) -> None:
    with pytest.raises(ValueError, match=expected_match):
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
    request = SimpleNamespace(instrument="SPX500_USD", timeframe="H1", style=ChartRenderStyle.CANDLESTICK)
    runtime_config = FakeRuntimeConfigManager()
    security = FakeSecurityManager()
    context = DummyContext(
        args=["spx500usd", "1h"],
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
    request = SimpleNamespace(instrument="SPX500_USD", timeframe="H1", style=ChartRenderStyle.CANDLESTICK)
    runtime_config = FakeRuntimeConfigManager()
    security = FakeSecurityManager()
    context = DummyContext(
        args=["spx500usd", "1h"],
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
    request = SimpleNamespace(instrument="SPX500_USD", timeframe="H1", style=ChartRenderStyle.CANDLESTICK)
    runtime_config = FakeRuntimeConfigManager()
    security = FakeSecurityManager()
    context = DummyContext(
        args=["spx500usd", "1h"],
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
        instrument="SPX500_USD",
        bid=3050.10,
        ask=3050.40,
        time=datetime(2026, 3, 29, 2, 0, tzinfo=timezone.utc),
    )

    class FailingAccountClient:
        async def get_pricing(self, instrument: str):
            raise AssertionError("REST pricing should not be used when a fresh live quote exists.")

    context = DummyContext(
        args=["spx500usd", "--live"],
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
        args=["spx500usd", "--live"],
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

    assert account_client.calls == ["SPX500_USD"]
    assert "Source: live stream unavailable or stale, falling back to REST." in message.text_replies[-1]
    assert "Source: REST pricing" in message.text_replies[-1]


@pytest.mark.asyncio
async def test_vwap_command_renders_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    message = DummyMessage()
    update = DummyUpdate(message)
    security = FakeSecurityManager()
    frame = pd.DataFrame(
        {
            "time": pd.to_datetime(
                [
                    datetime(2026, 3, 29, 9, 0, tzinfo=timezone.utc),
                    datetime(2026, 3, 29, 10, 0, tzinfo=timezone.utc),
                    datetime(2026, 3, 29, 11, 0, tzinfo=timezone.utc),
                    datetime(2026, 3, 29, 12, 0, tzinfo=timezone.utc),
                ],
                utc=True,
            ),
            "open": [3048.0, 3049.0, 3050.0, 3051.0],
            "high": [3049.0, 3050.0, 3051.0, 3052.0],
            "low": [3047.5, 3048.5, 3049.5, 3050.5],
            "close": [3048.5, 3049.5, 3050.5, 3051.5],
            "tick_volume": [100, 110, 120, 130],
        }
    )

    class FakeMarketDataProvider:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str, int]] = []

        def get_candles(self, instrument: str, timeframe: str, count: int):
            self.calls.append((instrument, timeframe, count))
            return frame

        def get_candle_freshness(self, instrument: str, timeframe: str):
            return SimpleNamespace(source="oanda_api")

    provider = FakeMarketDataProvider()
    context = DummyContext(
        args=["spx500usd", "h1", "--anchor", "daily", "--bands", "2,1"],
        bot_data={
            bot_module.SECURITY_MANAGER_KEY: security,
            MARKET_DATA_PROVIDER_KEY: provider,
        },
    )

    async def fake_to_thread(func, /, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(bot_module.asyncio, "to_thread", fake_to_thread)
    monkeypatch.setattr(bot_module, "_security_manager", lambda _context: security)

    await bot_module.vwap_command(update, context)

    assert provider.calls
    assert "VWAP SPX500_USD H1" in message.text_replies[-1]
    assert "Anchor: D (daily)" in message.text_replies[-1]
    assert "Bands:" in message.text_replies[-1]
    assert "Caveat:" in message.text_replies[-1]


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

    assert "Open Trades (1)" in message.text_replies[-1]
    assert "Unrealized P/L:  +2.50 USD" in message.text_replies[-1]
    assert "Current Mid:     1.10260" in message.text_replies[-1]
    assert "Entry:           1.10000 (-26.0p)" in message.text_replies[-1]


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
async def test_time_alert_command_accepts_dated_fixed_time(
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
            return SimpleNamespace(id=8, session_name=None, local_time=payload["local_time"])

    repository = RecordingAlertRepository()
    context = DummyContext(
        args=["at", "2027-04-10", "09:30", "once", "CPI", "prep"],
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
    assert created["schedule"] == "once"
    assert created["local_time"] == "2027-04-10 09:30"
    assert created["next_fire_at"] is not None
    assert message.text_replies[-1] == "Time alert #8 created for 2027-04-10 09:30."


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
async def test_export_time_alerts_command_sends_versioned_json_document(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    message = DummyMessage()
    update = DummyUpdate(message)
    security = FakeSecurityManager()
    alerts = [
        TimeAlert(
            id=5,
            chat_id=2002,
            kind=TimeAlertKind.FIXED_TIME,
            status=TimeAlertStatus.ACTIVE,
            schedule="daily",
            timezone_name="Asia/Singapore",
            local_time="09:30",
            session_name=None,
            note="desk prep",
            created_at=datetime(2026, 3, 29, 0, 0, tzinfo=timezone.utc),
            next_fire_at=datetime(2026, 3, 29, 1, 30, tzinfo=timezone.utc),
            last_fired_at=None,
        ),
        TimeAlert(
            id=6,
            chat_id=2002,
            kind=TimeAlertKind.FIXED_TIME,
            status=TimeAlertStatus.ACTIVE,
            schedule="once",
            timezone_name="Asia/Singapore",
            local_time="2027-04-10 09:30",
            session_name=None,
            note=None,
            created_at=datetime(2026, 3, 29, 0, 0, tzinfo=timezone.utc),
            next_fire_at=datetime(2027, 4, 10, 1, 30, tzinfo=timezone.utc),
            last_fired_at=None,
        ),
    ]

    class RecordingAlertRepository:
        def list_active_time_alerts_for_chat(self, chat_id: int):
            assert chat_id == 2002
            return alerts

    context = DummyContext(
        args=[],
        bot_data={
            bot_module.SECURITY_MANAGER_KEY: security,
            bot_module.ALERT_REPOSITORY_KEY: RecordingAlertRepository(),
        },
    )

    async def fake_to_thread(func, /, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(bot_module.asyncio, "to_thread", fake_to_thread)
    monkeypatch.setattr(bot_module, "_security_manager", lambda _context: security)

    await bot_module.export_time_alerts_command(update, context)

    assert len(message.documents) == 1
    sent_document, sent_filename, sent_kwargs = message.documents[0]
    exported = json.loads(sent_document.getvalue().decode("utf-8"))
    assert sent_filename is not None and sent_filename.endswith(".json")
    assert sent_kwargs["caption"] == "Exported 2 active time alerts."
    assert exported["schema_version"] == 1
    assert exported["timezone_name"] == "Asia/Singapore"
    assert len(exported["alerts"]) == 2
    assert exported["alerts"][1]["local_time"] == "2027-04-10 09:30"
    assert "id" not in exported["alerts"][0]


@pytest.mark.asyncio
async def test_import_time_alerts_command_merges_duplicates_and_skips_expired_dated_alerts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    message = DummyMessage()
    update = DummyUpdate(message)
    security = FakeSecurityManager()
    import_payload = {
        "schema_version": 1,
        "exported_at": "2026-04-09T00:00:00Z",
        "timezone_name": "Asia/Singapore",
        "alerts": [
            {
                "kind": "FIXED_TIME",
                "schedule": "daily",
                "timezone_name": "Asia/Singapore",
                "local_time": "09:30",
                "session_name": None,
                "note": "desk prep",
            },
            {
                "kind": "FIXED_TIME",
                "schedule": "once",
                "timezone_name": "Asia/Singapore",
                "local_time": "2027-04-10 09:30",
                "session_name": None,
                "note": "CPI",
            },
            {
                "kind": "FIXED_TIME",
                "schedule": "once",
                "timezone_name": "Asia/Singapore",
                "local_time": "2026-04-08 09:30",
                "session_name": None,
                "note": "expired",
            },
        ],
    }
    message.reply_to_message = SimpleNamespace(document=DummyDocument(json.dumps(import_payload).encode("utf-8")))

    class RecordingAlertRepository:
        def __init__(self) -> None:
            self.created_batches: list[list[dict[str, object]]] = []

        def create_time_alerts(self, payloads):
            materialized = [dict(payload) for payload in payloads]
            self.created_batches.append(materialized)
            return [
                SimpleNamespace(id=index + 1, local_time=payload["local_time"], session_name=payload["session_name"])
                for index, payload in enumerate(materialized)
            ]

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

    await bot_module.import_time_alerts_command(update, context)

    assert len(repository.created_batches) == 1
    assert len(repository.created_batches[0]) == 2
    assert repository.created_batches[0][0]["local_time"] == "09:30"
    assert repository.created_batches[0][1]["local_time"] == "2027-04-10 09:30"
    assert message.text_replies[-1] == "Imported 2 time alerts. Skipped 1 expired dated alert."


@pytest.mark.asyncio
async def test_import_time_alerts_command_rejects_invalid_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    message = DummyMessage()
    message.reply_to_message = SimpleNamespace(document=DummyDocument(b"not json"))
    update = DummyUpdate(message)
    security = FakeSecurityManager()
    context = DummyContext(
        args=[],
        bot_data={
            bot_module.SECURITY_MANAGER_KEY: security,
            bot_module.ALERT_REPOSITORY_KEY: SimpleNamespace(create_time_alerts=lambda payloads: payloads),
        },
    )

    async def fake_to_thread(func, /, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(bot_module.asyncio, "to_thread", fake_to_thread)
    monkeypatch.setattr(bot_module, "_security_manager", lambda _context: security)

    await bot_module.import_time_alerts_command(update, context)

    assert message.text_replies[-1] == "Time alert import file must be valid JSON."


@pytest.mark.asyncio
async def test_tradehistory_command_uses_service_and_formats_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    message = DummyMessage()
    update = DummyUpdate(message)
    security = FakeSecurityManager()
    trade_history_service = FakeTradeHistoryService()
    context = DummyContext(
        args=["month", "closed", "SPX500_USD", "2"],
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

    assert trade_history_service.trade_history_calls == [("month", "closed", "SPX500_USD", 2)]
    assert "Trade History - MONTH - CLOSED - SPX500_USD" in message.text_replies[-1]
    assert message.text_replies[-1].splitlines()[1] == "P&L (2026-04-01): +12.20"
    assert "Gross Realized PnL: +12.50" in message.text_replies[-1]
    assert "Page 2/3" in message.text_replies[-1]
    assert "Prev: /tradehistory month closed SPX500_USD 1" in message.text_replies[-1]
    assert "Next: /tradehistory month closed SPX500_USD 3" in message.text_replies[-1]


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
        args=["spx500usd", "3050", "above"],
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


def test_help_text_documents_order_block_filter_and_omits_stale_stage_text() -> None:
    help_text = bot_module._build_help_text(is_admin=True)

    assert "/ob <symbol> [tf] [all|mitigated|unmitigated]" in help_text
    assert "Stage 14" not in help_text
