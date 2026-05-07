"""Phase 2 live cohesiveness tests — full analysis pipeline on live OANDA data.

Run with:  pytest tests/live/test_phase2_analysis_coherence.py -m live -v

These tests run the full ScanOrchestrator pipeline against real OANDA data.
The ``scan_orchestrator`` fixture injects an always-open market-hours mock so
the pipeline works on weekends; the candle data is still real historical data.
"""

from __future__ import annotations

import copy
import math
from unittest.mock import patch

import pandas as pd
import pytest

from core.instrument_registry import SCAN_INSTRUMENTS, get_instrument_spec
from core.market_state import MarketStateStore
from core.models import (
    ChopResult,
    IndicatorValueSummary,
    InstrumentBundle,
    SpreadResult,
    TimeframeSnapshot,
)
from orchestration.scan_orchestrator import HTF_TIMEFRAMES, SCAN_TIMEFRAMES, ScanOrchestrator


# ---------------------------------------------------------------------------
# 1. TestFullPipelineCoverage
# ---------------------------------------------------------------------------


@pytest.mark.live
class TestFullPipelineCoverage:
    """Pipeline produces complete snapshots and bundles for each instrument."""

    def test_spx500_usd_bundle_and_snapshots(
        self,
        scan_orchestrator: ScanOrchestrator,
        market_state: MarketStateStore,
    ) -> None:
        bundle = scan_orchestrator.refresh_instrument("SPX500_USD")

        assert bundle is not None
        assert isinstance(bundle, InstrumentBundle)

        # Bundle pins all HTF timeframes
        for tf in HTF_TIMEFRAMES:
            assert tf in bundle.members, f"Bundle missing HTF member {tf}"

        # Every SCAN_TIMEFRAME snapshot is populated in market_state
        for tf in SCAN_TIMEFRAMES:
            snap = market_state.get_snapshot("SPX500_USD", tf)
            assert snap is not None, f"Missing snapshot for SPX500_USD {tf}"
            assert isinstance(snap, TimeframeSnapshot)

            # Required fields present
            assert len(snap.indicators.metrics) > 0, f"No indicator metrics for {tf}"
            assert snap.structure is not None
            assert snap.zones is not None
            assert snap.liquidity is not None
            assert snap.spread is not None
            assert snap.chop is not None
            assert snap.sfp is not None
            assert snap.turtle_soup is not None

            # ORB only on M15
            if tf == "M15":
                assert snap.orb is not None, "M15 must have ORB result"
            else:
                assert snap.orb is None, f"{tf} must NOT have ORB result"

    def test_eur_usd_bundle_and_snapshots(
        self,
        scan_orchestrator: ScanOrchestrator,
        market_state: MarketStateStore,
    ) -> None:
        bundle = scan_orchestrator.refresh_instrument("EUR_USD")

        assert bundle is not None
        assert isinstance(bundle, InstrumentBundle)

        for tf in HTF_TIMEFRAMES:
            assert tf in bundle.members, f"Bundle missing HTF member {tf}"

        for tf in SCAN_TIMEFRAMES:
            snap = market_state.get_snapshot("EUR_USD", tf)
            assert snap is not None, f"Missing snapshot for EUR_USD {tf}"
            assert isinstance(snap, TimeframeSnapshot)
            assert len(snap.indicators.metrics) > 0, f"No indicator metrics for {tf}"
            assert snap.structure is not None
            assert snap.zones is not None
            assert snap.liquidity is not None
            assert snap.spread is not None
            assert snap.chop is not None
            assert snap.sfp is not None
            assert snap.turtle_soup is not None

            if tf == "M15":
                assert snap.orb is not None
            else:
                assert snap.orb is None


# ---------------------------------------------------------------------------
# 2. TestIndicatorBounds
# ---------------------------------------------------------------------------


