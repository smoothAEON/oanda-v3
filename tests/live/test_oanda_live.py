"""Live integration tests that hit the real OANDA API.

Run with:  pytest tests/live/ -m live -v
Requires a valid .env with OANDA_API_KEY, OANDA_ACCOUNT_ID, OANDA_ENVIRONMENT.
"""

from __future__ import annotations

import pandas as pd
import pytest

from core.candle_policy import CANONICAL_COLUMNS, validate_candle_df
from core.instrument_registry import SCAN_INSTRUMENTS, get_instrument_spec
from core.logging_setup import configure_logging
from data.csv_persistence import CandleCsvStore
from data.persistence.trade_store import TradeStore
from providers.base import CandleFreshness, PriceSnapshot
from providers.cache import CandleCache
from providers.oanda import OandaMarketDataProvider


@pytest.fixture(scope="module")
def provider(live_settings, tmp_path_factory):
    """Build a real OandaMarketDataProvider backed by a temp cache."""
    configure_logging(live_settings)
    cache_dir = tmp_path_factory.mktemp("live_cache")
    cache = CandleCache(
        csv_store=CandleCsvStore(root_dir=cache_dir / "cache"),
        trade_store=TradeStore(db_path=cache_dir / "live.json"),
    )
    return OandaMarketDataProvider(settings=live_settings, cache=cache)


# ---------------------------------------------------------------------------
# Candle fetching
# ---------------------------------------------------------------------------


@pytest.mark.live
class TestLiveGetCandles:
    def test_fetches_eur_usd_h1_candles(self, provider: OandaMarketDataProvider) -> None:
        df = provider.get_candles("EUR_USD", "H1", count=10)

        assert isinstance(df, pd.DataFrame)
        assert len(df) == 10
        assert list(df.columns) == list(CANONICAL_COLUMNS)

    def test_candles_are_valid_schema(self, provider: OandaMarketDataProvider) -> None:
        df = provider.get_candles("EUR_USD", "H1", count=5)
        validated = validate_candle_df(df)
        assert len(validated) == 5

    def test_candles_are_sorted_ascending(self, provider: OandaMarketDataProvider) -> None:
        df = provider.get_candles("EUR_USD", "H1", count=10)
        times = df["time"].tolist()
        assert times == sorted(times)

    def test_candles_have_utc_timestamps(self, provider: OandaMarketDataProvider) -> None:
        df = provider.get_candles("EUR_USD", "H1", count=5)
        assert str(df["time"].dt.tz) == "UTC"

    def test_candles_have_positive_prices(self, provider: OandaMarketDataProvider) -> None:
        df = provider.get_candles("EUR_USD", "H1", count=5)
        assert (df["open"] > 0).all()
        assert (df["high"] > 0).all()
        assert (df["low"] > 0).all()
        assert (df["close"] > 0).all()

    def test_candles_high_gte_low(self, provider: OandaMarketDataProvider) -> None:
        df = provider.get_candles("EUR_USD", "H1", count=20)
        assert (df["high"] >= df["low"]).all()

    def test_candles_have_nonnegative_tick_volume(self, provider: OandaMarketDataProvider) -> None:
        df = provider.get_candles("EUR_USD", "H1", count=10)
        assert (df["tick_volume"] >= 0).all()
        assert df["tick_volume"].dtype == "int64"

    def test_no_duplicate_timestamps(self, provider: OandaMarketDataProvider) -> None:
        df = provider.get_candles("EUR_USD", "H1", count=20)
        assert df["time"].is_unique

    def test_candle_spacing_matches_timeframe(self, provider: OandaMarketDataProvider) -> None:
        df = provider.get_candles("EUR_USD", "H1", count=10)
        diffs = df["time"].diff().dropna()
        # H1 candles should be spaced by 1 hour (or multiples if weekend gaps)
        for diff in diffs:
            minutes = diff.total_seconds() / 60
            assert minutes >= 60  # At least 1 hour apart
            assert minutes % 60 == 0  # Always whole hours


@pytest.mark.live
class TestLiveMultipleTimeframes:
    @pytest.mark.parametrize("timeframe", ["M5", "M15", "H1", "H4"])
    def test_fetches_candles_for_timeframe(
        self, provider: OandaMarketDataProvider, timeframe: str
    ) -> None:
        df = provider.get_candles("EUR_USD", timeframe, count=5)
        assert len(df) == 5
        assert list(df.columns) == list(CANONICAL_COLUMNS)

    def test_daily_candles(self, provider: OandaMarketDataProvider) -> None:
        df = provider.get_candles("EUR_USD", "D", count=5)
        assert len(df) == 5
        diffs = df["time"].diff().dropna()
        for diff in diffs:
            # Daily candles: at least 1 day apart (weekends cause gaps)
            assert diff.total_seconds() >= 86400


