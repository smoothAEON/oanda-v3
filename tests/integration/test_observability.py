from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import structlog.testing
from freezegun import freeze_time

from config.settings import Settings, load_settings
from core.candle_policy import get_timeframe_delta
from core.instrument_registry import get_instrument_spec
from core.logging_setup import configure_logging
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
from data.csv_persistence import CandleCsvStore
from data.persistence.trade_store import TradeStore
from providers.cache import CandleCache
from providers.oanda import OandaMarketDataProvider


BASE_TIME = datetime(2026, 3, 20, 10, 0, tzinfo=timezone.utc)


def write_env_file(path: Path, **overrides: str) -> Path:
    values = {
        "OANDA_API_KEY": "api-key",
        "OANDA_ACCOUNT_ID": "account-id",
        "OANDA_ENVIRONMENT": "practice",
        "LOG_LEVEL": "DEBUG",
        "LOG_JSON": "true",
        "DEFAULT_CANDLE_COUNT": "500",
        "DEFAULT_SWING_LENGTH": "10",
        "CALENDAR_REFRESH_HOURS": "1",
        "TINYDB_PATH": str(path.parent / "bot.json"),
    }
    values.update(overrides)
    path.write_text(
        "\n".join(f"{key}={value}" for key, value in values.items()),
        encoding="utf-8",
    )
    return path


def build_settings(tmp_path: Path, **overrides: str) -> Settings:
    env_file = write_env_file(tmp_path / ".env", **overrides)
    return load_settings(env_file=env_file)


def make_candle_payload() -> dict[str, object]:
    return {
        "candles": [
            {
                "time": "2026-03-20T08:00:00Z",
                "complete": True,
                "volume": 100,
                "mid": {"o": "1.1000", "h": "1.1005", "l": "1.0995", "c": "1.1002"},
            },
            {
                "time": "2026-03-20T09:00:00Z",
                "complete": True,
                "volume": 101,
                "mid": {"o": "1.1010", "h": "1.1015", "l": "1.1005", "c": "1.1012"},
            },
            {
                "time": "2026-03-20T10:00:00Z",
                "complete": False,
                "volume": 102,
                "mid": {"o": "1.1020", "h": "1.1025", "l": "1.1015", "c": "1.1022"},
            },
        ]
    }


class DummyOandaProvider(OandaMarketDataProvider):
    def __init__(self, *, settings: Settings, cache: CandleCache) -> None:
        super().__init__(settings=settings, cache=cache, api_client=object())

    def _request_candles_payload(
        self,
        instrument: str,
        timeframe: str,
        count: int,
        since,
    ) -> dict[str, object]:
        return make_candle_payload()


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
    timeframe: str,
    last_completed_candle: datetime,
    staleness_seconds: float = 0.0,
) -> TimeframeSnapshot:
    return TimeframeSnapshot(
        instrument="EUR_USD",
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
        spread=build_spread(),
        freshness=SnapshotFreshness(
            instrument="EUR_USD",
            timeframe=timeframe,
            last_completed_candle=last_completed_candle,
            fetched_at=last_completed_candle + timedelta(minutes=5),
            source="oanda_api",
            candle_count=500,
            is_fresh=staleness_seconds == 0.0,
            staleness_seconds=staleness_seconds,
        ),
    )


@freeze_time("2026-03-20T10:15:00Z")
def test_stage_04_logging_emits_required_events_and_fields(tmp_path: Path) -> None:
    settings = build_settings(tmp_path)
    configure_logging(settings)

    assert logging.getLogger().level == logging.DEBUG

    cache = CandleCache(
        csv_store=CandleCsvStore(settings=settings),
        trade_store=TradeStore(settings=settings),
    )
    provider = DummyOandaProvider(settings=settings, cache=cache)

    with structlog.testing.capture_logs() as logs:
        provider.get_candles("EUR_USD", "H1", count=2)

    cache_lookup_events = [entry for entry in logs if entry["event"] == "cache_lookup"]
    fetched_events = [entry for entry in logs if entry["event"] == "candles_fetched"]
    excluded_events = [entry for entry in logs if entry["event"] == "current_bar_excluded"]

    assert any(entry["cache_level"] == "memory" and entry["hit"] is False for entry in cache_lookup_events)
    assert any(entry["cache_level"] == "csv" and entry["hit"] is False for entry in cache_lookup_events)

    fetched = fetched_events[0]
    assert fetched["instrument"] == "EUR_USD"
    assert fetched["timeframe"] == "H1"
    assert fetched["source"] == "oanda_api"
    assert fetched["candle_count"] == 2
    assert "last_completed_candle" in fetched
    assert "fetch_duration_ms" in fetched

    excluded = excluded_events[0]
    assert excluded["instrument"] == "EUR_USD"
    assert excluded["timeframe"] == "H1"
    assert excluded["excluded"] is True
    assert excluded["reason"] == "complete_flag_false"
    assert "last_bar_time" in excluded

    cache.trade_store.close()


def test_snapshot_publication_logging_emits_snapshot_events_without_bundles(tmp_path: Path) -> None:
    settings = build_settings(tmp_path)
    configure_logging(settings)

    store = MarketStateStore()

    with structlog.testing.capture_logs() as logs:
        store.publish_snapshot(build_snapshot(timeframe="H1", last_completed_candle=BASE_TIME))
        store.publish_snapshot(
            build_snapshot(
                timeframe="H4",
                last_completed_candle=BASE_TIME,
                staleness_seconds=3600.0,
            )
        )

    snapshot_events = [entry for entry in logs if entry["event"] == "snapshot_published"]
    bundle_events = [entry for entry in logs if entry["event"] == "bundle_published"]

    assert len(snapshot_events) == 2
    assert snapshot_events[0]["snapshot_version"] == 1
    assert snapshot_events[0]["instrument"] == "EUR_USD"
    assert snapshot_events[0]["timeframe"] == "H1"
    assert snapshot_events[0]["is_stale"] is False
    assert snapshot_events[1]["timeframe"] == "H4"
    assert snapshot_events[1]["is_stale"] is True
    assert bundle_events == []
