from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from alerts.alert_repository import AlertRepository
from bot import bot as bot_module
from bot.security_manager import SecurityManager
from config.settings import load_settings
from core.enums import AlertStatus, IndicatorKind, TradeState
from core.models import CalendarRefreshStatus, ExcursionSample, TradeRecord
from data.persistence.trade_store import TradeStore
from journal.excursion_repository import ExcursionRepository
from journal.trade_repository import TradeRepository


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


class RecorderMessage:
    def __init__(self) -> None:
        self.texts: list[str] = []

    async def reply_text(self, text: str, **kwargs) -> None:  # type: ignore[no-untyped-def]
        self.texts.append(text)


class StubAccountClient:
    def __init__(self) -> None:
        self.calls = 0

    async def get_pricing(self, instrument: str):
        self.calls += 1
        return SimpleNamespace(
            instrument=instrument,
            bid=1.1025,
            ask=1.1027,
            spread_pips=2.0,
            fetched_at=BASE_TIME,
        )


def build_update(user_id: int = 111, chat_id: int = 222) -> SimpleNamespace:
    return SimpleNamespace(
        effective_message=RecorderMessage(),
        effective_user=SimpleNamespace(id=user_id, username="tester", first_name="Test"),
        effective_chat=SimpleNamespace(id=chat_id),
    )


def make_trade(*, trade_id: str, state: TradeState, notes: str | None = None) -> TradeRecord:
    payload: dict[str, object] = {
        "trade_id": trade_id,
        "instrument": "EUR_USD",
        "units": 1.0,
        "open_price": 1.1000,
        "state": state,
        "opened_at": BASE_TIME,
        "notes": notes,
    }
    if state == TradeState.CLOSED:
        payload.update(
            {
                "close_price": 1.1042,
                "sl_price": 1.0900,
                "tp_price": 1.1042,
                "gslo_price": None,
                "close_reason": "TP_HIT",
                "pips": 42.0,
                "instrument_pnl": 4.20,
                "instrument_pnl_currency": "usd",
                "account_pnl": 4.20,
                "account_currency": "usd",
                "closed_at": BASE_TIME,
            }
        )
    return TradeRecord.model_validate(payload)


@pytest.fixture()
def runtime_context(tmp_path: Path):
    env_file = write_env_file(tmp_path / ".env", tinydb_path=tmp_path / "bot.json")
    settings = load_settings(env_file=env_file)
    store = TradeStore(db_path=settings.tinydb_path, settings=settings)
    security = SecurityManager(store=store, settings=settings)
    security.authenticate(
        user_id=111,
        chat_id=222,
        password="bot-password",
        username="tester",
        first_name="Test",
    )
    trade_repository = TradeRepository(store=store)
    excursion_repository = ExcursionRepository(store=store)
    alert_repository = AlertRepository(store=store)
    account_client = StubAccountClient()
    bot_data = {
        bot_module.BOT_RUNTIME_KEY: SimpleNamespace(
            scan_orchestrator=SimpleNamespace(
                market_hours_status=SimpleNamespace(
                    is_market_open=True,
                    reason="open",
                    next_open_at=None,
                    next_close_at=BASE_TIME,
                ),
                market_hours_service=SimpleNamespace(
                    get_status=lambda: SimpleNamespace(
                        is_market_open=True,
                        reason="open",
                        next_open_at=None,
                        next_close_at=BASE_TIME,
                    )
                ),
                calendar_status=CalendarRefreshStatus(
                    last_attempted_at=BASE_TIME,
                    last_refreshed_at=BASE_TIME,
                    calendar_version=1,
                    event_count=1,
                    next_high_impact=BASE_TIME,
                    used_cached=False,
                    last_error=None,
                ),
                calendar_provider=SimpleNamespace(
                    get_upcoming_high_impact=lambda hours_ahead: (
                        SimpleNamespace(
                            event_time=BASE_TIME,
                            currency="USD",
                            title="CPI",
                            impact="HIGH",
                        ),
                    )
                ),
            ),
            stream_task=SimpleNamespace(
                stream_status=lambda: SimpleNamespace(
                    state="RUNNING",
                    reconnect_count=0,
                    last_tick_at=BASE_TIME,
                )
            ),
        ),
        bot_module.SECURITY_MANAGER_KEY: security,
        bot_module.TRADE_REPOSITORY_KEY: trade_repository,
        bot_module.EXCURSION_REPOSITORY_KEY: excursion_repository,
        bot_module.ALERT_REPOSITORY_KEY: alert_repository,
        bot_module.ACCOUNT_CLIENT_KEY: account_client,
        bot_module.SETTINGS_KEY: settings,
    }
    return SimpleNamespace(bot_data=bot_data, settings=settings, store=store, trade_repository=trade_repository, excursion_repository=excursion_repository, alert_repository=alert_repository, security=security, account_client=account_client)


