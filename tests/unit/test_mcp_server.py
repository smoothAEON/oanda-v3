from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest
from starlette.testclient import TestClient
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.responses import JSONResponse
from starlette.routing import Route

from bot.bot import _run_application_with_mcp
from config.settings import Settings, load_settings
from core.candle_policy import get_timeframe_delta
from core.enums import AlertStatus, IndicatorKind, TradeState
from core.events import PriceTick
from core.instrument_registry import get_instrument_spec
from core.market_state import MarketStateStore
from core.models import (
    ActiveZoneSummary,
    AlertHistoryRecord,
    ExcursionSample,
    IndicatorValueSummary,
    LiquidityLevelSummary,
    LiquidityPoolSummary,
    OrderBlockSummary,
    PreviousHighLowSummary,
    SmcContextSummary,
    SnapshotFreshness,
    SpreadResult,
    StructureBreak,
    StructureEventSummary,
    TimeframeSnapshot,
    TradeRecord,
)
from data.persistence.trade_store import TradeStore
from alerts.alert_repository import AlertRepository
from mcp_server.adapters import BotMcpService
from mcp_server.auth import QueryParamAPIKeyMiddleware
from mcp_server.server import TOOL_SPECS, build_mcp_http_app
from providers.base import CandleFreshness


BASE_TIME = datetime(2026, 4, 4, 12, 0, tzinfo=timezone.utc)


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
        "MCP_HTTP_ENABLED": "true",
        "MCP_HTTP_HOST": "0.0.0.0",
        "MCP_HTTP_PORT": "8080",
        "MCP_HTTP_PATH": "/mcp",
        "MCP_HTTP_API_KEY": "secret-key",
        "MCP_DEFAULT_CHAT_ID": "555",
    }
    values.update(overrides)
    path.write_text("\n".join(f"{key}={value}" for key, value in values.items()), encoding="utf-8")
    return path


def build_settings(tmp_path: Path, **overrides: str) -> Settings:
    env_file = write_env_file(tmp_path / ".env", **overrides)
    return load_settings(env_file=env_file)


def build_spread(instrument: str = "SPX500_USD") -> SpreadResult:
    spec = get_instrument_spec(instrument)
    bid = 3300.0
    ask = bid + (spec.pip_size * 2.0)
    return SpreadResult(
        instrument=instrument,
        bid=bid,
        ask=ask,
        raw_spread=ask - bid,
        spread_pips=2.0,
        pip_size=spec.pip_size,
        fetched_at=BASE_TIME,
    )


def build_snapshot(*, instrument: str = "SPX500_USD", timeframe: str = "H1") -> TimeframeSnapshot:
    delta = get_timeframe_delta(timeframe)
    return TimeframeSnapshot(
        instrument=instrument,
        timeframe=timeframe,
        version=1,
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
                previous_high=3350.0,
                previous_low=3300.0,
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
            candle_count=500,
            is_fresh=True,
            staleness_seconds=0.0,
        ),
    )


def build_analysis_snapshot(*, instrument: str = "SPX500_USD", timeframe: str = "H1") -> TimeframeSnapshot:
    latest_break = StructureBreak(
        kind="BOS",
        direction="BULLISH",
        level=3310.0,
        occurred_at=BASE_TIME - timedelta(hours=1),
    )
    return build_snapshot(instrument=instrument, timeframe=timeframe).model_copy(
        update={
            "structure": StructureEventSummary(latest_break=latest_break, recent_breaks=(latest_break,)),
            "zones": ActiveZoneSummary(
                order_blocks=(
                    OrderBlockSummary(
                        direction="BEARISH",
                        upper_price=3330.0,
                        lower_price=3325.0,
                        created_at=BASE_TIME - timedelta(hours=2),
                        is_mitigated=False,
                    ),
                    OrderBlockSummary(
                        direction="BULLISH",
                        upper_price=3305.0,
                        lower_price=3300.0,
                        created_at=BASE_TIME - timedelta(hours=4),
                        is_mitigated=True,
                    ),
                )
            ),
            "liquidity": LiquidityPoolSummary(
                levels=(
                    LiquidityLevelSummary(
                        side="BUY_SIDE",
                        price=3340.0,
                        occurred_at=BASE_TIME - timedelta(hours=3),
                        was_swept=False,
                    ),
                )
            ),
        }
    )


def build_candle_frame(*, timeframe: str = "H1", count: int = 3) -> pd.DataFrame:
    delta = get_timeframe_delta(timeframe)
    times = [BASE_TIME - (delta * offset) for offset in range(count - 1, -1, -1)]
    return pd.DataFrame(
        {
            "time": pd.to_datetime(times, utc=True),
            "open": [3300.0 + index for index in range(count)],
            "high": [3301.0 + index for index in range(count)],
            "low": [3299.0 + index for index in range(count)],
            "close": [3300.5 + index for index in range(count)],
            "tick_volume": [100 + index for index in range(count)],
        }
    )


def test_mcp_http_app_enforces_query_param_api_key(tmp_path: Path) -> None:
    settings = build_settings(tmp_path)
    runtime = SimpleNamespace(settings=settings)
    app = build_mcp_http_app(runtime=runtime, settings=settings)
    with TestClient(app) as client:
        health = client.get("/healthz")
        assert health.status_code == 200
        assert health.json()["ok"] is True

        unauthorized = client.get("/mcp")
        assert unauthorized.status_code == 401


def test_mcp_http_app_honors_custom_streamable_path(tmp_path: Path) -> None:
    settings = build_settings(tmp_path, MCP_HTTP_PATH="/gateway")
    runtime = SimpleNamespace(settings=settings)
    app = build_mcp_http_app(runtime=runtime, settings=settings)

    route_paths = {getattr(route, "path", None) for route in app.routes}
    assert "/gateway" in route_paths
    assert "/mcp" not in route_paths
    assert "/healthz" in route_paths

    with TestClient(app) as client:
        assert client.get("/healthz").status_code == 200
        assert client.get("/gateway").status_code == 401
        assert client.get("/mcp?api_key=secret-key").status_code == 404


def test_query_param_api_key_middleware_allows_valid_requests() -> None:
    async def ok_endpoint(_request):
        return JSONResponse({"ok": True})

    app = Starlette(
        routes=[Route("/ok", endpoint=ok_endpoint)],
        middleware=[Middleware(QueryParamAPIKeyMiddleware, api_key="secret-key")],
    )

    with TestClient(app) as client:
        assert client.get("/ok").status_code == 401
        assert client.get("/ok?api_key=secret-key").status_code == 200


def test_mcp_alert_tools_use_default_chat_id(tmp_path: Path) -> None:
    settings = build_settings(tmp_path, MCP_DEFAULT_CHAT_ID="777")
    store = TradeStore(db_path=settings.tinydb_path, settings=settings)
    alert_repository = AlertRepository(store=store)
    runtime = SimpleNamespace(settings=settings, alert_repository=alert_repository)
    service = BotMcpService(runtime=runtime, settings=settings)

    created = asyncio.run(
        service.create_price_alert("SPX500_USD", target_price=3350.0, direction="above", note="breakout")
    )
    assert created["chat_id"] == 777

    listed = asyncio.run(service.list_price_alerts())
    assert listed["chat_id"] == 777
    assert listed["alerts"][0]["id"] == created["id"]

    cleared = asyncio.run(service.clear_price_alert(created["id"]))
    assert cleared["status"] == "CANCELLED"
    store.close()


