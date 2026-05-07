from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from freezegun import freeze_time
import numpy as np
import pandas as pd

from core.candle_policy import get_timeframe_delta, trim_to_closed, validate_candle_df
from core.instrument_registry import get_instrument_spec
from core.models import (
    ActiveZoneSummary,
    LiquidityPoolSummary,
    SnapshotFreshness,
    SpreadResult,
    StructureEventSummary,
    TimeframeSnapshot,
)
from indicators import build_indicator_summary
from indicators.pandasta_wrappers import SUPPORTED_PANDASTA_WRAPPERS, build_pandasta_metrics
from indicators.talib_wrappers import SUPPORTED_TALIB_WRAPPERS, build_talib_metrics


def build_candles(count: int, *, end_time: datetime) -> pd.DataFrame:
    start_time = end_time - timedelta(hours=count - 1)
    rows = []
    for index in range(count):
        time = start_time + timedelta(hours=index)
        close = 100.0 + (index * 0.2) + ((index % 7) * 0.03)
        rows.append(
            {
                "time": time,
                "open": close - 0.12,
                "high": close + 0.35,
                "low": close - 0.42,
                "close": close,
                "tick_volume": 100 + index,
            }
        )
    return pd.DataFrame(rows)


def build_fake_pandasta_module(
    calls: dict[str, list[object]],
    *,
    squeeze_signal: str,
) -> SimpleNamespace:
    def vwap(high: pd.Series, low: pd.Series, close: pd.Series, volume: pd.Series) -> pd.Series:
        assert isinstance(high.index, pd.DatetimeIndex)
        assert isinstance(volume.index, pd.DatetimeIndex)
        calls.setdefault("lengths", []).append(len(close))
        return pd.Series(
            np.linspace(200.0, 200.0 + (len(close) - 1), len(close)),
            index=close.index,
            name="VWAP_D",
        )

    def squeeze(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.DataFrame:
        assert isinstance(close.index, pd.DatetimeIndex)
        calls.setdefault("lengths", []).append(len(close))
        on = np.zeros(len(close), dtype=int)
        off = np.zeros(len(close), dtype=int)
        no_squeeze = np.zeros(len(close), dtype=int)
        if squeeze_signal == "ON":
            on[-1] = 1
        elif squeeze_signal == "OFF":
            off[-1] = 1
        else:
            no_squeeze[-1] = 1

        return pd.DataFrame(
            {
                "SQZ_20_2.0_20_1.5": np.linspace(-2.0, 3.0, len(close)),
                "SQZ_ON": on,
                "SQZ_OFF": off,
                "SQZ_NO": no_squeeze,
            },
            index=close.index,
        )

    def ichimoku(
        high: pd.Series,
        low: pd.Series,
        close: pd.Series,
        *,
        lookahead: bool,
    ) -> tuple[pd.DataFrame, pd.DataFrame]:
        assert isinstance(close.index, pd.DatetimeIndex)
        calls.setdefault("lookahead", []).append(lookahead)
        historical = pd.DataFrame(
            {
                "ITS_9": np.linspace(10.0, 20.0, len(close)),
                "IKS_26": np.linspace(30.0, 40.0, len(close)),
                "ISA_9": np.linspace(50.0, 60.0, len(close)),
                "ISB_26": np.linspace(70.0, 80.0, len(close)),
            },
            index=close.index,
        )
        spans = pd.DataFrame(
            {
                "ISA_9": [111.0, 999.0],
                "ISB_26": [222.0, 888.0],
            },
            index=pd.DatetimeIndex(
                [close.index[-1] + timedelta(hours=1), close.index[-1] + timedelta(hours=2)]
            ),
        )
        return historical, spans

    return SimpleNamespace(vwap=vwap, squeeze=squeeze, ichimoku=ichimoku)


def build_freshness(last_completed_candle: datetime) -> SnapshotFreshness:
    return SnapshotFreshness(
        instrument="EUR_USD",
        timeframe="H1",
        last_completed_candle=last_completed_candle,
        fetched_at=last_completed_candle + timedelta(minutes=5),
        source="oanda_api",
        candle_count=500,
        is_fresh=True,
        staleness_seconds=0.0,
    )


def build_spread() -> SpreadResult:
    spec = get_instrument_spec("EUR_USD")
    bid = 1.1000
    ask = bid + (spec.pip_size * 2.0)
    return SpreadResult(
        instrument="EUR_USD",
        bid=bid,
        ask=ask,
        raw_spread=ask - bid,
        spread_pips=2.0,
        pip_size=spec.pip_size,
        fetched_at=datetime(2026, 3, 20, 10, 0, tzinfo=timezone.utc),
    )


@freeze_time("2026-03-20T10:30:00Z")
def test_build_indicator_summary_is_deterministic_and_closed_bar_only(
    monkeypatch,
) -> None:
    end_time = datetime(2026, 3, 20, 10, 0, tzinfo=timezone.utc)
    candles = build_candles(120, end_time=end_time)
    original = candles.copy(deep=True)
    calls: dict[str, list[object]] = {}
    fake_module = build_fake_pandasta_module(calls, squeeze_signal="ON")

    monkeypatch.setattr("indicators.pandasta_wrappers._load_pandasta_module", lambda: fake_module)

    first = build_indicator_summary(candles, "H1")
    second = build_indicator_summary(candles, "H1")

    pd.testing.assert_frame_equal(candles, original)
    assert first.model_dump() == second.model_dump()
    assert calls["lengths"] == [119, 119, 119, 119]
    assert calls["lookahead"] == [False, False]

    talib_names = tuple(metric.name for metric in first.metrics[: len(SUPPORTED_TALIB_WRAPPERS)])
    pandas_names = tuple(metric.name for metric in first.metrics[len(SUPPORTED_TALIB_WRAPPERS) :])
    assert talib_names == SUPPORTED_TALIB_WRAPPERS
    assert pandas_names == SUPPORTED_PANDASTA_WRAPPERS

    metrics = {metric.name: metric for metric in first.metrics}
    assert metrics["squeeze_momentum"].signal == "ON"
    assert metrics["ichimoku_span_a"].value == 111.0
    assert metrics["ichimoku_span_b"].value == 222.0
    assert tuple(metric.name for metric in first.tick_volume_metrics) == (
        "tick_obv",
        "tick_mfi",
        "tick_adosc",
    )


def test_talib_metrics_preserve_order_and_publish_none_for_warmup() -> None:
    end_time = datetime(2026, 3, 20, 4, 0, tzinfo=timezone.utc)
    candles = build_candles(5, end_time=end_time)
    metrics = build_talib_metrics(candles)

    assert tuple(metric.name for metric in metrics) == SUPPORTED_TALIB_WRAPPERS

    by_name = {metric.name: metric for metric in metrics}
    assert by_name["ema"].value is None
    assert by_name["rsi"].value is None
    assert by_name["adx"].value is None
    assert by_name["macd"].value is None


def test_pandasta_wrapper_uses_temporary_datetime_index_and_disables_lookahead(
    monkeypatch,
) -> None:
    end_time = datetime(2026, 3, 20, 6, 0, tzinfo=timezone.utc)
    candles = validate_candle_df(build_candles(80, end_time=end_time))
    closed = trim_to_closed(candles, "H1")
    calls: dict[str, list[object]] = {}
    fake_module = build_fake_pandasta_module(calls, squeeze_signal="NO_SQUEEZE")

    monkeypatch.setattr("indicators.pandasta_wrappers._load_pandasta_module", lambda: fake_module)

    metrics = build_pandasta_metrics(closed)

    assert isinstance(candles.index, pd.RangeIndex)
    assert tuple(metric.name for metric in metrics) == SUPPORTED_PANDASTA_WRAPPERS
    assert calls["lookahead"] == [False]
    assert {metric.name: metric.signal for metric in metrics}["squeeze_momentum"] == "NO_SQUEEZE"
    assert {metric.name: metric.value for metric in metrics}["ichimoku_span_a"] == 111.0


@freeze_time("2026-03-20T10:30:00Z")
def test_indicator_summary_serializes_through_timeframe_snapshot(
    monkeypatch,
) -> None:
    end_time = datetime(2026, 3, 20, 10, 0, tzinfo=timezone.utc)
    candles = build_candles(120, end_time=end_time)
    fake_module = build_fake_pandasta_module({}, squeeze_signal="OFF")

    monkeypatch.setattr("indicators.pandasta_wrappers._load_pandasta_module", lambda: fake_module)

    summary = build_indicator_summary(candles, "H1")
    last_completed_candle = datetime(2026, 3, 20, 9, 0, tzinfo=timezone.utc)
    snapshot = TimeframeSnapshot(
        instrument="EUR_USD",
        timeframe="H1",
        last_completed_candle=last_completed_candle,
        computed_at=last_completed_candle + timedelta(minutes=1),
        candle_range_start=last_completed_candle - get_timeframe_delta("H1"),
        candle_range_end=last_completed_candle,
        indicators=summary,
        structure=StructureEventSummary(),
        zones=ActiveZoneSummary(),
        liquidity=LiquidityPoolSummary(),
        spread=build_spread(),
        freshness=build_freshness(last_completed_candle),
    )

    dumped = snapshot.model_dump()

    assert dumped["indicators"]["metrics"][0]["name"] == "ema"
    assert dumped["indicators"]["metrics"][-1]["name"] == "ichimoku_span_b"
    assert dumped["indicators"]["tick_volume_metrics"][0]["name"] == "tick_obv"
