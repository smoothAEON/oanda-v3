from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import structlog.testing
from freezegun import freeze_time
import pandas as pd
import pytest

from config.settings import Settings, load_settings
from core.logging_setup import configure_logging, get_logger
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
from data.csv_persistence import CandleCsvStore
from data.persistence.trade_store import TradeStore
from providers.cache import CandleCache
from providers.oanda import OandaMarketDataProvider
from core.candle_policy import get_timeframe_delta
from core.instrument_registry import get_instrument_spec
from filters.spread import evaluate_spread
from providers.base import PriceSnapshot
from smc.htf_bias import HTFBiasAnalyzer, HTFBiasTuning, PinnedHTFMember


def write_env_file(path: Path, **overrides: str) -> Path:
    values = {
        "OANDA_API_KEY": "api-key",
        "OANDA_ACCOUNT_ID": "account-id",
        "OANDA_ENVIRONMENT": "practice",
        "TELEGRAM_BOT_TOKEN": "telegram-token",
        "TELEGRAM_CHAT_ID": "123456789",
        "TELEGRAM_BOT_PASSWORD": "bot-password",
        "TELEGRAM_ADMIN_IDS": "111,222",
        "LOG_LEVEL": "DEBUG",
        "LOG_JSON": "true",
        "DEFAULT_CANDLE_COUNT": "500",
        "DEFAULT_SWING_LENGTH": "10",
        "RUPTURES_PENALTY": "10.0",
        "SCAN_INTERVAL_MINUTES": "5",
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


BASE_TIME = datetime(2026, 3, 20, 10, 0, tzinfo=timezone.utc)


def build_freshness(
    *,
    timeframe: str,
    last_completed_candle: datetime,
    staleness_seconds: float = 0.0,
) -> SnapshotFreshness:
    return SnapshotFreshness(
        instrument="EUR_USD",
        timeframe=timeframe,
        last_completed_candle=last_completed_candle,
        fetched_at=last_completed_candle + timedelta(minutes=5),
        source="oanda_api",
        candle_count=500,
        is_fresh=staleness_seconds == 0.0,
        staleness_seconds=staleness_seconds,
    )


def build_spread() -> SpreadResult:
    spec = get_instrument_spec("EUR_USD")
    return SpreadResult(
        instrument="EUR_USD",
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
        spread=build_spread(),
        chop=ChopResult(status="PASS", reason="placeholder"),
        freshness=build_freshness(
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
    direction: str,
    end_time: datetime = BASE_TIME,
) -> TimeframeSnapshot:
    delta = get_timeframe_delta(timeframe)
    latest_break = StructureBreak(
        kind="BOS",
        direction=direction,
        level=1.1050 if direction == "BULLISH" else 1.0950,
        occurred_at=end_time - delta,
    )
    return TimeframeSnapshot(
        instrument="EUR_USD",
        timeframe=timeframe,
        version=1,
        last_completed_candle=end_time,
        computed_at=end_time + timedelta(minutes=1),
        candle_range_start=end_time - (delta * 29),
        candle_range_end=end_time,
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
                    created_at=end_time - (delta * 2),
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
                as_of=end_time - delta,
            ),
            retracement=RetracementSummary(
                direction=direction,
                current_retracement_pct=35.0,
                deepest_retracement_pct=55.0,
                as_of=end_time,
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
            last_completed_candle=end_time,
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

    assert any(
        entry["cache_level"] == "memory" and entry["hit"] is False
        for entry in cache_lookup_events
    )
    assert any(
        entry["cache_level"] == "csv" and entry["hit"] is False
        for entry in cache_lookup_events
    )

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


def test_stage_05_logging_emits_snapshot_and_bundle_events(tmp_path: Path) -> None:
    settings = build_settings(tmp_path)
    configure_logging(settings)

    store = MarketStateStore()
    h1 = build_snapshot(timeframe="H1", last_completed_candle=BASE_TIME)
    h4 = build_snapshot(
        timeframe="H4",
        last_completed_candle=BASE_TIME,
        staleness_seconds=3600.0,
    )

    with structlog.testing.capture_logs() as logs:
        store.publish_snapshot(h1)
        store.publish_snapshot(h4)
        store.assemble_bundle(
            "EUR_USD",
            ["H1", "H4"],
            HTFBiasResult(timeframe_votes={"H1": "BULLISH", "H4": "BEARISH"}),
            (CalendarEvent(title="CPI", event_time=BASE_TIME + timedelta(hours=2), impact="HIGH"),),
            1,
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

    bundle = bundle_events[0]
    assert bundle["bundle_version"] == 1
    assert bundle["instrument"] == "EUR_USD"
    assert bundle["members"] == {"H1": 1, "H4": 1}
    assert bundle["mixed_freshness"] is True
    assert bundle["stalest_timeframe"] == "H4"


def test_stage_08_spread_logging_emits_required_fields(tmp_path: Path) -> None:
    settings = build_settings(tmp_path)
    configure_logging(settings)

    price = PriceSnapshot(
        instrument="EUR_USD",
        bid=1.1000,
        ask=1.1002,
        spread_price=0.0002,
        spread_pips=2.0,
        fetched_at=BASE_TIME,
    )

    with structlog.testing.capture_logs() as logs:
        result = evaluate_spread(price)

    spread_events = [entry for entry in logs if entry["event"] == "spread_checked"]

    assert result.is_spiking is True
    assert len(spread_events) == 1
    assert spread_events[0]["instrument"] == "EUR_USD"
    assert spread_events[0]["spread_pips"] == pytest.approx(2.0)
    assert spread_events[0]["threshold_pips"] == pytest.approx(3.0)
    assert spread_events[0]["is_acceptable"] is True
    assert spread_events[0]["is_spiking"] is True
    assert spread_events[0]["spread_ratio"] == pytest.approx(2.0 / 0.3)


def test_stage_09_logging_emits_htf_bias_and_changepoint_events(tmp_path: Path) -> None:
    settings = build_settings(
        tmp_path,
        RUPTURES_PENALTY="2.0",
        HTF_BIAS_WEIGHT_D="0.50",
        HTF_BIAS_WEIGHT_H4="0.30",
        HTF_BIAS_WEIGHT_H1="0.20",
        HTF_BIAS_NEUTRAL_BAND="0.15",
        HTF_TRANSITION_WINDOW_D="3",
        HTF_TRANSITION_WINDOW_H4="4",
        HTF_TRANSITION_WINDOW_H1="6",
    )
    configure_logging(settings)
    analyzer = HTFBiasAnalyzer(
        tuning=HTFBiasTuning.from_settings(settings),
        logger=get_logger("tests.stage09"),
    )

    with structlog.testing.capture_logs() as logs:
        analyzer.compute(
            [
                PinnedHTFMember(
                    snapshot=build_bias_snapshot(timeframe="D", direction="BULLISH"),
                    candles=build_candles(
                        timeframe="D",
                        closes=[100 + (index * 0.20) for index in range(30)],
                        end_time=BASE_TIME,
                    ),
                    source_snapshot_version=1,
                ),
                PinnedHTFMember(
                    snapshot=build_bias_snapshot(timeframe="H4", direction="BULLISH"),
                    candles=build_candles(
                        timeframe="H4",
                        closes=[90 + (index * 0.16) for index in range(30)],
                        end_time=BASE_TIME,
                    ),
                    source_snapshot_version=1,
                ),
                PinnedHTFMember(
                    snapshot=build_bias_snapshot(timeframe="H1", direction="BULLISH"),
                    candles=build_candles(
                        timeframe="H1",
                        closes=[100 + (index * 0.05) for index in range(24)]
                        + [101.2 + (index * 0.60) for index in range(6)],
                        end_time=BASE_TIME,
                    ),
                    source_snapshot_version=1,
                ),
            ]
        )

    changepoint_events = [entry for entry in logs if entry["event"] == "changepoint_detected"]
    bias_events = [entry for entry in logs if entry["event"] == "htf_bias_computed"]

    assert len(changepoint_events) == 1
    assert changepoint_events[0]["instrument"] == "EUR_USD"
    assert changepoint_events[0]["timeframe"] == "H1"
    assert changepoint_events[0]["method"] == "ruptures_pelt_rbf"

    assert len(bias_events) == 1
    assert bias_events[0]["instrument"] == "EUR_USD"
    assert bias_events[0]["direction"] == "BULLISH"
    assert bias_events[0]["timeframe_votes"] == {"D": "BULLISH", "H4": "BULLISH", "H1": "BULLISH"}
    assert "duration_ms" in bias_events[0]
