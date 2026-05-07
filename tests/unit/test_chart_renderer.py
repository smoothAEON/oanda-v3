from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import inspect
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

import pandas as pd
import pytest
from pydantic import ValidationError

from config.settings import load_settings
from core.candle_policy import get_timeframe_delta
from core.enums import AlertStatus, ChartMode, TradeState
from core.instrument_registry import get_instrument_spec
from core.market_state import MarketStateStore
from core.models import (
    ActiveZoneSummary,
    IndicatorValueSummary,
    LiquidityPoolSummary,
    OrderBlockSummary,
    PendingOrder,
    PriceAlert,
    SnapshotFreshness,
    SmcContextSummary,
    SpreadResult,
    StructureEventSummary,
    TimeframeSnapshot,
    TradeRecord,
)
renderer_module = pytest.importorskip(
    "charting.renderer",
    reason="Stage 12 chart renderer is not available on this branch yet.",
)

ChartRenderer = getattr(renderer_module, "ChartRenderer", None)
if ChartRenderer is None:
    pytest.skip("Stage 12 chart renderer is not available on this branch yet.", allow_module_level=True)


BASE_TIME = datetime(2026, 3, 21, 8, 0, tzinfo=timezone.utc)


def build_settings(tmp_path: Path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "OANDA_API_KEY=api-key",
                "OANDA_ACCOUNT_ID=account-id",
                "OANDA_ENVIRONMENT=practice",
                "TELEGRAM_BOT_TOKEN=telegram-token",
                "TELEGRAM_CHAT_ID=123456789",
                "TELEGRAM_BOT_PASSWORD=bot-password",
                "TELEGRAM_ADMIN_IDS=111,222",
                "TINYDB_PATH=data/bot.json",
            ]
        ),
        encoding="utf-8",
    )
    return load_settings(env_file=env_file)


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


def build_spread(instrument: str = "EUR_USD") -> SpreadResult:
    spec = get_instrument_spec(instrument)
    bid = 1.1000
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


def build_snapshot(
    *,
    instrument: str = "EUR_USD",
    timeframe: str = "H1",
    last_completed_candle: datetime = BASE_TIME,
    order_block_time: datetime | None = None,
) -> TimeframeSnapshot:
    delta = get_timeframe_delta(timeframe)
    order_block_time = order_block_time or (last_completed_candle - delta)
    latest_block = OrderBlockSummary(
        direction="BULLISH",
        upper_price=1.1020,
        lower_price=1.1000,
        created_at=order_block_time,
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
        zones=ActiveZoneSummary(order_blocks=(latest_block,)),
        liquidity=LiquidityPoolSummary(),
        smc_context=SmcContextSummary(),
        spread=build_spread(instrument),
        freshness=SnapshotFreshness(
            instrument=instrument,
            timeframe=timeframe,
            last_completed_candle=last_completed_candle,
            fetched_at=last_completed_candle + timedelta(minutes=5),
            source="oanda_api",
            candle_count=500,
            is_fresh=True,
            staleness_seconds=0.0,
        ),
    )


def build_renderer(**kwargs):
    signature = inspect.signature(ChartRenderer)
    if any(parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in signature.parameters.values()):
        return ChartRenderer(**kwargs)
    aliases = {
        "market_state": ("market_state", "state_store", "market_state_store"),
        "market_data_provider": ("market_data_provider", "provider", "data_provider"),
        "scan_orchestrator": ("scan_orchestrator", "orchestrator"),
        "trade_repository": ("trade_repository", "trades", "trade_store"),
        "alert_repository": ("alert_repository", "alerts", "alert_store"),
        "account_client": ("account_client", "execution_client"),
        "settings": ("settings",),
    }
    accepted: dict[str, object] = {}
    for canonical, candidates in aliases.items():
        if canonical not in kwargs:
            continue
        for candidate in candidates:
            if candidate in signature.parameters:
                accepted[candidate] = kwargs[canonical]
                break
    for name, value in kwargs.items():
        if name in aliases:
            continue
        if name in signature.parameters:
            accepted[name] = value
    return ChartRenderer(**accepted)


