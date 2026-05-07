from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Event

import pandas as pd
import pytest

from core.candle_policy import get_timeframe_delta
from core.instrument_registry import get_instrument_spec
from core.market_state import MarketStateStore
from core.models import (
    ActiveZoneSummary,
    CalendarEvent,
    ChopResult,
    HTFBiasResult,
    IndicatorMetric,
    IndicatorValueSummary,
    LiquidityPoolSummary,
    OrderBlockSummary,
    PreviousHighLowSummary,
    RetracementSummary,
    SnapshotFreshness,
    SmcContextSummary,
    SpreadResult,
    StructureBreak,
    StructureEventSummary,
    TimeframeSnapshot,
)
from smc.htf_bias import HTFBiasAnalyzer, HTFBiasTuning, PinnedHTFMember


BASE_TIME = datetime(2026, 3, 20, 10, 0, tzinfo=timezone.utc)


def build_freshness(
    *,
    instrument: str = "EUR_USD",
    timeframe: str = "H1",
    last_completed_candle: datetime = BASE_TIME,
    staleness_seconds: float = 0.0,
) -> SnapshotFreshness:
    return SnapshotFreshness(
        instrument=instrument,
        timeframe=timeframe,
        last_completed_candle=last_completed_candle,
        fetched_at=last_completed_candle + timedelta(minutes=5),
        source="oanda_api",
        candle_count=500,
        is_fresh=staleness_seconds == 0.0,
        staleness_seconds=staleness_seconds,
    )


def build_spread(instrument: str = "EUR_USD") -> SpreadResult:
    spec = get_instrument_spec(instrument)
    return SpreadResult(
        instrument=instrument,
        raw_spread=spec.typical_spread_pips * spec.pip_size,
        spread_pips=spec.typical_spread_pips,
        pip_size=spec.pip_size,
        typical_spread_pips=spec.typical_spread_pips,
        max_spread_pips=spec.max_spread_pips,
        is_acceptable=True,
        is_spiking=False,
        spread_ratio=1.0,
    )


def build_snapshot(
    *,
    instrument: str = "EUR_USD",
    timeframe: str,
    last_completed_candle: datetime,
    staleness_seconds: float = 0.0,
) -> TimeframeSnapshot:
    return TimeframeSnapshot(
        instrument=instrument,
        timeframe=timeframe,
        last_completed_candle=last_completed_candle,
        computed_at=last_completed_candle + timedelta(minutes=1),
        candle_range_start=last_completed_candle - get_timeframe_delta(timeframe),
        candle_range_end=last_completed_candle,
        indicators=IndicatorValueSummary(),
        structure=StructureEventSummary(),
        zones=ActiveZoneSummary(),
        liquidity=LiquidityPoolSummary(),
        spread=build_spread(instrument),
        chop=ChopResult(status="PASS", reason="placeholder"),
        freshness=build_freshness(
            instrument=instrument,
            timeframe=timeframe,
            last_completed_candle=last_completed_candle,
            staleness_seconds=staleness_seconds,
        ),
    )


