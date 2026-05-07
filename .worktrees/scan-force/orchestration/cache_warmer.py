"""Stage 11 cache warming jobs."""

from __future__ import annotations

from time import perf_counter

from config.settings import Settings, get_settings
from core.instrument_registry import SCAN_INSTRUMENTS, get_instrument_spec, normalize_instrument
from core.logging_setup import get_logger
from data.market_hours import MarketHoursService, coerce_market_hours_overview
from orchestration.scan_orchestrator import SCAN_TIMEFRAMES
from providers.base import MarketDataProvider
from providers.oanda import OandaMarketDataProvider


class CacheWarmer:
    """Warm the candle cache through the same provider path as scans."""

    def __init__(
        self,
        market_data_provider: MarketDataProvider | None = None,
        *,
        settings: Settings | None = None,
        market_hours_service: MarketHoursService | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.market_data_provider = market_data_provider or OandaMarketDataProvider(
            settings=self.settings
        )
        self.market_hours_service = market_hours_service or MarketHoursService()
        self.logger = get_logger(__name__)
        self.market_hours_status = None

    def warm_all(self) -> int:
        """Warm all Stage 11 instruments and timeframes."""

        started = perf_counter()
        self.market_hours_status = coerce_market_hours_overview(self.market_hours_service.get_status())
        warmed = 0
        for instrument in SCAN_INSTRUMENTS:
            warmed += self.warm_instrument(instrument)
        self.logger.info(
            "cache_warm_completed",
            instruments_warmed=len(SCAN_INSTRUMENTS),
            timeframe_count=warmed,
            total_duration_ms=round((perf_counter() - started) * 1000.0, 3),
            skipped_reason=None if warmed else "all_categories_closed",
        )
        return warmed

    def warm_instrument(self, instrument: str) -> int:
        """Warm one instrument without publishing snapshots or bundles."""

        resolved = normalize_instrument(instrument)
        instrument_status = self._instrument_market_status(resolved)
        if not instrument_status.is_market_open:
            self.logger.info(
                "cache_warm_skipped",
                instrument=resolved,
                reason=instrument_status.reason,
                category=instrument_status.category,
            )
            return 0

        warmed = 0
        for timeframe in SCAN_TIMEFRAMES:
            self.market_data_provider.get_candles(
                resolved,
                timeframe,
                self.settings.default_candle_count,
            )
            warmed += 1
        return warmed

    def _instrument_market_status(self, instrument: str):
        overview = self.market_hours_status or coerce_market_hours_overview(
            self.market_hours_service.get_status()
        )
        return overview.category_status(get_instrument_spec(instrument).category)


__all__ = ["CacheWarmer"]