@pytest.mark.asyncio
async def test_journal_label_and_maemfe_round_trip(runtime_context) -> None:
    open_trade = make_trade(trade_id="trade-1", state=TradeState.OPEN)
    closed_trade = make_trade(trade_id="trade-2", state=TradeState.CLOSED, notes="runner idea")
    runtime_context.trade_repository.upsert(open_trade)
    runtime_context.trade_repository.upsert(closed_trade)
    runtime_context.excursion_repository.insert(
        ExcursionSample(
            trade_id="trade-1",
            sampled_at=BASE_TIME,
            bid=1.0988,
            ask=1.0990,
            adverse_pips=12.0,
            favorable_pips=22.0,
        )
    )
    runtime_context.excursion_repository.insert(
        ExcursionSample(
            trade_id="trade-1",
            sampled_at=BASE_TIME,
            bid=1.0975,
            ask=1.0977,
            adverse_pips=25.0,
            favorable_pips=5.0,
        )
    )

    list_update = build_update()
    detail_update = build_update()
    maemfe_list_update = build_update()
    maemfe_detail_update = build_update()

    await bot_module.journal_command(list_update, SimpleNamespace(bot_data=runtime_context.bot_data, args=[]))
    await bot_module.label_command(
        detail_update,
        SimpleNamespace(bot_data=runtime_context.bot_data, args=["trade-1", "asia", "breakout"]),
    )
    await bot_module.journal_command(
        detail_update,
        SimpleNamespace(bot_data=runtime_context.bot_data, args=["trade-1"]),
    )
    await bot_module.maemfe_command(maemfe_list_update, SimpleNamespace(bot_data=runtime_context.bot_data, args=[]))
    await bot_module.maemfe_command(
        maemfe_detail_update,
        SimpleNamespace(bot_data=runtime_context.bot_data, args=["trade-1"]),
    )

    assert "Trade Journal" in list_update.effective_message.texts[-1]
    assert "trade-1" in list_update.effective_message.texts[-1]
    assert "Label updated for trade trade-1." in detail_update.effective_message.texts[0]
    assert "Trade Detail" in detail_update.effective_message.texts[-1]
    assert "Note:" in detail_update.effective_message.texts[-1]
    assert "MAE:" in detail_update.effective_message.texts[-1]
    assert "MAE / MFE" in maemfe_list_update.effective_message.texts[-1]
    assert "trade-1" in maemfe_list_update.effective_message.texts[-1]
    assert "MAE/MFE" in maemfe_detail_update.effective_message.texts[-1]
    assert "Samples:  2" in maemfe_detail_update.effective_message.texts[-1]
    assert runtime_context.trade_repository.get("trade-1").notes == "asia breakout"


@pytest.mark.asyncio
async def test_maemfe_list_uses_side_correct_price_for_mixed_directions(runtime_context) -> None:
    long_trade = make_trade(trade_id="trade-long", state=TradeState.OPEN)
    short_trade = make_trade(trade_id="trade-short", state=TradeState.OPEN).model_copy(
        update={"units": -1.0}
    )
    runtime_context.trade_repository.upsert(long_trade)
    runtime_context.trade_repository.upsert(short_trade)

    update = build_update()

    await bot_module.maemfe_command(update, SimpleNamespace(bot_data=runtime_context.bot_data, args=[]))

    output = update.effective_message.texts[-1]
    assert "#trade-long" in output
    assert "#trade-short" in output
    assert "P/L: +25.0 pips" in output
    assert "P/L: -27.0 pips" in output


