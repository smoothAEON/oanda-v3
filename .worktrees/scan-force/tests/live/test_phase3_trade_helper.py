"""Phase 3 live cohesiveness tests: trade-helper runtime.

Tests the account client, poller, journal, stream, excursion tracking, and
price alerts against the real OANDA API.  All tests are auto-marked
``@pytest.mark.live`` by conftest.py.

Weekend-safe: excursion tracking uses a near-zero pip threshold so frozen
prices still produce samples; price alerts target the current bid so even
identical ticks cross the threshold.

Run with:  pytest tests/live/test_phase3_trade_helper.py -m live -v
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from core.enums import AlertStatus, TradeState
from core.events import Heartbeat, PriceTick, TradeOpenedEvent
from core.instrument_registry import SCAN_INSTRUMENTS, get_instrument_spec
from core.models import ExcursionSample, PriceAlert, TradeRecord
from providers.base import PriceSnapshot


# ---------------------------------------------------------------------------
# 1. Account client round-trip
# ---------------------------------------------------------------------------


class TestAccountClientLiveRoundtrip:
    """Validate that the account client returns well-formed trade and pricing data."""

    def test_open_trades_returns_nonempty_list(self, account_client) -> None:
        trades = asyncio.run(account_client.get_open_trades())
        assert isinstance(trades, list)
        assert len(trades) > 0, "Expected at least one open trade in the live account"

    def test_first_trade_has_required_fields(self, account_client) -> None:
        trades = asyncio.run(account_client.get_open_trades())
        trade = trades[0]

        assert isinstance(trade["id"], str)
        assert trade["instrument"] in SCAN_INSTRUMENTS
        assert isinstance(trade["currentUnits"], float)
        assert trade["currentUnits"] != 0
        assert isinstance(trade["price"], float)
        assert trade["price"] > 0
        assert isinstance(trade["openTime"], datetime)

    def test_get_trade_detail_matches(self, account_client) -> None:
        trades = asyncio.run(account_client.get_open_trades())
        first = trades[0]
        trade_id = first["id"]
        instrument = first["instrument"]

        detail = asyncio.run(account_client.get_trade_detail(trade_id))
        assert detail["id"] == trade_id
        assert detail["instrument"] == instrument

    def test_get_pricing_returns_valid_snapshot(self, account_client) -> None:
        trades = asyncio.run(account_client.get_open_trades())
        instrument = trades[0]["instrument"]

        snap = asyncio.run(account_client.get_pricing(instrument))
        assert isinstance(snap, PriceSnapshot)
        assert snap.bid > 0
        assert snap.ask >= snap.bid

    def test_trade_price_near_current_mid(self, account_client) -> None:
        """The trade's open price should be within a plausible range of the current mid."""
        trades = asyncio.run(account_client.get_open_trades())
        first = trades[0]
        instrument = first["instrument"]
        open_price = first["price"]

        snap = asyncio.run(account_client.get_pricing(instrument))
        current_mid = (snap.bid + snap.ask) / 2.0

        spec = get_instrument_spec(instrument)
        # Allow up to 5000 pips difference (generous for any instrument/duration)
        max_distance = 5000.0 * spec.pip_size
        distance = abs(open_price - current_mid)
        assert distance < max_distance, (
            f"open_price={open_price} vs mid={current_mid}, "
            f"distance={distance / spec.pip_size:.1f} pips exceeds 5000-pip limit"
        )


# ---------------------------------------------------------------------------
# 2. Poller detects open trade
# ---------------------------------------------------------------------------