def build_request(**overrides):
    base_payload = {
        "instrument": "spx500usd",
        "timeframe": "h1",
        "count": 500,
    }
    if hasattr(renderer_module, "ChartRequest"):
        request_type = renderer_module.ChartRequest
        selector_keys = {"smc", "trade", "alert", "indicator"}
        selector_payload = {key: overrides[key] for key in selector_keys if key in overrides}
        base_payload.update({key: value for key, value in overrides.items() if key not in selector_keys})
        candidate_payloads = []
        if selector_payload:
            candidate_payloads.extend(
                [
                    {**base_payload, "selection": selector_payload},
                    {**base_payload, "overlay_selection": selector_payload},
                    {**base_payload, "overlays": selector_payload},
                ]
            )
        else:
            candidate_payloads.append(base_payload)
            candidate_payloads.extend(
                [
                    {**base_payload, "selection": {}},
                    {**base_payload, "overlay_selection": {}},
                    {**base_payload, "overlays": {}},
                ]
            )
        last_error: Exception | None = None
        for payload in candidate_payloads:
            try:
                if hasattr(request_type, "model_validate"):
                    return request_type.model_validate(payload)
                signature = inspect.signature(request_type)
                if any(parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in signature.parameters.values()):
                    return request_type(**payload)
                accepted = {
                    name: value
                    for name, value in payload.items()
                    if name in signature.parameters
                }
                return request_type(**accepted)
            except Exception as exc:  # pragma: no cover - exercised when variants miss shape
                last_error = exc
                continue
        assert last_error is not None
        raise last_error
    raise AssertionError("ChartRequest is not defined in charting.renderer.")


def as_mapping(value):
    if isinstance(value, pd.DataFrame):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="python")
    if isinstance(value, dict):
        return value
    if hasattr(value, "__dict__"):
        return dict(vars(value))
    return value


def get_first_present(obj, names):
    for name in names:
        if isinstance(obj, dict) and name in obj:
            return obj[name]
        if hasattr(obj, name):
            return getattr(obj, name)
    raise AssertionError(f"None of {names} were present on {type(obj)!r}.")


def get_request_selection(request):
    for name in ("overlay_selection", "selection", "overlays", "overlay_keys"):
        if hasattr(request, name):
            return getattr(request, name)
    return None


def selection_keys(selection) -> tuple[str, ...]:
    if selection is None:
        return tuple()
    if isinstance(selection, dict):
        if "keys" in selection:
            return tuple(selection["keys"])
        return tuple(selection.keys())
    if hasattr(selection, "model_dump"):
        selection = selection.model_dump(mode="python")
        return selection_keys(selection)
    for candidate in ("keys", "resolved_keys", "selected_keys", "overlay_keys", "layers"):
        if hasattr(selection, candidate):
            value = getattr(selection, candidate)
            if callable(value):
                value = value()
            return tuple(value)
    if isinstance(selection, tuple):
        return selection
    raise AssertionError(f"Could not resolve selection keys from {type(selection)!r}.")


def build_trade(**overrides) -> TradeRecord:
    payload = {
        "trade_id": "trade-1",
        "instrument": "EUR_USD",
        "units": 1.0,
        "open_price": 1.1000,
        "state": TradeState.OPEN,
        "opened_at": BASE_TIME,
    }
    payload.update(overrides)
    return TradeRecord.model_validate(payload)


def build_alert(**overrides) -> PriceAlert:
    payload = {
        "id": 1,
        "instrument": "EUR_USD",
        "target_price": 1.1010,
        "direction": "above",
        "status": AlertStatus.PENDING,
        "chat_id": 123,
        "created_at": BASE_TIME,
        "fired_at": None,
    }
    payload.update(overrides)
    return PriceAlert.model_validate(payload)


def build_pending_order(instrument: str = "EUR_USD") -> PendingOrder:
    return PendingOrder(
        order_id="order-1",
        instrument=instrument,
        units=1000.0,
        price=1.1015,
        order_type="LIMIT",
        state="PENDING",
        stop_loss_price=1.0950,
        take_profit_price=1.1100,
        gslo_price=None,
        created_at=BASE_TIME,
    )


def make_renderer(tmp_path: Path, **kwargs):
    settings = build_settings(tmp_path)
    market_state = kwargs.pop("market_state", MarketStateStore())
    return build_renderer(
        settings=settings,
        market_state=market_state,
        **kwargs,
    )


def get_renderer_method(renderer, names):
    for name in names:
        if hasattr(renderer, name):
            return getattr(renderer, name)
    raise AssertionError(f"Renderer did not expose any of: {names}")


