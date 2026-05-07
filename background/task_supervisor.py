"""Stage 11 background task supervisor."""

from __future__ import annotations

from core.models import BackgroundTaskStatus, RuntimeHealthStatus, SchedulerStatus
from background.stream_task import PriceStreamTask


class TaskSupervisor:
    """Own the async background runtime without wiring it into the bot yet."""

    def __init__(
        self,
        *,
        stream_task: PriceStreamTask,
    ) -> None:
        self.stream_task = stream_task

    async def start_all(self) -> None:
        await self.stream_task.start()

    async def stop_all(self) -> None:
        await self.stream_task.stop()

    def health_snapshot(
        self,
        *,
        scheduler_status: SchedulerStatus | None = None,
        poller_status: BackgroundTaskStatus | None = None,
        last_scan=None,
        calendar_status=None,
        market_hours_status=None,
        macro_status=None,
    ) -> RuntimeHealthStatus:
        tasks = list(self.stream_task.task_statuses())
        if poller_status is not None:
            tasks.append(poller_status)

        return RuntimeHealthStatus(
            scheduler=scheduler_status,
            last_scan=last_scan,
            calendar=calendar_status,
            market_hours=market_hours_status,
            macro=macro_status,
            stream=self.stream_task.stream_status(),
            queues=self.stream_task.queue_statuses(),
            tasks=tuple(tasks),
        )


__all__ = ["TaskSupervisor"]