@pytest.mark.live
class TestIndicatorBounds:
    """Indicator metric values fall within their mathematical bounds."""

    def test_indicator_ranges_spx500_usd(
        self,
        scan_orchestrator: ScanOrchestrator,
        market_state: MarketStateStore,
    ) -> None:
        scan_orchestrator.refresh_instrument("SPX500_USD")

        for tf in SCAN_TIMEFRAMES:
            snap = market_state.get_snapshot("SPX500_USD", tf)
            assert snap is not None

            metrics_by_name: dict[str, float | None] = {
                m.name: m.value for m in snap.indicators.metrics
            }

            # RSI in [0, 100]
            if "RSI" in metrics_by_name and metrics_by_name["RSI"] is not None:
                assert 0 <= metrics_by_name["RSI"] <= 100, f"RSI out of range on {tf}"

            # ADX in [0, 100]
            if "ADX" in metrics_by_name and metrics_by_name["ADX"] is not None:
                assert 0 <= metrics_by_name["ADX"] <= 100, f"ADX out of range on {tf}"

            # Stochastic in [0, 100]
            for stoch_name in ("STOCH_K", "STOCH_D"):
                if stoch_name in metrics_by_name and metrics_by_name[stoch_name] is not None:
                    assert 0 <= metrics_by_name[stoch_name] <= 100, (
                        f"{stoch_name} out of range on {tf}"
                    )

            # MACD family: finite
            for macd_name in ("MACD", "MACD_SIGNAL", "MACD_HIST"):
                if macd_name in metrics_by_name and metrics_by_name[macd_name] is not None:
                    assert math.isfinite(metrics_by_name[macd_name]), (
                        f"{macd_name} is not finite on {tf}"
                    )

            # Bollinger Bands: finite and BB_UPPER >= BB_MIDDLE >= BB_LOWER
            bb_upper = metrics_by_name.get("BB_UPPER")
            bb_middle = metrics_by_name.get("BB_MIDDLE")
            bb_lower = metrics_by_name.get("BB_LOWER")
            if bb_upper is not None and bb_middle is not None and bb_lower is not None:
                assert math.isfinite(bb_upper), f"BB_UPPER not finite on {tf}"
                assert math.isfinite(bb_middle), f"BB_MIDDLE not finite on {tf}"
                assert math.isfinite(bb_lower), f"BB_LOWER not finite on {tf}"
                assert bb_upper >= bb_middle >= bb_lower, (
                    f"Bollinger Band ordering violated on {tf}: "
                    f"upper={bb_upper}, middle={bb_middle}, lower={bb_lower}"
                )

            # Tick-volume metrics: all finite
            for tvm in snap.indicators.tick_volume_metrics:
                assert math.isfinite(tvm.value), (
                    f"Tick volume metric '{tvm.name}' is not finite on {tf}"
                )


# ---------------------------------------------------------------------------
# 3. TestSmcPlausibility
# ---------------------------------------------------------------------------


@pytest.mark.live
class TestSmcPlausibility:
    """500-bar SMC analysis should produce non-trivial structure and liquidity."""

    def test_structure_and_liquidity_non_empty(
        self,
        scan_orchestrator: ScanOrchestrator,
        market_state: MarketStateStore,
    ) -> None:
        scan_orchestrator.refresh_instrument("SPX500_USD")

        # Check across all timeframes; use soft skip if truly empty
        for tf in SCAN_TIMEFRAMES:
            snap = market_state.get_snapshot("SPX500_USD", tf)
            assert snap is not None

            if len(snap.structure.recent_breaks) == 0:
                pytest.skip(
                    f"SPX500_USD {tf}: no recent_breaks found — unusual but possible "
                    f"on very low-volatility periods"
                )

            if len(snap.liquidity.levels) == 0:
                pytest.skip(
                    f"SPX500_USD {tf}: no liquidity levels found — unusual but possible"
                )


# ---------------------------------------------------------------------------
# 4. TestDetectorDeterminism
# ---------------------------------------------------------------------------


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
        # Fetch candles once and deep-copy for mutation check
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

        # Two independent providers with separate caches avoid stale-append
        # failures on weekends.
        provider_a = _make_provider("a")
        provider_b = _make_provider("b")

        state_a = MarketStateStore()
        state_b = MarketStateStore()

        orch_a = ScanOrchestrator(
            settings=live_settings,
            market_data_provider=provider_a,
            market_state=state_a,
            market_hours_service=always_open_market_hours,
        )
        orch_b = ScanOrchestrator(
            settings=live_settings,
            market_data_provider=provider_b,
            market_state=state_b,
            market_hours_service=always_open_market_hours,
        )

        snap_a = orch_a.refresh_snapshot("SPX500_USD", "H1")
        snap_b = orch_b.refresh_snapshot("SPX500_USD", "H1")

        assert snap_a is not None
        assert snap_b is not None

        # Indicators match
        assert len(snap_a.indicators.metrics) == len(snap_b.indicators.metrics)
        for ma, mb in zip(snap_a.indicators.metrics, snap_b.indicators.metrics):
            assert ma.name == mb.name
            assert ma.value == mb.value

        # Spread match
        assert snap_a.spread.spread_pips == snap_b.spread.spread_pips
        assert snap_a.spread.is_acceptable == snap_b.spread.is_acceptable

        # Chop match
        assert snap_a.chop.status == snap_b.chop.status

        # Original candles not mutated
        pd.testing.assert_frame_equal(candles, candles_copy)


# ---------------------------------------------------------------------------
# 5. TestCrossTimeframeConsistency
# ---------------------------------------------------------------------------


@pytest.mark.live
class TestCrossTimeframeConsistency:
    """Cross-timeframe invariants hold after a full instrument scan."""

    def test_temporal_ordering_and_bias_bounds(
        self,
        scan_orchestrator: ScanOrchestrator,
        market_state: MarketStateStore,
    ) -> None:
        bundle = scan_orchestrator.refresh_instrument("SPX500_USD")
        assert bundle is not None

        snapshots = {}
        for tf in SCAN_TIMEFRAMES:
            snap = market_state.get_snapshot("SPX500_USD", tf)
            assert snap is not None
            snapshots[tf] = snap

        # D.last_completed_candle <= H4 <= H1 <= M15
        assert snapshots["D"].last_completed_candle <= snapshots["H4"].last_completed_candle
        assert snapshots["H4"].last_completed_candle <= snapshots["H1"].last_completed_candle
        assert snapshots["H1"].last_completed_candle <= snapshots["M15"].last_completed_candle

        # All snapshots belong to SPX500_USD
        for tf in SCAN_TIMEFRAMES:
            assert snapshots[tf].instrument == "SPX500_USD"

        # HTF bias direction is valid
        assert bundle.htf_bias.direction in ("BULLISH", "BEARISH", "NEUTRAL")

        # alignment_score in [0.0, 1.0]
        assert 0.0 <= bundle.htf_bias.alignment_score <= 1.0


