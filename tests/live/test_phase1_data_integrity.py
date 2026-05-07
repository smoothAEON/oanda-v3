"""Phase 1 live cohesiveness tests: data integrity across the analysis pipeline.

These tests hit the real OANDA API and validate candle schema, cache fidelity,
temporal stability, freshness semantics, price continuity, and registry
completeness.  All tests are auto-marked ``@pytest.mark.live`` by conftest.py.

Weekend-safe: tests account for wider spreads, stale freshness, and weekend
gaps when forex markets are closed.

Run with:  pytest tests/live/test_phase1_data_integrity.py -m live -v
"""

from __future__ import annotations

import asyncio
import time

import numpy as np
import pandas as pd
import pytest

from core.candle_policy import CANONICAL_COLUMNS, validate_candle_df
from core.instrument_registry import SCAN_INSTRUMENTS, get_instrument_spec
from data.market_hours import MarketHoursService
from providers.base import CandleFreshness, PriceSnapshot


# ---------------------------------------------------------------------------
# 1. Temporal stability: closed bars must not change between fetches
# ---------------------------------------------------------------------------


class TestCandleTemporalStability:
    """Verify that closed candle bars are immutable across consecutive fetches."""

    def test_spx500_usd_h1_closed_bars_stable_over_30_seconds(
        self, live_settings, tmp_path
    ) -> None:
        """Fetch SPX500_USD H1 twice (30 s apart, fresh providers). Closed bars must match."""
        from core.logging_setup import configure_logging
        from data.csv_persistence import CandleCsvStore
        from data.persistence.trade_store import TradeStore
        from providers.cache import CandleCache
        from providers.oanda import OandaMarketDataProvider

        configure_logging(live_settings)

        def _make_provider(label: str) -> OandaMarketDataProvider:
            cache_dir = tmp_path / f"stability_{label}"
            cache = CandleCache(
                csv_store=CandleCsvStore(root_dir=cache_dir / "cache"),
                trade_store=TradeStore(db_path=cache_dir / "live.json"),
            )
            return OandaMarketDataProvider(settings=live_settings, cache=cache)

        provider_a = _make_provider("a")
        df_a = provider_a.get_candles("SPX500_USD", "H1", count=100)

        time.sleep(30)

        provider_b = _make_provider("b")
        df_b = provider_b.get_candles("SPX500_USD", "H1", count=100)

        # All closed bars from the first fetch must appear unchanged in the
        # second.  Only the very last bar may differ if a new candle closed.
        shared_times = set(df_a["time"]).intersection(set(df_b["time"]))
        assert len(shared_times) >= len(df_a) - 1, (
            f"Expected at least {len(df_a) - 1} overlapping bars, got {len(shared_times)}"
        )

        overlap_a = (
            df_a[df_a["time"].isin(shared_times)]
            .sort_values("time")
            .reset_index(drop=True)
        )
        overlap_b = (
            df_b[df_b["time"].isin(shared_times)]
            .sort_values("time")
            .reset_index(drop=True)
        )
        pd.testing.assert_frame_equal(overlap_a, overlap_b)

    def test_eur_usd_m15_closed_bars_stable_over_30_seconds(
        self, live_settings, tmp_path
    ) -> None:
        """Fetch EUR_USD M15 twice (30 s apart, fresh providers). Closed bars must match."""
        from core.logging_setup import configure_logging
        from data.csv_persistence import CandleCsvStore
        from data.persistence.trade_store import TradeStore
        from providers.cache import CandleCache
        from providers.oanda import OandaMarketDataProvider

        configure_logging(live_settings)

        def _make_provider(label: str) -> OandaMarketDataProvider:
            cache_dir = tmp_path / f"stability_m15_{label}"
            cache = CandleCache(
                csv_store=CandleCsvStore(root_dir=cache_dir / "cache"),
                trade_store=TradeStore(db_path=cache_dir / "live.json"),
            )
            return OandaMarketDataProvider(settings=live_settings, cache=cache)

        provider_a = _make_provider("a")
        df_a = provider_a.get_candles("EUR_USD", "M15", count=100)

        time.sleep(30)

        provider_b = _make_provider("b")
        df_b = provider_b.get_candles("EUR_USD", "M15", count=100)

        shared_times = set(df_a["time"]).intersection(set(df_b["time"]))
        assert len(shared_times) >= len(df_a) - 1

        overlap_a = (
            df_a[df_a["time"].isin(shared_times)]
            .sort_values("time")
            .reset_index(drop=True)
        )
        overlap_b = (
            df_b[df_b["time"].isin(shared_times)]
            .sort_values("time")
            .reset_index(drop=True)
        )
        pd.testing.assert_frame_equal(overlap_a, overlap_b)


