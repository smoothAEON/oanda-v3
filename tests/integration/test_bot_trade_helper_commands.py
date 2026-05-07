from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from alerts.alert_repository import AlertRepository
from bot import bot as bot_module
from bot.security_manager import SecurityManager
from bot.runtime import MARKET_DATA_PROVIDER_KEY
from config.settings import load_settings
from core.enums import AlertStatus, IndicatorKind, TradeState
from core.models import CalendarRefreshStatus, ExcursionSample, TradeRecord
from data.persistence.trade_store import TradeStore
from journal.excursion_repository import ExcursionRepository
from journal.trade_repository import TradeRepository
import pandas as pd


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
        self.documents: list[tuple[object, str | None, dict[str, object]]] = []
        self.reply_to_message = None

    async def reply_text(self, text: str, **kwargs) -> None:  # type: ignore[no-untyped-def]
        self.texts.append(text)

    async def reply_document(self, document, filename: str | None = None, **kwargs) -> None:  # type: ignore[no-untyped-def]
        self.documents.append((document, filename, dict(kwargs)))


class RecorderDownloadedFile:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload

    async def download_as_bytearray(self) -> bytearray:
        return bytearray(self.payload)


class RecorderDocument:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload

    async def get_file(self) -> RecorderDownloadedFile:
        return RecorderDownloadedFile(self.payload)


class StubAccountClient:
    def __init__(self) -> None:
        self.calls = 0
        self.range_calls: list[tuple[str, str, datetime, datetime]] = []

    async def get_pricing(self, instrument: str):
        self.calls += 1
        return SimpleNamespace(
            instrument=instrument,
            bid=1.1025,
            ask=1.1027,
            spread_pips=2.0,
            fetched_at=BASE_TIME,
        )

    async def get_bid_ask_candles_range(
        self,
        instrument: str,
        granularity: str,
        start_utc: datetime,
        end_utc: datetime,
    ):
        self.range_calls.append((instrument, granularity, start_utc, end_utc))
        return pd.DataFrame(
            {
                "time": pd.to_datetime(
                    [
                        BASE_TIME,
                        BASE_TIME.replace(minute=1),
                        BASE_TIME.replace(minute=2),
                    ],
                    utc=True,
                ),
                "bid_open": [1.1000, 1.0995, 1.1010],
                "bid_high": [1.1004, 1.1002, 1.1030],
                "bid_low": [1.0998, 1.0975, 1.1005],
                "bid_close": [1.1001, 1.0999, 1.1025],
                "ask_open": [1.1002, 1.0997, 1.1012],
                "ask_high": [1.1006, 1.1004, 1.1032],
                "ask_low": [1.1000, 1.0977, 1.1007],
                "ask_close": [1.1003, 1.1001, 1.1027],
                "tick_volume": [100, 110, 120],
            }
        )


class StubMarketDataProvider:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, int]] = []

    def get_candles(self, instrument: str, timeframe: str, count: int):
        self.calls.append((instrument, timeframe, count))
        return pd.DataFrame(
            {
                "time": pd.to_datetime(
                    [
                        BASE_TIME.replace(hour=5),
                        BASE_TIME.replace(hour=6),
                        BASE_TIME.replace(hour=7),
                        BASE_TIME.replace(hour=8),
                    ],
                    utc=True,
                ),
                "open": [1.1000, 1.1005, 1.1010, 1.1015],
                "high": [1.1008, 1.1013, 1.1018, 1.1023],
                "low": [1.0995, 1.1000, 1.1005, 1.1010],
                "close": [1.1004, 1.1009, 1.1014, 1.1019],
                "tick_volume": [100, 110, 120, 130],
            }
        )

    def get_candle_freshness(self, instrument: str, timeframe: str):
        return SimpleNamespace(source="oanda_api")