def test_mcp_create_time_alert_accepts_exact_datetime_and_forces_once(tmp_path: Path) -> None:
    settings = build_settings(tmp_path, MCP_DEFAULT_CHAT_ID="777")
    store = TradeStore(db_path=settings.tinydb_path, settings=settings)
    alert_repository = AlertRepository(store=store)
    runtime = SimpleNamespace(settings=settings, alert_repository=alert_repository)
    service = BotMcpService(runtime=runtime, settings=settings)

    created = asyncio.run(
        service.create_time_alert(
            "at",
            local_time="2027-04-05 09:30",
            schedule="daily",
            note="NFP prep",
        )
    )

    assert created["chat_id"] == 777
    assert created["kind"] == "FIXED_TIME"
    assert created["schedule"] == "once"
    assert created["local_time"] == "2027-04-05 09:30"
    assert created["next_fire_at"] == "2027-04-05T01:30:00Z"

    store.close()


def test_mcp_get_price_uses_rest_pricing_by_default(tmp_path: Path) -> None:
    settings = build_settings(tmp_path)

    class FakeAccountClient:
        def __init__(self) -> None:
            self.calls: list[str] = []

        async def get_pricing(self, instrument: str):
            self.calls.append(instrument)
            return SimpleNamespace(
                bid=3049.90,
                ask=3050.20,
                spread_pips=30.0,
                fetched_at=BASE_TIME,
            )

    def fail_latest_quote(*_args, **_kwargs):
        raise AssertionError("Live stream should not be consulted when prefer_live=false.")

    runtime = SimpleNamespace(
        settings=settings,
        account_client=FakeAccountClient(),
        stream_task=SimpleNamespace(latest_quote=fail_latest_quote),
    )
    service = BotMcpService(runtime=runtime, settings=settings)

    result = asyncio.run(service.get_price("spx500usd"))

    assert result["source"] == "rest_pricing"
    assert result["fallback_note"] is None
    assert result["bid"] == 3049.90
    assert runtime.account_client.calls == ["SPX500_USD"]


def test_mcp_get_price_prefers_live_stream_when_requested(tmp_path: Path) -> None:
    settings = build_settings(tmp_path)
    live_tick = PriceTick(
        instrument="SPX500_USD",
        bid=3050.10,
        ask=3050.40,
        time=BASE_TIME,
    )

    class FailingAccountClient:
        async def get_pricing(self, instrument: str):
            raise AssertionError("REST pricing should not be used when a fresh live quote exists.")

    runtime = SimpleNamespace(
        settings=settings,
        account_client=FailingAccountClient(),
        stream_task=SimpleNamespace(latest_quote=lambda instrument, max_age_seconds=None: live_tick),
    )
    service = BotMcpService(runtime=runtime, settings=settings)

    result = asyncio.run(service.get_price("spx500usd", prefer_live=True))

    assert result["source"] == "live_stream"
    assert result["fallback_note"] is None
    assert result["bid"] == 3050.10
    assert result["ask"] == 3050.40


def test_mcp_get_price_falls_back_to_rest_when_live_quote_missing(tmp_path: Path) -> None:
    settings = build_settings(tmp_path)

    class FakeAccountClient:
        def __init__(self) -> None:
            self.calls: list[str] = []

        async def get_pricing(self, instrument: str):
            self.calls.append(instrument)
            return SimpleNamespace(
                bid=3049.90,
                ask=3050.20,
                spread_pips=30.0,
                fetched_at=BASE_TIME,
            )

    runtime = SimpleNamespace(
        settings=settings,
        account_client=FakeAccountClient(),
        stream_task=SimpleNamespace(latest_quote=lambda instrument, max_age_seconds=None: None),
    )
    service = BotMcpService(runtime=runtime, settings=settings)

    result = asyncio.run(service.get_price("spx500usd", prefer_live=True))

    assert result["source"] == "rest_pricing"
    assert result["fallback_note"] == "live stream unavailable or stale; REST pricing used"
    assert runtime.account_client.calls == ["SPX500_USD"]


def test_mcp_get_journal_trade_prefers_live_stream_for_open_trade_price(tmp_path: Path) -> None:
    settings = build_settings(tmp_path)
    trade = TradeRecord(
        trade_id="trade-1",
        instrument="SPX500_USD",
        units=1.0,
        open_price=3000.0,
        close_price=None,
        sl_price=None,
        tp_price=None,
        gslo_price=None,
        state=TradeState.OPEN,
        close_reason=None,
        pips=None,
        instrument_pnl=None,
        instrument_pnl_currency=None,
        account_pnl=None,
        account_currency=None,
        opened_at=BASE_TIME - timedelta(hours=1),
        closed_at=None,
        notes=None,
    )
    live_tick = PriceTick(
        instrument="SPX500_USD",
        bid=3050.10,
        ask=3050.40,
        time=BASE_TIME,
    )

    class FakeTradeRepository:
        def get(self, trade_id: str):
            assert trade_id == "trade-1"
            return trade

    class FakeExcursionRepository:
        def list_for_trade(self, trade_id: str):
            assert trade_id == "trade-1"
            return []

        def get_mae_mfe(self, trade_id: str):
            assert trade_id == "trade-1"
            return {"mae_pips": 10.0, "mfe_pips": 20.0}

    class FailingAccountClient:
        async def get_pricing(self, instrument: str):
            raise AssertionError("REST pricing should not be used when a fresh live quote exists.")

    runtime = SimpleNamespace(
        settings=settings,
        trade_repository=FakeTradeRepository(),
        excursion_repository=FakeExcursionRepository(),
        account_client=FailingAccountClient(),
        stream_task=SimpleNamespace(latest_quote=lambda instrument, max_age_seconds=None: live_tick),
    )
    service = BotMcpService(runtime=runtime, settings=settings)

    result = asyncio.run(service.get_journal_trade("trade-1"))

    assert result["current_price"] == 3050.10
    assert result["current_price_source"] == "live_stream"
    assert result["current_price_fallback_note"] is None


def test_mcp_get_journal_trade_replays_open_trade_extremes(tmp_path: Path) -> None:
    settings = build_settings(tmp_path)
    trade = TradeRecord(
        trade_id="trade-1",
        instrument="BCO_USD",
        units=3.0,
        open_price=99.575,
        close_price=None,
        sl_price=None,
        tp_price=None,
        gslo_price=None,
        state=TradeState.OPEN,
        close_reason=None,
        pips=None,
        instrument_pnl=None,
        instrument_pnl_currency=None,
        account_pnl=None,
        account_currency=None,
        opened_at=BASE_TIME - timedelta(hours=2),
        closed_at=None,
        notes=None,
    )
    live_tick = PriceTick(
        instrument="BCO_USD",
        bid=100.60,
        ask=100.61,
        time=BASE_TIME,
    )

    class FakeTradeRepository:
        def get(self, trade_id: str):
            assert trade_id == "trade-1"
            return trade

    class FakeExcursionRepository:
        def list_for_trade(self, trade_id: str):
            return [
                ExcursionSample(
                    trade_id=trade_id,
                    sampled_at=BASE_TIME - timedelta(minutes=30),
                    bid=99.60,
                    ask=99.61,
                    adverse_pips=0.0,
                    favorable_pips=100.8,
                )
            ]

        def get_mae_mfe(self, trade_id: str):
            return {"trade_id": trade_id, "sample_count": 1, "mae_pips": 0.0, "mfe_pips": 100.8}

    class FakeAccountClient:
        async def get_bid_ask_candles_range(self, instrument: str, granularity: str, start_utc: datetime, end_utc: datetime):
            return pd.DataFrame(
                {
                    "time": pd.to_datetime(
                        [
                            "2026-04-04T10:32:00Z",
                            "2026-04-04T12:23:00Z",
                        ],
                        utc=True,
                    ),
                    "bid_open": [99.58, 100.80],
                    "bid_high": [99.62, 100.919],
                    "bid_low": [99.412, 100.70],
                    "bid_close": [99.50, 100.85],
                    "ask_open": [99.59, 100.81],
                    "ask_high": [99.63, 100.929],
                    "ask_low": [99.422, 100.71],
                    "ask_close": [99.51, 100.86],
                    "tick_volume": [110, 120],
                }
            )

        async def get_pricing(self, instrument: str):
            raise AssertionError("REST pricing should not be used when a fresh live quote exists.")

    runtime = SimpleNamespace(
        settings=settings,
        trade_repository=FakeTradeRepository(),
        excursion_repository=FakeExcursionRepository(),
        account_client=FakeAccountClient(),
        stream_task=SimpleNamespace(latest_quote=lambda instrument, max_age_seconds=None: live_tick),
    )
    service = BotMcpService(runtime=runtime, settings=settings)

    result = asyncio.run(service.get_journal_trade("trade-1"))

    assert result["current_price"] == 100.60
    assert result["mae_mfe"]["summary_source"] == "m1_bid_ask_replay"
    assert result["mae_mfe"]["mae_pips"] == pytest.approx(16.3)
    assert result["mae_mfe"]["mfe_pips"] == pytest.approx(134.4)


