"""Shared read helpers for the local agent runtime."""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from agent.runtime import AgentRuntime
from core.models import MacroContextStatus
from data.market_hours import coerce_market_hours_overview

SGT = ZoneInfo("Asia/Singapore")


def current_market_hours_overview(runtime: AgentRuntime):
    market_status = (
        runtime.scan_orchestrator.market_hours_status
        or runtime.scan_orchestrator.market_hours_service.get_status()
    )
    return coerce_market_hours_overview(market_status)


def current_macro_status(runtime: AgentRuntime) -> MacroContextStatus:
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


def calendar_window_bounds(
    started_at_utc: datetime,
    *,
    scope: str,
) -> tuple[datetime, datetime]:
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
    "calendar_window_bounds",
    "current_macro_status",
    "current_market_hours_overview",
]
