"""Persistent Telegram auth and session helpers."""

from __future__ import annotations

from datetime import datetime, timezone

from config.settings import Settings, get_settings
from core.models import BotSessionRecord
from data.persistence.trade_store import TradeStore


class SecurityManager:
    """Persist and validate one active session per Telegram user."""

    def __init__(
        self,
        *,
        store: TradeStore | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.store = store or TradeStore(settings=self.settings)

    def authenticate(
        self,
        *,
        user_id: int,
        chat_id: int,
        password: str,
        username: str | None = None,
        first_name: str | None = None,
    ) -> BotSessionRecord | None:
        """Authenticate a Telegram user and persist their session."""

        if password != self.settings.telegram_bot_password.get_secret_value():
            return None

        now = datetime.now(timezone.utc)
        session = BotSessionRecord(
            user_id=user_id,
            chat_id=chat_id,
            is_admin=self.is_admin(user_id),
            username=username,
            first_name=first_name,
            authenticated_at=now,
            last_activity_at=now,
        )
        return self.store.upsert_session(session)

    def is_admin(self, user_id: int) -> bool:
        """Return True when the Telegram user is configured as an admin."""

        return int(user_id) in set(self.settings.telegram_admin_ids)

    def get_session(self, user_id: int) -> BotSessionRecord | None:
        """Return the user's active session, if present."""

        return self.store.get_session(int(user_id))

    def get_session_for_chat(self, user_id: int, chat_id: int) -> BotSessionRecord | None:
        """Return the user's active session when it belongs to the current chat."""

        session = self.get_session(int(user_id))
        if session is None or session.chat_id != int(chat_id):
            return None
        return session

    def list_sessions(self) -> list[BotSessionRecord]:
        """Return all active sessions."""

        return self.store.list_sessions()

    def is_authenticated(self, user_id: int) -> bool:
        """Return True when the user has an active session."""

        return self.get_session(int(user_id)) is not None

    def touch(self, user_id: int) -> BotSessionRecord | None:
        """Refresh session activity for an authenticated user."""

        existing = self.get_session(int(user_id))
        if existing is None:
            return None

        refreshed = existing.model_copy(
            update={"last_activity_at": datetime.now(timezone.utc)}
        )
        return self.store.upsert_session(refreshed)

    def touch_for_chat(self, user_id: int, chat_id: int) -> BotSessionRecord | None:
        """Refresh session activity when the session belongs to the current chat."""

        existing = self.get_session_for_chat(int(user_id), int(chat_id))
        if existing is None:
            return None

        refreshed = existing.model_copy(
            update={"last_activity_at": datetime.now(timezone.utc)}
        )
        return self.store.upsert_session(refreshed)

    def logout(self, user_id: int) -> BotSessionRecord | None:
        """Delete the user's active session and return the removed record."""

        return self.store.delete_session(int(user_id))


__all__ = ["SecurityManager"]