def test_mcp_get_mae_mfe_uses_live_first_prices_with_rest_fallback(tmp_path: Path) -> None:
    settings = build_settings(tmp_path)

    class FakeTradeRepository:
        def list_open(self):
            return [
                TradeRecord(
                    trade_id="long-1",
                    instrument="SPX500_USD",
                    units=1.0,
                    open_price=3000.0,
                    close_price=None,
                    sl_price=None,
                    tp_price=None,
                    gslo_price=None,
                    state=TradeState.OPEN,
                    close_reason=None,
                    pips=None,
                    instrument_pnl=None,
                    instrument_pnl_currency=None,
                    account_pnl=None,
                    account_currency=None,
                    opened_at=BASE_TIME - timedelta(hours=2),
                    closed_at=None,
                    notes=None,
                ),
                TradeRecord(
                    trade_id="short-1",
                    instrument="EUR_USD",
                    units=-1.0,
                    open_price=1.1000,
                    close_price=None,
                    sl_price=None,
                    tp_price=None,
                    gslo_price=None,
                    state=TradeState.OPEN,
                    close_reason=None,
                    pips=None,
                    instrument_pnl=None,
                    instrument_pnl_currency=None,
                    account_pnl=None,
                    account_currency=None,
                    opened_at=BASE_TIME - timedelta(hours=2),
                    closed_at=None,
                    notes=None,
                ),
            ]

    class FakeExcursionRepository:
        def get_mae_mfe(self, trade_id: str):
            return {"mae_pips": 5.0, "mfe_pips": 12.0}

    live_tick = PriceTick(
        instrument="SPX500_USD",
        bid=3050.10,
        ask=3050.40,
        time=BASE_TIME,
    )

    class FakeAccountClient:
        def __init__(self) -> None:
            self.calls: list[str] = []

        async def get_pricing(self, instrument: str):
            self.calls.append(instrument)
            return SimpleNamespace(
                bid=1.0990,
                ask=1.0992,
                spread_pips=2.0,
                fetched_at=BASE_TIME,
            )

    runtime = SimpleNamespace(
        settings=settings,
        trade_repository=FakeTradeRepository(),
        excursion_repository=FakeExcursionRepository(),
        account_client=FakeAccountClient(),
        stream_task=SimpleNamespace(
            latest_quote=lambda instrument, max_age_seconds=None: live_tick if instrument == "SPX500_USD" else None
        ),
    )
    service = BotMcpService(runtime=runtime, settings=settings)

    result = asyncio.run(service.get_mae_mfe())

    long_record = next(item for item in result["open_trades"] if item["trade"]["trade_id"] == "long-1")
    short_record = next(item for item in result["open_trades"] if item["trade"]["trade_id"] == "short-1")

    assert long_record["current_price"] == 3050.10
    assert long_record["current_price_source"] == "live_stream"
    assert long_record["current_price_fallback_note"] is None
    assert short_record["current_price"] == 1.0992
    assert short_record["current_price_source"] == "rest_pricing"
    assert short_record["current_price_fallback_note"] == "live stream unavailable or stale; REST pricing used"
    assert runtime.account_client.calls == ["EUR_USD"]


def test_mcp_get_mae_mfe_replays_open_trade_extremes_from_m1_bid_ask_candles(tmp_path: Path) -> None:
    settings = build_settings(tmp_path)
    trade = TradeRecord(
        trade_id="5899",
        instrument="BCO_USD",
        units=3.0,
        open_price=99.575,
        close_price=None,
        sl_price=None,
        tp_price=None,
        gslo_price=None,
        state=TradeState.OPEN,
        close_reason=None,
        pips=None,
        instrument_pnl=None,
        instrument_pnl_currency=None,
        account_pnl=None,
        account_currency=None,
        opened_at=BASE_TIME - timedelta(hours=2),
        closed_at=None,
        notes=None,
    )

    class FakeTradeRepository:
        def list_open(self):
            return [trade]

    class FakeExcursionRepository:
        def list_for_trade(self, trade_id: str):
            assert trade_id == "5899"
            return [
                ExcursionSample(
                    trade_id="5899",
                    sampled_at=BASE_TIME - timedelta(minutes=30),
                    bid=99.60,
                    ask=99.61,
                    adverse_pips=0.0,
                    favorable_pips=100.8,
                )
            ]

        def get_mae_mfe(self, trade_id: str):
            return {"trade_id": trade_id, "sample_count": 1, "mae_pips": 0.0, "mfe_pips": 100.8}

    live_tick = PriceTick(
        instrument="BCO_USD",
        bid=100.60,
        ask=100.61,
        time=BASE_TIME,
    )

    class FakeAccountClient:
        async def get_bid_ask_candles_range(self, instrument: str, granularity: str, start_utc: datetime, end_utc: datetime):
            assert instrument == "BCO_USD"
            assert granularity == "M1"
            return pd.DataFrame(
                {
                    "time": pd.to_datetime(
                        [
                            "2026-04-04T10:31:00Z",
                            "2026-04-04T10:32:00Z",
                            "2026-04-04T12:23:00Z",
                        ],
                        utc=True,
                    ),
                    "bid_open": [99.57, 99.58, 100.80],
                    "bid_high": [99.60, 99.62, 100.919],
                    "bid_low": [99.55, 99.412, 100.70],
                    "bid_close": [99.58, 99.50, 100.85],
                    "ask_open": [99.58, 99.59, 100.81],
                    "ask_high": [99.61, 99.63, 100.929],
                    "ask_low": [99.56, 99.422, 100.71],
                    "ask_close": [99.59, 99.51, 100.86],
                    "tick_volume": [100, 110, 120],
                }
            )

        async def get_pricing(self, instrument: str):
            raise AssertionError("REST pricing should not be used when a fresh live quote exists.")

    runtime = SimpleNamespace(
        settings=settings,
        trade_repository=FakeTradeRepository(),
        excursion_repository=FakeExcursionRepository(),
        account_client=FakeAccountClient(),
        stream_task=SimpleNamespace(latest_quote=lambda instrument, max_age_seconds=None: live_tick),
    )
    service = BotMcpService(runtime=runtime, settings=settings)

    result = asyncio.run(service.get_mae_mfe())

    record = result["open_trades"][0]
    assert record["current_price"] == 100.60
    assert record["summary"]["summary_source"] == "m1_bid_ask_replay"
    assert record["summary"]["mae_pips"] == pytest.approx(16.3)
    assert record["summary"]["mfe_pips"] == pytest.approx(134.4)
    assert record["summary"]["mae_price"] == 99.412
    assert record["summary"]["mfe_price"] == 100.919
    assert record["summary"]["mae_at"] == "2026-04-04T10:32:00Z"
    assert record["summary"]["mfe_at"] == "2026-04-04T12:23:00Z"


