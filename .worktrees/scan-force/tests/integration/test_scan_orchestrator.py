from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest
import structlog.testing

from config.settings import Settings, load_settings
from core.models import (
    ActiveZoneSummary,
    CalendarEvent,
    ChopResult,
    HTFBiasResult,
    IndicatorValueSummary,
    LiquidityPoolSummary,
    MarketHoursOverview,
    MarketHoursStatus,
    ORBResult,
    SFPResult,
    SnapshotFreshness,
    SmcContextSummary,
    SpreadResult,
    StructureEventSummary,
    TimeframeSnapshot,
    TurtleSoupResult,
)
from orchestration.scan_orchestrator import HTF_TIMEFRAMES, SCAN_TIMEFRAMES, ScanOrchestrator
from providers.base import CandleFreshness, PriceSnapshot


BASE_TIME = datetime(2026, 3, 21, 8, 0, tzinfo=timezone.utc)


def write_env_file(path: Path, **overrides: str) -> Path:
    values = {
        "OANDA_API_KEY": "api-key",
        "OANDA_ACCOUNT_ID": "account-id",
        "OANDA_ENVIRONMENT": "practice",
        "TELEGRAM_BOT_TOKEN": "telegram-token",
        "TELEGRAM_CHAT_ID": "123456789",
        "TELEGRAM_BOT_PASSWORD": "bot-password",
        "TELEGRAM_ADMIN_IDS": "111,222",
        "TINYDB_PATH": str(path.parent / "bot.json"),
    }
    values.update(overrides)
    path.write_text(
        "\n".join(f"{key}={value}" for key, value in values.items()),
        encoding="utf-8",
    )
    return path


def build_settings(tmp_path: Path) -> Settings:
    return load_settings(env_file=write_env_file(tmp_path / ".env"))


class StubMarketDataProvider:
    def __init__(self, *, cached_available: bool = True) -> None:
        self.calls = 0
        self.cached_calls = 0
        self.price_calls = 0
        self.cached_available = cached_available

    @staticmethod
    def _build_frame(timeframe: str) -> pd.DataFrame:
        times = pd.date_range(end=BASE_TIME, periods=30, freq="1h", tz="UTC")
        if timeframe == "M15":
            times = pd.date_range(end=BASE_TIME, periods=30, freq="15min", tz="UTC")
        elif timeframe == "H4":
            times = pd.date_range(end=BASE_TIME, periods=30, freq="4h", tz="UTC")
        elif timeframe == "D":
            times = pd.date_range(end=BASE_TIME, periods=30, freq="1D", tz="UTC")
        return pd.DataFrame(
            {
                "time": times,
                "open": [1.0] * len(times),
                "high": [1.1] * len(times),
                "low": [0.9] * len(times),
                "close": [1.05] * len(times),
                "tick_volume": list(range(100, 100 + len(times))),
            }
        )

    def get_candles(self, instrument: str, timeframe: str, count: int | None = None) -> pd.DataFrame:
        self.calls += 1
        return self._build_frame(timeframe)

    def get_cached_candles(self, instrument: str, timeframe: str, count: int | None = None) -> pd.DataFrame | None:
        self.cached_calls += 1
        if not self.cached_available:
            return None
        return self._build_frame(timeframe)

    def get_current_price(self, instrument: str) -> PriceSnapshot:
        self.price_calls += 1
        return PriceSnapshot(
            instrument=instrument,
            bid=1.0500,
            ask=1.0502,
            spread_price=0.0002,
            spread_pips=2.0,
            fetched_at=BASE_TIME,
        )

    def get_candle_freshness(self, instrument: str, timeframe: str) -> CandleFreshness:
        return CandleFreshness(
            instrument=instrument,
            timeframe=timeframe,
            last_completed_candle=BASE_TIME,
            fetched_at=BASE_TIME + timedelta(minutes=1),
            source="oanda_api",
            candle_count=30,
            is_fresh=True,
            staleness_seconds=0.0,
        )