def test_chart_request_validates_required_contract_and_defaults(tmp_path: Path) -> None:
    request = build_request()

    assert request.instrument == "SPX500_USD"
    assert request.timeframe == "H1"
    assert request.count == 500

    with pytest.raises((ValidationError, ValueError), match="Unsupported instrument"):
        build_request(instrument="BTC_USD")

    with pytest.raises((ValidationError, ValueError), match="Unsupported timeframe"):
        build_request(timeframe="W")

    with pytest.raises((ValidationError, ValueError), match="greater than or equal to 2"):
        build_request(count=1)

    with pytest.raises((ValidationError, ValueError), match="less than or equal to 5000"):
        build_request(count=5001)


def test_explicit_selectors_replace_default_bundle(tmp_path: Path) -> None:
    default_request = build_request()
    explicit_request = build_request(
        smc=("structure",),
        trade=("positions", "sl", "tp"),
        alert=("pricealerts",),
        indicator=("ema", "rsi"),
    )

    default_selection = get_request_selection(default_request)
    explicit_selection = get_request_selection(explicit_request)

    assert set(selection_keys(default_selection)) == {
        "orderblocks",
        "positions",
        "orders",
        "sl",
        "tp",
        "gslo",
        "pricealerts",
    }
    assert set(selection_keys(explicit_selection)) == {
        "structure",
        "positions",
        "sl",
        "tp",
        "pricealerts",
        "ema",
        "rsi",
    }


def test_chart_modes_resolve_distinct_default_overlay_bundles(tmp_path: Path) -> None:
    compact_request = build_request(mode="compact")
    balanced_request = build_request(mode="balanced")
    full_request = build_request(mode="full")

    assert compact_request.mode == ChartMode.COMPACT
    assert balanced_request.mode == ChartMode.BALANCED
    assert full_request.mode == ChartMode.FULL
    assert set(selection_keys(get_request_selection(compact_request))) == {"orderblocks", "positions"}
    assert set(selection_keys(get_request_selection(balanced_request))) == {
        "orderblocks",
        "positions",
        "orders",
        "sl",
        "tp",
        "gslo",
        "pricealerts",
    }
    assert set(selection_keys(get_request_selection(full_request))) == {
        "orderblocks",
        "structure",
        "liquidity",
        "positions",
        "orders",
        "sl",
        "tp",
        "gslo",
        "pricealerts",
        "ema",
        "bollinger",
        "vwap",
        "rsi",
        "macd",
    }


def test_overlay_presets_parse_and_explicit_flags_replace_presets(tmp_path: Path) -> None:
    preset_request = build_request(overlays=("smc", "indicators"))
    preset_selection = get_request_selection(preset_request)
    assert set(selection_keys(preset_selection)) == {
        "orderblocks",
        "structure",
        "liquidity",
        "ema",
        "bollinger",
        "vwap",
        "rsi",
        "macd",
    }

    explicit_over_preset = build_request(
        overlays=("smc",),
        indicator=("ema",),
    )
    explicit_selection = get_request_selection(explicit_over_preset)
    assert set(selection_keys(explicit_selection)) == {"ema"}


def test_selector_validation_rejects_unknown_keys(tmp_path: Path) -> None:
    with pytest.raises((ValidationError, ValueError), match="Unsupported"):
        build_request(smc=("fvg",))

    with pytest.raises((ValidationError, ValueError), match="Unsupported"):
        build_request(trade=("positions", "orders", "exposure"))

    with pytest.raises((ValidationError, ValueError), match="Unsupported"):
        build_request(indicator=("ema", "ichimoku"))


def test_indicator_series_builder_returns_expected_chart_layers() -> None:
    candles = build_candles(closes=[100.0 + index * 0.25 for index in range(80)])
    series = renderer_module.build_indicator_series(candles)
    resolved = as_mapping(series)

    if isinstance(resolved, pd.DataFrame):
        assert len(resolved) == len(candles)
        assert {"ema20", "ema50", "bollinger_upper", "bollinger_middle", "bollinger_lower"} <= set(
            resolved.columns
        )
        assert {"vwap", "rsi", "macd", "macd_signal", "macd_hist"} <= set(resolved.columns)
    else:
        for key in (
            "ema20",
            "ema50",
            "bollinger_upper",
            "bollinger_middle",
            "bollinger_lower",
            "vwap",
            "rsi",
            "macd",
            "macd_signal",
            "macd_hist",
        ):
            assert key in resolved