def test_get_smc_snapshot_uses_published_state_before_refresh(tmp_path: Path) -> None:
    settings = build_settings(tmp_path)
    market_state = MarketStateStore()
    published = market_state.publish_snapshot(build_snapshot())

    class FakeScanOrchestrator:
        def __init__(self) -> None:
            self.calls = 0

        def refresh_snapshot(self, instrument: str, timeframe: str, *, force: bool = False):
            self.calls += 1
            return None

    runtime = SimpleNamespace(
        settings=settings,
        market_state=market_state,
        scan_orchestrator=FakeScanOrchestrator(),
    )
    service = BotMcpService(runtime=runtime, settings=settings)

    result = asyncio.run(service.get_smc_snapshot("SPX500_USD", "H1", refresh_policy="if_missing"))

    assert result["snapshot_version"] == published.version
    assert runtime.scan_orchestrator.calls == 0


def test_get_smc_snapshot_refreshes_when_missing(tmp_path: Path) -> None:
    settings = build_settings(tmp_path)
    snapshot = build_snapshot()

    class FakeScanOrchestrator:
        def __init__(self) -> None:
            self.calls = 0

        def refresh_snapshot(self, instrument: str, timeframe: str, *, force: bool = False):
            self.calls += 1
            return snapshot

    runtime = SimpleNamespace(
        settings=settings,
        market_state=MarketStateStore(),
        scan_orchestrator=FakeScanOrchestrator(),
    )
    service = BotMcpService(runtime=runtime, settings=settings)

    result = asyncio.run(service.get_smc_snapshot("SPX500_USD", "H1", refresh_policy="if_missing"))

    assert result["instrument"] == "SPX500_USD"
    assert runtime.scan_orchestrator.calls == 1


def _assert_no_final_decision_keys(payload) -> None:  # type: ignore[no-untyped-def]
    forbidden = {
        "bias",
        "direction",
        "valid",
        "entry",
        "target",
        "invalidation",
        "reward_risk",
        "score",
        "confidence",
        "recommendation",
        "trade_plan",
    }
    if isinstance(payload, dict):
        assert forbidden.isdisjoint(payload)
        for value in payload.values():
            _assert_no_final_decision_keys(value)
    elif isinstance(payload, list):
        for value in payload:
            _assert_no_final_decision_keys(value)


def test_mcp_analysis_payloads_are_sanitized_for_hybrid_evidence_tools(tmp_path: Path) -> None:
    settings = build_settings(tmp_path)
    snapshot = build_analysis_snapshot()
    market_state = MarketStateStore()
    market_state.publish_snapshot(snapshot)

    class FakeScanOrchestrator:
        last_scan_status = SimpleNamespace(
            run_kind="full",
            scanned_instruments=("SPX500_USD",),
            snapshots_published=1,
            errors=(),
        )

        def refresh_snapshot(self, instrument: str, timeframe: str, *, force: bool = False):
            raise AssertionError("refresh_policy=never should not refresh")

    runtime = SimpleNamespace(
        settings=settings,
        market_state=market_state,
        scan_orchestrator=FakeScanOrchestrator(),
    )
    service = BotMcpService(runtime=runtime, settings=settings)

    smc = asyncio.run(service.get_smc_snapshot("spx500usd", "H1", refresh_policy="never"))
    structure = asyncio.run(service.get_structure("spx500usd", "H1", refresh_policy="never"))
    order_blocks = asyncio.run(service.get_order_blocks("spx500usd", "H1", refresh_policy="never"))
    mitigated_blocks = asyncio.run(
        service.get_order_blocks("spx500usd", "H1", refresh_policy="never", mitigation_status="mitigated")
    )
    unmitigated_blocks = asyncio.run(
        service.get_order_blocks("spx500usd", "H1", refresh_policy="never", mitigation_status="unmitigated")
    )

    for payload in (smc, structure, order_blocks, mitigated_blocks, unmitigated_blocks):
        _assert_no_final_decision_keys(payload)

    assert smc["structure"]["latest_break"]["break_side"] == "BULLISH"
    assert smc["zones"][0]["zone_side"] == "BEARISH"
    assert smc["zones"][0]["mitigation_status"] == "UNMITIGATED"
    assert smc["liquidity"][0]["liquidity_side"] == "BUY_SIDE"
    assert structure["latest_break"]["break_side"] == "BULLISH"
    assert order_blocks["mitigation_status_filter"] == "all"
    assert order_blocks["order_block_counts"] == {"all": 2, "mitigated": 1, "unmitigated": 1}
    assert len(order_blocks["order_blocks"]) == 2
    assert order_blocks["order_blocks"][0]["zone_side"] == "BEARISH"
    assert mitigated_blocks["mitigation_status_filter"] == "mitigated"
    assert [item["mitigation_status"] for item in mitigated_blocks["order_blocks"]] == ["MITIGATED"]
    assert unmitigated_blocks["mitigation_status_filter"] == "unmitigated"
    assert [item["mitigation_status"] for item in unmitigated_blocks["order_blocks"]] == ["UNMITIGATED"]

    with pytest.raises(ValueError, match="Order-block mitigation filter"):
        asyncio.run(
            service.get_order_blocks(
                "spx500usd",
                "H1",
                refresh_policy="never",
                mitigation_status="inactive",
            )
        )


def test_mcp_scan_tools_forward_force_flags(tmp_path: Path) -> None:
    settings = build_settings(tmp_path)

    class FakeScanOrchestrator:
        def __init__(self) -> None:
            self.scan_all_calls: list[bool] = []
            self.refresh_instrument_calls: list[tuple[str, bool]] = []
            self.refresh_snapshot_calls: list[tuple[str, str, bool]] = []
            self.last_scan_status = {"forced_market_fetch": False}

        def scan_all(self, *, force: bool = False):
            self.scan_all_calls.append(force)
            return {"run_kind": "full", "forced_market_fetch": force}

        def refresh_instrument(self, instrument: str, *, force: bool = False):
            self.refresh_instrument_calls.append((instrument, force))
            self.last_scan_status = {"forced_market_fetch": force}
            return {"H1": build_snapshot(instrument=instrument, timeframe="H1")}

        def refresh_snapshot(self, instrument: str, timeframe: str, *, force: bool = False):
            self.refresh_snapshot_calls.append((instrument, timeframe, force))
            return build_snapshot(instrument=instrument, timeframe=timeframe)

    runtime = SimpleNamespace(settings=settings, scan_orchestrator=FakeScanOrchestrator())
    service = BotMcpService(runtime=runtime, settings=settings)

    scan_result = asyncio.run(service.scan_all(force=True))
    instrument_result = asyncio.run(service.scan_instrument("SPX500_USD", force=True))
    snapshot_result = asyncio.run(service.refresh_snapshot("SPX500_USD", "H1", force=True))

    assert scan_result["forced_market_fetch"] is True
    assert instrument_result["force"] is True
    assert "H1" in instrument_result["snapshots"]
    assert snapshot_result["force"] is True
    assert runtime.scan_orchestrator.scan_all_calls == [True]
    assert runtime.scan_orchestrator.refresh_instrument_calls == [("SPX500_USD", True)]
    assert runtime.scan_orchestrator.refresh_snapshot_calls == [("SPX500_USD", "H1", True)]


def test_mcp_snapshot_tools_reject_raw_or_unpublished_timeframes(tmp_path: Path) -> None:
    settings = build_settings(tmp_path)
    runtime = SimpleNamespace(
        settings=settings,
        market_state=MarketStateStore(),
        scan_orchestrator=SimpleNamespace(),
    )
    service = BotMcpService(runtime=runtime, settings=settings)

    with pytest.raises(ValueError, match="Unsupported timeframe 'S5'"):
        asyncio.run(service.refresh_snapshot("SPX500_USD", "S5"))
    with pytest.raises(ValueError, match="Published snapshot timeframe"):
        asyncio.run(service.get_smc_snapshot("SPX500_USD", "M5", refresh_policy="never"))


