from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

from core.candle_policy import get_timeframe_delta
from core.market_state import MarketStateStore
from core.models import (
    ActiveZoneSummary,
    IndicatorValueSummary,
    LiquidityPoolSummary,
    OrderBlockSummary,
    PriceAlert,
    SnapshotFreshness,
    SmcContextSummary,
    SpreadResult,
    StructureEventSummary,
    TimeframeSnapshot,
    TradeRecord,
)
from tests.unit.test_chart_renderer import (
    as_mapping,
    build_request,
    build_renderer,
    build_settings,
    build_trade,
    build_alert,
    build_pending_order,
    get_first_present,
    get_renderer_method,
)


BASE_TIME = datetime(2026, 3, 21, 8, 0, tzinfo=timezone.utc)


def build_candles(
    *,
    timeframe: str = "H1",
    closes: list[float],
    end_time: datetime = BASE_TIME,
) -> pd.DataFrame:
    delta = get_timeframe_delta(timeframe)
    rows: list[dict[str, object]] = []
    for index, close in enumerate(closes):
        candle_time = end_time - delta * (len(closes) - 1 - index)
        open_price = closes[index - 1] if index > 0 else close - 0.10
        rows.append(
            {
                "time": candle_time,
                "open": open_price,
                "high": max(open_price, close) + 0.15,
                "low": min(open_price, close) - 0.15,
                "close": close,
                "tick_volume": 100 + index,
            }
        )
    return pd.DataFrame(rows)


def build_snapshot(
    *,
    instrument: str = "EUR_USD",
    timeframe: str = "H1",
    last_completed_candle: datetime = BASE_TIME,
    order_block_upper_price: float = 100.40,
    order_block_lower_price: float = 100.20,
) -> TimeframeSnapshot:
    delta = get_timeframe_delta(timeframe)
    order_block = OrderBlockSummary(
        direction="BULLISH",
        upper_price=order_block_upper_price,
        lower_price=order_block_lower_price,
        created_at=last_completed_candle - delta,
        distance_pips=4.0,
        is_mitigated=False,
    )
    return TimeframeSnapshot(
        instrument=instrument,
        timeframe=timeframe,
        last_completed_candle=last_completed_candle,
        computed_at=last_completed_candle + timedelta(minutes=1),
        candle_range_start=last_completed_candle - delta,
        candle_range_end=last_completed_candle,
        indicators=IndicatorValueSummary(),
        structure=StructureEventSummary(),
        zones=ActiveZoneSummary(order_blocks=(order_block,)),
        liquidity=LiquidityPoolSummary(),
        smc_context=SmcContextSummary(),
        spread=SpreadResult(
            instrument=instrument,
            bid=100.00,
            ask=100.02,
            raw_spread=0.02,
            spread_pips=2.0,
            pip_size=0.01,
            fetched_at=BASE_TIME,
        ),
        freshness=SnapshotFreshness(
            instrument=instrument,
            timeframe=timeframe,
            last_completed_candle=last_completed_candle,
            fetched_at=last_completed_candle + timedelta(minutes=5),
            source="oanda_api",
            candle_count=500,
            is_fresh=False,
            staleness_seconds=3600.0,
        ),
    )


