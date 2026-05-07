"""Supervised live-price streaming and tick fan-out."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import RLock
from typing import Iterable

from config.settings import Settings, get_settings
from core.events import Heartbeat, PriceTick
from core.instrument_registry import normalize_instrument, validate_live_instrument
from core.logging_setup import get_logger, log_failure
from core.models import BackgroundTaskStatus, QueueDepthStatus, StreamHealthStatus
from alerts.price_alert_engine import PriceAlertEngine
from providers.stream_client import OandaStreamClient
from tracking.excursion_tracker import ExcursionTracker

_QUEUE_NAMES = ("excursion", "price_alert")


@dataclass
class _TaskState:
    name: str
    state: str = "STOPPED"
    restart_count: int = 0
    started_at: datetime | None = None
    last_heartbeat_at: datetime | None = None
    last_error_at: datetime | None = None
    last_error: str | None = None


@dataclass
class _QueueState:
    queue: asyncio.Queue[str]
    latest_by_instrument: dict[str, PriceTick] = field(default_factory=dict)
    queued_instruments: set[str] = field(default_factory=set)


class PriceStreamTask:
    """Own the live stream, independent tick queues, and consumer loops."""

    def __init__(
        self,
        stream_client: OandaStreamClient,
        excursion_tracker: ExcursionTracker,
        price_alert_engine: PriceAlertEngine,
        *,
        settings: Settings | None = None,
        queue_maxsize: int = 1000,
    ) -> None:
        self.stream_client = stream_client
        self.excursion_tracker = excursion_tracker
        self.price_alert_engine = price_alert_engine
        self.settings = settings or get_settings()
        self.logger = get_logger(__name__)
        self._state_lock = RLock()
        self._base_instruments = self._normalize_instruments(self.settings.stream_instruments)
        self._open_trade_instruments: set[str] = set()
        self._alert_instruments: set[str] = set(self._engine_active_instruments())
        self._resubscribe_requested = False
        resolved_queue_maxsize = max(int(queue_maxsize), len(self.subscription_instruments()), 1)
        self._queue_states = {
            "excursion": _QueueState(queue=asyncio.Queue(maxsize=resolved_queue_maxsize)),
            "price_alert": _QueueState(queue=asyncio.Queue(maxsize=resolved_queue_maxsize)),
        }
        self.excursion_queue: asyncio.Queue[str] = self._queue_states["excursion"].queue
        self.price_alert_queue: asyncio.Queue[str] = self._queue_states["price_alert"].queue

        self._stop_event: asyncio.Event | None = None
        self._tasks: dict[str, asyncio.Task] = {}
        self._task_states = {
            "stream_producer": _TaskState(name="stream_producer"),
            "excursion_consumer": _TaskState(name="excursion_consumer"),
            "price_alert_consumer": _TaskState(name="price_alert_consumer"),
        }
        self._dropped_events = {name: 0 for name in _QUEUE_NAMES}
        self._latest_ticks: dict[str, PriceTick] = {}
        self._stream_started_at: datetime | None = None
        self._last_tick_at: datetime | None = None
        self._last_heartbeat_at: datetime | None = None
        self._last_error_at: datetime | None = None
        self._last_error: str | None = None
        self._reconnect_count = 0

    def refresh_price_alert_instruments(self) -> tuple[str, ...]:
        """Reload alert-bearing instruments from storage and update subscriptions."""

        refresh = getattr(self.price_alert_engine, "refresh_pending_alert_index", None)
        active = refresh() if callable(refresh) else self._engine_active_instruments()
        return self._set_price_alert_instruments(active)

    def update_open_trade_instruments(self, instruments: Iterable[str]) -> tuple[str, ...]:
        """Update the live stream watchlist for currently open trades."""

        normalized = self._normalize_instruments(instruments)
        return self._set_open_trade_instruments(normalized)

    def subscription_instruments(self) -> tuple[str, ...]:
        """Return the current live stream subscription universe."""

        with self._state_lock:
            return self._subscription_universe_locked()

    async def start(self) -> None:
        """Start the producer and both consumers."""

        if self._tasks:
            return

        self._stop_event = asyncio.Event()
        self._stream_started_at = datetime.now(timezone.utc)
        self.logger.info(
            "price_stream_task_starting",
            instruments=self.subscription_instruments(),
            queue_maxsize=self.excursion_queue.maxsize,
        )
        self._tasks = {
            "stream_producer": asyncio.create_task(
                self._run_stream(),
                name="stream_producer",
            ),
            "excursion_consumer": asyncio.create_task(
                self._run_consumer(
                    name="excursion_consumer",
                    queue_name="excursion",
                    handler=self.excursion_tracker.process_tick,
                ),
                name="excursion_consumer",
            ),
            "price_alert_consumer": asyncio.create_task(
                self._run_consumer(
                    name="price_alert_consumer",
                    queue_name="price_alert",
                    handler=self.price_alert_engine.evaluate_tick,
                ),
                name="price_alert_consumer",
            ),
        }

    async def stop(self) -> None:
        """Stop all producer and consumer tasks."""

        if self._stop_event is not None:
            self._stop_event.set()

        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for task, result in zip(tasks, results, strict=False):
                if isinstance(result, asyncio.CancelledError):
                    continue
                if isinstance(result, Exception):
                    log_failure(
                        self.logger,
                        "price_stream_shutdown_task_failed",
                        result,
                        level="warning",
                        task_name=task.get_name(),
                    )
        self._tasks = {}
        for state in self._task_states.values():
            state.state = "STOPPED"
        self.logger.info("price_stream_task_stopped")

    def stream_status(self) -> StreamHealthStatus:
        state = "RUNNING" if self._tasks else "STOPPED"
        if self._last_error is not None and self._tasks:
            state = "DEGRADED"
        return StreamHealthStatus(
            state=state,
            started_at=self._stream_started_at,
            last_tick_at=self._last_tick_at,
            last_heartbeat_at=self._last_heartbeat_at,
            reconnect_count=self._reconnect_count,
            last_error_at=self._last_error_at,
            last_error=self._last_error,
        )

    def task_statuses(self) -> tuple[BackgroundTaskStatus, ...]:
        return tuple(
            BackgroundTaskStatus(
                name=state.name,
                state=state.state,
                restart_count=state.restart_count,
                started_at=state.started_at,
                last_heartbeat_at=state.last_heartbeat_at,
                last_error_at=state.last_error_at,
                last_error=state.last_error,
            )
            for state in self._task_states.values()
        )

    def queue_statuses(self) -> tuple[QueueDepthStatus, ...]:
        return (
            QueueDepthStatus(name="excursion", depth=self.excursion_queue.qsize()),
            QueueDepthStatus(name="price_alert", depth=self.price_alert_queue.qsize()),
        )

    def latest_quote(
        self,
        instrument: str,
        *,
        max_age_seconds: float | None = None,
    ) -> PriceTick | None:
        """Return the freshest cached streamed quote for one instrument."""

        tick = self._latest_ticks.get(instrument)
        if tick is None:
            return None
        if max_age_seconds is not None:
            age = (datetime.now(timezone.utc) - tick.time).total_seconds()
            if age > max_age_seconds:
                return None
        return tick

    async def _run_stream(self) -> None:
        assert self._stop_event is not None
        state = self._task_states["stream_producer"]
        state.started_at = state.started_at or datetime.now(timezone.utc)
        state.state = "RUNNING"
        backoff_seconds = 1.0

        while not self._stop_event.is_set():
            subscribed_instruments = self._begin_subscription_cycle()
            try:
                async for event in self.stream_client.stream_prices(subscribed_instruments):
                    if self._stop_event.is_set():
                        break
                    if self._last_error is not None:
                        self._last_error = None
                        self._last_error_at = None
                        state.last_error = None
                        state.last_error_at = None
                    state.last_heartbeat_at = datetime.now(timezone.utc)
                    if isinstance(event, Heartbeat):
                        self._last_heartbeat_at = event.time
                        if self._subscription_changed(subscribed_instruments):
                            break
                        continue

                    self._last_tick_at = event.time
                    self._latest_ticks[event.instrument] = event
                    self._enqueue_latest("excursion", self.excursion_queue, event)
                    consumer_count = 1
                    if self._instrument_has_active_price_alert(event.instrument):
                        self._enqueue_latest("price_alert", self.price_alert_queue, event)
                        consumer_count = 2
                    self.logger.debug(
                        "price_tick_enqueued",
                        instrument=event.instrument,
                        consumer_count=consumer_count,
                        queue_depths={
                            "excursion": self.excursion_queue.qsize(),
                            "price_alert": self.price_alert_queue.qsize(),
                        },
                    )
                    if self._subscription_changed(subscribed_instruments):
                        break

                if self._stop_event.is_set():
                    break
                if self._subscription_changed(subscribed_instruments):
                    self.logger.info(
                        "price_stream_resubscribe_requested",
                        previous_instruments=subscribed_instruments,
                        next_instruments=self.subscription_instruments(),
                    )
                    backoff_seconds = 1.0
                    continue

                self._reconnect_count += 1
                state.restart_count += 1
                self.logger.warning(
                    "price_stream_reconnect_scheduled",
                    backoff_seconds=backoff_seconds,
                    reconnect_count=self._reconnect_count,
                )
                await asyncio.sleep(backoff_seconds)
                backoff_seconds = min(backoff_seconds * 2.0, 60.0)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                now = datetime.now(timezone.utc)
                self._last_error = str(exc)
                self._last_error_at = now
                state.state = "DEGRADED"
                state.last_error = str(exc)
                state.last_error_at = now
                self._reconnect_count += 1
                state.restart_count += 1
                log_failure(
                    self.logger,
                    "price_stream_task_failed",
                    exc,
                    instruments=subscribed_instruments,
                    backoff_seconds=backoff_seconds,
                    reconnect_count=self._reconnect_count,
                )
                await asyncio.sleep(backoff_seconds)
                backoff_seconds = min(backoff_seconds * 2.0, 60.0)
            else:
                backoff_seconds = 1.0
                state.state = "RUNNING"

        state.state = "STOPPED"

    async def _run_consumer(self, *, name: str, queue_name: str, handler) -> None:
        assert self._stop_event is not None
        state = self._task_states[name]
        queue_state = self._queue_states[queue_name]
        queue = queue_state.queue
        state.started_at = state.started_at or datetime.now(timezone.utc)
        state.state = "RUNNING"

        while not self._stop_event.is_set():
            try:
                instrument = await asyncio.wait_for(queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                raise

            queue_state.queued_instruments.discard(instrument)
            tick = queue_state.latest_by_instrument.pop(instrument, None)
            try:
                if tick is None:
                    continue
                await asyncio.to_thread(handler, tick)
                if queue_name == "price_alert":
                    self._set_price_alert_instruments(self._engine_active_instruments())
                state.last_heartbeat_at = datetime.now(timezone.utc)
                state.state = "RUNNING"
            except Exception as exc:
                now = datetime.now(timezone.utc)
                state.last_error = str(exc)
                state.last_error_at = now
                state.state = "DEGRADED"
                log_failure(
                    self.logger,
                    "price_stream_consumer_failed",
                    exc,
                    consumer=name,
                    queue_depth=queue.qsize(),
                    instrument=getattr(tick, "instrument", None),
                )
            finally:
                queue.task_done()

        state.state = "STOPPED"

    def _enqueue_latest(self, queue_name: str, queue: asyncio.Queue[str], event: PriceTick) -> None:
        queue_state = self._queue_states[queue_name]
        replaced = event.instrument in queue_state.latest_by_instrument
        queue_state.latest_by_instrument[event.instrument] = event
        if event.instrument in queue_state.queued_instruments:
            if replaced:
                self._dropped_events[queue_name] += 1
            return

        try:
            queue.put_nowait(event.instrument)
            queue_state.queued_instruments.add(event.instrument)
            return
        except asyncio.QueueFull:
            queue_state.latest_by_instrument.pop(event.instrument, None)
            self._dropped_events[queue_name] += 1
        self.logger.warning(
            "price_stream_queue_overflow",
            queue_name=queue_name,
            latest_instrument=event.instrument,
            queue_depth=queue.qsize(),
            dropped_total=self._dropped_events[queue_name],
        )

    @staticmethod
    def _normalize_instruments(instruments: Iterable[str]) -> tuple[str, ...]:
        normalized: list[str] = []
        seen: set[str] = set()
        for raw in instruments:
            instrument = validate_live_instrument(normalize_instrument(str(raw)))
            if instrument in seen:
                continue
            seen.add(instrument)
            normalized.append(instrument)
        return tuple(normalized)

    def _begin_subscription_cycle(self) -> tuple[str, ...]:
        with self._state_lock:
            instruments = self._subscription_universe_locked()
            self._resubscribe_requested = False
            return instruments

    def _subscription_changed(self, current: tuple[str, ...]) -> bool:
        with self._state_lock:
            return self._resubscribe_requested or self._subscription_universe_locked() != current

    def _instrument_has_active_price_alert(self, instrument: str) -> bool:
        with self._state_lock:
            return instrument in self._alert_instruments

    def _engine_active_instruments(self) -> tuple[str, ...]:
        active = getattr(self.price_alert_engine, "active_instruments", None)
        if not callable(active):
            return self._base_instruments
        return tuple(active())

    def _set_price_alert_instruments(self, instruments: Iterable[str]) -> tuple[str, ...]:
        normalized = set(self._normalize_instruments(instruments))
        with self._state_lock:
            previous = self._subscription_universe_locked()
            if normalized == self._alert_instruments:
                return previous
            self._alert_instruments = normalized
            current = self._subscription_universe_locked()
            if current != previous:
                self._resubscribe_requested = True
            return current

    def _set_open_trade_instruments(self, instruments: Iterable[str]) -> tuple[str, ...]:
        normalized = set(self._normalize_instruments(instruments))
        with self._state_lock:
            previous = self._subscription_universe_locked()
            if normalized == self._open_trade_instruments:
                return previous
            self._open_trade_instruments = normalized
            current = self._subscription_universe_locked()
            if current != previous:
                self._resubscribe_requested = True
            return current

    def _subscription_universe_locked(self) -> tuple[str, ...]:
        ordered: list[str] = []
        seen: set[str] = set()
        for instrument in self._base_instruments:
            if instrument in seen:
                continue
            seen.add(instrument)
            ordered.append(instrument)
        for instrument in sorted(self._open_trade_instruments):
            if instrument in seen:
                continue
            seen.add(instrument)
            ordered.append(instrument)
        for instrument in sorted(self._alert_instruments):
            if instrument in seen:
                continue
            seen.add(instrument)
            ordered.append(instrument)
        return tuple(ordered)


__all__ = ["PriceStreamTask"]
