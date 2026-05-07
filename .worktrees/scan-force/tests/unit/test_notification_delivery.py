from __future__ import annotations

import logging

import pytest

from notifications.delivery import deliver_message_blocking


class _ThreadsafeNotifier:
    def __init__(self) -> None:
        self.blocking_calls: list[tuple[int, str]] = []
        self.async_calls: list[tuple[int, str]] = []

    async def send_message(self, *, chat_id: int, text: str) -> None:
        self.async_calls.append((chat_id, text))

    def send_message_blocking(self, *, chat_id: int, text: str) -> None:
        self.blocking_calls.append((chat_id, text))


@pytest.mark.asyncio
async def test_delivery_prefers_blocking_notifier_in_async_context() -> None:
    notifier = _ThreadsafeNotifier()

    error = deliver_message_blocking(
        notifier,
        chat_id=7,
        text="ping",
        logger=logging.getLogger(__name__),
        failure_event="notification_failed",
    )

    assert error is None
    assert notifier.blocking_calls == [(7, "ping")]
    assert notifier.async_calls == []
