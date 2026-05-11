from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pandas as pd
from freezegun import freeze_time

from config.settings import Settings, load_settings
from data.csv_persistence import CandleCsvStore
from data.persistence.trade_store import TradeStore
from providers.cache import CandleCache
from providers.oanda import OandaMarketDataProvider


def write_env_file(path: Path, **overrides: str) -> Path:
    values = {
        "OANDA_API_KEY": "api-key",
        "OANDA_ACCOUNT_ID": "account-id",
        "OANDA_ENVIRONMENT": "practice",
        "LOG_LEVEL": "INFO",
        "LOG_JSON": "false",
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


def build_cache(settings: Settings) -> CandleCache:
    return CandleCache(
        csv_store=CandleCsvStore(settings=settings),
        trade_store=TradeStore(settings=settings),
    )


def make_candle_payload(
    times: Sequence[str],
    *,
    complete: Sequence[bool] | None = None,
    start_price: float = 1.1000,
) -> dict[str, object]:
    flags = list(complete) if complete is not None else [True] * len(times)
    candles: list[dict[str, object]] = []
    for index, timestamp in enumerate(times):
        base = start_price + (index * 0.0010)
        candles.append(
            {
                "time": timestamp,
                "complete": flags[index],
                "volume": 100 + index,
                "mid": {
                    "o": f"{base:.5f}",
                    "h": f"{base + 0.0005:.5f}",
                    "l": f"{base - 0.0005:.5f}",
                    "c": f"{base + 0.0002:.5f}",
                },
            }
        )
    return {"candles": candles}


class DummyOandaProvider(OandaMarketDataProvider):
    def __init__(
        self,
        *,
        settings: Settings,
        cache: CandleCache,
        candle_payloads: list[dict[str, object]] | None = None,
        pricing_payload: dict[str, object] | None = None,
    ) -> None:
        super().__init__(settings=settings, cache=cache, api_client=object())
        self._candle_payloads = candle_payloads or []
        self._pricing_payload = pricing_payload or {}
        self.candle_requests: list[tuple[str, str, int, str | None]] = []

    def _request_candles_payload(
        self,
        instrument: str,
        timeframe: str,
        count: int,
        since: pd.Timestamp | None,
    ) -> dict[str, object]:
        self.candle_requests.append(
            (
                instrument,
                timeframe,
                count,
                None if since is None else since.isoformat(),
            )
        )
        if not self._candle_payloads:
            raise AssertionError("Unexpected candle API request.")
        return self._candle_payloads.pop(0)

    def _request_pricing_payload(self, instrument: str) -> dict[str, object]:
        return self._pricing_payload


@freeze_time("2026-03-20T10:15:00Z")
def test_provider_cache_cold_miss_then_memory_hit(tmp_path: Path) -> None:
    settings = build_settings(tmp_path)
    cache = build_cache(settings)
    provider = DummyOandaProvider(
        settings=settings,
        cache=cache,
        candle_payloads=[
            make_candle_payload(
                [
                    "2026-03-20T08:00:00Z",
                    "2026-03-20T09:00:00Z",
                    "2026-03-20T10:00:00Z",
                ],
                complete=[True, True, False],
            )
        ],
    )

    first = provider.get_candles("EUR_USD", "H1", count=2)
    second = provider.get_candles("EUR_USD", "H1", count=2)

    assert provider.candle_requests == [("EUR_USD", "H1", 2, None)]
    assert first["time"].tolist() == [
        pd.Timestamp("2026-03-20T08:00:00Z"),
        pd.Timestamp("2026-03-20T09:00:00Z"),
    ]
    assert second["time"].tolist() == first["time"].tolist()
    assert cache.csv_store.path_for("EUR_USD", "H1").exists()
    cache.trade_store.close()


@freeze_time("2026-03-20T10:15:00Z")
def test_provider_cache_uses_csv_after_restart(tmp_path: Path) -> None:
    settings = build_settings(tmp_path)
    seed_cache = build_cache(settings)
    seeding_provider = DummyOandaProvider(
        settings=settings,
        cache=seed_cache,
        candle_payloads=[
            make_candle_payload(
                [
                    "2026-03-20T08:00:00Z",
                    "2026-03-20T09:00:00Z",
                    "2026-03-20T10:00:00Z",
                ],
                complete=[True, True, False],
            )
        ],
    )
    seeding_provider.get_candles("EUR_USD", "H1", count=2)
    seed_cache.trade_store.close()

    restarted_cache = build_cache(settings)
    restarted_provider = DummyOandaProvider(settings=settings, cache=restarted_cache)

    result = restarted_provider.get_candles("EUR_USD", "H1", count=2)

    assert restarted_provider.candle_requests == []
    assert result["time"].tolist() == [
        pd.Timestamp("2026-03-20T08:00:00Z"),
        pd.Timestamp("2026-03-20T09:00:00Z"),
    ]
    restarted_cache.trade_store.close()


def test_provider_cache_stale_refresh_appends_newer_candles(tmp_path: Path) -> None:
    settings = build_settings(tmp_path)
    cache = build_cache(settings)
    provider = DummyOandaProvider(
        settings=settings,
        cache=cache,
        candle_payloads=[
            make_candle_payload(
                [
                    "2026-03-20T08:00:00Z",
                    "2026-03-20T09:00:00Z",
                    "2026-03-20T10:00:00Z",
                ],
                complete=[True, True, False],
            ),
            make_candle_payload(
                [
                    "2026-03-20T10:00:00Z",
                    "2026-03-20T11:00:00Z",
                ],
                complete=[True, False],
                start_price=1.1020,
            ),
        ],
    )

    with freeze_time("2026-03-20T10:15:00Z"):
        provider.get_candles("EUR_USD", "H1", count=2)

    with freeze_time("2026-03-20T11:15:00Z"):
        refreshed = provider.get_candles("EUR_USD", "H1", count=2)

    assert provider.candle_requests == [
        ("EUR_USD", "H1", 2, None),
        ("EUR_USD", "H1", 2, "2026-03-20T10:00:00+00:00"),
    ]
    assert refreshed["time"].tolist() == [
        pd.Timestamp("2026-03-20T09:00:00Z"),
        pd.Timestamp("2026-03-20T10:00:00Z"),
    ]
    cache.trade_store.close()


@freeze_time("2026-03-20T10:15:00Z")
def test_provider_cache_replaces_fresh_but_too_short_history(tmp_path: Path) -> None:
    settings = build_settings(tmp_path)
    cache = build_cache(settings)
    short_history = pd.DataFrame(
        {
            "time": ["2026-03-20T09:00:00Z"],
            "open": [1.1000],
            "high": [1.1005],
            "low": [1.0995],
            "close": [1.1002],
            "tick_volume": [100],
        }
    )
    cache.csv_store.save_candles("EUR_USD", "H1", short_history)
    cache.trade_store.upsert_cache_metadata(
        instrument="EUR_USD",
        timeframe="H1",
        last_completed_candle=pd.Timestamp("2026-03-20T09:00:00Z").to_pydatetime(),
        fetched_at=pd.Timestamp("2026-03-20T10:10:00Z").to_pydatetime(),
        candle_count=1,
        source="csv",
    )

    provider = DummyOandaProvider(
        settings=settings,
        cache=cache,
        candle_payloads=[
            make_candle_payload(
                [
                    "2026-03-20T08:00:00Z",
                    "2026-03-20T09:00:00Z",
                    "2026-03-20T10:00:00Z",
                ],
                complete=[True, True, False],
            )
        ],
    )

    result = provider.get_candles("EUR_USD", "H1", count=2)

    assert provider.candle_requests == [("EUR_USD", "H1", 2, None)]
    assert result["time"].tolist() == [
        pd.Timestamp("2026-03-20T08:00:00Z"),
        pd.Timestamp("2026-03-20T09:00:00Z"),
    ]
    cache.trade_store.close()


def test_current_price_uses_registry_pip_size_for_spread_calculation(tmp_path: Path) -> None:
    settings = build_settings(tmp_path)
    provider = DummyOandaProvider(
        settings=settings,
        cache=build_cache(settings),
        pricing_payload={
            "prices": [
                {
                    "time": "2026-03-20T10:15:00Z",
                    "bids": [{"price": "149.120"}],
                    "asks": [{"price": "149.180"}],
                }
            ]
        },
    )

    snapshot = provider.get_current_price("USD_JPY")

    assert snapshot.bid == 149.120
    assert snapshot.ask == 149.180
    assert round(snapshot.spread_price, 3) == 0.060
    assert round(snapshot.spread_pips, 2) == 6.0
    assert snapshot.fetched_at == pd.Timestamp("2026-03-20T10:15:00Z").to_pydatetime()
    provider.cache.trade_store.close()