class StubCalendarProvider:
    def __init__(self) -> None:
        self.calendar_version = 1
        self.last_attempted_at = BASE_TIME
        self.last_refreshed_at = BASE_TIME
        self.last_error = None

    def get_events(self, *, force: bool = False):
        return (
            CalendarEvent(
                title="CPI",
                event_time=BASE_TIME + timedelta(hours=2),
                impact="HIGH",
                currency="USD",
                is_blackout=True,
            ),
        )

    def filter_events(self, *, currencies=None, countries=None, impacts=None, window_start=None, window_end=None):
        return self.get_events()

    def is_event_blackout(self, *args, **kwargs) -> bool:
        return False


class OpenMarketHours:
    def get_status(self):
        from core.models import MarketHoursStatus

        return MarketHoursStatus(
            checked_at=BASE_TIME,
            is_market_open=True,
            source="test",
            reason="open",
            next_open_at=None,
            next_close_at=BASE_TIME + timedelta(hours=5),
        )


class ClosedMarketHours:
    def get_status(self):
        return MarketHoursStatus(
            checked_at=BASE_TIME,
            is_market_open=False,
            source="test",
            reason="closed",
            next_open_at=BASE_TIME + timedelta(days=1),
            next_close_at=None,
        )


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


class StubSmcAdapter:
    def analyze(self, instrument: str, timeframe: str, candles: pd.DataFrame):
        return type(
            "SmcResult",
            (),
            {
                "structure": StructureEventSummary(),
                "zones": ActiveZoneSummary(),
                "liquidity": LiquidityPoolSummary(),
                "smc_context": SmcContextSummary(),
                "order_block_candidates": (),
            },
        )()


class StubHTFBiasAnalyzer:
    def compute(self, members):
        return HTFBiasResult(
            direction="BULLISH",
            alignment_score=0.7,
            timeframe_votes={"D": "BULLISH", "H4": "BULLISH", "H1": "BULLISH"},
        )


def build_indicator_summary(candles: pd.DataFrame, timeframe: str) -> IndicatorValueSummary:
    return IndicatorValueSummary()


def build_spread(_price) -> SpreadResult:
    return SpreadResult(
        instrument=_price.instrument,
        raw_spread=0.0002,
        spread_pips=2.0,
        pip_size=0.0001,
        typical_spread_pips=0.3,
        max_spread_pips=3.0,
        is_acceptable=True,
        is_spiking=True,
        spread_ratio=2.0 / 0.3,
    )


def build_chop(_indicators) -> ChopResult:
    return ChopResult(status="PASS", reason="adx_above_threshold")


def build_seed_snapshot(instrument: str, timeframe: str) -> TimeframeSnapshot:
    last_completed_candle = BASE_TIME
    if timeframe == "M15":
        candle_delta = timedelta(minutes=15)
    elif timeframe == "H4":
        candle_delta = timedelta(hours=4)
    elif timeframe == "D":
        candle_delta = timedelta(days=1)
    else:
        candle_delta = timedelta(hours=1)
    return TimeframeSnapshot(
        instrument=instrument,
        timeframe=timeframe,
        last_completed_candle=last_completed_candle,
        computed_at=BASE_TIME + timedelta(minutes=1),
        candle_range_start=last_completed_candle - candle_delta,
        candle_range_end=last_completed_candle,
        indicators=IndicatorValueSummary(),
        structure=StructureEventSummary(),
        zones=ActiveZoneSummary(),
        liquidity=LiquidityPoolSummary(),
        smc_context=SmcContextSummary(),
        spread=SpreadResult(
            instrument=instrument,
            raw_spread=0.0002,
            spread_pips=2.0,
            pip_size=0.0001,
            typical_spread_pips=0.3,
            max_spread_pips=3.0,
            is_acceptable=True,
            is_spiking=False,
            spread_ratio=2.0 / 0.3,
        ),
        chop=ChopResult(status="PASS", reason="seeded"),
        sfp=SFPResult(),
        turtle_soup=TurtleSoupResult(),
        orb=ORBResult() if timeframe == "M15" else None,
        freshness=SnapshotFreshness(
            instrument=instrument,
            timeframe=timeframe,
            last_completed_candle=last_completed_candle,
            fetched_at=BASE_TIME + timedelta(minutes=1),
            source="csv",
            candle_count=30,
            is_fresh=False,
            staleness_seconds=3600.0,
        ),
    )


