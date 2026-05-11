"""Stage 11 scan orchestration and publication flow."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from time import perf_counter

import pandas as pd

from config.settings import Settings, get_settings
from core.analysis_config import PUBLISHED_SNAPSHOT_TIMEFRAMES
from core.instrument_registry import SCAN_INSTRUMENTS, get_instrument_spec, normalize_instrument
from core.logging_setup import get_logger, log_failure
from core.market_state import MarketStateStore
from core.models import (
    CalendarRefreshStatus,
    MacroContextStatus,
    MarketHoursOverview,
    ScanCycleStatus,
    SnapshotFreshness,
    SpreadResult,
    TimeframeSnapshot,
)
from data.forex_calendar import ForexCalendarClient
from data.macro import MacroContextService
from data.market_hours import MarketHoursService, coerce_market_hours_overview
from indicators import build_indicator_summary
from providers.base import MarketDataProvider, PriceSnapshot
from providers.oanda import OandaMarketDataProvider
from smc.provider import SmcAdapter

SCAN_TIMEFRAMES: tuple[str, ...] = PUBLISHED_SNAPSHOT_TIMEFRAMES
_CALENDAR_CURRENCIES = frozenset({"AUD", "CAD", "CHF", "EUR", "GBP", "JPY", "NZD", "USD"})


@dataclass(frozen=True)
class _SnapshotArtifacts:
    snapshot: TimeframeSnapshot
    candles: pd.DataFrame


class ScanOrchestrator:
    """Compose Stage 11 scanning into testable steps."""

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        market_data_provider: MarketDataProvider | None = None,
        calendar_provider: ForexCalendarClient | None = None,
        market_hours_service: MarketHoursService | None = None,
        macro_context_service: MacroContextService | None = None,
        market_state: MarketStateStore | None = None,
        smc_adapter: SmcAdapter | None = None,
        indicator_builder: Callable[[pd.DataFrame, str], object] = build_indicator_summary,
    ) -> None:
        self.settings = settings or get_settings()
        self.market_data_provider = market_data_provider or OandaMarketDataProvider(
            settings=self.settings
        )
        self.calendar_provider = calendar_provider or ForexCalendarClient(settings=self.settings)
        self.market_hours_service = market_hours_service or MarketHoursService()
        self.macro_context_service = macro_context_service or MacroContextService(
            settings=self.settings
        )
        self.market_state = market_state or MarketStateStore()
        self.smc_adapter = smc_adapter or SmcAdapter(
            swing_length=self.settings.default_swing_length
        )
        self.indicator_builder = indicator_builder
        self.logger = get_logger(__name__)

        self.last_scan_status = ScanCycleStatus(run_kind="full")
        self.calendar_status = CalendarRefreshStatus()
        self.market_hours_status: MarketHoursOverview | None = None
        self.macro_status = MacroContextStatus()

    def scan_all(self, *, force: bool = False) -> ScanCycleStatus:
        """Run the full Stage 11 scheduled scan."""

        return self._run_scan(tuple(SCAN_INSTRUMENTS), run_kind="full", force=force)

    def refresh_instrument(self, instrument: str, *, force: bool = False) -> dict[str, TimeframeSnapshot] | None:
        """Refresh one instrument through the full snapshot publish path."""

        resolved = normalize_instrument(instrument)
        get_instrument_spec(resolved)
        self._refresh_market_hours()
        self.refresh_macro(force=False)

        try:
            self.refresh_calendar(force=False)
            snapshots, snapshots_published, skipped_reason, forced_market_fetch = self._scan_instrument(
                resolved,
                force=force,
            )
        except Exception as exc:
            log_failure(
                self.logger,
                "instrument_refresh_failed",
                exc,
                instrument=resolved,
            )
            raise
        self.last_scan_status = ScanCycleStatus(
            run_kind="instrument_refresh",
            started_at=datetime.now(timezone.utc),
            completed_at=datetime.now(timezone.utc),
            requested_instruments=(resolved,),
            scanned_instruments=(resolved,) if snapshots is not None else (),
            snapshots_published=snapshots_published,
            skipped_reason=None if snapshots is not None else (skipped_reason or "snapshots_unavailable"),
            forced_market_fetch=forced_market_fetch,
            errors=(),
        )
        return snapshots

    def refresh_snapshot(
        self,
        instrument: str,
        timeframe: str,
        *,
        force: bool = False,
    ) -> TimeframeSnapshot | None:
        """Refresh one timeframe snapshot."""

        resolved = normalize_instrument(instrument)
        get_instrument_spec(resolved)
        self._refresh_market_hours()
        self.refresh_macro(force=False)
        instrument_market_status = self._instrument_market_status(resolved)

        try:
            current_price = None
            if instrument_market_status.is_market_open:
                current_price = self.market_data_provider.get_current_price(resolved)
            artifacts = self._build_snapshot(
                resolved,
                timeframe,
                current_price=current_price,
                cache_only=not instrument_market_status.is_market_open and not force,
                market_open=instrument_market_status.is_market_open,
            )
        except Exception as exc:
            log_failure(
                self.logger,
                "snapshot_refresh_failed",
                exc,
                instrument=resolved,
                timeframe=timeframe,
            )
            raise
        skipped_reason = None if artifacts is not None else "market_closed_no_cache"
        self.last_scan_status = ScanCycleStatus(
            run_kind="snapshot_refresh",
            started_at=datetime.now(timezone.utc),
            completed_at=datetime.now(timezone.utc),
            requested_instruments=(resolved,),
            scanned_instruments=(resolved,) if artifacts is not None else (),
            snapshots_published=1 if artifacts is not None else 0,
            skipped_reason=skipped_reason,
            forced_market_fetch=force and not instrument_market_status.is_market_open,
            errors=(),
        )
        return None if artifacts is None else artifacts.snapshot

    def refresh_calendar(self, *, force: bool = True) -> CalendarRefreshStatus:
        """Refresh or load calendar data and publish typed status."""

        events = self.calendar_provider.get_events(force=force)
        # Read properties after get_events so the data is already fresh and no second refresh fires.
        next_high_impact = next(
            (event.event_time for event in events if event.impact == "HIGH"),
            None,
        )
        self.calendar_status = CalendarRefreshStatus(
            last_attempted_at=self.calendar_provider.last_attempted_at,
            last_refreshed_at=self.calendar_provider.last_refreshed_at,
            calendar_version=self.calendar_provider.calendar_version,
            event_count=len(events),
            next_high_impact=next_high_impact,
            used_cached=getattr(self.calendar_provider, "last_request_used_cached", False),
            last_error=self.calendar_provider.last_error,
        )
        return self.calendar_status

    def refresh_macro(self, *, force: bool = True) -> MacroContextStatus:
        """Refresh or read the bounded macro surface without breaking scans."""

        try:
            status = self.macro_context_service.get_status(force=force)
        except Exception as exc:
            log_failure(self.logger, "macro_refresh_failed", exc, level="warning")
            status = self.macro_status.model_copy(
                update={
                    "last_attempted_at": datetime.now(timezone.utc),
                    "last_error": str(exc),
                    "used_cached": False,
                }
            )
        self.macro_status = status
        return status

    def _run_scan(
        self,
        instruments: tuple[str, ...],
        *,
        run_kind: str,
        force: bool = False,
    ) -> ScanCycleStatus:
        started_at = datetime.now(timezone.utc)
        started_perf = perf_counter()
        self._refresh_market_hours()

        self.refresh_calendar(force=False)
        self.refresh_macro(force=False)
        scanned: list[str] = []
        errors: list[str] = []
        skipped_reasons: list[str] = []
        snapshots_published = 0
        forced_market_fetch = False

        for instrument in instruments:
            try:
                snapshots, published, skipped_reason, used_force_market_fetch = self._scan_instrument(
                    instrument,
                    force=force,
                )
            except Exception as exc:
                log_failure(
                    self.logger,
                    "scan_instrument_failed",
                    exc,
                    instrument=instrument,
                    run_kind=run_kind,
                )
                errors.append(f"{instrument}: {exc}")
                continue
            if snapshots is None:
                if skipped_reason is not None:
                    skipped_reasons.append(skipped_reason)
                continue
            scanned.append(instrument)
            snapshots_published += published
            forced_market_fetch = forced_market_fetch or used_force_market_fetch

        completed_at = datetime.now(timezone.utc)
        skipped_reason = None
        if not scanned and not errors and skipped_reasons:
            unique_reasons = tuple(dict.fromkeys(skipped_reasons))
            skipped_reason = unique_reasons[0] if len(unique_reasons) == 1 else "mixed_skip_reasons"
        status = ScanCycleStatus(
            run_kind=run_kind,
            started_at=started_at,
            completed_at=completed_at,
            requested_instruments=instruments,
            scanned_instruments=tuple(scanned),
            snapshots_published=snapshots_published,
            skipped_reason=skipped_reason,
            forced_market_fetch=forced_market_fetch,
            errors=tuple(errors),
        )
        self.last_scan_status = status
        self.logger.info(
            "scan_cycle_completed",
            instruments_scanned=len(scanned),
            total_duration_ms=round((perf_counter() - started_perf) * 1000.0, 3),
            snapshots_published=snapshots_published,
            errors=tuple(errors),
        )
        return status

    def _scan_instrument(
        self,
        instrument: str,
        *,
        force: bool = False,
    ) -> tuple[dict[str, TimeframeSnapshot] | None, int, str | None, bool]:
        relevant_currencies = self._calendar_currencies_for_instrument(instrument)
        if self.calendar_provider.is_event_blackout(currencies=relevant_currencies):
            self.logger.warning(
                "calendar_blackout_observed",
                instrument=instrument,
                currencies=relevant_currencies,
            )

        instrument_market_status = self._instrument_market_status(instrument)
        market_open = instrument_market_status.is_market_open
        forced_market_fetch = force and not market_open
        current_price: PriceSnapshot | None = None
        if market_open:
            current_price = self.market_data_provider.get_current_price(instrument)
        snapshots: dict[str, TimeframeSnapshot] = {}
        for timeframe in SCAN_TIMEFRAMES:
            artifacts = self._build_snapshot(
                instrument,
                timeframe,
                current_price=current_price,
                cache_only=not market_open and not force,
                market_open=market_open,
            )
            if artifacts is None:
                return None, 0, "market_closed_no_cache", False
            snapshots[timeframe] = artifacts.snapshot

        self.calendar_provider.filter_events(currencies=relevant_currencies)
        return snapshots, len(SCAN_TIMEFRAMES), None, forced_market_fetch

    def _build_snapshot(
        self,
        instrument: str,
        timeframe: str,
        *,
        current_price: PriceSnapshot | None,
        market_open: bool,
        cache_only: bool = False,
    ) -> _SnapshotArtifacts | None:
        if cache_only:
            candles = self.market_data_provider.get_cached_candles(
                instrument,
                timeframe,
                self.settings.default_candle_count,
            )
            if candles is None:
                return None
        else:
            candles = self.market_data_provider.get_candles(
                instrument,
                timeframe,
                self.settings.default_candle_count,
            )
        smc_result = self._timed_detector(
            "smc",
            instrument,
            timeframe,
            len(candles),
            lambda: self.smc_adapter.analyze(instrument, timeframe, candles),
            output_counter=lambda result: (
                len(result.order_block_candidates)
                + len(result.structure.recent_breaks)
                + len(result.liquidity.levels)
            ),
        )
        indicators = self._timed_detector(
            "indicators",
            instrument,
            timeframe,
            len(candles),
            lambda: self.indicator_builder(candles, timeframe),
            output_counter=lambda result: len(result.metrics) + len(result.tick_volume_metrics),
        )
        try:
            freshness = self._build_snapshot_freshness(instrument, timeframe, candles)
        except RuntimeError as exc:
            if not cache_only:
                raise
            self.logger.warning(
                "closed_market_snapshot_skipped",
                instrument=instrument,
                timeframe=timeframe,
                reason=str(exc),
            )
            return None
        if current_price is None:
            spread = self._closed_market_spread_context(
                instrument,
                timeframe,
                candles,
            )
        else:
            spread = self._spread_from_price(current_price)

        snapshot = TimeframeSnapshot(
            instrument=instrument,
            timeframe=timeframe,
            last_completed_candle=candles["time"].iloc[-1].to_pydatetime(),
            computed_at=datetime.now(timezone.utc),
            candle_range_start=candles["time"].iloc[0].to_pydatetime(),
            candle_range_end=candles["time"].iloc[-1].to_pydatetime(),
            indicators=indicators,
            structure=smc_result.structure,
            zones=smc_result.zones,
            liquidity=smc_result.liquidity,
            smc_context=smc_result.smc_context,
            spread=spread,
            freshness=freshness,
        )
        published = self.market_state.publish_snapshot(snapshot)
        return _SnapshotArtifacts(snapshot=published, candles=candles)

    def _closed_market_spread_context(
        self,
        instrument: str,
        timeframe: str,
        candles: pd.DataFrame,
    ) -> SpreadResult:
        existing_snapshot = self.market_state.get_snapshot(instrument, timeframe)
        if existing_snapshot is not None:
            return existing_snapshot.spread

        reference_close = float(candles["close"].iloc[-1])
        synthetic_price = PriceSnapshot(
            instrument=instrument,
            bid=reference_close,
            ask=reference_close,
            spread_price=0.0,
            spread_pips=0.0,
            fetched_at=datetime.now(timezone.utc),
        )
        spread = self._spread_from_price(synthetic_price).model_copy(
            update={
                "source": "closed_market_cache",
                "fallback_note": "No live spread available; bid and ask mirror the cached close.",
            }
        )
        self.logger.info(
            "closed_market_spread_synthesized",
            instrument=instrument,
            timeframe=timeframe,
            reference_close=reference_close,
            spread_pips=spread.spread_pips,
            fallback_note=spread.fallback_note,
        )
        return spread

    @staticmethod
    def _spread_from_price(price: PriceSnapshot) -> SpreadResult:
        spec = get_instrument_spec(price.instrument)
        raw_spread = price.ask - price.bid
        return SpreadResult(
            instrument=price.instrument,
            bid=price.bid,
            ask=price.ask,
            raw_spread=raw_spread,
            spread_pips=raw_spread / spec.pip_size,
            pip_size=spec.pip_size,
            fetched_at=price.fetched_at,
            source=getattr(price, "source", None),
            fallback_note=getattr(price, "fallback_note", None),
        )

    def _refresh_market_hours(self) -> MarketHoursOverview:
        overview = coerce_market_hours_overview(self.market_hours_service.get_status())
        self.market_hours_status = overview
        return overview

    def _instrument_market_status(self, instrument: str):
        overview = self.market_hours_status or self._refresh_market_hours()
        return overview.category_status(get_instrument_spec(instrument).category)

    def _build_snapshot_freshness(
        self,
        instrument: str,
        timeframe: str,
        candles: pd.DataFrame,
    ) -> SnapshotFreshness:
        freshness = self.market_data_provider.get_candle_freshness(instrument, timeframe)
        last_completed_candle = freshness.last_completed_candle
        fetched_at = freshness.fetched_at
        source = freshness.source
        candle_count = freshness.candle_count
        latest_candle = candles["time"].iloc[-1].to_pydatetime()
        if last_completed_candle is None or fetched_at is None or source is None or candle_count <= 0:
            raise RuntimeError(
                f"Freshness metadata missing for {instrument} {timeframe}; snapshot publication cannot infer provenance."
            )
        if last_completed_candle != latest_candle:
            raise RuntimeError(
                f"Freshness boundary mismatch for {instrument} {timeframe}: "
                f"{last_completed_candle.isoformat()} != {latest_candle.isoformat()}."
            )
        return SnapshotFreshness(
            instrument=instrument,
            timeframe=timeframe,
            last_completed_candle=last_completed_candle,
            fetched_at=fetched_at,
            source=source,
            candle_count=candle_count,
            is_fresh=freshness.is_fresh,
            staleness_seconds=freshness.staleness_seconds,
        )

    def _timed_detector(
        self,
        detector_name: str,
        instrument: str,
        timeframe: str,
        input_candle_count: int,
        fn: Callable[[], object],
        *,
        output_counter: Callable[[object], int],
    ):
        started = perf_counter()
        try:
            result = fn()
        except Exception as exc:
            log_failure(
                self.logger,
                "detector_failed",
                exc,
                detector_name=detector_name,
                instrument=instrument,
                timeframe=timeframe,
                input_candle_count=input_candle_count,
            )
            raise
        self.logger.info(
            "detector_executed",
            detector_name=detector_name,
            instrument=instrument,
            timeframe=timeframe,
            input_candle_count=input_candle_count,
            duration_ms=round((perf_counter() - started) * 1000.0, 3),
            output_count=output_counter(result),
            is_provisional=False,
        )
        return result

    def _record_skipped_scan(
        self,
        run_kind: str,
        instruments: tuple[str, ...],
        skipped_reason: str,
    ) -> None:
        self.last_scan_status = ScanCycleStatus(
            run_kind=run_kind,
            started_at=datetime.now(timezone.utc),
            completed_at=datetime.now(timezone.utc),
            requested_instruments=instruments,
            scanned_instruments=(),
            snapshots_published=0,
            skipped_reason=skipped_reason,
            errors=(),
        )

    @staticmethod
    def _calendar_currencies_for_instrument(instrument: str) -> tuple[str, ...]:
        parts = [part.upper() for part in instrument.split("_") if part]
        resolved = tuple(part for part in parts if part in _CALENDAR_CURRENCIES)
        if resolved:
            return resolved
        return ("USD",)


__all__ = ["SCAN_TIMEFRAMES", "ScanOrchestrator"]