def test_mcp_get_candles_uses_account_client_directly_by_default(tmp_path: Path) -> None:
    settings = build_settings(tmp_path)
    frame = build_candle_frame(count=2)

    class FakeAccountClient:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str, int]] = []

        async def get_candles(self, instrument: str, granularity: str, count: int) -> pd.DataFrame:
            self.calls.append((instrument, granularity, count))
            return frame

    runtime = SimpleNamespace(
        settings=settings,
        account_client=FakeAccountClient(),
    )
    service = BotMcpService(runtime=runtime, settings=settings)

    result = asyncio.run(service.get_candles("SPX500_USD", "H1", count=2))

    assert result["source"] == "oanda_api_direct"
    assert result["force"] is False
    assert result["freshness"] is None
    assert result["returned_count"] == 2
    assert result["bars"][0]["open"] == 3300.0
    assert "fetched directly from OANDA" in result["warning"]
    assert runtime.account_client.calls == [("SPX500_USD", "H1", 2)]


def test_mcp_get_candles_accepts_raw_oanda_granularity_and_live_catalog_instrument(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = build_settings(tmp_path)
    frame = build_candle_frame(timeframe="S5", count=2)
    monkeypatch.setattr("bot.parsing.validate_live_instrument", lambda instrument: instrument)

    class FakeAccountClient:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str, int]] = []

        async def get_candles(self, instrument: str, granularity: str, count: int) -> pd.DataFrame:
            self.calls.append((instrument, granularity, count))
            return frame

    runtime = SimpleNamespace(
        settings=settings,
        account_client=FakeAccountClient(),
    )
    service = BotMcpService(runtime=runtime, settings=settings)

    result = asyncio.run(service.get_candles("DE30_EUR", "5s", count=2))

    assert result["instrument"] == "DE30_EUR"
    assert result["timeframe"] == "S5"
    assert result["requested_count"] == 2
    assert result["returned_count"] == 2
    assert result["freshness"] is None
    assert runtime.account_client.calls == [("DE30_EUR", "S5", 2)]


def test_mcp_get_candles_rejects_monthly_and_over_limit(tmp_path: Path) -> None:
    settings = build_settings(tmp_path)

    class FakeAccountClient:
        async def get_candles(self, instrument: str, granularity: str, count: int) -> pd.DataFrame:
            raise AssertionError("invalid requests should fail before provider access")

    service = BotMcpService(
        runtime=SimpleNamespace(settings=settings, account_client=FakeAccountClient()),
        settings=settings,
    )

    with pytest.raises(ValueError, match="Unsupported OANDA candle granularity"):
        asyncio.run(service.get_candles("SPX500_USD", "M", count=2))
    with pytest.raises(ValueError, match="less than or equal to 5000"):
        asyncio.run(service.get_candles("SPX500_USD", "H1", count=5001))


def test_mcp_get_candles_force_is_direct_fetch_compatibility_flag(tmp_path: Path) -> None:
    settings = build_settings(tmp_path)
    frame = build_candle_frame(count=2)

    class FakeAccountClient:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str, int]] = []

        async def get_candles(self, instrument: str, granularity: str, count: int) -> pd.DataFrame:
            self.calls.append((instrument, granularity, count))
            return frame

    runtime = SimpleNamespace(
        settings=settings,
        account_client=FakeAccountClient(),
    )
    service = BotMcpService(runtime=runtime, settings=settings)

    result = asyncio.run(service.get_candles("SPX500_USD", "H1", count=2, force=True))

    assert result["source"] == "oanda_api_direct"
    assert result["force"] is True
    assert result["freshness"] is None
    assert "no additional effect" in result["warning"]
    assert runtime.account_client.calls == [("SPX500_USD", "H1", 2)]


def test_mcp_get_vwap_returns_semantic_payload(tmp_path: Path) -> None:
    settings = build_settings(tmp_path)
    frame = build_candle_frame(timeframe="H1", count=72)

    class FakeMarketDataProvider:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str, int]] = []
            self.freshness_calls: list[tuple[str, str]] = []

        def get_candles(self, instrument: str, timeframe: str, count: int) -> pd.DataFrame:
            self.calls.append((instrument, timeframe, count))
            return frame

        def get_candle_freshness(self, instrument: str, timeframe: str) -> CandleFreshness:
            self.freshness_calls.append((instrument, timeframe))
            return CandleFreshness(
                instrument=instrument,
                timeframe=timeframe,
                last_completed_candle=BASE_TIME,
                fetched_at=BASE_TIME + timedelta(minutes=1),
                source="oanda_api",
                candle_count=len(frame),
                is_fresh=True,
                staleness_seconds=0.0,
            )

    runtime = SimpleNamespace(
        settings=settings,
        market_data_provider=FakeMarketDataProvider(),
    )
    service = BotMcpService(runtime=runtime, settings=settings)

    result = asyncio.run(service.get_vwap("SPX500_USD", "H1", "D", [2.0, 1.0]))

    assert any(spec["name"] == "get_vwap" for spec in TOOL_SPECS)
    assert result["instrument"] == "SPX500_USD"
    assert result["timeframe"] == "H1"
    assert result["anchor"] == "D"
    assert result["source"] == "oanda_api"
    assert result["volume_type"] == "tick_count"
    assert len(result["bands"]) == 2
    assert result["bands"][0]["deviation"] == 1.0
    assert "tick count" in result["caveat"]
    assert runtime.market_data_provider.calls
    assert runtime.market_data_provider.freshness_calls == [("SPX500_USD", "H1")]


def test_mcp_list_transfers_defaults_to_trailing_year_and_updates_tool_surface(tmp_path: Path) -> None:
    settings = build_settings(tmp_path)

    class FakeHistoryClient:
        def __init__(self) -> None:
            self.calls: list[tuple[datetime, datetime, str]] = []

        def fetch_transactions_for_window_sync(
            self,
            start_utc: datetime,
            end_utc: datetime,
            type_filter: str,
        ) -> list[dict[str, object]]:
            self.calls.append((start_utc, end_utc, type_filter))
            return [
                {
                    "id": "101",
                    "type": "TRANSFER_FUNDS",
                    "time": BASE_TIME - timedelta(days=2),
                    "amount": 100.0,
                    "fundingReason": "CLIENT_FUNDING",
                },
                {
                    "id": "102",
                    "type": "TRANSFER_FUNDS",
                    "time": BASE_TIME - timedelta(days=1),
                    "amount": -25.0,
                    "fundingReason": "ACCOUNT_TRANSFER",
                },
            ]

    history_client = FakeHistoryClient()
    runtime = SimpleNamespace(
        settings=settings,
        trade_history_service=SimpleNamespace(history_client=history_client),
    )
    service = BotMcpService(runtime=runtime, settings=settings)

    result = asyncio.run(service.list_transfers(limit=1))

    assert history_client.calls
    window_start_utc, window_end_utc, type_filter = history_client.calls[0]
    assert window_end_utc - window_start_utc == timedelta(days=365)
    assert type_filter == "TRANSFER_FUNDS"
    assert result["limit"] == 1
    assert result["returned_count"] == 1
    assert result["transfers"][0]["id"] == "102"
    assert result["transfers"][0]["fundingReason"] == "ACCOUNT_TRANSFER"
    assert any(spec["name"] == "list_transfers" for spec in TOOL_SPECS)
    assert "transfers" in BotMcpService.capabilities_payload()["surfaces"]["reads"]
    assert any(
        spec["name"] == "list_transfers"
        for spec in BotMcpService.tool_surface_payload(TOOL_SPECS)["tools"]
    )