@pytest.mark.live
class TestLiveMultipleInstruments:
    @pytest.mark.parametrize(
        "instrument",
        ["SPX500_USD", "EUR_USD", "GBP_USD", "USD_JPY", "GBP_JPY"],
    )
    def test_fetches_candles_for_instrument(
        self, provider: OandaMarketDataProvider, instrument: str
    ) -> None:
        df = provider.get_candles(instrument, "H1", count=5)
        assert len(df) == 5
        assert (df["open"] > 0).all()


# ---------------------------------------------------------------------------
# Pricing
# ---------------------------------------------------------------------------


@pytest.mark.live
class TestLiveGetCurrentPrice:
    def test_eur_usd_price_snapshot(self, provider: OandaMarketDataProvider) -> None:
        snap = provider.get_current_price("EUR_USD")

        assert isinstance(snap, PriceSnapshot)
        assert snap.instrument == "EUR_USD"
        assert snap.bid > 0
        assert snap.ask > 0
        assert snap.ask >= snap.bid
        assert snap.spread_price >= 0
        assert snap.spread_pips >= 0
        assert snap.fetched_at is not None

    def test_spx500_usd_price_is_in_index_range(self, provider: OandaMarketDataProvider) -> None:
        snap = provider.get_current_price("SPX500_USD")
        # spx500usd should be somewhere in the hundreds to thousands range
        assert snap.bid > 500
        assert snap.ask > 500

    def test_spread_pips_uses_correct_pip_size(self, provider: OandaMarketDataProvider) -> None:
        snap = provider.get_current_price("EUR_USD")
        spec = get_instrument_spec("EUR_USD")
        expected_pips = snap.spread_price / spec.pip_size
        assert snap.spread_pips == pytest.approx(expected_pips, rel=1e-6)

    @pytest.mark.parametrize(
        "instrument",
        ["SPX500_USD", "XAG_USD", "EUR_USD", "USD_JPY", "GBP_JPY"],
    )
    def test_price_snapshot_for_instrument(
        self, provider: OandaMarketDataProvider, instrument: str
    ) -> None:
        snap = provider.get_current_price(instrument)
        assert snap.bid > 0
        assert snap.ask >= snap.bid
        assert snap.spread_pips >= 0


# ---------------------------------------------------------------------------
# Cache behavior
# ---------------------------------------------------------------------------


@pytest.mark.live
class TestLiveCacheBehavior:
    def test_second_call_hits_memory_cache(self, provider: OandaMarketDataProvider) -> None:
        first = provider.get_candles("EUR_USD", "H1", count=10)
        second = provider.get_candles("EUR_USD", "H1", count=10)

        # Same data, served from memory on the second call
        pd.testing.assert_frame_equal(first, second)

    def test_csv_persisted_after_fetch(self, provider: OandaMarketDataProvider) -> None:
        provider.get_candles("GBP_USD", "H1", count=5)
        path = provider.cache.csv_store.path_for("GBP_USD", "H1")
        assert path.exists()

    def test_freshness_after_fetch(self, provider: OandaMarketDataProvider) -> None:
        provider.get_candles("EUR_USD", "M15", count=5)
        freshness = provider.get_candle_freshness("EUR_USD", "M15")

        assert isinstance(freshness, CandleFreshness)
        assert freshness.candle_count > 0
        assert freshness.source == "oanda_api"
        assert freshness.last_completed_candle is not None
        assert freshness.fetched_at is not None

    def test_smaller_count_returns_tail_from_cache(
        self, provider: OandaMarketDataProvider
    ) -> None:
        big = provider.get_candles("EUR_USD", "H1", count=20)
        small = provider.get_candles("EUR_USD", "H1", count=5)

        # The 5 most recent candles should match
        pd.testing.assert_frame_equal(
            big.tail(5).reset_index(drop=True),
            small.reset_index(drop=True),
        )


# ---------------------------------------------------------------------------
# Spread sanity checks against registry
# ---------------------------------------------------------------------------


@pytest.mark.live
class TestLiveSpreadSanity:
    @pytest.mark.parametrize(
        "instrument",
        ["EUR_USD", "GBP_USD", "USD_JPY"],
    )
    def test_live_spread_within_max_spread(
        self, provider: OandaMarketDataProvider, instrument: str
    ) -> None:
        """Spread should normally be within max_spread_pips (may fail on weekends)."""
        snap = provider.get_current_price(instrument)
        spec = get_instrument_spec(instrument)
        # Use a generous multiplier since markets may be closed
        assert snap.spread_pips < spec.max_spread_pips * spec.spike_multiplier, (
            f"{instrument} spread {snap.spread_pips:.1f} pips exceeds "
            f"{spec.max_spread_pips * spec.spike_multiplier:.1f} "
            f"(max_spread * spike_multiplier)"
        )
