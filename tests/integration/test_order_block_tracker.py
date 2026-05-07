from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from core.candle_policy import get_timeframe_delta
from core.instrument_registry import get_instrument_spec
from core.market_state import MarketStateStore
from core.models import (
    ActiveZoneSummary,
    IndicatorValueSummary,
    InstrumentOrderBlockTracker,
    LiquidityPoolSummary,
    OrderBlockRecord,
    SnapshotFreshness,
    SmcContextSummary,
    SpreadResult,
    StructureEventSummary,
    TimeframeSnapshot,
)
from smc.provider import SmcAdapter


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
        ),
    )


def build_record(
    *,
    timeframe: str,
    direction: str,
    upper_price: float,
    lower_price: float,
    created_at: datetime,
    source_snapshot_version: int,
    status: str = "ACTIVE",
    mitigated_at: datetime | None = None,
) -> OrderBlockRecord:
    return OrderBlockRecord(
        id=SmcAdapter.build_order_block_record_id(
            instrument="EUR_USD",
            timeframe=timeframe,
            direction=direction,
            created_at=pd.Timestamp(created_at),
            upper_price=upper_price,
            lower_price=lower_price,
        ),
        instrument="EUR_USD",
        timeframe=timeframe,
        direction=direction,
        upper_price=upper_price,
        lower_price=lower_price,
        created_at=created_at,
        status=status,
        mitigated_at=mitigated_at,
        source_snapshot_version=source_snapshot_version,
        last_analyzed_close=upper_price - 0.0005,
    )


def test_market_state_store_publishes_order_block_trackers_with_pinned_snapshots() -> None:
    store = MarketStateStore()
    h1 = store.publish_snapshot(build_snapshot(timeframe="H1", last_completed_candle=BASE_TIME))
    h4 = store.publish_snapshot(build_snapshot(timeframe="H4", last_completed_candle=BASE_TIME))

    tracker_v1 = store.assemble_order_block_tracker(
        "EUR_USD",
        (
            build_record(
                timeframe="H1",
                direction="BULLISH",
                upper_price=1.1020,
                lower_price=1.1010,
                created_at=BASE_TIME - timedelta(hours=2),
                source_snapshot_version=h1.version,
                status="MITIGATED",
                mitigated_at=BASE_TIME - timedelta(hours=1),
            ),
            build_record(
                timeframe="H4",
                direction="BEARISH",
                upper_price=1.1100,
                lower_price=1.1080,
                created_at=BASE_TIME - timedelta(hours=6),
                source_snapshot_version=h4.version,
            ),
        ),
        {"H1": h1.version, "H4": h4.version},
    )

    assert tracker_v1.tracker_version == 1
    assert tracker_v1.source_snapshot_versions == {"H1": 1, "H4": 1}
    assert len(tracker_v1.records) == 2
    assert tracker_v1.records[0].source_snapshot_version == 1
    assert tracker_v1.records[1].timeframe == "H4"

    latest = store.get_order_block_tracker("EUR_USD")
    latest.source_snapshot_versions["H1"] = 999
    assert store.get_order_block_tracker("EUR_USD").source_snapshot_versions["H1"] == 1

    h4_v2 = store.publish_snapshot(
        build_snapshot(
            timeframe="H4",
            last_completed_candle=BASE_TIME + timedelta(hours=4),
        )
    )
    tracker_v2 = store.assemble_order_block_tracker(
        "EUR_USD",
        (
            build_record(
                timeframe="H1",
                direction="BULLISH",
                upper_price=1.1020,
                lower_price=1.1010,
                created_at=BASE_TIME - timedelta(hours=2),
                source_snapshot_version=h1.version,
                status="MITIGATED",
                mitigated_at=BASE_TIME - timedelta(hours=1),
            ),
            build_record(
                timeframe="H4",
                direction="BEARISH",
                upper_price=1.1090,
                lower_price=1.1070,
                created_at=BASE_TIME + timedelta(hours=1),
                source_snapshot_version=h4_v2.version,
            ),
        ),
        {"H1": h1.version, "H4": h4_v2.version},
    )

    assert tracker_v2.tracker_version == 2
    assert tracker_v2.source_snapshot_versions["H4"] == 2
    assert tracker_v2.records[1].source_snapshot_version == 2


def test_market_state_store_rejects_order_block_trackers_with_unpublished_versions() -> None:
    store = MarketStateStore()
    store.publish_snapshot(build_snapshot(timeframe="H1", last_completed_candle=BASE_TIME))

    with pytest.raises(KeyError):
        store.publish_order_block_tracker(
            InstrumentOrderBlockTracker(
                instrument="EUR_USD",
                created_at=BASE_TIME,
                records=(
                    build_record(
                        timeframe="H1",
                        direction="BULLISH",
                        upper_price=1.1020,
                        lower_price=1.1010,
                        created_at=BASE_TIME - timedelta(hours=2),
                        source_snapshot_version=999,
                    ),
                ),
                source_snapshot_versions={"H1": 999},
            )
        )