# ---------------------------------------------------------------------------
# 2. Cache fidelity: cached vs direct API, memory vs CSV
# ---------------------------------------------------------------------------


class TestCacheFidelity:
    """Validate that the 3-tier cache returns data identical to a direct API fetch."""

    def test_cached_candles_match_direct_api_fetch(
        self, live_provider, account_client
    ) -> None:
        """EUR_USD H4 from cache must match a direct account_client fetch within tolerance."""
        cached_df = live_provider.get_candles("EUR_USD", "H4", count=20)

        direct_df = asyncio.run(
            account_client.get_candles("EUR_USD", "H4", count=20)
        )

        # Compare closed bars in the overlap window
        shared_times = set(cached_df["time"]).intersection(set(direct_df["time"]))
        assert len(shared_times) > 0, "No overlapping bars between cache and direct fetch"

        overlap_cached = (
            cached_df[cached_df["time"].isin(shared_times)]
            .sort_values("time")
            .reset_index(drop=True)
        )
        overlap_direct = (
            direct_df[direct_df["time"].isin(shared_times)]
            .sort_values("time")
            .reset_index(drop=True)
        )

        for col in ("open", "high", "low", "close"):
            np.testing.assert_allclose(
                overlap_cached[col].values,
                overlap_direct[col].values,
                rtol=1e-9,
                err_msg=f"Column '{col}' differs between cache and direct API",
            )
        np.testing.assert_array_equal(
            overlap_cached["tick_volume"].values,
            overlap_direct["tick_volume"].values,
        )

    def test_csv_persisted_on_disk(self, live_provider) -> None:
        """After a cache-mediated fetch the CSV file must exist on disk."""
        live_provider.get_candles("EUR_USD", "H4", count=10)
        csv_path = live_provider.cache.csv_store.path_for("EUR_USD", "H4")
        assert csv_path.exists(), f"CSV not found at {csv_path}"

    def test_csv_matches_memory_cache(self, live_provider) -> None:
        """Candles loaded from CSV must match the in-memory cache entry."""
        live_provider.get_candles("EUR_USD", "H4", count=10)

        csv_df = live_provider.cache.csv_store.load_candles("EUR_USD", "H4")
        assert csv_df is not None, "CSV store returned None after fetch"

        # The memory cache is keyed inside the CandleCache; read directly.
        memory_entry = live_provider.cache._memory_cache.get(("EUR_USD", "H4"))
        assert memory_entry is not None, "Memory cache entry missing after fetch"

        # CSV round-trip may change datetime resolution (us vs ns); compare values only
        pd.testing.assert_frame_equal(
            csv_df.reset_index(drop=True),
            memory_entry.candles.reset_index(drop=True),
            check_dtype=False,
        )


# ---------------------------------------------------------------------------
# 3. Canonical schema on all instruments
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("instrument", SCAN_INSTRUMENTS)
class TestCanonicalSchemaOnAllInstruments:
    """Every scan instrument must produce correctly-shaped canonical candle data."""

    def test_canonical_schema(self, live_provider, instrument: str) -> None:
        """Fetch H1 candles and verify column order, types, sort, and invariants."""
        df = live_provider.get_candles(instrument, "H1", count=20)

        # Column order matches CANONICAL_COLUMNS
        assert list(df.columns) == list(CANONICAL_COLUMNS), (
            f"{instrument}: column order mismatch: {list(df.columns)}"
        )

        # time is UTC-aware and NOT the index
        assert df.index.name != "time", f"{instrument}: 'time' must not be the index"
        assert hasattr(df["time"].dt, "tz"), f"{instrument}: time column has no tz info"
        assert str(df["time"].dt.tz) == "UTC", (
            f"{instrument}: time tz is {df['time'].dt.tz}, expected UTC"
        )

        # high >= low
        assert (df["high"] >= df["low"]).all(), f"{instrument}: high < low detected"

        # OHLC > 0
        for col in ("open", "high", "low", "close"):
            assert (df[col] > 0).all(), f"{instrument}: {col} contains non-positive values"

        # tick_volume >= 0 and int64
        assert (df["tick_volume"] >= 0).all(), f"{instrument}: negative tick_volume"
        assert df["tick_volume"].dtype == np.int64, (
            f"{instrument}: tick_volume dtype is {df['tick_volume'].dtype}, expected int64"
        )

        # No duplicate timestamps
        assert df["time"].is_unique, f"{instrument}: duplicate timestamps detected"

        # Ascending sort
        times = df["time"].tolist()
        assert times == sorted(times), f"{instrument}: candles not sorted ascending"


