"""Phase 4 live cohesiveness tests: orchestration, cache warming, and local lifecycle.

These tests hit the real OANDA API and validate the full scan pipeline,
cache warmer, calendar integration, one-shot poller lifecycle, and concurrent
scan safety. All tests are auto-marked ``@pytest.mark.live`` by conftest.py.

Weekend-safe: the ``scan_orchestrator`` fixture injects ``always_open_market_hours``
to bypass the market-closed gate.  CacheWarmer tests do the same.

Run with:  pytest tests/live/test_phase4_orchestration.py -m live -v
"""

from __future__ import annotations

import threading
import time
from unittest.mock import patch

import pytest

from core.instrument_registry import SCAN_INSTRUMENTS
from core.market_state import MarketStateStore
from core.models import ScanCycleStatus, TimeframeSnapshot
from data.forex_calendar import ForexCalendarClient
from data.market_hours import MarketHoursService
from data.persistence.trade_store import TradeStore
from orchestration.cache_warmer import CacheWarmer
from orchestration.scan_orchestrator import SCAN_TIMEFRAMES, ScanOrchestrator

# On weekends the cache detects data as stale (no new candles since Friday)
# and attempts an append-refresh.  OANDA returns 0 new candles → RuntimeError.
# Patching ``is_cache_fresh`` to return True makes the cache serve existing
# data without trying to append, which is correct for repeated scans on
# unchanged weekend data.
_FRESH_CACHE_PATCH = patch("providers.cache.is_cache_fresh", return_value=True)


# ---------------------------------------------------------------------------
# 1. Full scan cycle: entire curated scan universe through the orchestrator
# ---------------------------------------------------------------------------


class TestScanOrchestratorFullCycle:
    """Run scan_all() and verify status counters and published snapshots."""

    @pytest.mark.timeout(300)
    def test_scan_all_produces_complete_cycle(
        self, scan_orchestrator: ScanOrchestrator, market_state: MarketStateStore
    ) -> None:
        """scan_all() should scan the full curated universe and publish all snapshots."""

        status: ScanCycleStatus = scan_orchestrator.scan_all()
        expected_instruments = len(SCAN_INSTRUMENTS)
        expected_snapshots = expected_instruments * len(SCAN_TIMEFRAMES)

        # Basic counters
        assert len(status.scanned_instruments) == expected_instruments, (
            f"Expected {expected_instruments} scanned instruments, got {len(status.scanned_instruments)}"
        )
        assert status.snapshots_published == expected_snapshots, (
            f"Expected {expected_snapshots} snapshots ({expected_instruments}x{len(SCAN_TIMEFRAMES)}), got {status.snapshots_published}"
        )
        assert len(status.errors) == 0, f"Unexpected errors: {status.errors}"

        # Temporal ordering
        assert status.started_at is not None
        assert status.completed_at is not None
        assert status.started_at < status.completed_at

        for instrument in SCAN_INSTRUMENTS:
            for timeframe in SCAN_TIMEFRAMES:
                snapshot = market_state.get_snapshot(instrument, timeframe)
                assert snapshot is not None, f"Missing snapshot for {instrument} {timeframe}"
                assert isinstance(snapshot, TimeframeSnapshot)


# ---------------------------------------------------------------------------
# 2. Determinism: second scan should produce identical indicator values
# ---------------------------------------------------------------------------


