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
    IndicatorValueSummary,
    LiquidityPoolSummary,
    MarketHoursOverview,
    MarketHoursStatus,
    SmcContextSummary,
    StructureEventSummary,
)
from orchestration.scan_orchestrator import SCAN_TIMEFRAMES, ScanOrchestrator
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
        self.last_request_used_cached = False

    def get_events(self, *, force: bool = False):
        self.last_request_used_cached = not force
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


class StubMacroService:
    def get_status(self, *, force: bool = False):
        from core.models import MacroContextStatus

        return MacroContextStatus(last_attempted_at=BASE_TIME, used_cached=not force)


class OpenMarketHours:
    def get_status(self):
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


class StubIndicatorAlertEngine:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.raise_on_next = False

    def evaluate_for_snapshot(self, instrument, granularity, candles, current_summary):
        if self.raise_on_next:
            raise RuntimeError("stub engine failure")
        self.calls.append((instrument, granularity))
        return []


def build_indicator_summary(candles: pd.DataFrame, timeframe: str) -> IndicatorValueSummary:
    return IndicatorValueSummary()


def make_orchestrator(
    tmp_path: Path,
    *,
    provider: StubMarketDataProvider | None = None,
    market_hours_service=None,
    indicator_alert_engine: StubIndicatorAlertEngine | None = None,
) -> ScanOrchestrator:
    return ScanOrchestrator(
        settings=build_settings(tmp_path),
        market_data_provider=provider or StubMarketDataProvider(),
        calendar_provider=StubCalendarProvider(),
        market_hours_service=market_hours_service or OpenMarketHours(),
        macro_context_service=StubMacroService(),
        smc_adapter=StubSmcAdapter(),
        indicator_builder=build_indicator_summary,
        indicator_alert_engine=indicator_alert_engine,
    )


def test_scan_orchestrator_runs_full_cycle_and_publishes_snapshots(tmp_path: Path, monkeypatch) -> None:
    provider = StubMarketDataProvider()
    orchestrator = make_orchestrator(tmp_path, provider=provider)
    monkeypatch.setattr("orchestration.scan_orchestrator.SCAN_INSTRUMENTS", ("EUR_USD",))

    with structlog.testing.capture_logs() as logs:
        status = orchestrator.scan_all()

    assert status.scanned_instruments == ("EUR_USD",)
    assert status.snapshots_published == 4
    assert not hasattr(status, "bundles_" + "published")
    assert not hasattr(orchestrator.market_state, "get_bundle")
    assert set(orchestrator.refresh_instrument("EUR_USD") or {}) == set(SCAN_TIMEFRAMES)
    assert all(orchestrator.market_state.get_snapshot("EUR_USD", tf) is not None for tf in SCAN_TIMEFRAMES)
    assert provider.price_calls >= 2
    assert any(entry["event"] == "detector_executed" for entry in logs)
    assert any(entry["event"] == "scan_cycle_completed" for entry in logs)


def test_scan_orchestrator_reports_market_closed_no_cache_when_cache_absent(tmp_path: Path) -> None:
    provider = StubMarketDataProvider(cached_available=False)
    engine = StubIndicatorAlertEngine()
    orchestrator = make_orchestrator(
        tmp_path,
        provider=provider,
        market_hours_service=ClosedMarketHours(),
        indicator_alert_engine=engine,
    )

    status = orchestrator.scan_all()

    assert status.skipped_reason == "market_closed_no_cache"
    assert status.scanned_instruments == ()
    assert provider.calls == 0
    assert provider.price_calls == 0
    assert provider.cached_calls > 0
    assert engine.calls == []


def test_scan_orchestrator_closed_market_uses_cache_only_and_publishes_snapshots(tmp_path: Path) -> None:
    provider = StubMarketDataProvider(cached_available=True)
    engine = StubIndicatorAlertEngine()
    orchestrator = make_orchestrator(
        tmp_path,
        provider=provider,
        market_hours_service=ClosedMarketHours(),
        indicator_alert_engine=engine,
    )

    status = orchestrator._run_scan(("XAU_USD",), run_kind="full")

    assert status.skipped_reason is None
    assert status.scanned_instruments == ("XAU_USD",)
    assert status.snapshots_published == 4
    assert provider.calls == 0
    assert provider.price_calls == 0
    assert provider.cached_calls == len(SCAN_TIMEFRAMES)
    assert engine.calls == []
    assert all(orchestrator.market_state.get_snapshot("XAU_USD", tf) is not None for tf in SCAN_TIMEFRAMES)


def test_scan_orchestrator_force_bypasses_closed_market_cache_gate(tmp_path: Path) -> None:
    provider = StubMarketDataProvider(cached_available=False)
    orchestrator = make_orchestrator(
        tmp_path,
        provider=provider,
        market_hours_service=ClosedMarketHours(),
    )

    status = orchestrator._run_scan(("XAU_USD",), run_kind="full", force=True)

    assert status.skipped_reason is None
    assert status.scanned_instruments == ("XAU_USD",)
    assert status.forced_market_fetch is True
    assert provider.calls == len(SCAN_TIMEFRAMES)
    assert provider.cached_calls == 0
    assert provider.price_calls == 0


def test_scan_orchestrator_uses_instrument_category_market_hours(tmp_path: Path, monkeypatch) -> None:
    provider = StubMarketDataProvider(cached_available=True)
    orchestrator = make_orchestrator(
        tmp_path,
        provider=provider,
        market_hours_service=MixedMarketHours(),
    )
    monkeypatch.setattr("orchestration.scan_orchestrator.SCAN_INSTRUMENTS", ("EUR_USD", "XAU_USD"))

    status = orchestrator.scan_all()

    assert status.scanned_instruments == ("EUR_USD", "XAU_USD")
    assert provider.price_calls == 1
    assert provider.calls == len(SCAN_TIMEFRAMES)
    assert provider.cached_calls == len(SCAN_TIMEFRAMES)


def test_refresh_snapshot_raises_when_freshness_provenance_is_missing(tmp_path: Path) -> None:
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

    orchestrator = make_orchestrator(tmp_path, provider=BrokenFreshnessProvider())

    with pytest.raises(RuntimeError, match="cannot infer provenance"):
        orchestrator.refresh_snapshot("XAU_USD", "M15")


def test_refresh_snapshot_calls_indicator_engine_for_fresh_open_market_snapshot(tmp_path: Path) -> None:
    engine = StubIndicatorAlertEngine()
    orchestrator = make_orchestrator(tmp_path, indicator_alert_engine=engine)

    snapshot = orchestrator.refresh_snapshot("XAU_USD", "M15")

    assert snapshot is not None
    assert engine.calls == [("XAU_USD", "M15")]


def test_scan_orchestrator_engine_exception_does_not_abort_snapshot(tmp_path: Path) -> None:
    engine = StubIndicatorAlertEngine()
    engine.raise_on_next = True
    orchestrator = make_orchestrator(tmp_path, indicator_alert_engine=engine)

    with structlog.testing.capture_logs() as logs:
        snapshot = orchestrator.refresh_snapshot("XAU_USD", "M15")

    assert snapshot is not None
    warning_events = [entry for entry in logs if entry.get("log_level") == "warning"]
    assert any(entry.get("event") == "indicator_alert_eval_failed" for entry in warning_events)
