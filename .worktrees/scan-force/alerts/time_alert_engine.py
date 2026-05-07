"""Scheduled time-alert evaluation for chat-scoped reminders."""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from alerts.alert_repository import AlertRepository
from bot.runtime_config import RuntimeConfigManager
from core.logging_setup import get_logger
from core.models import TimeAlert
from notifications.delivery import deliver_message_blocking
from notifications.notifier import Notifier

DEFAULT_TIME_ALERT_TIMEZONE = "Asia/Singapore"
SESSION_OPEN_SCHEDULE: dict[str, dict[str, int | str]] = {
    "london": {"kind": "daily", "hour": 8, "minute": 0},
    "newyork": {"kind": "daily", "hour": 13, "minute": 0},
    "market_open": {"kind": "weekly", "weekday": 6, "hour": 22, "minute": 0},
}


def next_fixed_time_fire_at(
    local_time_text: str,
    *,
    now_utc: datetime | None = None,
    timezone_name: str = DEFAULT_TIME_ALERT_TIMEZONE,
) -> datetime:
    """Return the next UTC fire time for one fixed HH:MM local reminder."""

    now = now_utc or datetime.now(timezone.utc)
    zone = ZoneInfo(timezone_name)
    hours, minutes = _parse_hhmm(local_time_text)
    local_now = now.astimezone(zone)
    candidate = datetime.combine(
        local_now.date(),
        time(hour=hours, minute=minutes),
        tzinfo=zone,
    )
    if candidate <= local_now:
        candidate = candidate + timedelta(days=1)
    return candidate.astimezone(timezone.utc)


def next_session_fire_at(
    session_name: str,
    *,
    now_utc: datetime | None = None,
) -> datetime:
    """Return the next UTC fire time for one named session-open reminder."""

    now = now_utc or datetime.now(timezone.utc)
    schedule = SESSION_OPEN_SCHEDULE[session_name]
    hour = int(schedule["hour"])
    minute = int(schedule["minute"])
    candidate = datetime.combine(now.date(), time(hour=hour, minute=minute), tzinfo=timezone.utc)
    if schedule["kind"] == "daily":
        if candidate <= now:
            candidate = candidate + timedelta(days=1)
        return candidate

    weekday = int(schedule["weekday"])
    days_ahead = (weekday - now.weekday()) % 7
    if days_ahead == 0 and candidate <= now:
        days_ahead = 7
    return candidate + timedelta(days=days_ahead)


def next_time_alert_fire_at(
    alert: TimeAlert,
    *,
    now_utc: datetime | None = None,
) -> datetime | None:
    """Return the next UTC fire time after one reminder dispatch."""

    now = now_utc or datetime.now(timezone.utc)
    if alert.schedule == "once":
        return None
    if alert.schedule == "daily":
        assert alert.local_time is not None
        return next_fixed_time_fire_at(
            alert.local_time,
            now_utc=now + timedelta(seconds=1),
            timezone_name=alert.timezone_name,
        )
    assert alert.session_name is not None
    return next_session_fire_at(alert.session_name, now_utc=now + timedelta(seconds=1))


class TimeAlertEngine:
    """Evaluate due reminders and dispatch Telegram notifications."""

    def __init__(
        self,
        alert_repository: AlertRepository,
        *,
        runtime_config_manager: RuntimeConfigManager | None = None,
        notifier: Notifier | None = None,
    ) -> None:
        self.alert_repository = alert_repository
        self.runtime_config_manager = runtime_config_manager
        self.notifier = notifier
        self.logger = get_logger(__name__)

    def evaluate_due_alerts(self, *, now_utc: datetime | None = None) -> list[TimeAlert]:
        """Evaluate and advance all due reminders."""

        now = now_utc or datetime.now(timezone.utc)
        fired: list[TimeAlert] = []
        for alert in self.alert_repository.list_active_time_alerts():
            if alert.next_fire_at is None or alert.next_fire_at > now:
                continue
            if alert.kind.value == "SESSION" and not self._session_alerts_enabled():
                continue

            next_fire_at = next_time_alert_fire_at(alert, now_utc=now)
            if self.notifier is not None:
                error = deliver_message_blocking(
                    self.notifier,
                    chat_id=alert.chat_id,
                    text=_build_time_alert_message(alert, fired_at=now),
                    logger=self.logger,
                    failure_event="time_alert_notification_failed",
                    alert_id=alert.id,
                )
                if error is not None:
                    continue

            updated = self.alert_repository.mark_time_alert_triggered(
                alert.id,
                next_fire_at=next_fire_at,
                fired_at=now,
            )
            if updated is None:
                continue
            fired.append(updated)
        return fired

    def _session_alerts_enabled(self) -> bool:
        if self.runtime_config_manager is None:
            return True
        return self.runtime_config_manager.session_alerts_enabled()

def _build_time_alert_message(alert: TimeAlert, *, fired_at: datetime) -> str:
    if alert.session_name is not None:
        label = {
            "london": "London session open",
            "newyork": "New York session open",
            "market_open": "Weekly market open",
        }[alert.session_name]
        lines = [
            "Time Alert",
            label,
            f"Time: {fired_at.strftime('%Y-%m-%d %H:%M:%S UTC')}",
        ]
    else:
        zone = ZoneInfo(alert.timezone_name)
        local_time_text = fired_at.astimezone(zone).strftime("%Y-%m-%d %H:%M")
        lines = [
            "Time Alert",
            f"{local_time_text} {alert.timezone_name}",
        ]
    if alert.note:
        lines.append(f"Note: {alert.note}")
    return "\n".join(lines)


def _parse_hhmm(value: str) -> tuple[int, int]:
    try:
        hours_text, minutes_text = value.split(":", maxsplit=1)
        hours = int(hours_text)
        minutes = int(minutes_text)
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError("Time must use HH:MM in 24-hour format.") from exc
    if not 0 <= hours <= 23 or not 0 <= minutes <= 59:
        raise ValueError("Time must use HH:MM in 24-hour format.")
    return hours, minutes


__all__ = [
    "DEFAULT_TIME_ALERT_TIMEZONE",
    "SESSION_OPEN_SCHEDULE",
    "TimeAlertEngine",
    "next_fixed_time_fire_at",
    "next_session_fire_at",
    "next_time_alert_fire_at",
]