def test_state_first_refresh_precedes_smc_payload_building(tmp_path: Path) -> None:
    settings = build_settings(tmp_path)
    market_state = MarketStateStore()
    stale_snapshot = build_snapshot()
    market_state.publish_snapshot(stale_snapshot)
    candles = build_candles(closes=[100.0 + index * 0.25 for index in range(40)])
    call_order: list[str] = []

    class RecordingProvider:
        def get_candles(self, instrument: str, timeframe: str, count: int | None = None):
            call_order.append("get_candles")
            return candles

        def get_current_price(self, instrument: str):
            call_order.append("get_current_price")
            return SimpleNamespace(
                instrument=instrument,
                bid=101.0,
                ask=101.1,
                spread_price=0.1,
                spread_pips=1.0,
                fetched_at=BASE_TIME,
            )

        def get_candle_freshness(self, instrument: str, timeframe: str):
            call_order.append("get_candle_freshness")
            return stale_snapshot.freshness

    class RecordingScanOrchestrator:
        def refresh_snapshot(self, instrument: str, timeframe: str):
            call_order.append("refresh_snapshot")
            refreshed = build_snapshot(last_completed_candle=BASE_TIME + timedelta(hours=1))
            market_state.publish_snapshot(refreshed)
            return refreshed

    renderer = build_renderer(
        settings=settings,
        market_state=market_state,
        market_data_provider=RecordingProvider(),
        scan_orchestrator=RecordingScanOrchestrator(),
        trade_repository=SimpleNamespace(list_open=lambda: []),
        alert_repository=SimpleNamespace(list_pending_price_alerts=lambda: []),
        account_client=SimpleNamespace(get_open_orders=lambda: []),
    )

    build_render_payload = get_renderer_method(
        renderer,
        ("build_render_payload", "prepare_render_payload", "_build_render_payload"),
    )
    payload = build_render_payload(
        build_request(
            instrument="EUR_USD",
            smc=("orderblocks", "structure", "liquidity"),
            trade=("positions",),
            alert=("pricealerts",),
        )
    )
    resolved = as_mapping(payload)

    assert call_order[0] == "refresh_snapshot"
    assert "get_candles" in call_order
    assert call_order.index("refresh_snapshot") < call_order.index("get_candles")
    assert get_first_present(resolved, ("omitted_layers", "clipped_layers", "omitted_overlays")) == ()
    order_blocks = get_first_present(resolved, ("order_block_annotations", "order_blocks", "smc_order_blocks"))
    first_order_block = order_blocks[0] if isinstance(order_blocks, (list, tuple)) else order_blocks
    assert get_first_present(first_order_block, ("anchor_time", "created_at", "time", "start_time")) == (
        BASE_TIME
    )


def test_runtime_overlays_filter_to_requested_instrument(tmp_path: Path) -> None:
    settings = build_settings(tmp_path)
    market_state = MarketStateStore()
    market_state.publish_snapshot(build_snapshot())
    candles = build_candles(closes=[100.0 + index * 0.25 for index in range(40)])

    class RecordingProvider:
        def get_candles(self, instrument: str, timeframe: str, count: int | None = None):
            return candles

        def get_current_price(self, instrument: str):
            return SimpleNamespace(
                instrument=instrument,
                bid=101.0,
                ask=101.1,
                spread_price=0.1,
                spread_pips=1.0,
                fetched_at=BASE_TIME,
            )

        def get_candle_freshness(self, instrument: str, timeframe: str):
            return SimpleNamespace(
                instrument=instrument,
                timeframe=timeframe,
                last_completed_candle=BASE_TIME,
                fetched_at=BASE_TIME,
                source="oanda_api",
                candle_count=len(candles),
                is_fresh=True,
                staleness_seconds=0.0,
            )

    class RecordingScanOrchestrator:
        def refresh_snapshot(self, instrument: str, timeframe: str):
            return build_snapshot(last_completed_candle=BASE_TIME + timedelta(hours=1))

    renderer = build_renderer(
        settings=settings,
        market_state=market_state,
        market_data_provider=RecordingProvider(),
        scan_orchestrator=RecordingScanOrchestrator(),
        trade_repository=SimpleNamespace(
            list_open=lambda: [
                build_trade(instrument="EUR_USD"),
                build_trade(trade_id="other", instrument="SPX500_USD"),
            ]
        ),
        alert_repository=SimpleNamespace(
            list_pending_price_alerts=lambda: [
                build_alert(instrument="EUR_USD"),
                build_alert(id=2, instrument="SPX500_USD"),
            ]
        ),
        account_client=SimpleNamespace(
            get_open_orders=lambda: [
                build_pending_order("EUR_USD"),
                build_pending_order("SPX500_USD"),
            ]
        ),
    )

    build_render_payload = get_renderer_method(
        renderer,
        ("build_render_payload", "prepare_render_payload", "_build_render_payload"),
    )
    payload = build_render_payload(
        build_request(
            instrument="EUR_USD",
            smc=("orderblocks",),
            trade=("positions", "orders", "sl", "tp", "gslo"),
            alert=("pricealerts",),
        )
    )
    resolved = as_mapping(payload)

    trades = get_first_present(resolved, ("trade_overlays", "trades", "open_trades"))
    orders = get_first_present(resolved, ("order_overlays", "pending_orders", "orders"))
    alerts = get_first_present(resolved, ("price_alert_overlays", "price_alerts", "alerts"))

    assert len(trades) == 1
    assert len(orders) == 1
    assert len(alerts) == 1
    assert all(get_first_present(item, ("instrument",)) == "EUR_USD" for item in trades)
    assert all(get_first_present(item, ("instrument",)) == "EUR_USD" for item in orders)
    assert all(get_first_present(item, ("instrument",)) == "EUR_USD" for item in alerts)