def test_mcp_yfinance_tools_delegate_and_update_tool_surface(tmp_path: Path) -> None:
    settings = build_settings(tmp_path)

    class FakeYFinanceService:
        def __init__(self) -> None:
            self.search_calls: list[tuple[str, int, int, bool]] = []
            self.ticker_calls: list[tuple[str, bool, int]] = []
            self.history_calls: list[tuple[str, str | None, str, str | None, str | None, bool, bool, bool, int]] = []
            self.news_calls: list[tuple[str, int]] = []

        def search_tickers(
            self,
            query: str,
            *,
            limit: int,
            news_count: int,
            enable_fuzzy: bool,
        ) -> dict[str, object]:
            self.search_calls.append((query, limit, news_count, enable_fuzzy))
            return {
                "provider": "yfinance",
                "query": query,
                "limit": limit,
                "news_count": news_count,
                "enable_fuzzy": enable_fuzzy,
                "returned_count": 1,
                "quotes": [{"symbol": "SPY", "short_name": "SPDR S&P 500 ETF Trust"}],
                "news": [],
            }

        def get_ticker(
            self,
            symbol: str,
            *,
            include_news: bool,
            news_limit: int,
        ) -> dict[str, object]:
            self.ticker_calls.append((symbol, include_news, news_limit))
            return {
                "provider": "yfinance",
                "symbol": symbol,
                "quote": {"last_price": 655.83, "day_change": -0.09},
                "profile": {"long_name": "SPDR S&P 500 ETF Trust"},
                "calendar": {"earnings_date": [BASE_TIME.date()]},
                "available_option_expiration_count": 2,
                "options_expirations": ["2026-04-06", "2026-04-07"],
                "options_expirations_truncated": False,
                "news": (
                    [
                        {
                            "id": "story-1",
                            "title": "Sample story",
                            "published_at": BASE_TIME,
                        }
                    ]
                    if include_news
                    else []
                ),
                "warnings": [],
            }

        def get_history(
            self,
            symbol: str,
            *,
            period: str | None,
            interval: str,
            start: str | None,
            end: str | None,
            prepost: bool,
            actions: bool,
            auto_adjust: bool,
            max_rows: int,
        ) -> dict[str, object]:
            self.history_calls.append(
                (symbol, period, interval, start, end, prepost, actions, auto_adjust, max_rows)
            )
            return {
                "provider": "yfinance",
                "symbol": symbol,
                "period": period,
                "interval": interval,
                "start": start,
                "end": end,
                "prepost": prepost,
                "actions": actions,
                "auto_adjust": auto_adjust,
                "requested_max_rows": max_rows,
                "available_count": 3,
                "returned_count": 2,
                "truncated": True,
                "history": [
                    {"time": BASE_TIME - timedelta(days=1), "open": 650.0, "close": 652.0},
                    {"time": BASE_TIME, "open": 653.0, "close": 655.83},
                ],
            }

        def get_news(self, symbol: str, *, limit: int) -> dict[str, object]:
            self.news_calls.append((symbol, limit))
            return {
                "provider": "yfinance",
                "symbol": symbol,
                "limit": limit,
                "returned_count": 1,
                "news": [{"id": "story-1", "title": "Sample story", "published_at": BASE_TIME}],
            }

    fake_yfinance = FakeYFinanceService()
    runtime = SimpleNamespace(settings=settings)
    service = BotMcpService(runtime=runtime, settings=settings, yfinance_service=fake_yfinance)

    search_result = asyncio.run(
        service.search_yfinance_tickers("spy", limit=3, news_count=1, enable_fuzzy=True)
    )
    ticker_result = asyncio.run(service.get_yfinance_ticker("SPY", include_news=True, news_limit=1))
    history_result = asyncio.run(
        service.get_yfinance_history(
            "SPY",
            period="5d",
            interval="1d",
            start=None,
            end=None,
            prepost=False,
            actions=False,
            auto_adjust=True,
            max_rows=2,
        )
    )
    news_result = asyncio.run(service.get_yfinance_news("SPY", limit=1))

    assert fake_yfinance.search_calls == [("spy", 3, 1, True)]
    assert fake_yfinance.ticker_calls == [("SPY", True, 1)]
    assert fake_yfinance.history_calls == [("SPY", "5d", "1d", None, None, False, False, True, 2)]
    assert fake_yfinance.news_calls == [("SPY", 1)]
    assert search_result["quotes"][0]["symbol"] == "SPY"
    assert ticker_result["quote"]["last_price"] == 655.83
    assert isinstance(ticker_result["news"][0]["published_at"], str)
    assert history_result["returned_count"] == 2
    assert history_result["history"][1]["close"] == 655.83
    assert isinstance(history_result["history"][0]["time"], str)
    assert news_result["news"][0]["title"] == "Sample story"
    assert any(spec["name"] == "search_yfinance_tickers" for spec in TOOL_SPECS)
    assert any(spec["name"] == "get_yfinance_ticker" for spec in TOOL_SPECS)
    assert any(spec["name"] == "get_yfinance_history" for spec in TOOL_SPECS)
    assert any(spec["name"] == "get_yfinance_news" for spec in TOOL_SPECS)
    assert "yfinance" in BotMcpService.capabilities_payload()["surfaces"]["reads"]
    assert any(
        spec["name"] == "get_yfinance_history"
        for spec in BotMcpService.tool_surface_payload(TOOL_SPECS)["tools"]
    )


def test_mcp_get_ohlc_mid_uses_account_client_directly(tmp_path: Path) -> None:
    settings = build_settings(tmp_path)
    frame = build_candle_frame(count=2)

    class FakeAccountClient:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str, int]] = []

        async def get_candles(self, instrument: str, granularity: str, count: int) -> pd.DataFrame:
            self.calls.append((instrument, granularity, count))
            return frame

    runtime = SimpleNamespace(
        settings=settings,
        account_client=FakeAccountClient(),
    )
    service = BotMcpService(runtime=runtime, settings=settings)

    result = asyncio.run(service.get_ohlc("SPX500_USD", "H1", count=2, price_component="mid"))

    assert result["price_component"] == "mid"
    assert result["source"] == "oanda_api_direct"
    assert result["freshness"] is None
    assert result["bars"][0]["open"] == 3300.0
    assert "fetched directly from OANDA" in result["warning"]
    assert runtime.account_client.calls == [("SPX500_USD", "H1", 2)]


def test_mcp_get_ohlc_supports_bid_ask_mode(tmp_path: Path) -> None:
    settings = build_settings(tmp_path)
    frame = pd.DataFrame(
        {
            "time": pd.to_datetime([BASE_TIME - timedelta(hours=1), BASE_TIME], utc=True),
            "bid_open": [3300.0, 3301.0],
            "bid_high": [3301.0, 3302.0],
            "bid_low": [3299.0, 3300.0],
            "bid_close": [3300.5, 3301.5],
            "ask_open": [3300.2, 3301.2],
            "ask_high": [3301.2, 3302.2],
            "ask_low": [3299.2, 3300.2],
            "ask_close": [3300.7, 3301.7],
            "tick_volume": [100, 101],
        }
    )

    class FakeAccountClient:
        def __init__(self) -> None:
            self.bid_ask_calls: list[tuple[str, str, int]] = []

        async def get_bid_ask_candles(self, instrument: str, granularity: str, count: int) -> pd.DataFrame:
            self.bid_ask_calls.append((instrument, granularity, count))
            return frame

    runtime = SimpleNamespace(
        settings=settings,
        account_client=FakeAccountClient(),
    )
    service = BotMcpService(runtime=runtime, settings=settings)

    result = asyncio.run(service.get_ohlc("SPX500_USD", "H1", count=2, price_component="bid_ask"))

    assert result["price_component"] == "bid_ask"
    assert result["source"] == "oanda_api_bid_ask_direct"
    assert result["freshness"] is None
    assert result["bars"][0]["bid_open"] == 3300.0
    assert result["bars"][0]["ask_close"] == 3300.7
    assert runtime.account_client.bid_ask_calls == [("SPX500_USD", "H1", 2)]