def test_renderer_clips_far_overlays_without_expanding_candle_focus(tmp_path: Path, monkeypatch) -> None:
    candles = build_candles(closes=[100.0, 100.25, 100.50, 100.75, 101.0])
    snapshot = build_snapshot(
        instrument="EUR_USD",
        order_block_time=candles["time"].iloc[1].to_pydatetime(),
    )
    market_state = MarketStateStore()
    market_state.publish_snapshot(snapshot)

    call_log: list[str] = []

    class FakeProvider:
        def get_candles(self, instrument: str, timeframe: str, count: int | None = None):
            call_log.append("get_candles")
            return candles

        def get_current_price(self, instrument: str):
            call_log.append("get_current_price")
            return SimpleNamespace(
                instrument=instrument,
                bid=101.0,
                ask=101.1,
                spread_price=0.1,
                spread_pips=1.0,
                fetched_at=BASE_TIME,
            )

        def get_candle_freshness(self, instrument: str, timeframe: str):
            call_log.append("get_candle_freshness")
            return SimpleNamespace(
                instrument=instrument,
                timeframe=timeframe,
                last_completed_candle=snapshot.last_completed_candle,
                fetched_at=BASE_TIME,
                source="oanda_api",
                candle_count=len(candles),
                is_fresh=True,
                staleness_seconds=0.0,
            )

    class FakeTradeRepository:
        def list_open(self):
            call_log.append("list_open")
            return [
                build_trade(instrument="EUR_USD"),
                build_trade(trade_id="other", instrument="SPX500_USD"),
            ]

    class FakeAlertRepository:
        def list_pending_price_alerts(self):
            call_log.append("list_pending_price_alerts")
            return [
                build_alert(instrument="EUR_USD", target_price=101.5),
                build_alert(id=2, instrument="SPX500_USD", target_price=2500.0),
            ]

    class FakeAccountClient:
        def get_open_orders(self):
            call_log.append("get_open_orders")
            return [
                build_pending_order("EUR_USD"),
                build_pending_order("SPX500_USD"),
            ]

    class FakeScanOrchestrator:
        def __init__(self):
            self.calls: list[tuple[str, str, str]] = []

        def refresh_snapshot(self, instrument: str, timeframe: str):
            self.calls.append(("refresh_snapshot", instrument, timeframe))
            return snapshot

    scan_orchestrator = FakeScanOrchestrator()
    renderer = make_renderer(
        tmp_path,
        market_data_provider=FakeProvider(),
        trade_repository=FakeTradeRepository(),
        alert_repository=FakeAlertRepository(),
        account_client=FakeAccountClient(),
        scan_orchestrator=scan_orchestrator,
    )

    build_render_payload = get_renderer_method(
        renderer,
        ("build_render_payload", "prepare_render_payload", "_build_render_payload"),
    )
    payload = build_render_payload(
        build_request(
            instrument="EUR_USD",
            smc=("orderblocks", "structure", "liquidity"),
            trade=("positions", "orders", "sl", "tp", "gslo"),
            alert=("pricealerts",),
        )
    )
    resolved = as_mapping(payload)

    visible_low = get_first_present(resolved, ("visible_price_low", "price_low", "y_min"))
    visible_high = get_first_present(resolved, ("visible_price_high", "price_high", "y_max"))
    omitted = get_first_present(resolved, ("omitted_layers", "clipped_layers", "omitted_overlays"))
    order_blocks = get_first_present(
        resolved,
        ("order_block_annotations", "order_blocks", "smc_order_blocks"),
    )

    assert visible_low <= candles["low"].min()
    assert visible_high >= candles["high"].max()
    assert visible_high < 200.0
    assert visible_low > 50.0
    assert any(str(item).startswith("orderblock:EUR_USD:") for item in omitted)
    assert any(str(item).startswith("position:EUR_USD:trade-1:") for item in omitted)
    assert any(str(item).startswith("order:EUR_USD:order-1:") for item in omitted)
    assert all("SPX500_USD" not in str(item) for item in omitted)

    first_order_block = order_blocks[0] if isinstance(order_blocks, (list, tuple)) else order_blocks
    anchor_time = get_first_present(
        first_order_block,
        ("anchor_time", "created_at", "time", "start_time"),
    )
    assert anchor_time == snapshot.zones.order_blocks[0].created_at