class TestPollerDetectsOpenTrade:
    """TradePollerTask must detect at least one open trade and emit a TradeOpenedEvent."""

    def test_first_poll_emits_open_event(
        self, account_client, trade_repository, journal_service, live_settings
    ) -> None:
        from background.poller_task import TradePollerTask

        poller = TradePollerTask(
            account_client,
            trade_repository,
            journal_service,
            settings=live_settings,
        )

        events = asyncio.run(poller.poll_once())
        open_events = [e for e in events if isinstance(e, TradeOpenedEvent)]
        assert len(open_events) >= 1, "Expected at least one TradeOpenedEvent on first poll"

        event = open_events[0]
        assert isinstance(event.trade_id, str) and event.trade_id.strip()
        assert event.instrument in SCAN_INSTRUMENTS
        assert event.units != 0
        assert event.open_price > 0

        open_trades = trade_repository.list_open()
        ids = {t.trade_id for t in open_trades}
        assert event.trade_id in ids

        matching = [t for t in open_trades if t.trade_id == event.trade_id]
        assert matching[0].state == TradeState.OPEN

    def test_second_poll_emits_no_new_opens(
        self, account_client, trade_repository, journal_service, live_settings
    ) -> None:
        from background.poller_task import TradePollerTask

        poller = TradePollerTask(
            account_client,
            trade_repository,
            journal_service,
            settings=live_settings,
        )

        # First poll — seeds the journal
        asyncio.run(poller.poll_once())

        # Second poll — no new trades expected
        events = asyncio.run(poller.poll_once())
        open_events = [e for e in events if isinstance(e, TradeOpenedEvent)]
        assert len(open_events) == 0, (
            "Second poll should emit zero TradeOpenedEvent (trade already known)"
        )


# ---------------------------------------------------------------------------
# 3. Poller to journal round-trip
# ---------------------------------------------------------------------------


class TestPollerToJournalRoundtrip:
    """After the poller creates a trade, it must be readable with all fields populated."""

    def test_journal_trade_record_fully_populated(
        self, account_client, trade_repository, journal_service, live_settings
    ) -> None:
        from background.poller_task import TradePollerTask

        poller = TradePollerTask(
            account_client,
            trade_repository,
            journal_service,
            settings=live_settings,
        )

        events = asyncio.run(poller.poll_once())
        open_events = [e for e in events if isinstance(e, TradeOpenedEvent)]
        assert len(open_events) >= 1

        trade_id = open_events[0].trade_id
        record = trade_repository.get(trade_id)
        assert record is not None, f"Trade {trade_id} not found in repository"

        assert record.trade_id == trade_id
        assert record.instrument in SCAN_INSTRUMENTS
        assert record.units != 0
        assert record.open_price > 0
        assert record.state == TradeState.OPEN
        assert isinstance(record.opened_at, datetime)
        assert record.opened_at.tzinfo is not None

        # Pydantic re-validation must succeed
        roundtrip = TradeRecord.model_validate(record.model_dump())
        assert roundtrip.trade_id == record.trade_id
        assert roundtrip.state == record.state


# ---------------------------------------------------------------------------
# 4. Live stream tick flow
# ---------------------------------------------------------------------------


class TestLiveStreamTickFlow:
    """Validate that the stream client yields real PriceTick and Heartbeat events."""

    @pytest.mark.asyncio
    async def test_stream_yields_ticks_and_heartbeats(self, stream_client) -> None:
        ticks: list[PriceTick] = []
        heartbeats: list[Heartbeat] = []
        requested = ("XAU_USD", "EUR_USD")

        async def _collect() -> None:
            async for event in stream_client.stream_prices(requested):
                if isinstance(event, PriceTick):
                    ticks.append(event)
                elif isinstance(event, Heartbeat):
                    heartbeats.append(event)
                # On weekends ticks are rare; stop after any event arrives
                if len(ticks) >= 1 or len(heartbeats) >= 2:
                    return

        try:
            await asyncio.wait_for(_collect(), timeout=60.0)
        except (TimeoutError, asyncio.TimeoutError):
            # Even on timeout, check what we collected
            pass

        # At minimum, heartbeats should arrive (every ~5s)
        total_events = len(ticks) + len(heartbeats)
        assert total_events >= 1, (
            "Expected at least 1 event (tick or heartbeat) from the stream"
        )

        for tick in ticks:
            assert tick.instrument in requested, (
                f"Unexpected instrument {tick.instrument}"
            )
            assert tick.bid > 0
            assert tick.ask >= tick.bid
            assert tick.time.tzinfo is not None


# ---------------------------------------------------------------------------
# 5. Stream fan-out to queues
# ---------------------------------------------------------------------------