def test_scan_orchestrator_runs_full_cycle_and_emits_stage11_logs(tmp_path: Path, monkeypatch) -> None:
    settings = build_settings(tmp_path)
    provider = StubMarketDataProvider()
    orchestrator = ScanOrchestrator(
        settings=settings,
        market_data_provider=provider,
        calendar_provider=StubCalendarProvider(),
        market_hours_service=OpenMarketHours(),
        smc_adapter=StubSmcAdapter(),
        htf_bias_analyzer=StubHTFBiasAnalyzer(),
        indicator_builder=build_indicator_summary,
        spread_evaluator=build_spread,
        chop_evaluator=build_chop,
        sfp_detector=lambda candles, timeframe, swing_length=50: SFPResult(),
        turtle_soup_detector=lambda candles, timeframe: TurtleSoupResult(),
        orb_detector=lambda candles, timeframe: ORBResult(),
    )
    monkeypatch.setattr("orchestration.scan_orchestrator.SCAN_INSTRUMENTS", ("EUR_USD",))

    with structlog.testing.capture_logs() as logs:
        status = orchestrator.scan_all()

    assert status.scanned_instruments == ("EUR_USD",)
    assert status.snapshots_published == 4
    assert orchestrator.market_state.get_bundle("EUR_USD") is not None
    assert any(entry["event"] == "detector_executed" for entry in logs)
    assert any(entry["event"] == "scan_cycle_completed" for entry in logs)


def test_refresh_calendar_propagates_cached_status(tmp_path: Path) -> None:
    settings = build_settings(tmp_path)
    provider = StubMarketDataProvider()

    class CachedCalendarProvider(StubCalendarProvider):
        def __init__(self) -> None:
            super().__init__()
            self.last_request_used_cached = True

    orchestrator = ScanOrchestrator(
        settings=settings,
        market_data_provider=provider,
        calendar_provider=CachedCalendarProvider(),
        market_hours_service=OpenMarketHours(),
        smc_adapter=StubSmcAdapter(),
        htf_bias_analyzer=StubHTFBiasAnalyzer(),
        indicator_builder=build_indicator_summary,
        spread_evaluator=build_spread,
        chop_evaluator=build_chop,
        sfp_detector=lambda candles, timeframe, swing_length=50: SFPResult(),
        turtle_soup_detector=lambda candles, timeframe: TurtleSoupResult(),
        orb_detector=lambda candles, timeframe: ORBResult(),
    )

    status = orchestrator.refresh_calendar(force=False)

    assert status.used_cached is True
    assert status.event_count == 1


def test_scan_orchestrator_reports_market_closed_no_cache_when_cache_absent(tmp_path: Path) -> None:
    settings = build_settings(tmp_path)
    provider = StubMarketDataProvider(cached_available=False)

    class RecordingIndicatorAlertEngine:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str]] = []

        def evaluate_for_snapshot(self, instrument, granularity, candles, current_summary):
            self.calls.append((instrument, granularity))
            return []

    engine = RecordingIndicatorAlertEngine()
    orchestrator = ScanOrchestrator(
        settings=settings,
        market_data_provider=provider,
        calendar_provider=StubCalendarProvider(),
        market_hours_service=ClosedMarketHours(),
        smc_adapter=StubSmcAdapter(),
        htf_bias_analyzer=StubHTFBiasAnalyzer(),
        indicator_builder=build_indicator_summary,
        spread_evaluator=build_spread,
        chop_evaluator=build_chop,
        sfp_detector=lambda candles, timeframe, swing_length=50: SFPResult(),
        turtle_soup_detector=lambda candles, timeframe: TurtleSoupResult(),
        orb_detector=lambda candles, timeframe: ORBResult(),
        indicator_alert_engine=engine,
    )

    status = orchestrator.scan_all()

    assert status.skipped_reason == "market_closed_no_cache"
    assert provider.calls == 0
    assert provider.price_calls == 0
    assert provider.cached_calls > 0
    assert engine.calls == []