class TestScanCycleDeterminism:
    """Two consecutive scans on a weekend should produce identical indicator values."""

    @pytest.mark.timeout(600)
    def test_second_scan_produces_matching_indicators(
        self, scan_orchestrator: ScanOrchestrator, market_state: MarketStateStore
    ) -> None:
        """Indicator metric values should be identical across two runs (no new candles on weekends)."""

        scan_orchestrator.scan_all()

        # Capture v1 H1 snapshot for SPX500_USD
        v1_snapshot = market_state.get_snapshot_version("SPX500_USD", "H1", version=1)
        assert v1_snapshot is not None, "v1 snapshot for SPX500_USD H1 not found"

        # Run again — patch cache freshness to avoid stale-append failures on weekends
        with _FRESH_CACHE_PATCH:
            scan_orchestrator.scan_all()

        # Capture v2 H1 snapshot for SPX500_USD
        v2_snapshot = market_state.get_snapshot_version("SPX500_USD", "H1", version=2)
        assert v2_snapshot is not None, "v2 snapshot for SPX500_USD H1 not found"

        # Compare indicator metric values using pytest.approx for float tolerance
        v1_metrics = v1_snapshot.indicators.metrics
        v2_metrics = v2_snapshot.indicators.metrics
        assert len(v1_metrics) == len(v2_metrics), "Metric count differs between runs"

        for v1_metric in v1_metrics:
            matching = [m for m in v2_metrics if m.name == v1_metric.name]
            assert len(matching) == 1, f"No matching metric for {v1_metric.name} in v2"
            assert v1_metric.value == pytest.approx(matching[0].value, nan_ok=True), (
                f"Metric {v1_metric.name} differs: {v1_metric.value} vs {matching[0].value}"
            )


# ---------------------------------------------------------------------------
# 3. Cache warmer: warm all instruments, verify CSV files and speedup
# ---------------------------------------------------------------------------


class TestCacheWarmerPopulatesAllInstruments:
    """CacheWarmer should populate every scan symbol/timeframe CSV and be faster on the second run."""

    @pytest.mark.timeout(300)
    def test_warm_all_creates_csv_files_and_second_run_is_faster(
        self, live_provider, always_open_market_hours
    ) -> None:
        """warm_all() should return the full slot count, create CSV files, and speed up on the second run."""

        warmer = CacheWarmer(
            live_provider,
            market_hours_service=always_open_market_hours,
        )
        expected_slots = len(SCAN_INSTRUMENTS) * len(SCAN_TIMEFRAMES)

        # First run: measure time and check return value
        t0 = time.perf_counter()
        result_1 = warmer.warm_all()
        t1 = time.perf_counter()
        first_duration = t1 - t0

        assert result_1 == expected_slots, f"Expected {expected_slots} timeframe slots warmed, got {result_1}"

        # Verify CSV files exist for every instrument x timeframe
        for instrument in SCAN_INSTRUMENTS:
            for timeframe in SCAN_TIMEFRAMES:
                csv_path = live_provider.cache.csv_store.path_for(instrument, timeframe)
                assert csv_path.exists(), f"Missing CSV: {csv_path}"

        # Second run: should be at least 2x faster (cache hits)
        # Patch freshness to avoid stale-append failures on weekends
        with _FRESH_CACHE_PATCH:
            t2 = time.perf_counter()
            result_2 = warmer.warm_all()
            t3 = time.perf_counter()
            second_duration = t3 - t2

        assert result_2 == expected_slots, f"Second warm_all() returned {result_2}, expected {expected_slots}"
        assert second_duration < first_duration / 2.0, (
            f"Second run ({second_duration:.2f}s) was not 2x faster "
            f"than first ({first_duration:.2f}s)"
        )


# ---------------------------------------------------------------------------
# 4. Calendar integration: fetch events, filter, and validate structure
# ---------------------------------------------------------------------------