def test_renderer_preserves_bullish_and_bearish_order_blocks_in_same_payload(tmp_path: Path) -> None:
    candles = build_candles(closes=[159.10, 159.25, 159.05, 159.30, 159.18], timeframe="M15")
    snapshot = build_snapshot(instrument="USD_JPY", timeframe="M15")
    bullish_block = OrderBlockSummary(
        direction="BULLISH",
        upper_price=159.13,
        lower_price=158.96,
        created_at=candles["time"].iloc[1].to_pydatetime(),
        distance_pips=8.0,
        is_mitigated=False,
    )
    bearish_block = OrderBlockSummary(
        direction="BEARISH",
        upper_price=159.39,
        lower_price=159.23,
        created_at=candles["time"].iloc[2].to_pydatetime(),
        distance_pips=5.0,
        is_mitigated=False,
    )
    snapshot = snapshot.model_copy(
        update={"zones": ActiveZoneSummary(order_blocks=(bullish_block, bearish_block))}
    )
    market_state = MarketStateStore()
    market_state.publish_snapshot(snapshot)

    class FakeProvider:
        def get_candles(self, instrument: str, timeframe: str, count: int | None = None):
            return candles

    renderer = make_renderer(
        tmp_path,
        market_state=market_state,
        market_data_provider=FakeProvider(),
    )
    build_render_payload = get_renderer_method(
        renderer,
        ("build_render_payload", "prepare_render_payload", "_build_render_payload"),
    )
    payload = build_render_payload(
        build_request(instrument="USD_JPY", timeframe="M15", smc=("orderblocks",))
    )
    resolved = as_mapping(payload)
    order_blocks = get_first_present(
        resolved,
        ("order_block_annotations", "order_blocks", "smc_order_blocks"),
    )
    directions = {
        get_first_present(as_mapping(order_block), ("direction",))
        for order_block in order_blocks
    }

    assert directions == {"BULLISH", "BEARISH"}


def test_renderer_returns_artifact_handle_and_cleans_up_on_close(tmp_path: Path, monkeypatch) -> None:
    candles = build_candles(closes=[100.0, 100.25, 100.50, 100.75, 101.0])
    snapshot = build_snapshot(instrument="SPX500_USD")
    market_state = MarketStateStore()
    market_state.publish_snapshot(snapshot)

    class FakeProvider:
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
                last_completed_candle=snapshot.last_completed_candle,
                fetched_at=BASE_TIME,
                source="oanda_api",
                candle_count=len(candles),
                is_fresh=True,
                staleness_seconds=0.0,
            )

    class FakeExecutor:
        def __init__(self, *args, **kwargs):
            self.calls = []

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def submit(self, fn, payload):
            self.calls.append((fn, payload))

            @dataclass
            class _Future:
                result_value: object

                def result(self, *args, **kwargs):
                    return self.result_value

            output_path = tmp_path / "chart.png"
            output_path.write_bytes(b"png")
            return _Future(result_value=SimpleNamespace(path=output_path))

    monkeypatch.setattr(renderer_module, "ProcessPoolExecutor", FakeExecutor)

    renderer = make_renderer(
        tmp_path,
        market_data_provider=FakeProvider(),
        scan_orchestrator=SimpleNamespace(refresh_snapshot=lambda *args, **kwargs: snapshot),
    )
    render_chart = get_renderer_method(renderer, ("render", "render_chart", "_render"))
    result = render_chart(build_request())

    artifact = get_first_present(result, ("artifact", "chart_artifact", "output"))
    artifact_path = get_first_present(artifact, ("path", "file_path"))
    assert Path(artifact_path).exists()

    executor = getattr(renderer_module, "ProcessPoolExecutor")
    assert executor is FakeExecutor

    close = getattr(artifact, "close", None) or getattr(result, "close", None)
    assert close is not None
    close()
    assert not Path(artifact_path).exists()


def test_renderer_failure_does_not_poison_follow_up_render(tmp_path: Path, monkeypatch) -> None:
    candles = build_candles(closes=[100.0, 100.25, 100.50, 100.75, 101.0])
    snapshot = build_snapshot(instrument="SPX500_USD")
    market_state = MarketStateStore()
    market_state.publish_snapshot(snapshot)

    class FakeProvider:
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
                last_completed_candle=snapshot.last_completed_candle,
                fetched_at=BASE_TIME,
                source="oanda_api",
                candle_count=len(candles),
                is_fresh=True,
                staleness_seconds=0.0,
            )

    failures = {"count": 0}

    class FakeExecutor:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def submit(self, fn, payload):
            failures["count"] += 1

            @dataclass
            class _Future:
                failure_count: int

                def result(self, *args, **kwargs):
                    if self.failure_count == 1:
                        raise RuntimeError("render failed")
                    output_path = tmp_path / f"chart-{self.failure_count}.png"
                    output_path.write_bytes(b"png")
                    return SimpleNamespace(path=output_path)

            return _Future(failure_count=failures["count"])

    monkeypatch.setattr(renderer_module, "ProcessPoolExecutor", FakeExecutor)

    renderer = make_renderer(
        tmp_path,
        market_data_provider=FakeProvider(),
        scan_orchestrator=SimpleNamespace(refresh_snapshot=lambda *args, **kwargs: snapshot),
    )

    with pytest.raises(RuntimeError, match="render failed"):
        render_chart = get_renderer_method(renderer, ("render", "render_chart", "_render"))
        render_chart(build_request())

    render_chart = get_renderer_method(renderer, ("render", "render_chart", "_render"))
    result = render_chart(build_request())
    artifact = get_first_present(result, ("artifact", "chart_artifact", "output"))
    artifact_path = get_first_present(artifact, ("path", "file_path"))
    assert Path(artifact_path).exists()


