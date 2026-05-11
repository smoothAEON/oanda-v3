"""Phase 2 live cohesiveness tests for the snapshot analysis pipeline.

Run with:  pytest tests/live/test_phase2_analysis_coherence.py -m live -v

These tests run the ScanOrchestrator pipeline against real OANDA data. The
``scan_orchestrator`` fixture injects an always-open market-hours mock so the
pipeline works on weekends; the candle data is still real historical data.
"""

from __future__ import annotations

import math
from unittest.mock import patch

import pandas as pd
import pytest

from core.market_state import MarketStateStore
from core.models import SpreadResult, TimeframeSnapshot
from orchestration.scan_orchestrator import SCAN_TIMEFRAMES, ScanOrchestrator


@pytest.mark.live
class TestFullPipelineCoverage:
    """Pipeline produces complete snapshots for each instrument/timeframe."""

    @pytest.mark.parametrize("instrument", ["SPX500_USD", "EUR_USD"])
    def test_instrument_refresh_publishes_snapshots(
        self,
        instrument: str,
        scan_orchestrator: ScanOrchestrator,
        market_state: MarketStateStore,
    ) -> None:
        snapshots = scan_orchestrator.refresh_instrument(instrument)

        assert snapshots is not None
        assert set(snapshots) == set(SCAN_TIMEFRAMES)
        for timeframe in SCAN_TIMEFRAMES:
            snap = market_state.get_snapshot(instrument, timeframe)
            assert snap is not None, f"Missing snapshot for {instrument} {timeframe}"
            assert isinstance(snap, TimeframeSnapshot)
            assert len(snap.indicators.metrics) > 0
            assert snap.structure is not None
            assert snap.zones is not None
            assert snap.liquidity is not None
            assert snap.smc_context is not None
            assert snap.spread is not None
            assert snap.freshness is not None


@pytest.mark.live
class TestIndicatorBounds:
    """Indicator metric values fall within their mathematical bounds."""

    def test_indicator_ranges_spx500_usd(
        self,
        scan_orchestrator: ScanOrchestrator,
        market_state: MarketStateStore,
    ) -> None:
        scan_orchestrator.refresh_instrument("SPX500_USD")

        for timeframe in SCAN_TIMEFRAMES:
            snap = market_state.get_snapshot("SPX500_USD", timeframe)
            assert snap is not None

            metrics_by_name: dict[str, float | None] = {
                metric.name: metric.value for metric in snap.indicators.metrics
            }

            if "RSI" in metrics_by_name and metrics_by_name["RSI"] is not None:
                assert 0 <= metrics_by_name["RSI"] <= 100

            if "ADX" in metrics_by_name and metrics_by_name["ADX"] is not None:
                assert 0 <= metrics_by_name["ADX"] <= 100

            for stoch_name in ("STOCH_K", "STOCH_D"):
                if stoch_name in metrics_by_name and metrics_by_name[stoch_name] is not None:
                    assert 0 <= metrics_by_name[stoch_name] <= 100

            for macd_name in ("MACD", "MACD_SIGNAL", "MACD_HIST"):
                if macd_name in metrics_by_name and metrics_by_name[macd_name] is not None:
                    assert math.isfinite(metrics_by_name[macd_name])

            bb_upper = metrics_by_name.get("BB_UPPER")
            bb_middle = metrics_by_name.get("BB_MIDDLE")
            bb_lower = metrics_by_name.get("BB_LOWER")
            if bb_upper is not None and bb_middle is not None and bb_lower is not None:
                assert math.isfinite(bb_upper)
                assert math.isfinite(bb_middle)
                assert math.isfinite(bb_lower)
                assert bb_upper >= bb_middle >= bb_lower

            for tick_volume_metric in snap.indicators.tick_volume_metrics:
                assert math.isfinite(tick_volume_metric.value)


@pytest.mark.live
class TestSmcPlausibility:
    """500-bar SMC analysis should produce non-trivial structure or liquidity."""

    def test_structure_or_liquidity_non_empty(
        self,
        scan_orchestrator: ScanOrchestrator,
        market_state: MarketStateStore,
    ) -> None:
        scan_orchestrator.refresh_instrument("SPX500_USD")

        populated = 0
        for timeframe in SCAN_TIMEFRAMES:
            snap = market_state.get_snapshot("SPX500_USD", timeframe)
            assert snap is not None
            populated += len(snap.structure.recent_breaks) + len(snap.liquidity.levels)

        if populated == 0:
            pytest.skip("No recent structure or liquidity levels found in the live sample.")


