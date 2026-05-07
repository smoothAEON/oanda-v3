from __future__ import annotations

from datetime import datetime, timedelta, timezone

from core.models import MarketHoursOverview, MarketHoursStatus
from orchestration.cache_warmer import CacheWarmer, SCAN_TIMEFRAMES


BASE_TIME = datetime(2026, 3, 29, 8, 0, tzinfo=timezone.utc)


class RecordingProvider:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, int]] = []

    def get_candles(self, instrument: str, timeframe: str, count: int | None = None):
        self.calls.append((instrument, timeframe, count or 0))
        return None


class MixedMarketHours:
    def get_status(self):
        return MarketHoursOverview(
            overall=MarketHoursStatus(
                checked_at=BASE_TIME,
                is_market_open=True,
                source="test",
                category="overall",
                reason="partial_open",
                next_open_at=None,
                next_close_at=BASE_TIME + timedelta(hours=5),
            ),
            fx=MarketHoursStatus(
                checked_at=BASE_TIME,
                is_market_open=True,
                source="test",
                category="fx",
                reason="open",
                next_open_at=None,
                next_close_at=BASE_TIME + timedelta(hours=5),
            ),
            metals=MarketHoursStatus(
                checked_at=BASE_TIME,
                is_market_open=False,
                source="test",
                category="metals",
                reason="holiday_closed",
                next_open_at=BASE_TIME + timedelta(days=1),
                next_close_at=None,
            ),
        )


def test_cache_warmer_skips_closed_categories(monkeypatch) -> None:
    provider = RecordingProvider()
    warmer = CacheWarmer(
        market_data_provider=provider,
        market_hours_service=MixedMarketHours(),
    )
    monkeypatch.setattr("orchestration.cache_warmer.SCAN_INSTRUMENTS", ("EUR_USD", "XAU_USD"))

    warmed = warmer.warm_all()

    assert warmed == len(SCAN_TIMEFRAMES)
    assert all(instrument == "EUR_USD" for instrument, _, _ in provider.calls)