def test_chart_payload_filters_price_alerts_to_request_chat(tmp_path: Path) -> None:
    settings = build_settings(tmp_path)
    market_state = MarketStateStore()
    market_state.publish_snapshot(build_snapshot())
    candles = build_candles(closes=[100.0 + index * 0.25 for index in range(40)])

    class RecordingProvider:
        def get_candles(self, instrument: str, timeframe: str, count: int | None = None):
            return candles

        def get_current_price(self, instrument: str):
            return SimpleNamespace(
                instrument=instrument,
                bid=101.0,
                ask=101.1,
                spread_price=0.1,
                spread_pips=1.0,
                fetched_at=BASE_TIME,
            )

        def get_candle_freshness(self, instrument: str, timeframe: str):
            return SimpleNamespace(
                instrument=instrument,
                timeframe=timeframe,
                last_completed_candle=BASE_TIME,
                fetched_at=BASE_TIME,
                source="oanda_api",
                candle_count=len(candles),
                is_fresh=True,
                staleness_seconds=0.0,
            )

    class RecordingScanOrchestrator:
        def refresh_snapshot(self, instrument: str, timeframe: str):
            return build_snapshot(last_completed_candle=BASE_TIME + timedelta(hours=1))

    class ChatScopedAlerts:
        def list_pending_price_alerts(self):
            raise AssertionError("chart payload should use the chat-scoped alert query")

        def list_pending_price_alerts_for_chat(self, chat_id: int):
            assert chat_id == 123
            return [
                build_alert(id=1, instrument="EUR_USD", chat_id=123, target_price=101.5),
                build_alert(id=2, instrument="EUR_USD", chat_id=999, target_price=101.8),
            ][:1]

    renderer = build_renderer(
        settings=settings,
        market_state=market_state,
        market_data_provider=RecordingProvider(),
        scan_orchestrator=RecordingScanOrchestrator(),
        trade_repository=SimpleNamespace(list_open=lambda: []),
        alert_repository=ChatScopedAlerts(),
        account_client=SimpleNamespace(get_open_orders=lambda: []),
    )

    build_render_payload = get_renderer_method(
        renderer,
        ("build_render_payload", "prepare_render_payload", "_build_render_payload"),
    )
    payload = build_render_payload(
        build_request(
            instrument="EUR_USD",
            chat_id=123,
            alert=("pricealerts",),
        )
    )
    resolved = as_mapping(payload)
    alerts = get_first_present(resolved, ("price_alert_overlays", "price_alerts", "alerts"))

    assert len(alerts) == 1
    assert get_first_present(alerts[0], ("alert_id", "id")) == 1


def test_chart_payload_includes_stale_snapshot_warning(tmp_path: Path) -> None:
    settings = build_settings(tmp_path)
    market_state = MarketStateStore()
    stale_snapshot = build_snapshot()
    market_state.publish_snapshot(stale_snapshot)
    candles = build_candles(closes=[100.0 + index * 0.25 for index in range(40)])

    class RecordingProvider:
        def get_candles(self, instrument: str, timeframe: str, count: int | None = None):
            return candles

        def get_current_price(self, instrument: str):
            return SimpleNamespace(
                instrument=instrument,
                bid=101.0,
                ask=101.1,
                spread_price=0.1,
                spread_pips=1.0,
                fetched_at=BASE_TIME,
            )

        def get_candle_freshness(self, instrument: str, timeframe: str):
            return stale_snapshot.freshness

    renderer = build_renderer(
        settings=settings,
        market_state=market_state,
        market_data_provider=RecordingProvider(),
        scan_orchestrator=SimpleNamespace(refresh_snapshot=lambda instrument, timeframe: stale_snapshot),
        trade_repository=SimpleNamespace(list_open=lambda: []),
        alert_repository=SimpleNamespace(list_pending_price_alerts=lambda: []),
        account_client=SimpleNamespace(get_open_orders=lambda: []),
    )

    build_render_payload = get_renderer_method(
        renderer,
        ("build_render_payload", "prepare_render_payload", "_build_render_payload"),
    )
    payload = build_render_payload(
        build_request(
            instrument="EUR_USD",
            overlays=("clean",),
        )
    )
    resolved = as_mapping(payload)

    assert get_first_present(resolved, ("warning_text",)) == "Warning: chart overlays use stale snapshot state."
