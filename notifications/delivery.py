"""Blocking notification delivery helpers shared across runtime services."""

from __future__ import annotations

import asyncio
from threading import Thread
from typing import Any

from core.logging_setup import log_failure
from notifications.notifier import BlockingNotifier, Notifier


def deliver_message_blocking(
    notifier: Notifier,
    *,
    chat_id: int,
    text: str,
    logger,
    failure_event: str,
    **fields: Any,
) -> Exception | None:
    """Send one notification synchronously and return any delivery error."""

    if isinstance(notifier, BlockingNotifier):
        return _deliver_via_blocking_notifier(
            notifier,
            chat_id=chat_id,
            text=text,
            logger=logger,
            failure_event=failure_event,
            **fields,
        )
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return _deliver_without_loop(
            notifier,
            chat_id=chat_id,
            text=text,
            logger=logger,
            failure_event=failure_event,
            **fields,
        )
    return _deliver_from_running_loop(
        notifier,
        chat_id=chat_id,
        text=text,
        logger=logger,
        failure_event=failure_event,
        **fields,
    )


def _deliver_via_blocking_notifier(
    notifier: BlockingNotifier,
    *,
    chat_id: int,
    text: str,
    logger,
    failure_event: str,
    **fields: Any,
) -> Exception | None:
    try:
        notifier.send_message_blocking(chat_id=chat_id, text=text)
    except Exception as exc:
        log_failure(logger, failure_event, exc, **fields)
        return exc
    return None


def _deliver_without_loop(
    notifier: Notifier,
    *,
    chat_id: int,
    text: str,
    logger,
    failure_event: str,
    **fields: Any,
) -> Exception | None:
    try:
        asyncio.run(notifier.send_message(chat_id=chat_id, text=text))
    except Exception as exc:
        log_failure(logger, failure_event, exc, **fields)
        return exc
    return None


def _deliver_from_running_loop(
    notifier: Notifier,
    *,
    chat_id: int,
    text: str,
    logger,
    failure_event: str,
    **fields: Any,
) -> Exception | None:
    result: dict[str, Exception | None] = {"error": None}

    def runner() -> None:
        try:
            asyncio.run(notifier.send_message(chat_id=chat_id, text=text))
        except Exception as exc:  # pragma: no cover - exercised through caller tests
            result["error"] = exc

    thread = Thread(target=runner, name="notification_delivery", daemon=True)
    thread.start()
    thread.join()
    if result["error"] is not None:
        log_failure(logger, failure_event, result["error"], **fields)
    return result["error"]


__all__ = ["deliver_message_blocking"]