def test_scan_orchestrator_closed_market_uses_cache_only_and_publishes_stale_state(tmp_path: Path) -> None:
    settings = build_settings(tmp_path)
    provider = StubMarketDataProvider(cached_available=True)

    class RecordingIndicatorAlertEngine:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str]] = []

        def evaluate_for_snapshot(self, instrument, granularity, candles, current_summary):
            self.calls.append((instrument, granularity))
            return []

    engine = RecordingIndicatorAlertEngine()
    orchestrator = ScanOrchestrator(
        settings=settings,
        market_data_provider=provider,
        calendar_provider=StubCalendarProvider(),
        market_hours_service=ClosedMarketHours(),
        smc_adapter=StubSmcAdapter(),
        htf_bias_analyzer=StubHTFBiasAnalyzer(),
        indicator_builder=build_indicator_summary,
        spread_evaluator=build_spread,
        chop_evaluator=build_chop,
        sfp_detector=lambda candles, timeframe, swing_length=50: SFPResult(),
        turtle_soup_detector=lambda candles, timeframe: TurtleSoupResult(),
        orb_detector=lambda candles, timeframe: ORBResult(),
        indicator_alert_engine=engine,
    )
    for timeframe in SCAN_TIMEFRAMES:
        orchestrator.market_state.publish_snapshot(build_seed_snapshot("XAU_USD", timeframe))

    status = orchestrator._run_scan(("XAU_USD",), run_kind="full")
    bundle = orchestrator.market_state.get_bundle("XAU_USD")

    assert status.skipped_reason is None
    assert status.scanned_instruments == ("XAU_USD",)
    assert provider.calls == 0
    assert provider.price_calls == 0
    assert provider.cached_calls == len(SCAN_TIMEFRAMES)
    assert engine.calls == []
    assert bundle is not None
    assert set(bundle.members) == set(SCAN_TIMEFRAMES)


def test_scan_orchestrator_closed_market_bootstraps_from_cache_without_seed_snapshot(
    tmp_path: Path,
) -> None:
    settings = build_settings(tmp_path)
    provider = StubMarketDataProvider(cached_available=True)

    orchestrator = ScanOrchestrator(
        settings=settings,
        market_data_provider=provider,
        calendar_provider=StubCalendarProvider(),
        market_hours_service=ClosedMarketHours(),
        smc_adapter=StubSmcAdapter(),
        htf_bias_analyzer=StubHTFBiasAnalyzer(),
        indicator_builder=build_indicator_summary,
        spread_evaluator=build_spread,
        chop_evaluator=build_chop,
        sfp_detector=lambda candles, timeframe, swing_length=50: SFPResult(),
        turtle_soup_detector=lambda candles, timeframe: TurtleSoupResult(),
        orb_detector=lambda candles, timeframe: ORBResult(),
    )

    status = orchestrator._run_scan(("XAU_USD",), run_kind="full")
    bundle = orchestrator.market_state.get_bundle("XAU_USD")

    assert status.skipped_reason is None
    assert status.scanned_instruments == ("XAU_USD",)
    assert provider.calls == 0
    assert provider.price_calls == 0
    assert provider.cached_calls == len(SCAN_TIMEFRAMES)
    assert bundle is not None
    assert set(bundle.members) == set(SCAN_TIMEFRAMES)
    for timeframe in SCAN_TIMEFRAMES:
        snapshot = orchestrator.market_state.get_snapshot("XAU_USD", timeframe)
        assert snapshot is not None
        assert snapshot.spread.instrument == "XAU_USD"