def test_run_application_with_mcp_uses_embedded_server_and_disables_access_log(tmp_path: Path) -> None:
    settings = build_settings(tmp_path)
    events: list[str] = []
    stop_event = asyncio.Event()
    app = None

    class DummyUpdater:
        def __init__(self) -> None:
            self.running = False
            self.error_callback = None

        async def start_polling(self, *, error_callback=None):
            self.running = True
            self.error_callback = error_callback
            events.append("updater.start")

        async def stop(self):
            self.running = False
            events.append("updater.stop")

    class DummyApplication:
        def __init__(self) -> None:
            self.bot_data = {"bot_runtime": SimpleNamespace(settings=settings)}
            self.updater = DummyUpdater()
            self.running = False
            self.post_init = self._post_init
            self.post_stop = None
            self.post_shutdown = self._post_shutdown

        async def initialize(self):
            events.append("initialize")

        async def start(self):
            self.running = True
            events.append("start")

        async def stop(self):
            self.running = False
            events.append("stop")

        async def shutdown(self):
            events.append("shutdown")

        async def _post_init(self, _application):
            events.append("post_init")

        async def _post_shutdown(self, _application):
            events.append("post_shutdown")

    class DummyConfig:
        def __init__(self, app, **kwargs):
            self.app = app
            self.kwargs = kwargs

    class DummyServer:
        last_instance = None

        def __init__(self, config):
            self.config = config
            self.should_exit = False
            DummyServer.last_instance = self

        async def serve(self):
            events.append("server.serve")
            while not self.should_exit:
                await asyncio.sleep(0.01)
            events.append("server.exit")

    async def run_test() -> None:
        nonlocal app
        trigger = asyncio.create_task(_trigger_stop(stop_event))
        app = DummyApplication()
        await _run_application_with_mcp(
            app,
            settings,
            stop_event=stop_event,
            config_factory=DummyConfig,
            server_factory=DummyServer,
        )
        await trigger

    asyncio.run(run_test())

    assert events[:5] == ["initialize", "post_init", "updater.start", "start", "server.serve"]
    assert "updater.stop" in events
    assert "stop" in events
    assert events[-1] == "post_shutdown"
    assert DummyServer.last_instance is not None
    assert DummyServer.last_instance.config.kwargs["access_log"] is False
    assert DummyServer.last_instance.config.kwargs["host"] == "0.0.0.0"
    assert DummyServer.last_instance.config.kwargs["port"] == 8080
    assert app is not None
    assert app.updater.error_callback is not None


async def _trigger_stop(stop_event: asyncio.Event) -> None:
    await asyncio.sleep(0.05)
    stop_event.set()


def test_mcp_tool_surface_includes_expansion_tools() -> None:
    tool_names = {spec["name"] for spec in TOOL_SPECS}
    capabilities = BotMcpService.capabilities_payload()

    assert {
        "get_macro_context",
        "get_trade_stats",
        "get_spread_snapshot",
        "get_correlation",
        "clear_all_price_alerts",
        "replace_alert_grid",
        "clear_all_indicator_alerts",
        "list_alert_history",
    }.issubset(tool_names)
    assert {
        "get_market_context_pack",
        "get_instrument_context_pack",
        "get_historical_bars",
        "get_" + "htf_bias",
        "get_" + "trade_plan",
        "get_" + "sfp",
        "get_" + "turtle_soup",
        "get_" + "support_resistance",
        "get_" + "fibonacci",
    }.isdisjoint(tool_names)
    assert "market_context_pack" not in capabilities["surfaces"]["reads"]
    assert "instrument_context_pack" not in capabilities["surfaces"]["reads"]
    assert "historical_candles" not in capabilities["surfaces"]["reads"]
    assert "raw_oanda_candles" in capabilities["surfaces"]["reads"]
    assert capabilities["raw_oanda_candle_granularities"][0] == "S5"
    assert capabilities["raw_oanda_candle_granularities"][-1] == "W"
    assert "M" not in capabilities["raw_oanda_candle_granularities"]
    assert "macro_context" in capabilities["surfaces"]["reads"]
    assert "spread_snapshot" in capabilities["surfaces"]["reads"]
    assert "trade_stats" in capabilities["surfaces"]["reads"]
    assert "alert_history" in capabilities["surfaces"]["reads"]
    assert "correlation" in capabilities["surfaces"]["reads"]
    assert "price_alert_grids" in capabilities["surfaces"]["writes"]


def test_mcp_get_macro_context_delegates_force_flag(tmp_path: Path) -> None:
    settings = build_settings(tmp_path)

    class FakeScanOrchestrator:
        def __init__(self) -> None:
            self.calls: list[bool] = []

        def refresh_macro(self, *, force: bool = False):
            self.calls.append(force)
            return {"force": force, "vix": {"value": 31.05}}

    runtime = SimpleNamespace(settings=settings, scan_orchestrator=FakeScanOrchestrator())
    service = BotMcpService(runtime=runtime, settings=settings)

    result = asyncio.run(service.get_macro_context(force=True))

    assert result["force"] is True
    assert result["vix"]["value"] == 31.05
    assert runtime.scan_orchestrator.calls == [True]


def test_mcp_get_trade_history_forwards_start_and_end_dates(tmp_path: Path) -> None:
    settings = build_settings(tmp_path)

    class FakeTradeHistoryService:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str, str | None, int, str | None, str | None]] = []

        def get_trade_history(
            self,
            period: str,
            view: str,
            instrument: str | None,
            page: int,
            start_date: str | None = None,
            end_date: str | None = None,
        ):
            self.calls.append((period, view, instrument, page, start_date, end_date))
            return {"period": "custom:2026-04-01:2026-04-01", "rows": []}

    runtime = SimpleNamespace(settings=settings, trade_history_service=FakeTradeHistoryService())
    service = BotMcpService(runtime=runtime, settings=settings)

    result = asyncio.run(
        service.get_trade_history(
            "day",
            "all",
            "spx500usd",
            2,
            start_date="2026-04-01",
            end_date="2026-04-01",
        )
    )

    assert result["period"] == "custom:2026-04-01:2026-04-01"
    assert runtime.trade_history_service.calls == [
        ("day", "all", "SPX500_USD", 2, "2026-04-01", "2026-04-01")
    ]


def test_mcp_price_batch_tools_and_alert_history_use_default_chat_scope(tmp_path: Path) -> None:
    settings = build_settings(tmp_path, MCP_DEFAULT_CHAT_ID="777")
    store = TradeStore(db_path=settings.tinydb_path, settings=settings)
    alert_repository = AlertRepository(store=store)
    runtime = SimpleNamespace(settings=settings, alert_repository=alert_repository)
    service = BotMcpService(runtime=runtime, settings=settings)

    created = asyncio.run(service.create_price_alert("spx500usd", target_price=3350.0, direction="above"))
    other_chat = alert_repository.upsert_price_alert(
        {
            "instrument": "SPX500_USD",
            "target_price": 3360.0,
            "direction": "above",
            "chat_id": 999,
            "created_at": BASE_TIME,
        }
    )

    cleared = asyncio.run(service.clear_all_price_alerts(confirm=True, instrument="spx500usd"))
    replaced = asyncio.run(
        service.replace_alert_grid(
            "spx500usd",
            alerts=[
                {"target_price": 3345.0, "direction": "below", "note": "retest"},
                {"target_price": 3365.0, "direction": "above"},
            ],
            confirm=True,
        )
    )

    alert_repository.insert_alert_history(
        AlertHistoryRecord(
            id=1,
            alert_type="price",
            alert_id=created["id"],
            chat_id=777,
            instrument="SPX500_USD",
            granularity=None,
            indicator=None,
            triggered_at=BASE_TIME,
            trigger_value=3350.0,
            alert_snapshot={"direction": "above"},
            trigger_context={"bid": 3349.8},
        )
    )
    alert_repository.insert_alert_history(
        AlertHistoryRecord(
            id=2,
            alert_type="price",
            alert_id=other_chat.id,
            chat_id=999,
            instrument="SPX500_USD",
            granularity=None,
            indicator=None,
            triggered_at=BASE_TIME + timedelta(minutes=1),
            trigger_value=3360.0,
            alert_snapshot={"direction": "above"},
            trigger_context={"bid": 3359.8},
        )
    )
    history = asyncio.run(service.list_alert_history(alert_type="price", limit=10))
    pending = alert_repository.list_pending_price_alerts_for_chat(777)

    assert cleared["chat_id"] == 777
    assert cleared["cleared_count"] == 1
    assert replaced["chat_id"] == 777
    assert replaced["cleared_count"] == 0
    assert replaced["created_count"] == 2
    assert {(item.instrument, item.target_price) for item in pending} == {
        ("SPX500_USD", 3345.0),
        ("SPX500_USD", 3365.0),
    }
    assert alert_repository.get_price_alert(other_chat.id).status == AlertStatus.PENDING
    assert history["returned_count"] == 1
    assert history["entries"][0]["chat_id"] == 777

    store.close()