@pytest.mark.asyncio
async def test_indicator_alert_commands_round_trip_through_tinydb(runtime_context) -> None:
    create_update = build_update()
    list_update = build_update()
    clear_update = build_update()
    post_clear_list_update = build_update()

    await bot_module.indicator_alert_command(
        create_update,
        SimpleNamespace(
            bot_data=runtime_context.bot_data,
            args=["EUR_USD", "H1", "RSI", "above", "30", "oversold watch"],
        ),
    )
    await bot_module.list_indicator_alerts_command(
        list_update,
        SimpleNamespace(bot_data=runtime_context.bot_data, args=[]),
    )
    await bot_module.clear_indicator_alert_command(
        clear_update,
        SimpleNamespace(bot_data=runtime_context.bot_data, args=["1"]),
    )
    await bot_module.list_indicator_alerts_command(
        post_clear_list_update,
        SimpleNamespace(bot_data=runtime_context.bot_data, args=[]),
    )

    assert "Indicator alert #1 created" in create_update.effective_message.texts[-1]
    assert "Active Indicator Alerts" in list_update.effective_message.texts[-1]
    assert "EUR_USD" in list_update.effective_message.texts[-1]
    assert "Indicator alert 1 cleared." in clear_update.effective_message.texts[-1]
    assert "No active indicator alerts." in post_clear_list_update.effective_message.texts[-1]
    assert runtime_context.alert_repository.list_active_indicator_alerts() == []


@pytest.mark.asyncio
async def test_indicator_alert_defaults_are_idempotent_per_chat(runtime_context) -> None:
    first_update = build_update()
    second_update = build_update()

    await bot_module.indicator_alert_command(
        first_update,
        SimpleNamespace(bot_data=runtime_context.bot_data, args=["defaults"]),
    )
    first_count = len(runtime_context.alert_repository.list_active_indicator_alerts())

    await bot_module.indicator_alert_command(
        second_update,
        SimpleNamespace(bot_data=runtime_context.bot_data, args=["defaults"]),
    )
    second_count = len(runtime_context.alert_repository.list_active_indicator_alerts())

    assert first_count > 0
    assert second_count == first_count
    assert first_update.effective_message.texts[-1].startswith("Created ")
    assert second_update.effective_message.texts[-1].startswith("Created 0 default indicator alerts.")


@pytest.mark.asyncio
async def test_time_alert_commands_round_trip_through_tinydb(runtime_context) -> None:
    create_update = build_update()
    list_update = build_update()
    clear_update = build_update()
    post_clear_list_update = build_update()

    await bot_module.time_alert_command(
        create_update,
        SimpleNamespace(bot_data=runtime_context.bot_data, args=["at", "09:30", "daily", "desk", "prep"]),
    )
    await bot_module.list_time_alerts_command(
        list_update,
        SimpleNamespace(bot_data=runtime_context.bot_data, args=[]),
    )
    await bot_module.clear_time_alert_command(
        clear_update,
        SimpleNamespace(bot_data=runtime_context.bot_data, args=["1"]),
    )
    await bot_module.list_time_alerts_command(
        post_clear_list_update,
        SimpleNamespace(bot_data=runtime_context.bot_data, args=[]),
    )

    assert "Time alert #1 created" in create_update.effective_message.texts[-1]
    assert "Active Time Alerts" in list_update.effective_message.texts[-1]
    assert "09:30" in list_update.effective_message.texts[-1]
    assert "Time alert 1 cleared." in clear_update.effective_message.texts[-1]
    assert "No active time alerts." in post_clear_list_update.effective_message.texts[-1]
    assert runtime_context.alert_repository.list_active_time_alerts() == []
