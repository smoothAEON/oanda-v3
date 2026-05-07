"""Shared read-only runtime view helpers for Telegram and MCP surfaces."""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from bot.runtime import BotRuntime
from core.models import BackgroundTaskStatus, MacroContextStatus, RuntimeHealthStatus, SchedulerStatus, TimeframeSnapshot
from data.market_hours import coerce_market_hours_overview
from orchestration.scheduler import TRADE_POLLER_JOB_ID

SGT = ZoneInfo("Asia/Singapore")


def current_market_hours_overview(runtime: BotRuntime):
    """Return the latest market-hours overview with coercion applied."""

    market_status = (
        runtime.scan_orchestrator.market_hours_status
        or runtime.scan_orchestrator.market_hours_service.get_status()
    )
    return coerce_market_hours_overview(market_status)


def current_macro_status(runtime: BotRuntime) -> MacroContextStatus:
    """Return a bounded macro status, refreshing lazily when needed."""

    orchestrator = runtime.scan_orchestrator
    status = getattr(orchestrator, "macro_status", None)
    if status is not None and (status.last_refreshed_at is not None or status.last_error is not None):
        return status

    refresh_macro = getattr(orchestrator, "refresh_macro", None)
    if callable(refresh_macro):
        try:
            return refresh_macro(force=False)
        except TypeError:
            return refresh_macro()
        except Exception as exc:
            return MacroContextStatus(last_error=str(exc))

    return status or MacroContextStatus()


def trade_poller_status_from_scheduler(scheduler_status: SchedulerStatus) -> BackgroundTaskStatus:
    """Project scheduler-owned trade-poller state into the shared health surface."""

    job = next((item for item in scheduler_status.jobs if item.job_id == TRADE_POLLER_JOB_ID), None)
    if job is None:
        return BackgroundTaskStatus(
            name="trade_poller",
            state="FAILED",
            restart_count=0,
            started_at=scheduler_status.started_at,
            last_heartbeat_at=None,
            last_error_at=scheduler_status.started_at,
            last_error="trade poller job not registered",
        )

    if scheduler_status.state != "RUNNING":
        state = "STOPPED"
    elif job.last_error is not None:
        state = "DEGRADED"
    else:
        state = "RUNNING"
    return BackgroundTaskStatus(
        name="trade_poller",
        state=state,
        restart_count=0,
        started_at=job.last_started_at or scheduler_status.started_at,
        last_heartbeat_at=job.last_completed_at or job.last_succeeded_at,
        last_error_at=job.last_failed_at,
        last_error=job.last_error,
    )


def build_runtime_health(runtime: BotRuntime) -> RuntimeHealthStatus:
    """Build the shared runtime-health payload for non-Telegram surfaces."""

    scheduler_status = runtime.scheduler.status()
    market_hours_status = current_market_hours_overview(runtime)
    macro_status = current_macro_status(runtime)
    return runtime.task_supervisor.health_snapshot(
        scheduler_status=scheduler_status,
        poller_status=trade_poller_status_from_scheduler(scheduler_status),
        last_scan=runtime.scan_orchestrator.last_scan_status,
        calendar_status=runtime.scan_orchestrator.calendar_status,
        market_hours_status=market_hours_status,
        macro_status=macro_status,
    )


def calendar_window_bounds(
    started_at_utc: datetime,
    *,
    scope: str,
) -> tuple[datetime, datetime]:
    """Return the UTC window used by calendar views."""

    started_at_sgt = started_at_utc.astimezone(SGT)
    if scope == "today":
        next_boundary_sgt = datetime.combine(
            started_at_sgt.date() + timedelta(days=1),
            time.min,
            tzinfo=SGT,
        )
    else:
        next_boundary_sgt = datetime.combine(
            started_at_sgt.date() + timedelta(days=7),
            time.max,
            tzinfo=SGT,
        )
    return started_at_utc, next_boundary_sgt.astimezone(timezone.utc)


__all__ = [
    "SGT",
    "build_runtime_health",
    "calendar_window_bounds",
    "current_macro_status",
    "current_market_hours_overview",
    "trade_poller_status_from_scheduler",
]
