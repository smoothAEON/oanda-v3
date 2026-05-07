"""Protocol-only notifier contracts."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


class Notifier(Protocol):
    """Minimal async contract for background notifications."""

    async def send_message(self, *, chat_id: int, text: str) -> None:
        """Send a text notification to a chat."""


@runtime_checkable
class BlockingNotifier(Protocol):
    """Optional sync contract for thread-safe blocking delivery."""

    def send_message_blocking(self, *, chat_id: int, text: str) -> None:
        """Send a text notification from a non-async caller."""


__all__ = ["BlockingNotifier", "Notifier"]
