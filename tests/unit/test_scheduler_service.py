from __future__ import annotations

from datetime import datetime, timezone
from threading import Event, Thread
from types import SimpleNamespace

import orchestration.scheduler as scheduler_module
from orchestration.scheduler import (
    AUTO_SCAN_JOB_ID,
    MACRO_REFRESH_JOB_ID,
    MARKET_OPEN_WARM_JOB_ID,
    TIME_ALERT_JOB_ID,
    SchedulerService,
)


class StubOrchestrator:
    def __init__(self, run_started: Event | None = None, allow_finish: Event | None = None) -> None:
        self.run_started = run_started
        self.allow_finish = allow_finish
        self.scan_calls = 0
        self.calendar_calls = 0
        self.macro_calls = 0
        self._next_market_opens = [
            datetime(2026, 3, 29, 21, 0, tzinfo=timezone.utc),
            datetime(2026, 3, 30, 21, 0, tzinfo=timezone.utc),
        ]
        self.market_hours_service = SimpleNamespace(
            next_market_open_at=self._next_market_open_at,
        )

    def scan_all(self):
        self.scan_calls += 1
        if self.run_started is not None:
            self.run_started.set()
        if self.allow_finish is not None:
            self.allow_finish.wait(timeout=2)
        return None

    def refresh_calendar(self, *, force: bool = True):
        self.calendar_calls += 1
        return None

    def refresh_macro(self, *, force: bool = True):
        self.macro_calls += 1
        return None

    def _next_market_open_at(self):
        if len(self._next_market_opens) == 1:
            return self._next_market_opens[0]
        return self._next_market_opens.pop(0)


class StubWarmer:
    def __init__(self, result: int = 0) -> None:
        self.result = result
        self.calls = 0

    def warm_all(self):
        self.calls += 1
        return self.result


def test_scheduler_service_registers_stage11_jobs_with_utc_status() -> None:
    scheduler = SchedulerService(
        scan_orchestrator=StubOrchestrator(),
        cache_warmer=StubWarmer(),
    )
    try:
        scheduler.start()
        status = scheduler.status()
        job_ids = {job.job_id for job in status.jobs}

        assert AUTO_SCAN_JOB_ID in job_ids
        assert MACRO_REFRESH_JOB_ID in job_ids
        assert MARKET_OPEN_WARM_JOB_ID in job_ids
        assert status.timezone == "UTC"
        assert all(job.next_run_at is not None for job in status.jobs)
        assert all(job.next_run_at.tzinfo is not None for job in status.jobs)
        assert scheduler._scheduler.get_job(AUTO_SCAN_JOB_ID).coalesce is True
        assert scheduler._scheduler.get_job(AUTO_SCAN_JOB_ID).max_instances == 1
    finally:
        scheduler.shutdown(wait=False)


def test_scheduler_service_registers_time_alert_job_when_engine_present() -> None:
    scheduler = SchedulerService(
        scan_orchestrator=StubOrchestrator(),
        cache_warmer=StubWarmer(),
        time_alert_engine=type("StubTimeAlertEngine", (), {"evaluate_due_alerts": lambda self: []})(),
    )
    try:
        scheduler.start()
        job_ids = {job.job_id for job in scheduler.status().jobs}

        assert TIME_ALERT_JOB_ID in job_ids
        assert scheduler._scheduler.get_job(TIME_ALERT_JOB_ID) is not None
    finally:
        scheduler.shutdown(wait=False)


def test_scheduler_service_reschedules_dynamic_market_open_warm_job() -> None:
    orchestrator = StubOrchestrator()
    warmer = StubWarmer()
    scheduler = SchedulerService(
        scan_orchestrator=orchestrator,
        cache_warmer=warmer,
    )
    try:
        scheduler.start()
        first_run = scheduler._scheduler.get_job(MARKET_OPEN_WARM_JOB_ID).next_run_time

        scheduler._managed_jobs[MARKET_OPEN_WARM_JOB_ID].trigger()

        second_run = scheduler._scheduler.get_job(MARKET_OPEN_WARM_JOB_ID).next_run_time
        assert warmer.calls == 1
        assert second_run is not None
        assert second_run > first_run
    finally:
        scheduler.shutdown(wait=False)


def test_scheduler_service_queues_one_rerun_without_overlap() -> None:
    run_started = Event()
    allow_finish = Event()
    orchestrator = StubOrchestrator(run_started=run_started, allow_finish=allow_finish)
    scheduler = SchedulerService(
        scan_orchestrator=orchestrator,
        cache_warmer=StubWarmer(),
    )
    try:
        scheduler.start()
        managed = scheduler._managed_jobs[AUTO_SCAN_JOB_ID]

        first = Thread(target=managed.trigger)
        second = Thread(target=managed.trigger)
        first.start()
        run_started.wait(timeout=2)
        second.start()
        allow_finish.set()
        first.join(timeout=2)
        second.join(timeout=2)

        assert orchestrator.scan_calls == 2
        assert scheduler.status().jobs[0].pending_rerun is False
    finally:
        scheduler.shutdown(wait=False)


def test_managed_job_applies_failure_backoff_before_rerun(monkeypatch) -> None:
    sleep_calls: list[int] = []
    monkeypatch.setattr(scheduler_module.time, "sleep", lambda seconds: sleep_calls.append(seconds))

    job_holder = {}
    attempts = {"count": 0}

    def flaky_job() -> None:
        attempts["count"] += 1
        if attempts["count"] <= 3:
            with job_holder["managed"]._state_lock:
                job_holder["managed"].pending_rerun = True
            raise RuntimeError(f"boom-{attempts['count']}")

    managed = scheduler_module._ManagedJob(
        job_id="backoff_test",
        fn=flaky_job,
        logger=scheduler_module.get_logger("tests.scheduler.backoff"),
    )
    job_holder["managed"] = managed

    managed.trigger()

    assert attempts["count"] == 4
    assert sleep_calls == [5, 15, 60]
    assert managed.consecutive_failures == 0
