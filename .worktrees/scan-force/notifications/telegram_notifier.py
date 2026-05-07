"""Concrete Telegram notifier implementation."""

from __future__ import annotations

import asyncio
from threading import get_ident

from telegram import Bot


class TelegramNotifier:
    """Send background notifications through a PTB bot instance."""

    def __init__(self, bot: Bot) -> None:
        self.bot = bot
        self._loop = asyncio.get_running_loop()
        self._loop_thread_id = get_ident()

    async def send_message(self, *, chat_id: int, text: str) -> None:
        await self.bot.send_message(chat_id=chat_id, text=text)

    def send_message_blocking(self, *, chat_id: int, text: str) -> None:
        if get_ident() == self._loop_thread_id:
            raise RuntimeError(
                "TelegramNotifier.send_message_blocking cannot be called from the PTB event loop thread."
            )
        future = asyncio.run_coroutine_threadsafe(
            self.send_message(chat_id=chat_id, text=text),
            self._loop,
        )
        future.result()


__all__ = ["TelegramNotifier"]