# ---------------------------------------------------------------------------
# 4. Freshness semantics vs market hours
# ---------------------------------------------------------------------------


_FRESHNESS_INSTRUMENTS = ("SPX500_USD", "EUR_USD", "USD_JPY")


class TestFreshnessSemanticsVsMarketHours:
    """Freshness metadata must be consistent with the real market-hours calendar."""

    @pytest.mark.parametrize("instrument", _FRESHNESS_INSTRUMENTS)
    def test_freshness_consistent_with_market_hours(
        self, live_provider, instrument: str
    ) -> None:
        """Fetch H1 candles and check freshness against real market-hours status."""
        mh = MarketHoursService()
        status = mh.get_status()

        live_provider.get_candles(instrument, "H1", count=20)
        freshness = live_provider.get_candle_freshness(instrument, "H1")

        assert isinstance(freshness, CandleFreshness)
        assert freshness.candle_count > 0
        assert freshness.last_completed_candle is not None
        assert freshness.staleness_seconds is not None

        if not status.is_market_open:
            # Weekend/closed: is_fresh may be False but must not error.
            # staleness_seconds must be non-negative and < 4 days (345600 s).
            assert freshness.staleness_seconds >= 0, (
                f"{instrument}: negative staleness_seconds={freshness.staleness_seconds}"
            )
            assert freshness.staleness_seconds < 345_600, (
                f"{instrument}: staleness_seconds={freshness.staleness_seconds} "
                f"exceeds 4-day maximum"
            )
        else:
            # Market open: freshly fetched data should report fresh.
            assert freshness.is_fresh, (
                f"{instrument}: expected is_fresh=True during open market, "
                f"staleness_seconds={freshness.staleness_seconds}"
            )


# ---------------------------------------------------------------------------
# 5. Price continuity: last close vs current mid
# ---------------------------------------------------------------------------


_CONTINUITY_INSTRUMENTS = ("SPX500_USD", "EUR_USD", "USD_JPY")


class TestPriceContinuity:
    """The last closed H1 candle's close must be reasonably near the current mid price."""

    @pytest.mark.parametrize("instrument", _CONTINUITY_INSTRUMENTS)
    def test_last_close_near_current_mid(
        self, live_provider, instrument: str
    ) -> None:
        """Assert |last_close - current_mid| < 5 * typical_spread * pip_size."""
        df = live_provider.get_candles(instrument, "H1", count=5)
        last_close = df["close"].iloc[-1]

        snap = live_provider.get_current_price(instrument)
        current_mid = (snap.bid + snap.ask) / 2.0

        spec = get_instrument_spec(instrument)
        # Generous tolerance: 5 * typical_spread_pips * pip_size
        tolerance = 5.0 * spec.typical_spread_pips * spec.pip_size

        diff = abs(last_close - current_mid)
        assert diff < tolerance, (
            f"{instrument}: |last_close({last_close}) - mid({current_mid})| = {diff} "
            f"exceeds tolerance {tolerance} "
            f"(5 * {spec.typical_spread_pips} pips * {spec.pip_size})"
        )


# ---------------------------------------------------------------------------
# 6. Registry completeness and live spread sanity
# ---------------------------------------------------------------------------


def test_registry_completeness(live_provider) -> None:
    """Every SCAN_INSTRUMENT has valid registry metadata and a sane live spread."""
    for instrument in SCAN_INSTRUMENTS:
        spec = get_instrument_spec(instrument)

        # Metadata must have non-zero values
        assert spec.pip_size > 0, f"{instrument}: pip_size is zero"
        assert spec.typical_spread_pips > 0, f"{instrument}: typical_spread_pips is zero"
        assert spec.max_spread_pips > 0, f"{instrument}: max_spread_pips is zero"
        assert spec.lot_size > 0, f"{instrument}: lot_size is zero"

        # Live spread sanity: extra generous multiplier for weekend
        # Weekend spreads on metals can be 50-100x normal, so we use a very
        # wide envelope.  The test still catches absurd values (wrong instrument,
        # negative spread, etc.) without flaking on weekend widening.
        snap = live_provider.get_current_price(instrument)
        weekend_factor = 50 if spec.category == "metal" else 10
        generous_limit = spec.max_spread_pips * spec.spike_multiplier * weekend_factor
        assert snap.spread_pips < generous_limit, (
            f"{instrument}: spread {snap.spread_pips:.2f} pips exceeds "
            f"generous limit {generous_limit:.2f} "
            f"(max_spread * spike_multiplier * {weekend_factor})"
        )