def test_worker_renderer_uses_mplfinance_plot(tmp_path: Path, monkeypatch) -> None:
    calls: dict[str, object] = {}

    class FakeAxes:
        def set_ylim(self, low, high):
            calls["ylim"] = (low, high)

        def axhspan(self, *args, **kwargs):
            calls.setdefault("axhspan", 0)
            calls["axhspan"] = int(calls["axhspan"]) + 1

        def axhline(self, *args, **kwargs):
            calls.setdefault("axhline", 0)
            calls["axhline"] = int(calls["axhline"]) + 1

    class FakeFigure:
        def savefig(self, path, *args, **kwargs):
            Path(path).write_bytes(b"png")

    fake_mpf = ModuleType("mplfinance")
    fake_mpf.make_addplot = lambda *args, **kwargs: {"args": args, "kwargs": kwargs}
    fake_mpf.make_marketcolors = lambda **kwargs: {"marketcolors": kwargs}
    fake_mpf.make_mpf_style = lambda **kwargs: {"style": kwargs}
    fake_mpf.plot = lambda frame, **kwargs: (
        calls.setdefault("mplfinance_plot_kwargs", kwargs),
        (FakeFigure(), [FakeAxes()]),
    )[1]
    fake_matplotlib = ModuleType("matplotlib")
    fake_matplotlib.use = lambda backend: calls.setdefault("backend", backend)
    fake_pyplot = ModuleType("matplotlib.pyplot")
    fake_pyplot.close = lambda fig: calls.setdefault("closed", True)

    monkeypatch.setitem(sys.modules, "mplfinance", fake_mpf)
    monkeypatch.setitem(sys.modules, "matplotlib", fake_matplotlib)
    monkeypatch.setitem(sys.modules, "matplotlib.pyplot", fake_pyplot)

    candles = build_candles(closes=[100.0, 100.25, 100.50, 100.75, 101.0])
    payload = renderer_module.ChartRenderPayload(
        instrument="EUR_USD",
        timeframe="H1",
        mode=ChartMode.BALANCED,
        count=len(candles),
        overlay_selection=renderer_module.ResolvedChartSelection(keys=("orderblocks",), smc=("orderblocks",)),
        candles=tuple(
            {
                "time": row.time.to_pydatetime() if hasattr(row.time, "to_pydatetime") else row.time,
                "open": float(row.open),
                "high": float(row.high),
                "low": float(row.low),
                "close": float(row.close),
                "tick_volume": int(row.tick_volume),
            }
            for row in candles.itertuples(index=False)
        ),
        visible_price_low=float(candles["low"].min()),
        visible_price_high=float(candles["high"].max()),
        order_block_annotations=(),
        structure_annotations=(),
        liquidity_annotations=(),
        trade_overlays=(),
        order_overlays=(),
        price_alert_overlays=(),
        omitted_layers=(),
        artifact_path=str(tmp_path / "worker-chart.png"),
    )

    result = renderer_module._render_chart_payload(payload)

    assert calls["backend"] == "Agg"
    assert "mplfinance_plot_kwargs" in calls
    plot_kwargs = calls["mplfinance_plot_kwargs"]
    assert plot_kwargs["type"] == "candle"
    assert plot_kwargs["columns"] == ("open", "high", "low", "close", "tick_volume")
    assert plot_kwargs["volume"] is True
    assert plot_kwargs["volume_panel"] == 1
    assert plot_kwargs["num_panels"] == 2
    assert plot_kwargs["panel_ratios"] == (8, 2)
    assert plot_kwargs["ylabel_lower"] == "Tick Volume"
    style = plot_kwargs["style"]
    assert style["style"]["facecolor"] == "#000000"
    assert style["style"]["figcolor"] == "#000000"
    assert Path(result.path).exists()