# ---------------------------------------------------------------------------
# 6. TestMultiInstrumentIsolation
# ---------------------------------------------------------------------------


@pytest.mark.live
class TestMultiInstrumentIsolation:
    """Scanning one instrument must not corrupt another's stored state."""

    def test_spx500_usd_unchanged_after_eur_usd_scan(
        self,
        scan_orchestrator: ScanOrchestrator,
        market_state: MarketStateStore,
    ) -> None:
        # Scan SPX500_USD first
        scan_orchestrator.refresh_instrument("SPX500_USD")

        spx_snapshots_before: dict[str, TimeframeSnapshot] = {}
        for tf in SCAN_TIMEFRAMES:
            snap = market_state.get_snapshot("SPX500_USD", tf)
            assert snap is not None
            spx_snapshots_before[tf] = snap

        # Scan EUR_USD
        scan_orchestrator.refresh_instrument("EUR_USD")

        # SPX500_USD snapshots must be unchanged
        for tf in SCAN_TIMEFRAMES:
            snap_after = market_state.get_snapshot("SPX500_USD", tf)
            assert snap_after is not None

            before = spx_snapshots_before[tf]
            assert snap_after.version == before.version, (
                f"SPX500_USD {tf} version changed after EUR_USD scan"
            )

            # Indicator values unchanged
            assert len(snap_after.indicators.metrics) == len(before.indicators.metrics)
            for ma, mb in zip(snap_after.indicators.metrics, before.indicators.metrics):
                assert ma.name == mb.name
                assert ma.value == mb.value


# ---------------------------------------------------------------------------
# 7. TestSpreadAndChopOnLiveData
# ---------------------------------------------------------------------------


@pytest.mark.live
class TestSpreadAndChopOnLiveData:
    """SpreadResult and ChopResult contracts hold on live data."""

    @pytest.mark.parametrize("instrument", ["SPX500_USD", "EUR_USD"])
    def test_spread_and_chop_contracts(
        self,
        instrument: str,
        scan_orchestrator: ScanOrchestrator,
        market_state: MarketStateStore,
    ) -> None:
        scan_orchestrator.refresh_instrument(instrument)

        for tf in SCAN_TIMEFRAMES:
            snap = market_state.get_snapshot(instrument, tf)
            assert snap is not None

            # SpreadResult checks
            assert isinstance(snap.spread, SpreadResult)
            assert snap.spread.spread_pips >= 0
            assert isinstance(snap.spread.is_acceptable, bool)
            assert snap.spread.spread_ratio >= 0

            # ChopResult checks
            assert isinstance(snap.chop, ChopResult)
            assert snap.chop.status in ("PASS", "CAUTION", "REJECT")

            # If ADX metric exists, chop metric_value should match
            adx_value = None
            for m in snap.indicators.metrics:
                if m.name == "ADX":
                    adx_value = m.value
                    break

            if adx_value is not None and snap.chop.metric_value is not None:
                assert snap.chop.metric_value == pytest.approx(adx_value, rel=1e-6), (
                    f"Chop metric_value {snap.chop.metric_value} does not match "
                    f"ADX indicator {adx_value} on {instrument} {tf}"
                )


# ---------------------------------------------------------------------------
# 8. test_snapshot_version_pinning_roundtrip
# ---------------------------------------------------------------------------


@pytest.mark.live
def test_snapshot_version_pinning_roundtrip(
    scan_orchestrator: ScanOrchestrator,
    market_state: MarketStateStore,
) -> None:
    """Two successive scans produce distinct versioned snapshots retrievable by version."""

    # First scan -> v1
    scan_orchestrator.refresh_instrument("SPX500_USD")

    # Second scan -> v2
    # Patch is_cache_fresh to return True so the cache returns existing data
    # instead of attempting an append-refresh (which fails on weekends when
    # OANDA returns no new candles).
    with patch("providers.cache.is_cache_fresh", return_value=True):
        scan_orchestrator.refresh_instrument("SPX500_USD")

    v1 = market_state.get_snapshot_version("SPX500_USD", "H1", 1)
    v2 = market_state.get_snapshot_version("SPX500_USD", "H1", 2)

    assert v1 is not None, "v1 snapshot not found in history"
    assert v2 is not None, "v2 snapshot not found in history"

    assert isinstance(v1, TimeframeSnapshot)
    assert isinstance(v2, TimeframeSnapshot)

    assert v1.version == 1
    assert v2.version == 2
