"""Stage 11 APScheduler lifecycle and job management."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import time
from threading import Lock, RLock
from typing import Callable

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from alerts.time_alert_engine import TimeAlertEngine
from background.poller_task import TradePollerTask
from config.settings import Settings, get_settings
from core.logging_setup import get_logger, log_failure
from core.models import SchedulerJobStatus, SchedulerStatus
from journal.trade_history_service import TradeHistoryService
from orchestration.cache_warmer import CacheWarmer
from orchestration.scan_orchestrator import ScanOrchestrator

AUTO_SCAN_JOB_ID = "auto_scan"
LONDON_WARM_JOB_ID = "london_warm"
NY_WARM_JOB_ID = "ny_warm"
MARKET_OPEN_WARM_JOB_ID = "market_open_warm"
CALENDAR_REFRESH_JOB_ID = "calendar_refresh"
MACRO_REFRESH_JOB_ID = "macro_refresh"
TRADE_POLLER_JOB_ID = "trade_poller"
TRADE_HISTORY_SYNC_JOB_ID = "trade_history_sync"
TIME_ALERT_JOB_ID = "time_alerts"

@dataclass
class _ManagedJob:
    job_id: str
    fn: Callable[[], object]
    logger: object

    def __post_init__(self) -> None:
        self._execution_lock = Lock()
        self._state_lock = RLock()
        self.is_running = False
        self.pending_rerun = False
        self.consecutive_failures = 0
        self.last_started_at: datetime | None = None
        self.last_completed_at: datetime | None = None
        self.last_succeeded_at: datetime | None = None
        self.last_failed_at: datetime | None = None
        self.last_error: str | None = None

    def trigger(self) -> None:
        if not self._execution_lock.acquire(blocking=False):
            with self._state_lock:
                self.pending_rerun = True
            self.logger.warning("scheduler_job_rerun_queued", job_id=self.job_id)
            return

        try:
            while True:
                started = datetime.now(timezone.utc)
                with self._state_lock:
                    self.is_running = True
                    self.pending_rerun = False
                    self.last_started_at = started

                try:
                    self.fn()
                except Exception as exc:
                    finished = datetime.now(timezone.utc)
                    with self._state_lock:
                        self.is_running = False
                        self.last_completed_at = finished
                        self.last_failed_at = finished
                        self.last_error = str(exc)
                        self.consecutive_failures += 1
                        failure_count = self.consecutive_failures
                        continue_rerun = self.pending_rerun
                    log_failure(
                        self.logger,
                        "scheduler_job_failed",
                        exc,
                        job_id=self.job_id,
                    )
                    if not continue_rerun:
                        return
                    backoff_seconds = self._failure_backoff_seconds(failure_count)
                    self.logger.warning(
                        "scheduler_job_rerun_backoff",
                        job_id=self.job_id,
                        backoff_seconds=backoff_seconds,
                        consecutive_failures=failure_count,
                    )
                    time.sleep(backoff_seconds)
                    continue

                finished = datetime.now(timezone.utc)
                with self._state_lock:
                    self.is_running = False
                    self.last_completed_at = finished
                    self.last_succeeded_at = finished
                    self.last_error = None
                    self.consecutive_failures = 0
                    continue_rerun = self.pending_rerun
                if not continue_rerun:
                    return
        finally:
            with self._state_lock:
                self.is_running = False
            self._execution_lock.release()

    @staticmethod
    def _failure_backoff_seconds(consecutive_failures: int) -> int:
        if consecutive_failures <= 1:
            return 5
        if consecutive_failures == 2:
            return 15
        return 60


class SchedulerService:
    """Own APScheduler lifecycle and Stage 11 job registration."""

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        scan_orchestrator: ScanOrchestrator | None = None,
        cache_warmer: CacheWarmer | None = None,
        trade_poller: TradePollerTask | None = None,
        trade_history_service: TradeHistoryService | None = None,
        time_alert_engine: TimeAlertEngine | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.scan_orchestrator = scan_orchestrator or ScanOrchestrator(settings=self.settings)
        self.cache_warmer = cache_warmer or CacheWarmer(
            settings=self.settings,
            market_hours_service=self.scan_orchestrator.market_hours_service,
        )
        self.trade_poller = trade_poller
        self.trade_history_service = trade_history_service
        self.time_alert_engine = time_alert_engine
        self.logger = get_logger(__name__)

        self._scheduler = BackgroundScheduler(timezone="UTC")
        self._jobs_registered = False
        self._started_at: datetime | None = None
        self._paused_at: datetime | None = None
        self._state = "STOPPED"
        self._managed_jobs: dict[str, _ManagedJob] = {}
        self._scan_interval_minutes = self.settings.scan_interval_minutes

    def start(self) -> None:
        """Register jobs and start the scheduler."""

        if not self._jobs_registered:
            self._register_jobs()
        if self._state == "STOPPED":
            try:
                self._scheduler.start()
            except Exception as exc:
                log_failure(self.logger, "scheduler_start_failed", exc)
                raise
            self._started_at = datetime.now(timezone.utc)
        elif self._state == "PAUSED":
            try:
                self._scheduler.resume()
            except Exception as exc:
                log_failure(self.logger, "scheduler_resume_failed", exc)
                raise
        self._state = "RUNNING"
        self._paused_at = None
        self.logger.info("scheduler_started", state=self._state)

    def pause(self) -> None:
        """Pause all registered jobs."""

        if self._state != "RUNNING":
            return
        try:
            self._scheduler.pause()
        except Exception as exc:
            log_failure(self.logger, "scheduler_pause_failed", exc)
            raise
        self._state = "PAUSED"
        self._paused_at = datetime.now(timezone.utc)
        self.logger.info("scheduler_paused", state=self._state)

    def resume(self) -> None:
        """Resume all registered jobs."""

        if self._state != "PAUSED":
            return
        try:
            self._scheduler.resume()
        except Exception as exc:
            log_failure(self.logger, "scheduler_resume_failed", exc)
            raise
        self._state = "RUNNING"
        self._paused_at = None
        self.logger.info("scheduler_resumed", state=self._state)

    def shutdown(self, *, wait: bool = False) -> None:
        """Shutdown the scheduler and retain historical status state."""

        if self._state == "STOPPED":
            return
        try:
            self._scheduler.shutdown(wait=wait)
        except Exception as exc:
            log_failure(self.logger, "scheduler_shutdown_failed", exc, wait=wait)
            raise
        self._state = "STOPPED"
        self._paused_at = None
        self.logger.info("scheduler_stopped", wait=wait)

    def reschedule_auto_scan(self, interval_minutes: int) -> SchedulerStatus:
        """Persist an in-memory auto-scan cadence change and reschedule the job."""

        if interval_minutes <= 0:
            raise ValueError("interval_minutes must be a positive integer.")

        self._scan_interval_minutes = int(interval_minutes)
        if self._jobs_registered:
            try:
                self._scheduler.reschedule_job(
                    AUTO_SCAN_JOB_ID,
                    trigger=IntervalTrigger(minutes=self._scan_interval_minutes, timezone="UTC"),
                )
            except Exception as exc:
                log_failure(
                    self.logger,
                    "scheduler_reschedule_failed",
                    exc,
                    job_id=AUTO_SCAN_JOB_ID,
                    interval_minutes=self._scan_interval_minutes,
                )
                raise
        self.logger.info(
            "scheduler_rescheduled",
            job_id=AUTO_SCAN_JOB_ID,
            interval_minutes=self._scan_interval_minutes,
        )
        return self.status()

    def status(self) -> SchedulerStatus:
        """Return the typed scheduler status surface."""

        jobs: list[SchedulerJobStatus] = []
        for job_id, managed in sorted(self._managed_jobs.items()):
            job = self._scheduler.get_job(job_id)
            jobs.append(
                SchedulerJobStatus(
                    job_id=job_id,
                    is_paused=self._state == "PAUSED",
                    is_running=managed.is_running,
                    pending_rerun=managed.pending_rerun,
                    last_started_at=managed.last_started_at,
                    last_completed_at=managed.last_completed_at,
                    last_succeeded_at=managed.last_succeeded_at,
                    last_failed_at=managed.last_failed_at,
                    next_run_at=None if job is None else job.next_run_time,
                    last_error=managed.last_error,
                )
            )

        return SchedulerStatus(
            state=self._state,
            timezone="UTC",
            started_at=self._started_at,
            paused_at=self._paused_at,
            jobs=tuple(jobs),
        )

    def _register_jobs(self) -> None:
        self._add_job(
            AUTO_SCAN_JOB_ID,
            self.scan_orchestrator.scan_all,
            trigger=IntervalTrigger(minutes=self._scan_interval_minutes, timezone="UTC"),
        )
        self._add_job(
            LONDON_WARM_JOB_ID,
            self.cache_warmer.warm_all,
            trigger=CronTrigger(hour=8, minute=0, timezone="UTC"),
        )
        self._add_job(
            NY_WARM_JOB_ID,
            self.cache_warmer.warm_all,
            trigger=CronTrigger(hour=13, minute=0, timezone="UTC"),
        )
        self._schedule_market_open_warm()
        self._add_job(
            CALENDAR_REFRESH_JOB_ID,
            lambda: self.scan_orchestrator.refresh_calendar(force=True),
            trigger=IntervalTrigger(hours=self.settings.calendar_refresh_hours, timezone="UTC"),
        )
        self._add_job(
            MACRO_REFRESH_JOB_ID,
            lambda: self.scan_orchestrator.refresh_macro(force=True),
            trigger=IntervalTrigger(hours=self.settings.macro_refresh_hours, timezone="UTC"),
        )

        if self.trade_poller is not None:
            self._add_job(
                TRADE_POLLER_JOB_ID,
                self.trade_poller.run_once,
                trigger=IntervalTrigger(seconds=self.settings.poll_interval_seconds, timezone="UTC"),
            )
        if self.trade_history_service is not None:
            self._add_job(
                TRADE_HISTORY_SYNC_JOB_ID,
                self.trade_history_service.incremental_sync,
                trigger=IntervalTrigger(seconds=self.settings.poll_interval_seconds, timezone="UTC"),
            )
        if self.time_alert_engine is not None:
            self._add_job(
                TIME_ALERT_JOB_ID,
                self.time_alert_engine.evaluate_due_alerts,
                trigger=IntervalTrigger(minutes=1, timezone="UTC"),
            )

        self._jobs_registered = True

    def _schedule_market_open_warm(self) -> None:
        next_open_at = self.scan_orchestrator.market_hours_service.next_market_open_at()
        if next_open_at is None:
            self.logger.warning("scheduler_market_open_warm_unscheduled")
            return
        if next_open_at <= datetime.now(timezone.utc):
            next_open_at = datetime.now(timezone.utc) + timedelta(seconds=1)
        self._add_job(
            MARKET_OPEN_WARM_JOB_ID,
            self._run_market_open_warm,
            trigger=DateTrigger(run_date=next_open_at, timezone="UTC"),
        )
        self.logger.info(
            "scheduler_market_open_warm_scheduled",
            next_run_at=next_open_at,
        )

    def _run_market_open_warm(self) -> int:
        try:
            return self.cache_warmer.warm_all()
        finally:
            self._schedule_market_open_warm()

    def _add_job(self, job_id: str, fn: Callable[[], object], *, trigger) -> None:
        managed = self._managed_jobs.get(job_id)
        if managed is None:
            managed = _ManagedJob(job_id=job_id, fn=fn, logger=self.logger)
            self._managed_jobs[job_id] = managed
        else:
            managed.fn = fn
        try:
            self._scheduler.add_job(
                managed.trigger,
                trigger=trigger,
                id=job_id,
                replace_existing=True,
                coalesce=True,
                max_instances=1,
                misfire_grace_time=60,
            )
        except Exception as exc:
            log_failure(self.logger, "scheduler_job_registration_failed", exc, job_id=job_id)
            raise
        self.logger.info("scheduler_job_registered", job_id=job_id)


__all__ = [
    "AUTO_SCAN_JOB_ID",
    "CALENDAR_REFRESH_JOB_ID",
    "LONDON_WARM_JOB_ID",
    "MACRO_REFRESH_JOB_ID",
    "MARKET_OPEN_WARM_JOB_ID",
    "NY_WARM_JOB_ID",
    "SchedulerService",
    "TIME_ALERT_JOB_ID",
    "TRADE_HISTORY_SYNC_JOB_ID",
    "TRADE_POLLER_JOB_ID",
]