def build_candles(
    *,
    timeframe: str,
    closes: list[float],
    end_time: datetime,
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


def build_bias_snapshot(
    *,
    timeframe: str,
    last_completed_candle: datetime,
    version: int,
    direction: str,
) -> TimeframeSnapshot:
    delta = get_timeframe_delta(timeframe)
    latest_break = StructureBreak(
        kind="BOS",
        direction=direction,
        level=1.1050 if direction == "BULLISH" else 1.0950,
        occurred_at=last_completed_candle - delta,
    )
    return TimeframeSnapshot(
        instrument="EUR_USD",
        timeframe=timeframe,
        version=version,
        last_completed_candle=last_completed_candle,
        computed_at=last_completed_candle + timedelta(minutes=1),
        candle_range_start=last_completed_candle - (delta * 29),
        candle_range_end=last_completed_candle,
        indicators=IndicatorValueSummary(
            metrics=(
                IndicatorMetric(
                    name="macd_hist",
                    value=0.8 if direction == "BULLISH" else -0.8,
                    source="talib",
                ),
                IndicatorMetric(
                    name="rsi",
                    value=62.0 if direction == "BULLISH" else 38.0,
                    source="talib",
                ),
                IndicatorMetric(name="adx", value=28.0, source="talib"),
            )
        ),
        structure=StructureEventSummary(latest_break=latest_break, recent_breaks=(latest_break,)),
        zones=ActiveZoneSummary(
            order_blocks=(
                OrderBlockSummary(
                    direction=direction,
                    upper_price=1.1020 if direction == "BULLISH" else 1.0980,
                    lower_price=1.1000 if direction == "BULLISH" else 1.0960,
                    created_at=last_completed_candle - (delta * 2),
                    is_mitigated=False,
                ),
            )
        ),
        liquidity=LiquidityPoolSummary(),
        smc_context=SmcContextSummary(
            previous_high_low=PreviousHighLowSummary(
                previous_high=1.1060,
                previous_low=1.0940,
                broken_high=direction == "BULLISH",
                broken_low=direction == "BEARISH",
                as_of=last_completed_candle - delta,
            ),
            retracement=RetracementSummary(
                direction=direction,
                current_retracement_pct=35.0,
                deepest_retracement_pct=55.0,
                as_of=last_completed_candle,
            ),
        ),
        spread=build_spread(),
        chop=ChopResult(
            status="PASS",
            reason="synthetic",
            metric_name="adx",
            metric_value=28.0,
            threshold=20.0,
        ),
        freshness=build_freshness(
            timeframe=timeframe,
            last_completed_candle=last_completed_candle,
        ),
    )


def test_market_state_store_publishes_monotonic_versions_and_retains_history() -> None:
    store = MarketStateStore(snapshot_history_retention=2)

    published = []
    for offset in range(4):
        snapshot = build_snapshot(
            timeframe="H1",
            last_completed_candle=BASE_TIME + (offset * timedelta(hours=1)),
        )
        published.append(store.publish_snapshot(snapshot))

    assert [snapshot.version for snapshot in published] == [1, 2, 3, 4]
    assert store.get_snapshot("EUR_USD", "H1").version == 4
    assert store.get_snapshot_version("EUR_USD", "H1", 1) is None
    assert store.get_snapshot_version("EUR_USD", "H1", 2).version == 2
    assert store.get_snapshot_version("EUR_USD", "H1", 4).version == 4


def test_market_state_store_assembles_bundle_with_pinned_members_and_copy_isolation() -> None:
    store = MarketStateStore()
    h1_v1 = store.publish_snapshot(
        build_snapshot(timeframe="H1", last_completed_candle=BASE_TIME)
    )
    h4_v1 = store.publish_snapshot(
        build_snapshot(
            timeframe="H4",
            last_completed_candle=BASE_TIME,
            staleness_seconds=7200.0,
        )
    )
    d_v1 = store.publish_snapshot(
        build_snapshot(timeframe="D", last_completed_candle=BASE_TIME)
    )

    bundle_v1 = store.assemble_bundle(
        "EUR_USD",
        ["H1", "H4", "D"],
        HTFBiasResult(timeframe_votes={"D": "BULLISH", "H4": "BEARISH", "H1": "BULLISH"}),
        [CalendarEvent(title="CPI", event_time=BASE_TIME + timedelta(hours=2), impact="HIGH")],
        1,
    )

    assert bundle_v1.bundle_version == 1
    assert bundle_v1.members == {"H1": h1_v1.version, "H4": h4_v1.version, "D": d_v1.version}
    assert bundle_v1.mixed_freshness is True
    assert bundle_v1.stalest_timeframe == "H4"
    assert bundle_v1.stalest_age_seconds == 7200.0
    assert store.get_snapshot_version("EUR_USD", "H4", h4_v1.version).version == 1

    latest_bundle = store.get_bundle("EUR_USD")
    latest_bundle.members["H1"] = 999
    assert store.get_bundle("EUR_USD").members["H1"] == 1

    h4_v2 = store.publish_snapshot(
        build_snapshot(
            timeframe="H4",
            last_completed_candle=BASE_TIME + timedelta(hours=4),
        )
    )
    bundle_v2 = store.assemble_bundle(
        "EUR_USD",
        ["H1", "H4", "D"],
        HTFBiasResult(timeframe_votes={"D": "BULLISH", "H4": "BULLISH", "H1": "BULLISH"}),
        (),
        2,
    )

    assert bundle_v1.members["H4"] == 1
    assert bundle_v2.bundle_version == 2
    assert bundle_v2.members["H4"] == h4_v2.version == 2
    assert bundle_v2.mixed_freshness is False
    assert bundle_v2.stalest_timeframe is None


def test_market_state_store_rejects_missing_pinned_bundle_versions() -> None:
    store = MarketStateStore()
    store.publish_snapshot(build_snapshot(timeframe="H1", last_completed_candle=BASE_TIME))

    with pytest.raises(KeyError):
        store.publish_bundle(
            store.assemble_bundle(
                "EUR_USD",
                ["H1"],
                HTFBiasResult(),
                (),
                0,
            ).model_copy(
                update={
                    "members": {"H1": 999},
                    "member_freshness": {"H1": build_freshness()},
                }
            )
        )


def test_market_state_store_can_publish_bundle_from_explicit_versions_used_by_htf_bias() -> None:
    store = MarketStateStore()
    d_v1 = store.publish_snapshot(
        build_bias_snapshot(
            timeframe="D",
            last_completed_candle=BASE_TIME,
            version=0,
            direction="BULLISH",
        )
    )
    h4_v1 = store.publish_snapshot(
        build_bias_snapshot(
            timeframe="H4",
            last_completed_candle=BASE_TIME,
            version=0,
            direction="BULLISH",
        )
    )
    h1_v1 = store.publish_snapshot(
        build_bias_snapshot(
            timeframe="H1",
            last_completed_candle=BASE_TIME,
            version=0,
            direction="BULLISH",
        )
    )

    analyzer = HTFBiasAnalyzer(
        tuning=HTFBiasTuning(
            weights={"D": 0.50, "H4": 0.30, "H1": 0.20},
            transition_windows={"D": 3, "H4": 4, "H1": 6},
            neutral_band=0.15,
            ruptures_penalty=10.0,
        ),
        logger=type("DummyLogger", (), {"info": lambda self, *args, **kwargs: None})(),
    )
    bias = analyzer.compute(
        [
            PinnedHTFMember(
                snapshot=d_v1,
                candles=build_candles(
                    timeframe="D",
                    closes=[100 + (index * 0.20) for index in range(30)],
                    end_time=BASE_TIME,
                ),
                source_snapshot_version=d_v1.version,
            ),
            PinnedHTFMember(
                snapshot=h4_v1,
                candles=build_candles(
                    timeframe="H4",
                    closes=[90 + (index * 0.16) for index in range(30)],
                    end_time=BASE_TIME,
                ),
                source_snapshot_version=h4_v1.version,
            ),
            PinnedHTFMember(
                snapshot=h1_v1,
                candles=build_candles(
                    timeframe="H1",
                    closes=[80 + (index * 0.12) for index in range(30)],
                    end_time=BASE_TIME,
                ),
                source_snapshot_version=h1_v1.version,
            ),
        ]
    )

    h1_v2 = store.publish_snapshot(
        build_bias_snapshot(
            timeframe="H1",
            last_completed_candle=BASE_TIME + timedelta(hours=1),
            version=0,
            direction="BEARISH",
        )
    )
    bundle = store.assemble_bundle_from_versions(
        "EUR_USD",
        {"D": d_v1.version, "H4": h4_v1.version, "H1": h1_v1.version},
        bias,
        (),
        0,
    )

    assert h1_v2.version == 2
    assert bias.direction == "BULLISH"
    assert bundle.members == {"D": 1, "H4": 1, "H1": 1}
    assert bundle.htf_bias.direction == "BULLISH"
    assert store.get_snapshot_version("EUR_USD", "H1", bundle.members["H1"]).version == 1


def test_market_state_store_concurrent_reads_never_observe_unresolvable_bundle_members() -> None:
    store = MarketStateStore()
    for timeframe in ("H1", "H4", "D"):
        store.publish_snapshot(
            build_snapshot(timeframe=timeframe, last_completed_candle=BASE_TIME)
        )
    store.assemble_bundle("EUR_USD", ["H1", "H4", "D"], HTFBiasResult(), (), 0)

    errors: list[str] = []
    stop_event = Event()

    def writer() -> None:
        current = BASE_TIME
        for _ in range(15):
            current += timedelta(hours=1)
            for timeframe in ("H1", "H4", "D"):
                store.publish_snapshot(
                    build_snapshot(
                        timeframe=timeframe,
                        last_completed_candle=current,
                    )
                )
            store.assemble_bundle("EUR_USD", ["H1", "H4", "D"], HTFBiasResult(), (), 0)
        stop_event.set()

    def reader() -> None:
        while not stop_event.is_set():
            bundle = store.get_bundle("EUR_USD")
            if bundle is None:
                continue
            for timeframe, version in bundle.members.items():
                snapshot = store.get_snapshot_version("EUR_USD", timeframe, version)
                if snapshot is None:
                    errors.append(f"Missing pinned snapshot {timeframe} v{version}.")
                    stop_event.set()
                    return
                if snapshot.freshness != bundle.member_freshness[timeframe]:
                    errors.append(f"Freshness mismatch for {timeframe} v{version}.")
                    stop_event.set()
                    return

    with ThreadPoolExecutor(max_workers=2) as executor:
        writer_future = executor.submit(writer)
        reader_future = executor.submit(reader)
        writer_future.result()
        reader_future.result(timeout=5)

    assert errors == []