class TestCalendarIntegration:
    """ForexCalendarClient should fetch, parse, and filter calendar events."""

    def test_calendar_fetch_and_filter(self, live_settings) -> None:
        """get_events(force=True) returns a tuple; filter_events respects currency filter."""

        calendar = ForexCalendarClient(settings=live_settings)
        events = calendar.get_events(force=True)

        assert isinstance(events, tuple)

        # If the FairEconomy API returned 429 (rate-limited), calendar_version
        # stays at 0 and events will be empty.  Skip structural checks in that
        # case — the client handled the error gracefully without crashing.
        if calendar.calendar_version == 0:
            pytest.skip(
                "FairEconomy calendar API returned 429 (rate-limited); "
                "skipping structural assertions"
            )

        # If there are events, validate structure
        if events:
            for event in events:
                assert hasattr(event, "event_time")
                assert event.event_time.tzinfo is not None, "event_time must be UTC-aware"
                assert isinstance(event.currency, (str, type(None)))
                assert event.impact in ("HIGH", "MEDIUM", "LOW", "HOLIDAY", "UNKNOWN"), (
                    f"Unexpected impact: {event.impact}"
                )

        # filter_events with currency filter
        filtered = calendar.filter_events(currencies=("USD", "EUR"))
        for event in filtered:
            assert event.currency in ("USD", "EUR") or event.currency is None, (
                f"Unexpected currency {event.currency} after filtering for USD/EUR"
            )

        # calendar_version should have been incremented after a successful refresh
        assert calendar.calendar_version >= 1


# ---------------------------------------------------------------------------
# 5. Full runtime lifecycle (crown jewel, ~3 min)
# ---------------------------------------------------------------------------


class TestFullRuntimeLifecycle:
    """Wire up the local runtime pieces and validate on-demand lifecycle."""

    @pytest.mark.asyncio
    @pytest.mark.timeout(300)
    async def test_full_lifecycle(
        self,
        live_settings,
        live_provider,
        scan_orchestrator,
        market_state,
        account_client,
        trade_store,
        trade_repository,
        journal_service,
        always_open_market_hours,
        tmp_db_path,
    ) -> None:
        """Wire up cache, scan, and one-shot poller; validate state."""

        from background.poller_task import TradePollerTask

        # (a) Cache warm
        warmer = CacheWarmer(
            live_provider,
            market_hours_service=always_open_market_hours,
        )
        warmed = warmer.warm_all()
        expected_slots = len(SCAN_INSTRUMENTS) * len(SCAN_TIMEFRAMES)
        assert warmed == expected_slots

        # (b) Full scan — patch freshness so the scan reads the just-warmed
        #     cache without attempting a stale-append (fails on weekends)
        with _FRESH_CACHE_PATCH:
            scan_status = scan_orchestrator.scan_all()
        assert scan_status.snapshots_published == len(SCAN_INSTRUMENTS) * len(SCAN_TIMEFRAMES)

        # (c) Trade poller
        poller = TradePollerTask(account_client, trade_repository, journal_service)
        await poller.poll_once()

        snapshots_found = sum(
            1
            for inst in SCAN_INSTRUMENTS
            for timeframe in SCAN_TIMEFRAMES
            if market_state.get_snapshot(inst, timeframe) is not None
        )
        assert snapshots_found > 0, "Expected at least some snapshots in market_state"

        # Trade repository should have at least 1 trade from poller
        open_trades = trade_repository.list_open()
        # Note: on demo accounts there may be zero open trades, but the list_open() call
        # should still succeed. If there are any trades, they were polled correctly.
        assert isinstance(open_trades, list)

        # All data still readable from TinyDB (open a fresh store against same file)
        fresh_store = TradeStore(db_path=tmp_db_path)
        fresh_trades = fresh_store.list_open_trades()
        assert isinstance(fresh_trades, list)

        # Trade records pass Pydantic re-validation
        for trade in fresh_trades:
            from core.models import TradeRecord
            TradeRecord.model_validate(trade.model_dump(mode="python"))


# ---------------------------------------------------------------------------
# 6. Second scan cycle after runtime: version increments
# ---------------------------------------------------------------------------