def test_scan_orchestrator_uses_instrument_category_market_hours(tmp_path: Path, monkeypatch) -> None:
    settings = build_settings(tmp_path)
    provider = StubMarketDataProvider(cached_available=True)
    orchestrator = ScanOrchestrator(
        settings=settings,
        market_data_provider=provider,
        calendar_provider=StubCalendarProvider(),
        market_hours_service=MixedMarketHours(),
        smc_adapter=StubSmcAdapter(),
        htf_bias_analyzer=StubHTFBiasAnalyzer(),
        indicator_builder=build_indicator_summary,
        spread_evaluator=build_spread,
        chop_evaluator=build_chop,
        sfp_detector=lambda candles, timeframe, swing_length=50: SFPResult(),
        turtle_soup_detector=lambda candles, timeframe: TurtleSoupResult(),
        orb_detector=lambda candles, timeframe: ORBResult(),
    )
    monkeypatch.setattr("orchestration.scan_orchestrator.SCAN_INSTRUMENTS", ("EUR_USD", "XAU_USD"))
    for timeframe in SCAN_TIMEFRAMES:
        orchestrator.market_state.publish_snapshot(build_seed_snapshot("XAU_USD", timeframe))

    status = orchestrator.scan_all()
    eur_bundle = orchestrator.market_state.get_bundle("EUR_USD")
    xau_bundle = orchestrator.market_state.get_bundle("XAU_USD")

    assert status.scanned_instruments == ("EUR_USD", "XAU_USD")
    assert provider.price_calls == 1
    assert provider.calls == len(SCAN_TIMEFRAMES)
    assert provider.cached_calls == len(SCAN_TIMEFRAMES)
    assert eur_bundle is not None
    assert xau_bundle is not None


def test_refresh_snapshot_raises_when_freshness_provenance_is_missing(tmp_path: Path) -> None:
    settings = build_settings(tmp_path)

    class BrokenFreshnessProvider(StubMarketDataProvider):
        def get_candle_freshness(self, instrument: str, timeframe: str) -> CandleFreshness:
            return CandleFreshness(
                instrument=instrument,
                timeframe=timeframe,
                last_completed_candle=None,
                fetched_at=None,
                source=None,
                candle_count=0,
                is_fresh=False,
                staleness_seconds=None,
            )

    orchestrator = ScanOrchestrator(
        settings=settings,
        market_data_provider=BrokenFreshnessProvider(),
        calendar_provider=StubCalendarProvider(),
        market_hours_service=OpenMarketHours(),
        smc_adapter=StubSmcAdapter(),
        htf_bias_analyzer=StubHTFBiasAnalyzer(),
        indicator_builder=build_indicator_summary,
        spread_evaluator=build_spread,
        chop_evaluator=build_chop,
        sfp_detector=lambda candles, timeframe, swing_length=50: SFPResult(),
        turtle_soup_detector=lambda candles, timeframe: TurtleSoupResult(),
        orb_detector=lambda candles, timeframe: ORBResult(),
    )

    with pytest.raises(RuntimeError, match="cannot infer provenance"):
        orchestrator.refresh_snapshot("XAU_USD", "M15")


# ---------------------------------------------------------------------------
# indicator_alert_engine integration
# ---------------------------------------------------------------------------