class TestStreamFanoutToQueues:
    """PriceStreamTask should fan ticks into independent consumer queues."""

    @pytest.mark.asyncio
    async def test_fanout_starts_and_stops_cleanly(
        self,
        stream_client,
        trade_repository,
        excursion_repository,
        alert_repository,
        live_settings,
    ) -> None:
        from alerts.price_alert_engine import PriceAlertEngine
        from background.stream_task import PriceStreamTask
        from tracking.excursion_tracker import ExcursionTracker

        tracker = ExcursionTracker(
            trade_repository,
            excursion_repository,
            settings=live_settings,
        )
        engine = PriceAlertEngine(alert_repository)

        task = PriceStreamTask(
            stream_client,
            tracker,
            engine,
            settings=live_settings,
        )

        tasks_before = set(asyncio.all_tasks())

        await task.start()

        # Let the stream run for 15 seconds
        await asyncio.sleep(15)

        # Stream should be running
        status = task.stream_status()
        assert status.state in ("RUNNING", "DEGRADED"), (
            f"Expected RUNNING or DEGRADED, got {status.state}"
        )

        # Task statuses should reflect running
        task_statuses = task.task_statuses()
        running_names = {ts.name for ts in task_statuses if ts.state == "RUNNING"}
        assert "stream_producer" in running_names, (
            "stream_producer should be RUNNING"
        )

        await task.stop()

        # After stop, all task states should be STOPPED
        post_statuses = task.task_statuses()
        for ts in post_statuses:
            assert ts.state == "STOPPED", (
                f"Task {ts.name} should be STOPPED after stop(), got {ts.state}"
            )

        stream_health = task.stream_status()
        assert stream_health.state == "STOPPED"

        # Verify no asyncio tasks leaked
        tasks_after = set(asyncio.all_tasks())
        leaked = tasks_after - tasks_before
        # The current test task itself is expected; filter it out
        leaked = {t for t in leaked if not t.get_name().startswith("Task-")}
        assert len(leaked) == 0, f"Leaked asyncio tasks: {leaked}"


# ---------------------------------------------------------------------------
# 6. Excursion tracking on live ticks
# ---------------------------------------------------------------------------


class TestExcursionTrackingOnLiveTicks:
    """ExcursionTracker must persist samples when fed real ticks."""

    @pytest.mark.asyncio
    async def test_excursion_samples_from_live_ticks(
        self,
        account_client,
        stream_client,
        trade_repository,
        excursion_repository,
        journal_service,
        live_settings,
    ) -> None:
        from background.poller_task import TradePollerTask
        from tracking.excursion_tracker import ExcursionTracker

        # First poll to detect open trades
        poller = TradePollerTask(
            account_client,
            trade_repository,
            journal_service,
            settings=live_settings,
        )
        events = await poller.poll_once()
        open_events = [e for e in events if isinstance(e, TradeOpenedEvent)]
        assert len(open_events) >= 1, "Need at least one open trade for excursion test"

        trade_id = open_events[0].trade_id
        trade_record = trade_repository.get(trade_id)
        assert trade_record is not None

        # Near-zero threshold so frozen weekend prices still trigger writes
        low_threshold_settings = live_settings.model_copy(
            update={"mae_mfe_min_pip_move": 1e-10}
        )
        tracker = ExcursionTracker(
            trade_repository,
            excursion_repository,
            settings=low_threshold_settings,
        )

        # Collect ticks and feed through the tracker (weekend = sparse ticks)
        all_samples: list[ExcursionSample] = []
        instrument = trade_record.instrument

        async def _collect_and_track() -> None:
            async for event in stream_client.stream_prices([instrument]):
                if isinstance(event, PriceTick):
                    samples = tracker.process_tick(event)
                    all_samples.extend(samples)
                if len(all_samples) >= 1:
                    return

        try:
            await asyncio.wait_for(_collect_and_track(), timeout=60.0)
        except (TimeoutError, asyncio.TimeoutError):
            pass  # Check what we collected below

        assert len(all_samples) >= 1, (
            "Expected at least 1 ExcursionSample — stream may be inactive on weekends"
        )

        for sample in all_samples:
            assert sample.trade_id == trade_id
            assert sample.sampled_at.tzinfo is not None
            assert sample.bid > 0
            assert sample.ask > 0
            assert sample.adverse_pips >= 0
            assert sample.favorable_pips >= 0

        # Round-trip via repository
        stored = excursion_repository.list_for_trade(trade_id)
        assert len(stored) >= 1, "ExcursionRepository should persist samples"

        # MAE/MFE math check on the first sample
        sample = all_samples[0]
        spec = get_instrument_spec(trade_record.instrument)
        if trade_record.units > 0:
            expected_adverse = max(
                0.0, (trade_record.open_price - sample.bid) / spec.pip_size
            )
            expected_favorable = max(
                0.0, (sample.bid - trade_record.open_price) / spec.pip_size
            )
        else:
            expected_adverse = max(
                0.0, (sample.ask - trade_record.open_price) / spec.pip_size
            )
            expected_favorable = max(
                0.0, (trade_record.open_price - sample.ask) / spec.pip_size
            )

        assert abs(sample.adverse_pips - expected_adverse) < 1e-6, (
            f"adverse_pips mismatch: {sample.adverse_pips} vs {expected_adverse}"
        )
        assert abs(sample.favorable_pips - expected_favorable) < 1e-6, (
            f"favorable_pips mismatch: {sample.favorable_pips} vs {expected_favorable}"
        )


