"""Unit tests for OandaMarketDataProvider edge cases and error handling."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
from freezegun import freeze_time

from config.settings import Settings, load_settings
from data.csv_persistence import CandleCsvStore
from data.persistence.trade_store import TradeStore
from providers.cache import CandleCache
from providers.oanda import OandaMarketDataProvider


def write_env_file(path: Path, **overrides: str) -> Path:
    values = {
        "OANDA_API_KEY": "k",
        "OANDA_ACCOUNT_ID": "a",
        "OANDA_ENVIRONMENT": "practice",
        "TINYDB_PATH": str(path.parent / "bot.json"),
    }
    values.update(overrides)
    path.write_text("\n".join(f"{k}={v}" for k, v in values.items()), encoding="utf-8")
    return path


def build_provider(
    tmp_path: Path,
    candle_payloads: list[dict] | None = None,
    pricing_payload: dict | None = None,
) -> tuple[StubProvider, CandleCache]:
    settings = load_settings(env_file=write_env_file(tmp_path / ".env"))
    cache = CandleCache(
        csv_store=CandleCsvStore(settings=settings),
        trade_store=TradeStore(settings=settings),
    )
    provider = StubProvider(
        settings=settings,
        cache=cache,
        candle_payloads=candle_payloads or [],
        pricing_payload=pricing_payload or {},
    )
    return provider, cache


class StubProvider(OandaMarketDataProvider):
    """Intercepts OANDA API calls for unit testing."""

    def __init__(
        self,
        *,
        settings: Settings,
        cache: CandleCache,
        candle_payloads: list[dict],
        pricing_payload: dict,
    ) -> None:
        super().__init__(settings=settings, cache=cache, api_client=object())
        self._candle_payloads = candle_payloads
        self._pricing_payload = pricing_payload

    def _request_candles_payload(self, instrument, timeframe, count, since):
        if not self._candle_payloads:
            raise AssertionError("Unexpected candle API call")
        return self._candle_payloads.pop(0)

    def _request_pricing_payload(self, instrument):
        return self._pricing_payload


class TestGetCandlesValidation:
    @freeze_time("2026-03-20T10:15:00Z")
    def test_rejects_unknown_instrument(self, tmp_path: Path) -> None:
        provider, cache = build_provider(tmp_path)
        with pytest.raises(KeyError, match="Unknown live OANDA instrument"):
            provider.get_candles("ZZZ_YYY", "H1")
        cache.trade_store.close()

    @freeze_time("2026-03-20T10:15:00Z")
    def test_rejects_unsupported_timeframe(self, tmp_path: Path) -> None:
        provider, cache = build_provider(tmp_path)
        with pytest.raises(ValueError, match="Unsupported timeframe"):
            provider.get_candles("EUR_USD", "M")
        cache.trade_store.close()

    @freeze_time("2026-03-20T10:15:00Z")
    def test_rejects_count_above_oanda_limit(self, tmp_path: Path) -> None:
        provider, cache = build_provider(tmp_path)
        with pytest.raises(ValueError, match="less than or equal to 5000"):
            provider.get_candles("EUR_USD", "H1", count=5001)
        cache.trade_store.close()

    @freeze_time("2026-03-20T10:15:00Z")
    def test_rejects_zero_count(self, tmp_path: Path) -> None:
        provider, cache = build_provider(tmp_path)
        with pytest.raises(ValueError, match="positive"):
            provider.get_candles("EUR_USD", "H1", count=0)
        cache.trade_store.close()

    @freeze_time("2026-03-20T10:15:00Z")
    def test_rejects_negative_count(self, tmp_path: Path) -> None:
        provider, cache = build_provider(tmp_path)
        with pytest.raises(ValueError, match="positive"):
            provider.get_candles("EUR_USD", "H1", count=-1)
        cache.trade_store.close()

    def test_request_payload_caps_count_and_adds_oanda_alignment(self, tmp_path: Path) -> None:
        settings = load_settings(env_file=write_env_file(tmp_path / ".env"))
        captured: dict[str, object] = {}

        class CaptureEndpoint:
            def __init__(self, *, instrument: str, params: dict[str, object]) -> None:
                captured["instrument"] = instrument
                captured["params"] = params
                self.response: dict[str, object] = {"candles": []}

        class FakeApi:
            def request(self, _endpoint: object) -> None:
                return None

        class CaptureProvider(OandaMarketDataProvider):
            @staticmethod
            def _import_instruments_candles_endpoint() -> object:
                return CaptureEndpoint

        cache = CandleCache(
            csv_store=CandleCsvStore(settings=settings),
            trade_store=TradeStore(settings=settings),
        )
        provider = CaptureProvider(
            settings=settings,
            cache=cache,
            api_client=FakeApi(),
        )

        provider._request_candles_payload("EUR_USD", "H1", 5000, None)

        params = captured["params"]
        assert captured["instrument"] == "EUR_USD"
        assert params["granularity"] == "H1"
        assert params["price"] == "M"
        assert params["count"] == 5000
        assert params["dailyAlignment"] == 17
        assert params["alignmentTimezone"] == "America/New_York"
        assert params["weeklyAlignment"] == "Friday"
        cache.trade_store.close()


class TestGetCandlesAPIResponse:
    @freeze_time("2026-03-20T10:15:00Z")
    def test_empty_candles_raises(self, tmp_path: Path) -> None:
        provider, cache = build_provider(tmp_path, candle_payloads=[{"candles": []}])
        with pytest.raises(RuntimeError, match="no candles"):
            provider.get_candles("EUR_USD", "H1", count=2)
        cache.trade_store.close()

    @freeze_time("2026-03-20T10:15:00Z")
    def test_all_incomplete_raises(self, tmp_path: Path) -> None:
        payload = {
            "candles": [
                {
                    "time": "2026-03-20T09:00:00Z",
                    "complete": False,
                    "volume": 100,
                    "mid": {"o": "1.1", "h": "1.2", "l": "1.0", "c": "1.15"},
                }
            ]
        }
        provider, cache = build_provider(tmp_path, candle_payloads=[payload])
        with pytest.raises(RuntimeError, match="no complete candles"):
            provider.get_candles("EUR_USD", "H1", count=1)
        cache.trade_store.close()

    @freeze_time("2026-03-20T10:15:00Z")
    def test_skips_incomplete_candles(self, tmp_path: Path) -> None:
        payload = {
            "candles": [
                {
                    "time": "2026-03-20T08:00:00Z",
                    "complete": True,
                    "volume": 100,
                    "mid": {"o": "1.1", "h": "1.2", "l": "1.0", "c": "1.15"},
                },
                {
                    "time": "2026-03-20T09:00:00Z",
                    "complete": True,
                    "volume": 101,
                    "mid": {"o": "1.15", "h": "1.25", "l": "1.05", "c": "1.2"},
                },
                {
                    "time": "2026-03-20T10:00:00Z",
                    "complete": False,
                    "volume": 10,
                    "mid": {"o": "1.2", "h": "1.3", "l": "1.1", "c": "1.22"},
                },
            ]
        }
        provider, cache = build_provider(tmp_path, candle_payloads=[payload])
        result = provider.get_candles("EUR_USD", "H1", count=2)
        assert len(result) == 2
        assert result["time"].iloc[-1] == pd.Timestamp("2026-03-20T09:00:00Z")
        cache.trade_store.close()

    @freeze_time("2026-03-20T10:15:00Z")
    def test_uses_default_candle_count(self, tmp_path: Path) -> None:
        # When count is None, should use settings.default_candle_count
        payload = {
            "candles": [
                {
                    "time": f"2026-03-20T0{i}:00:00Z",
                    "complete": True,
                    "volume": 100 + i,
                    "mid": {"o": "1.1", "h": "1.2", "l": "1.0", "c": "1.15"},
                }
                for i in range(3)
            ]
            + [
                {
                    "time": "2026-03-20T10:00:00Z",
                    "complete": False,
                    "volume": 10,
                    "mid": {"o": "1.1", "h": "1.2", "l": "1.0", "c": "1.15"},
                }
            ]
        }
        provider, cache = build_provider(tmp_path, candle_payloads=[payload])
        result = provider.get_candles("EUR_USD", "H1")
        assert len(result) <= 3  # Only 3 complete candles in payload
        cache.trade_store.close()


class TestGetCurrentPrice:
    def test_computes_spread_in_pips_for_spx500(self, tmp_path: Path) -> None:
        pricing = {
            "prices": [
                {
                    "time": "2026-03-20T10:15:00Z",
                    "bids": [{"price": "3050.50"}],
                    "asks": [{"price": "3051.00"}],
                }
            ]
        }
        provider, cache = build_provider(tmp_path, pricing_payload=pricing)
        snap = provider.get_current_price("SPX500_USD")
        assert snap.bid == 3050.50
        assert snap.ask == 3051.00
        assert snap.spread_pips == pytest.approx(0.5)  # 0.50 / 1.0
        cache.trade_store.close()

    def test_empty_prices_raises(self, tmp_path: Path) -> None:
        provider, cache = build_provider(tmp_path, pricing_payload={"prices": []})
        with pytest.raises(RuntimeError, match="no prices"):
            provider.get_current_price("EUR_USD")
        cache.trade_store.close()

    def test_missing_bids_raises(self, tmp_path: Path) -> None:
        pricing = {
            "prices": [
                {
                    "time": "2026-03-20T10:15:00Z",
                    "bids": [],
                    "asks": [{"price": "1.1"}],
                }
            ]
        }
        provider, cache = build_provider(tmp_path, pricing_payload=pricing)
        with pytest.raises(RuntimeError, match="bid/ask"):
            provider.get_current_price("EUR_USD")
        cache.trade_store.close()


class TestGetCandleFreshness:
    @freeze_time("2026-03-20T10:15:00Z")
    def test_empty_cache_returns_not_fresh(self, tmp_path: Path) -> None:
        provider, cache = build_provider(tmp_path)
        freshness = provider.get_candle_freshness("EUR_USD", "H1")
        assert freshness.is_fresh is False
        assert freshness.candle_count == 0
        assert freshness.source is None
        cache.trade_store.close()

    def test_rejects_unknown_instrument(self, tmp_path: Path) -> None:
        provider, cache = build_provider(tmp_path)
        with pytest.raises(KeyError):
            provider.get_candle_freshness("ZZZ_YYY", "H1")
        cache.trade_store.close()


class TestWeekendGapHandling:
    def test_d_candle_weekend_gap_returns_existing_cache(self, tmp_path: Path) -> None:
        """Stale D cache + OANDA returning only the forming Monday candle (complete=False)
        must fall back to existing cache data instead of raising.

        At Friday 10:15Z the last complete D candle is Thursday (2026-03-19). On Monday
        the cache is stale; _append_refresh fires with since=Friday. OANDA returns only the
        forming Monday candle (complete=False) — the weekend gap. The provider must return
        Thursday's cached data without error.
        """
        thursday_payload = {
            "candles": [
                {
                    "time": "2026-03-19T00:00:00Z",
                    "complete": True,
                    "volume": 500,
                    "mid": {"o": "3050.0", "h": "3060.0", "l": "3040.0", "c": "3055.0"},
                }
            ]
        }
        monday_gap_payload = {
            "candles": [
                {
                    "time": "2026-03-23T00:00:00Z",
                    "complete": False,
                    "volume": 10,
                    "mid": {"o": "3060.0", "h": "3065.0", "l": "3058.0", "c": "3062.0"},
                }
            ]
        }

        with freeze_time("2026-03-20T10:15:00Z"):
            provider, cache = build_provider(
                tmp_path, candle_payloads=[thursday_payload, monday_gap_payload]
            )
            provider.get_candles("SPX500_USD", "D", count=1)

        with freeze_time("2026-03-23T08:00:00Z"):
            result = provider.get_candles("SPX500_USD", "D", count=1)

        assert len(result) == 1
        assert result["time"].iloc[0] == pd.Timestamp("2026-03-19T00:00:00Z")
        cache.trade_store.close()


class TestParseOandaTime:
    def test_parses_zulu_time(self) -> None:
        result = OandaMarketDataProvider._parse_oanda_time("2026-03-20T10:15:00.000000000Z")
        assert result.year == 2026
        assert result.tzinfo is not None

    def test_parses_offset_time(self) -> None:
        result = OandaMarketDataProvider._parse_oanda_time("2026-03-20T10:15:00+00:00")
        assert result.year == 2026

    def test_none_raises(self) -> None:
        with pytest.raises(ValueError, match="OANDA timestamp is missing"):
            OandaMarketDataProvider._parse_oanda_time(None)