class StubIndicatorAlertEngine:
    """Records calls to evaluate_for_snapshot for assertion."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.raise_on_next: bool = False

    def evaluate_for_snapshot(self, instrument, granularity, candles, current_summary):
        if self.raise_on_next:
            raise RuntimeError("stub engine failure")
        self.calls.append((instrument, granularity))
        return []


def _make_full_orchestrator(tmp_path: Path, indicator_alert_engine=None) -> ScanOrchestrator:
    """Build a ScanOrchestrator with all-stub detectors, matching existing test pattern."""
    settings = build_settings(tmp_path)
    return ScanOrchestrator(
        settings=settings,
        market_data_provider=StubMarketDataProvider(),
        calendar_provider=StubCalendarProvider(),
        market_hours_service=OpenMarketHours(),
        smc_adapter=StubSmcAdapter(),
        htf_bias_analyzer=StubHTFBiasAnalyzer(),
        indicator_builder=build_indicator_summary,
        spread_evaluator=build_spread,
        chop_evaluator=build_chop,
        sfp_detector=lambda candles, timeframe, swing_length=50: SFPResult(),
        turtle_soup_detector=lambda candles, timeframe: TurtleSoupResult(),
        orb_detector=lambda candles, timeframe: ORBResult(),
        indicator_alert_engine=indicator_alert_engine,
    )


def test_scan_orchestrator_calls_engine_for_each_snapshot(tmp_path: Path) -> None:
    stub_engine = StubIndicatorAlertEngine()
    orchestrator = _make_full_orchestrator(tmp_path, indicator_alert_engine=stub_engine)

    orchestrator.refresh_snapshot("XAU_USD", "M15")

    assert ("XAU_USD", "M15") in stub_engine.calls


def test_scan_orchestrator_skips_engine_when_none(tmp_path: Path) -> None:
    orchestrator = _make_full_orchestrator(tmp_path, indicator_alert_engine=None)
    # Should not raise — no engine to call
    orchestrator.refresh_snapshot("XAU_USD", "M15")


def test_scan_orchestrator_engine_exception_does_not_abort_snapshot(
    tmp_path: Path,
) -> None:
    stub_engine = StubIndicatorAlertEngine()
    stub_engine.raise_on_next = True
    orchestrator = _make_full_orchestrator(tmp_path, indicator_alert_engine=stub_engine)

    with structlog.testing.capture_logs() as logs:
        snapshot = orchestrator.refresh_snapshot("XAU_USD", "M15")

    assert snapshot is not None
    warning_events = [l for l in logs if l.get("log_level") == "warning"]
    assert any(l.get("event") == "indicator_alert_eval_failed" for l in warning_events)


def test_scan_orchestrator_force_bypasses_closed_market_gate(tmp_path: Path) -> None:
    """force=True + market closed + no cache → get_candles called, bundle published."""
    settings = build_settings(tmp_path)
    provider = StubMarketDataProvider(cached_available=False)

    orchestrator = ScanOrchestrator(
        settings=settings,
        market_data_provider=provider,
        calendar_provider=StubCalendarProvider(),
        market_hours_service=ClosedMarketHours(),
        smc_adapter=StubSmcAdapter(),
        htf_bias_analyzer=StubHTFBiasAnalyzer(),
        indicator_builder=build_indicator_summary,
        spread_evaluator=build_spread,
        chop_evaluator=build_chop,
        sfp_detector=lambda candles, timeframe, swing_length=50: SFPResult(),
        turtle_soup_detector=lambda candles, timeframe: TurtleSoupResult(),
        orb_detector=lambda candles, timeframe: ORBResult(),
    )

    status = orchestrator._run_scan(("XAU_USD",), run_kind="full", force=True)

    assert status.skipped_reason is None
    assert status.scanned_instruments == ("XAU_USD",)
    assert provider.calls > 0          # get_candles was called (live fetch)
    assert provider.cached_calls == 0  # get_cached_candles was NOT called
    assert provider.price_calls == 0   # no current price when market closed
    assert orchestrator.market_state.get_bundle("XAU_USD") is not None


def test_scan_orchestrator_force_is_noop_when_market_open(tmp_path: Path) -> None:
    """force=True with open market produces same result as force=False."""
    settings = build_settings(tmp_path)

    def make_orchestrator():
        return ScanOrchestrator(
            settings=settings,
            market_data_provider=StubMarketDataProvider(),
            calendar_provider=StubCalendarProvider(),
            market_hours_service=OpenMarketHours(),
            smc_adapter=StubSmcAdapter(),
            htf_bias_analyzer=StubHTFBiasAnalyzer(),
            indicator_builder=build_indicator_summary,
            spread_evaluator=build_spread,
            chop_evaluator=build_chop,
            sfp_detector=lambda candles, timeframe, swing_length=50: SFPResult(),
            turtle_soup_detector=lambda candles, timeframe: TurtleSoupResult(),
            orb_detector=lambda candles, timeframe: ORBResult(),
        )

    status_normal = make_orchestrator()._run_scan(("XAU_USD",), run_kind="full")
    status_forced = make_orchestrator()._run_scan(("XAU_USD",), run_kind="full", force=True)

    assert status_normal.scanned_instruments == status_forced.scanned_instruments
    assert status_normal.bundles_published == status_forced.bundles_published
    assert status_normal.skipped_reason == status_forced.skipped_reason