# ---------------------------------------------------------------------------
# 7. Price alert fire on crossing
# ---------------------------------------------------------------------------


class TestPriceAlertFireOnCrossing:
    """PriceAlertEngine must fire a pending alert when the tick crosses the target."""

    @pytest.mark.asyncio
    async def test_price_alert_fires_on_live_tick(
        self,
        account_client,
        stream_client,
        alert_repository,
    ) -> None:
        from alerts.price_alert_engine import PriceAlertEngine

        instrument = "XAU_USD"

        # Get current price to set the target at exactly current bid
        snap = await account_client.get_pricing(instrument)
        current_bid = snap.bid

        now = datetime.now(timezone.utc)
        alert = PriceAlert(
            id=1,
            instrument=instrument,
            target_price=current_bid,
            direction="below",
            status=AlertStatus.PENDING,
            chat_id=1,
            notes=None,
            created_at=now,
            fired_at=None,
        )
        alert_repository.upsert_price_alert(alert)

        engine = PriceAlertEngine(alert_repository)

        # Collect ticks and evaluate until the alert fires (weekend = sparse ticks)
        fired_results = []

        async def _collect_and_evaluate() -> None:
            async for event in stream_client.stream_prices([instrument]):
                if isinstance(event, PriceTick):
                    result = engine.evaluate_tick(event)
                    if result:
                        fired_results.extend(result)
                        return

        try:
            await asyncio.wait_for(_collect_and_evaluate(), timeout=60.0)
        except (TimeoutError, asyncio.TimeoutError):
            pass

        assert len(fired_results) >= 1, (
            "Expected the price alert to fire — stream may be inactive on weekends"
        )

        # Verify the repository was updated
        stored = alert_repository.get_price_alert(1)
        assert stored is not None
        assert stored.status == AlertStatus.FIRED
        assert stored.fired_at is not None

        # Fire-once semantics: feeding more ticks should not re-fire
        refired = []
        tick_count = 0

        async def _collect_more() -> None:
            nonlocal tick_count
            async for event in stream_client.stream_prices([instrument]):
                if isinstance(event, PriceTick):
                    result = engine.evaluate_tick(event)
                    refired.extend(result)
                    tick_count += 1
                    if tick_count >= 3:
                        return

        try:
            await asyncio.wait_for(_collect_more(), timeout=30.0)
        except (TimeoutError, asyncio.TimeoutError):
            pass  # Best-effort on weekends

        assert len(refired) == 0, "Alert should not re-fire (fire-once semantics)"


# ---------------------------------------------------------------------------
# 8. Stream task graceful shutdown
# ---------------------------------------------------------------------------


class TestStreamTaskGracefulShutdown:
    """PriceStreamTask must drain cleanly within a bounded time after stop()."""

    @pytest.mark.asyncio
    async def test_stream_task_stops_within_timeout(
        self,
        stream_client,
        trade_repository,
        excursion_repository,
        alert_repository,
        live_settings,
    ) -> None:
        from alerts.price_alert_engine import PriceAlertEngine
        from background.stream_task import PriceStreamTask
        from tracking.excursion_tracker import ExcursionTracker

        tracker = ExcursionTracker(
            trade_repository,
            excursion_repository,
            settings=live_settings,
        )
        engine = PriceAlertEngine(alert_repository)

        task = PriceStreamTask(
            stream_client,
            tracker,
            engine,
            settings=live_settings,
        )

        await task.start()
        await asyncio.sleep(10)

        # Stop and assert drains within 5 seconds
        await asyncio.wait_for(task.stop(), timeout=5.0)

        status = task.stream_status()
        assert status.state == "STOPPED", (
            f"Expected STOPPED after graceful shutdown, got {status.state}"
        )