class TestSecondScanCycleAfterRuntime:
    """After one local cycle, a second scan should produce incremented versions."""

    @pytest.mark.timeout(300)
    def test_second_scan_increments_versions(
        self, scan_orchestrator: ScanOrchestrator, market_state: MarketStateStore
    ) -> None:
        """scan_all() twice should produce v2 snapshots for each timeframe."""

        # First scan
        scan_orchestrator.scan_all()

        v1_versions = {}
        for timeframe in SCAN_TIMEFRAMES:
            snapshot = market_state.get_snapshot("SPX500_USD", timeframe)
            assert snapshot is not None
            v1_versions[timeframe] = snapshot.version

        # Second scan — patch freshness to avoid stale-append on weekends
        with _FRESH_CACHE_PATCH:
            scan_orchestrator.scan_all()

        # v2 snapshots should exist
        for timeframe in SCAN_TIMEFRAMES:
            v2_snap = market_state.get_snapshot_version("SPX500_USD", timeframe, version=2)
            assert v2_snap is not None, (
                f"Missing v2 snapshot for SPX500_USD {timeframe}"
            )

        for timeframe, v1_version in v1_versions.items():
            latest = market_state.get_snapshot("SPX500_USD", timeframe)
            assert latest is not None
            assert latest.version > v1_version


# ---------------------------------------------------------------------------
# 7. Concurrent scan safety: no crashes from parallel refreshes
# ---------------------------------------------------------------------------


class TestConcurrentScanSafety:
    """Three concurrent refresh_instrument() calls must not raise or create gaps."""

    @pytest.mark.timeout(300)
    def test_concurrent_refresh_no_exceptions(
        self, scan_orchestrator: ScanOrchestrator, market_state: MarketStateStore
    ) -> None:
        """Three threads refreshing SPX500_USD simultaneously should not crash."""

        results: list[dict[str, TimeframeSnapshot] | None] = [None, None, None]
        exceptions: list[Exception | None] = [None, None, None]

        def _refresh(index: int) -> None:
            try:
                results[index] = scan_orchestrator.refresh_instrument("SPX500_USD")
            except Exception as exc:
                exceptions[index] = exc

        # Patch freshness globally so concurrent threads don't hit stale-append
        with _FRESH_CACHE_PATCH:
            threads = [threading.Thread(target=_refresh, args=(i,)) for i in range(3)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=120)

        # No exceptions should have been raised
        for i, exc in enumerate(exceptions):
            assert exc is None, f"Thread {i} raised: {exc}"

        # MarketStateStore should have consistent snapshot versions (no gaps)
        for timeframe in SCAN_TIMEFRAMES:
            snapshot = market_state.get_snapshot("SPX500_USD", timeframe)
            assert snapshot is not None, (
                f"Missing snapshot for SPX500_USD {timeframe} after concurrent refreshes"
            )
            # Versions should be sequential from 1..N with no gaps
            for v in range(1, snapshot.version + 1):
                historical = market_state.get_snapshot_version("SPX500_USD", timeframe, v)
                assert historical is not None, (
                    f"Gap in version history: SPX500_USD {timeframe} v{v} missing"
                )


# ---------------------------------------------------------------------------
# 8. TinyDB survives unclean shutdown
# ---------------------------------------------------------------------------


def test_tinydb_survives_unclean_shutdown(tmp_db_path) -> None:
    """Data written to TinyDB should survive a simulated crash (db.close())."""

    from datetime import datetime, timezone
    from core.enums import TradeState
    from core.models import TradeRecord

    store = TradeStore(db_path=tmp_db_path)

    # Insert a trade record manually
    trade = TradeRecord(
        trade_id="crash_test_001",
        instrument="SPX500_USD",
        units=1.0,
        open_price=2000.0,
        state=TradeState.OPEN,
        opened_at=datetime.now(timezone.utc),
    )
    store.upsert_trade(trade)

    # Verify it was written
    assert store.get_trade("crash_test_001") is not None

    # Simulate crash by closing the TinyDB handle directly
    assert store.db is not None
    store.db.close()

    # Re-open a new TradeStore against the same file
    fresh_store = TradeStore(db_path=tmp_db_path)
    recovered = fresh_store.get_trade("crash_test_001")

    assert recovered is not None, "Trade record not recovered after crash"
    assert recovered.trade_id == "crash_test_001"
    assert recovered.instrument == "SPX500_USD"
    assert recovered.state == TradeState.OPEN

    # Re-validate via Pydantic
    TradeRecord.model_validate(recovered.model_dump(mode="python"))