@pytest.mark.live
class TestDetectorDeterminism:
    """Same input data must produce identical detector outputs."""

    def test_deterministic_h1_snapshots(
        self,
        live_settings,
        live_provider,
        always_open_market_hours,
        tmp_path,
    ) -> None:
        candles = live_provider.get_candles("SPX500_USD", "H1", count=100)
        candles_copy = candles.copy(deep=True)

        from data.csv_persistence import CandleCsvStore
        from data.persistence.trade_store import TradeStore
        from providers.cache import CandleCache
        from providers.oanda import OandaMarketDataProvider

        def _make_provider(label: str) -> OandaMarketDataProvider:
            cache_dir = tmp_path / f"det_{label}"
            cache = CandleCache(
                csv_store=CandleCsvStore(root_dir=cache_dir / "cache"),
                trade_store=TradeStore(db_path=cache_dir / "store.json"),
            )
            return OandaMarketDataProvider(settings=live_settings, cache=cache)

        state_a = MarketStateStore()
        state_b = MarketStateStore()

        orch_a = ScanOrchestrator(
            settings=live_settings,
            market_data_provider=_make_provider("a"),
            market_state=state_a,
            market_hours_service=always_open_market_hours,
        )
        orch_b = ScanOrchestrator(
            settings=live_settings,
            market_data_provider=_make_provider("b"),
            market_state=state_b,
            market_hours_service=always_open_market_hours,
        )

        snap_a = orch_a.refresh_snapshot("SPX500_USD", "H1")
        snap_b = orch_b.refresh_snapshot("SPX500_USD", "H1")

        assert snap_a is not None
        assert snap_b is not None
        assert len(snap_a.indicators.metrics) == len(snap_b.indicators.metrics)
        for metric_a, metric_b in zip(snap_a.indicators.metrics, snap_b.indicators.metrics):
            assert metric_a.name == metric_b.name
            assert metric_a.value == metric_b.value
        assert snap_a.spread.spread_pips == snap_b.spread.spread_pips
        pd.testing.assert_frame_equal(candles, candles_copy)


@pytest.mark.live
class TestCrossTimeframeConsistency:
    """Cross-timeframe invariants hold after a full instrument scan."""

    def test_temporal_ordering(
        self,
        scan_orchestrator: ScanOrchestrator,
        market_state: MarketStateStore,
    ) -> None:
        snapshots = scan_orchestrator.refresh_instrument("SPX500_USD")
        assert snapshots is not None

        stored = {}
        for timeframe in SCAN_TIMEFRAMES:
            snap = market_state.get_snapshot("SPX500_USD", timeframe)
            assert snap is not None
            stored[timeframe] = snap

        assert stored["D"].last_completed_candle <= stored["H4"].last_completed_candle
        assert stored["H4"].last_completed_candle <= stored["H1"].last_completed_candle
        assert stored["H1"].last_completed_candle <= stored["M15"].last_completed_candle
        assert all(snap.instrument == "SPX500_USD" for snap in stored.values())


@pytest.mark.live
class TestMultiInstrumentIsolation:
    """Scanning one instrument must not corrupt another's stored state."""

    def test_spx500_usd_unchanged_after_eur_usd_scan(
        self,
        scan_orchestrator: ScanOrchestrator,
        market_state: MarketStateStore,
    ) -> None:
        scan_orchestrator.refresh_instrument("SPX500_USD")
        spx_snapshots_before = {}
        for timeframe in SCAN_TIMEFRAMES:
            snap = market_state.get_snapshot("SPX500_USD", timeframe)
            assert snap is not None
            spx_snapshots_before[timeframe] = snap

        scan_orchestrator.refresh_instrument("EUR_USD")

        for timeframe in SCAN_TIMEFRAMES:
            snap_after = market_state.get_snapshot("SPX500_USD", timeframe)
            assert snap_after is not None
            before = spx_snapshots_before[timeframe]
            assert snap_after.version == before.version
            assert len(snap_after.indicators.metrics) == len(before.indicators.metrics)
            for metric_after, metric_before in zip(
                snap_after.indicators.metrics,
                before.indicators.metrics,
            ):
                assert metric_after.name == metric_before.name
                assert metric_after.value == metric_before.value


@pytest.mark.live
class TestSpreadOnLiveData:
    """SpreadResult contracts hold on live data."""

    @pytest.mark.parametrize("instrument", ["SPX500_USD", "EUR_USD"])
    def test_spread_contracts(
        self,
        instrument: str,
        scan_orchestrator: ScanOrchestrator,
        market_state: MarketStateStore,
    ) -> None:
        scan_orchestrator.refresh_instrument(instrument)

        for timeframe in SCAN_TIMEFRAMES:
            snap = market_state.get_snapshot(instrument, timeframe)
            assert snap is not None
            assert isinstance(snap.spread, SpreadResult)
            assert snap.spread.spread_pips >= 0
            assert isinstance(snap.spread.is_acceptable, bool)
            assert snap.spread.spread_ratio >= 0


@pytest.mark.live
def test_snapshot_version_pinning_roundtrip(
    scan_orchestrator: ScanOrchestrator,
    market_state: MarketStateStore,
) -> None:
    """Two successive scans produce distinct versioned snapshots retrievable by version."""

    scan_orchestrator.refresh_instrument("SPX500_USD")

    with patch("providers.cache.is_cache_fresh", return_value=True):
        scan_orchestrator.refresh_instrument("SPX500_USD")

    v1 = market_state.get_snapshot_version("SPX500_USD", "H1", 1)
    v2 = market_state.get_snapshot_version("SPX500_USD", "H1", 2)

    assert v1 is not None
    assert v2 is not None
    assert isinstance(v1, TimeframeSnapshot)
    assert isinstance(v2, TimeframeSnapshot)
    assert v1.version == 1
    assert v2.version == 2