def test_mcp_clear_all_indicator_alerts_uses_default_chat_scope(tmp_path: Path) -> None:
    settings = build_settings(tmp_path, MCP_DEFAULT_CHAT_ID="777")
    store = TradeStore(db_path=settings.tinydb_path, settings=settings)
    alert_repository = AlertRepository(store=store)
    runtime = SimpleNamespace(settings=settings, alert_repository=alert_repository)
    service = BotMcpService(runtime=runtime, settings=settings)

    own = alert_repository.upsert_indicator_alert(
        {
            "instrument": "EUR_USD",
            "granularity": "H1",
            "indicator": IndicatorKind.RSI,
            "condition": "below",
            "threshold": 30.0,
            "chat_id": 777,
            "created_at": BASE_TIME,
            "status": AlertStatus.FIRED,
            "fired_at": BASE_TIME,
        }
    )
    other = alert_repository.upsert_indicator_alert(
        {
            "instrument": "EUR_USD",
            "granularity": "H1",
            "indicator": IndicatorKind.RSI,
            "condition": "below",
            "threshold": 30.0,
            "chat_id": 999,
            "created_at": BASE_TIME,
        }
    )

    cleared = asyncio.run(
        service.clear_all_indicator_alerts(
            confirm=True,
            instrument="EUR_USD",
            timeframe="H1",
            indicator="rsi",
        )
    )

    assert cleared["chat_id"] == 777
    assert cleared["cleared_count"] == 1
    assert cleared["cleared_alerts"][0]["id"] == own.id
    assert alert_repository.get_indicator_alert(own.id).status == AlertStatus.CANCELLED
    assert alert_repository.get_indicator_alert(other.id).status == AlertStatus.PENDING

    store.close()


def test_mcp_get_price_records_spread_history_for_explicit_read(tmp_path: Path) -> None:
    settings = build_settings(tmp_path)
    store = TradeStore(db_path=settings.tinydb_path, settings=settings)
    live_tick = PriceTick(
        instrument="SPX500_USD",
        bid=3050.10,
        ask=3050.40,
        time=BASE_TIME,
    )

    class FailingAccountClient:
        async def get_pricing(self, instrument: str):
            raise AssertionError("REST pricing should not be used when a fresh live quote exists.")

    runtime = SimpleNamespace(
        settings=settings,
        trade_store=store,
        account_client=FailingAccountClient(),
        stream_task=SimpleNamespace(latest_quote=lambda instrument, max_age_seconds=None: live_tick),
    )
    service = BotMcpService(runtime=runtime, settings=settings)

    result = asyncio.run(service.get_price("spx500usd", prefer_live=True))
    history = store.get_recent_spreads("SPX500_USD", limit=5)

    assert result["source"] == "live_stream"
    assert history[0]["reason"] == "mcp_get_price"
    assert history[0]["source"] == "live_stream"
    assert "is_" + "acceptable" not in history[0]
    assert "is_" + "spiking" not in history[0]
    assert history[0]["spread_pips"] == pytest.approx(0.3)

    store.close()


def test_mcp_get_spread_snapshot_records_history_and_enforces_live_contract(tmp_path: Path) -> None:
    settings = build_settings(tmp_path)
    store = TradeStore(db_path=settings.tinydb_path, settings=settings)
    live_tick = PriceTick(
        instrument="SPX500_USD",
        bid=3050.10,
        ask=3050.40,
        time=BASE_TIME,
    )

    class FakeAccountClient:
        async def get_pricing(self, instrument: str):
            return SimpleNamespace(
                bid=3049.90,
                ask=3050.20,
                spread_pips=0.3,
                fetched_at=BASE_TIME,
            )

    runtime = SimpleNamespace(
        settings=settings,
        trade_store=store,
        account_client=FakeAccountClient(),
        stream_task=SimpleNamespace(latest_quote=lambda instrument, max_age_seconds=None: live_tick),
    )
    service = BotMcpService(runtime=runtime, settings=settings)

    result = asyncio.run(
        service.get_spread_snapshot(
            "spx500usd",
            include_history=True,
            history_limit=5,
            prefer_live=True,
            require_live=True,
        )
    )

    assert result["quote_source"] == "live_stream"
    assert result["include_history"] is True
    assert result["current"]["spread_pips"] == pytest.approx(0.3)
    assert result["history"][0]["reason"] == "mcp_get_spread_snapshot"
    assert result["history"][0]["source"] == "live_stream"

    runtime.stream_task = SimpleNamespace(latest_quote=lambda instrument, max_age_seconds=None: None)
    service = BotMcpService(runtime=runtime, settings=settings)
    with pytest.raises(ValueError, match="Live stream quote unavailable or stale"):
        asyncio.run(service.get_spread_snapshot("spx500usd", require_live=True))

    store.close()


def test_mcp_get_trade_stats_delegates_to_trade_stats_service(tmp_path: Path) -> None:
    settings = build_settings(tmp_path)
    runtime = SimpleNamespace(settings=settings)
    service = BotMcpService(runtime=runtime, settings=settings)
    calls: list[tuple[str, str | None, str | None, str | None]] = []

    def fake_get_trade_stats(
        period: str,
        *,
        start_date: str | None = None,
        end_date: str | None = None,
        instrument: str | None = None,
    ):
        calls.append((period, start_date, end_date, instrument))
        return {"summary": {"period": "custom:2026-04-01:2026-04-01", "trade_count": 2}, "per_instrument": []}

    service._trade_stats_service = SimpleNamespace(get_trade_stats=fake_get_trade_stats)

    result = asyncio.run(
        service.get_trade_stats(
            "day",
            start_date="2026-04-01",
            end_date="2026-04-01",
            instrument="spx500usd",
        )
    )

    assert result["summary"]["trade_count"] == 2
    assert calls == [("day", "2026-04-01", "2026-04-01", "SPX500_USD")]


def test_mcp_get_correlation_delegates_to_correlation_service(tmp_path: Path) -> None:
    settings = build_settings(tmp_path)
    runtime = SimpleNamespace(settings=settings)
    service = BotMcpService(runtime=runtime, settings=settings)
    calls: list[tuple[str, str, str, int, str]] = []

    class FakeCorrelationService:
        async def get_correlation(
            self,
            primary: str,
            secondary: str,
            *,
            timeframe: str = "D",
            lookback: int = 60,
            secondary_transform: str = "raw",
        ):
            calls.append((primary, secondary, timeframe, lookback, secondary_transform))
            return {"primary": primary, "secondary": secondary, "correlation": -0.42}

    service._correlation_service = FakeCorrelationService()

    result = asyncio.run(
        service.get_correlation(
            "spx500usd",
            "USD_JPY",
            timeframe="D",
            lookback=20,
            secondary_transform="inverse",
        )
    )

    assert result["correlation"] == -0.42
    assert calls == [("spx500usd", "USD_JPY", "D", 20, "inverse")]
