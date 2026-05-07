from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Event

from core.candle_policy import get_timeframe_delta
from core.instrument_registry import get_instrument_spec
from core.market_state import MarketStateStore
from core.models import (
    ActiveZoneSummary,
    IndicatorValueSummary,
    LiquidityPoolSummary,
    SnapshotFreshness,
    SmcContextSummary,
    SpreadResult,
    StructureEventSummary,
    TimeframeSnapshot,
)


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
        smc_context=SmcContextSummary(),
        spread=build_spread(instrument),
        freshness=build_freshness(
            instrument=instrument,
            timeframe=timeframe,
            last_completed_candle=last_completed_candle,
            staleness_seconds=staleness_seconds,
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


def test_market_state_store_keeps_timeframes_independent_without_bundles() -> None:
    store = MarketStateStore()
    published = {
        timeframe: store.publish_snapshot(
            build_snapshot(timeframe=timeframe, last_completed_candle=BASE_TIME)
        )
        for timeframe in ("D", "H4", "H1", "M15")
    }

    assert {timeframe: snapshot.version for timeframe, snapshot in published.items()} == {
        "D": 1,
        "H4": 1,
        "H1": 1,
        "M15": 1,
    }
    assert store.get_snapshot("EUR_USD", "D") == published["D"]
    assert store.get_snapshot("EUR_USD", "M15") == published["M15"]
    assert not hasattr(store, "get_bundle")
    assert not hasattr(store, "publish_bundle")
    assert not hasattr(store, "assemble_bundle")


def test_market_state_store_concurrent_snapshot_reads_never_observe_missing_versions() -> None:
    store = MarketStateStore()
    for timeframe in ("D", "H4", "H1", "M15"):
        store.publish_snapshot(build_snapshot(timeframe=timeframe, last_completed_candle=BASE_TIME))

    errors: list[str] = []
    stop_event = Event()

    def writer() -> None:
        current = BASE_TIME
        for _ in range(15):
            current += timedelta(hours=1)
            for timeframe in ("D", "H4", "H1", "M15"):
                published = store.publish_snapshot(
                    build_snapshot(timeframe=timeframe, last_completed_candle=current)
                )
                if store.get_snapshot_version("EUR_USD", timeframe, published.version) is None:
                    errors.append(f"Missing snapshot {timeframe} v{published.version}.")
                    stop_event.set()
                    return
        stop_event.set()

    def reader() -> None:
        while not stop_event.is_set():
            for timeframe in ("D", "H4", "H1", "M15"):
                latest = store.get_snapshot("EUR_USD", timeframe)
                if latest is None:
                    errors.append(f"Missing latest snapshot for {timeframe}.")
                    stop_event.set()
                    return
                if store.get_snapshot_version("EUR_USD", timeframe, latest.version) is None:
                    errors.append(f"Missing historical snapshot {timeframe} v{latest.version}.")
                    stop_event.set()
                    return

    with ThreadPoolExecutor(max_workers=2) as executor:
        writer_future = executor.submit(writer)
        reader_future = executor.submit(reader)
        writer_future.result()
        reader_future.result(timeout=5)

    assert errors == []
