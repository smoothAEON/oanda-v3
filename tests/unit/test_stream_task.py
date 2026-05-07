from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import time

import pytest

from alerts.price_alert_engine import PriceAlertEngine
from background.stream_task import PriceStreamTask
from core.events import Heartbeat, PriceTick
from tracking.excursion_tracker import ExcursionTracker


BASE_TIME = datetime(2026, 3, 21, 8, 0, tzinfo=timezone.utc)


class StubStreamClient:
    async def stream_prices(self, instruments):
        yield Heartbeat(time=BASE_TIME)
        yield PriceTick(
            instrument="EUR_USD",
            bid=1.1000,
            ask=1.1002,
            time=BASE_TIME,
        )
        raise RuntimeError("stream dropped")


class StubTracker:
    def __init__(self) -> None:
        self.ticks: list[PriceTick] = []

    def process_tick(self, tick: PriceTick):
        self.ticks.append(tick)
        return []


class StubPriceAlertEngine:
    def __init__(self) -> None:
        self.ticks: list[PriceTick] = []

    def evaluate_tick(self, tick: PriceTick):
        self.ticks.append(tick)
        return []


class OneTickStreamClient:
    async def stream_prices(self, instruments):
        yield PriceTick(
            instrument="EUR_USD",
            bid=1.1000,
            ask=1.1002,
            time=BASE_TIME,
        )
        await asyncio.sleep(0.2)


class RecoveringStreamClient:
    def __init__(self) -> None:
        self.calls = 0

    async def stream_prices(self, instruments):
        self.calls += 1
        if self.calls == 1:
            yield PriceTick(
                instrument="EUR_USD",
                bid=1.1000,
                ask=1.1002,
                time=BASE_TIME,
            )
            raise RuntimeError("stream dropped")
        yield Heartbeat(time=BASE_TIME.replace(second=1))
        yield PriceTick(
            instrument="EUR_USD",
            bid=1.1001,
            ask=1.1003,
            time=BASE_TIME.replace(second=2),
        )
        await asyncio.sleep(0.2)


class SlowTracker:
    def __init__(self) -> None:
        self.ticks: list[PriceTick] = []

    def process_tick(self, tick: PriceTick):
        time.sleep(0.2)
        self.ticks.append(tick)
        return []


@pytest.mark.asyncio
async def test_stream_task_fans_out_ticks_and_records_reconnects() -> None:
    tracker = StubTracker()
    alerts = StubPriceAlertEngine()
    task = PriceStreamTask(StubStreamClient(), tracker, alerts)

    await task.start()
    await asyncio.sleep(0.2)
    await task.stop()

    assert len(tracker.ticks) >= 1
    assert len(alerts.ticks) >= 1
    assert task.stream_status().reconnect_count >= 1


def test_enqueue_latest_drops_oldest_when_queue_is_full() -> None:
    tracker = StubTracker()
    alerts = StubPriceAlertEngine()
    task = PriceStreamTask(StubStreamClient(), tracker, alerts, queue_maxsize=1)
    first = PriceTick(instrument="EUR_USD", bid=1.0, ask=1.1, time=BASE_TIME)
    second = PriceTick(
        instrument="EUR_USD",
        bid=1.0,
        ask=1.1,
        time=BASE_TIME.replace(second=1),
    )

    task._enqueue_latest("excursion", task.excursion_queue, first)
    task._enqueue_latest("excursion", task.excursion_queue, second)
    queued = task.excursion_queue.get_nowait()
    latest = task._queue_states["excursion"].latest_by_instrument[queued]

    assert queued == "EUR_USD"
    assert latest.time == second.time
    assert task._dropped_events["excursion"] == 1


def test_latest_quote_returns_cached_tick_and_respects_age() -> None:
    tracker = StubTracker()
    alerts = StubPriceAlertEngine()
    task = PriceStreamTask(StubStreamClient(), tracker, alerts)
    now = datetime.now(timezone.utc)
    tick = PriceTick(instrument="EUR_USD", bid=1.1000, ask=1.1002, time=now)

    task._latest_ticks["EUR_USD"] = tick

    assert task.latest_quote("EUR_USD") == tick
    assert task.latest_quote("EUR_USD", max_age_seconds=60) == tick
    assert task.latest_quote("EUR_USD", max_age_seconds=0) is None
    assert task.latest_quote("SPX500_USD") is None


@pytest.mark.asyncio
async def test_stream_consumers_run_in_threads_without_blocking_event_loop() -> None:
    tracker = SlowTracker()
    alerts = StubPriceAlertEngine()
    task = PriceStreamTask(OneTickStreamClient(), tracker, alerts, queue_maxsize=10)

    await task.start()
    await asyncio.sleep(0.01)
    started = asyncio.get_running_loop().time()
    await asyncio.sleep(0.05)
    elapsed = asyncio.get_running_loop().time() - started
    await asyncio.sleep(0.25)
    await task.stop()

    assert elapsed < 0.15
    assert tracker.ticks


@pytest.mark.asyncio
async def test_stream_status_clears_last_error_after_successful_reconnect() -> None:
    tracker = StubTracker()
    alerts = StubPriceAlertEngine()
    task = PriceStreamTask(RecoveringStreamClient(), tracker, alerts)

    await task.start()
    status = task.stream_status()
    deadline = asyncio.get_running_loop().time() + 2.5
    while asyncio.get_running_loop().time() < deadline:
        if status.reconnect_count >= 1 and status.last_error is None and status.last_tick_at is not None:
            break
        await asyncio.sleep(0.05)
        status = task.stream_status()
    await task.stop()

    assert status.state == "RUNNING"
    assert status.last_error is None
    assert status.last_error_at is None
    assert status.reconnect_count >= 1


def test_queue_coalescing_preserves_other_instruments() -> None:
    tracker = StubTracker()
    alerts = StubPriceAlertEngine()
    task = PriceStreamTask(StubStreamClient(), tracker, alerts, queue_maxsize=1)
    eur_tick = PriceTick(instrument="EUR_USD", bid=1.0, ask=1.1, time=BASE_TIME)
    spx_tick = PriceTick(instrument="SPX500_USD", bid=3000.0, ask=3000.2, time=BASE_TIME)
    newer_eur_tick = PriceTick(
        instrument="EUR_USD",
        bid=1.0,
        ask=1.1,
        time=BASE_TIME.replace(second=1),
    )

    task._enqueue_latest("excursion", task.excursion_queue, eur_tick)
    task._enqueue_latest("excursion", task.excursion_queue, spx_tick)
    task._enqueue_latest("excursion", task.excursion_queue, newer_eur_tick)

    queued = {
        task.excursion_queue.get_nowait(),
        task.excursion_queue.get_nowait(),
    }

    assert queued == {"EUR_USD", "SPX500_USD"}
    assert task._queue_states["excursion"].latest_by_instrument["EUR_USD"].time == newer_eur_tick.time
    assert task._queue_states["excursion"].latest_by_instrument["SPX500_USD"].time == spx_tick.time