def build_update(
    user_id: int = 111,
    chat_id: int = 222,
    *,
    message: RecorderMessage | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        effective_message=message or RecorderMessage(),
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
    market_data_provider = StubMarketDataProvider()
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
        MARKET_DATA_PROVIDER_KEY: market_data_provider,
        bot_module.SETTINGS_KEY: settings,
    }
    return SimpleNamespace(bot_data=bot_data, settings=settings, store=store, trade_repository=trade_repository, excursion_repository=excursion_repository, alert_repository=alert_repository, security=security, account_client=account_client, market_data_provider=market_data_provider)


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
    assert "+$4.20" in list_update.effective_message.texts[-1]
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
async def test_maemfe_prefers_replayed_open_trade_extremes_over_sparse_samples(runtime_context) -> None:
    trade = make_trade(trade_id="trade-replay", state=TradeState.OPEN)
    runtime_context.trade_repository.upsert(trade)
    runtime_context.excursion_repository.insert(
        ExcursionSample(
            trade_id="trade-replay",
            sampled_at=BASE_TIME,
            bid=1.0995,
            ask=1.0997,
            adverse_pips=5.0,
            favorable_pips=10.0,
        )
    )

    list_update = build_update()
    detail_update = build_update()

    await bot_module.maemfe_command(list_update, SimpleNamespace(bot_data=runtime_context.bot_data, args=[]))
    await bot_module.maemfe_command(
        detail_update,
        SimpleNamespace(bot_data=runtime_context.bot_data, args=["trade-replay"]),
    )

    list_output = list_update.effective_message.texts[-1]
    detail_output = detail_update.effective_message.texts[-1]
    assert "#trade-replay" in list_output
    assert "MAE: -25.0 pips" in list_output
    assert "MFE: +30.0 pips" in list_output
    assert "MAE (worst):  -25.0 pips  (at 08:01 UTC)" in detail_output
    assert "MFE (best):   +30.0 pips  (at 08:02 UTC)" in detail_output
    assert "Samples:  1" in detail_output


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
async def test_vwap_command_reads_market_data_without_scan(runtime_context) -> None:
    update = build_update()

    await bot_module.vwap_command(
        update,
        SimpleNamespace(bot_data=runtime_context.bot_data, args=["EUR_USD", "H1", "--anchor", "D"]),
    )

    output = update.effective_message.texts[-1]
    assert "VWAP EUR_USD H1" in output
    assert "Anchor: D (daily)" in output
    assert "Source: oanda_api" in output
    assert "Caveat:" in output
    assert runtime_context.market_data_provider.calls


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


@pytest.mark.asyncio
async def test_time_alert_export_import_round_trip_through_tinydb(runtime_context) -> None:
    create_update = build_update()
    export_update = build_update()
    import_message = RecorderMessage()
    import_update = build_update(message=import_message)
    list_update = build_update()

    await bot_module.time_alert_command(
        create_update,
        SimpleNamespace(bot_data=runtime_context.bot_data, args=["at", "2027-04-10", "09:30", "once", "CPI", "prep"]),
    )
    await bot_module.export_time_alerts_command(
        export_update,
        SimpleNamespace(bot_data=runtime_context.bot_data, args=[]),
    )

    exported_document, exported_filename, exported_kwargs = export_update.effective_message.documents[0]
    exported_payload = json.loads(exported_document.getvalue().decode("utf-8"))
    import_message.reply_to_message = SimpleNamespace(
        document=RecorderDocument(exported_document.getvalue())
    )

    await bot_module.import_time_alerts_command(
        import_update,
        SimpleNamespace(bot_data=runtime_context.bot_data, args=[]),
    )
    await bot_module.list_time_alerts_command(
        list_update,
        SimpleNamespace(bot_data=runtime_context.bot_data, args=[]),
    )

    assert exported_filename is not None and exported_filename.endswith(".json")
    assert exported_kwargs["caption"] == "Exported 1 active time alerts."
    assert exported_payload["schema_version"] == 1
    assert exported_payload["alerts"][0]["local_time"] == "2027-04-10 09:30"
    assert "Imported 1 time alerts." == import_update.effective_message.texts[-1]
    listed_text = list_update.effective_message.texts[-1]
    assert "#1  at 2027-04-10 09:30" in listed_text
    assert "#2  at 2027-04-10 09:30" in listed_text
    assert len(runtime_context.alert_repository.list_active_time_alerts_for_chat(222)) == 2
