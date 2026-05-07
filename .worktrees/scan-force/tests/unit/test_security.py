from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from freezegun import freeze_time

from bot.security_manager import SecurityManager
from config.settings import Settings, load_settings
from data.persistence.trade_store import PersistenceWriteError, TradeStore


def write_env_file(path: Path, **overrides: str) -> Path:
    values = {
        "OANDA_API_KEY": "api-key",
        "OANDA_ACCOUNT_ID": "account-id",
        "OANDA_ENVIRONMENT": "practice",
        "TELEGRAM_BOT_TOKEN": "telegram-token",
        "TELEGRAM_CHAT_ID": "123456789",
        "TELEGRAM_BOT_PASSWORD": "bot-password",
        "TELEGRAM_ADMIN_IDS": "111,222",
        "TINYDB_PATH": str(path.parent / "bot.json"),
    }
    values.update(overrides)
    path.write_text(
        "\n".join(f"{key}={value}" for key, value in values.items()),
        encoding="utf-8",
    )
    return path


def build_settings(tmp_path: Path, **overrides: str) -> Settings:
    return load_settings(env_file=write_env_file(tmp_path / ".env", **overrides))


@pytest.fixture()
def store(tmp_path: Path) -> TradeStore:
    trade_store = TradeStore(db_path=tmp_path / "security.json")
    yield trade_store
    trade_store.close()


def test_password_auth_persists_a_session_and_marks_admin(
    tmp_path: Path,
    store: TradeStore,
) -> None:
    settings = build_settings(tmp_path)
    manager = SecurityManager(store=store, settings=settings)

    session = manager.authenticate(
        user_id=111,
        chat_id=222,
        password="bot-password",
        username="alice",
        first_name="Alice",
    )

    assert session is not None
    assert session.user_id == 111
    assert session.chat_id == 222
    assert session.is_admin is True
    assert session.username == "alice"
    assert session.first_name == "Alice"
    assert session.last_activity_at >= session.authenticated_at
    assert manager.is_authenticated(111) is True
    assert manager.get_session(111) == session


def test_password_auth_rejects_bad_password_without_creating_session(
    tmp_path: Path,
    store: TradeStore,
) -> None:
    settings = build_settings(tmp_path)
    manager = SecurityManager(store=store, settings=settings)

    session = manager.authenticate(
        user_id=333,
        chat_id=444,
        password="wrong-password",
    )

    assert session is None
    assert manager.is_authenticated(333) is False
    assert manager.get_session(333) is None


def test_logout_invalidates_existing_session(
    tmp_path: Path,
    store: TradeStore,
) -> None:
    settings = build_settings(tmp_path)
    manager = SecurityManager(store=store, settings=settings)
    created = manager.authenticate(
        user_id=555,
        chat_id=666,
        password="bot-password",
    )
    assert created is not None

    removed = manager.logout(555)

    assert removed == created
    assert manager.get_session(555) is None
    assert manager.is_authenticated(555) is False


def test_touch_refreshes_last_activity_without_changing_authentication_time(
    tmp_path: Path,
    store: TradeStore,
) -> None:
    settings = build_settings(tmp_path)
    manager = SecurityManager(store=store, settings=settings)
    created = manager.authenticate(
        user_id=777,
        chat_id=888,
        password="bot-password",
    )
    assert created is not None

    before = created.last_activity_at
    touched = manager.touch(777)

    assert touched is not None
    assert touched.authenticated_at == created.authenticated_at
    assert touched.last_activity_at >= before


def test_chat_scoped_session_lookup_and_touch_require_matching_chat(
    tmp_path: Path,
    store: TradeStore,
) -> None:
    settings = build_settings(tmp_path)
    manager = SecurityManager(store=store, settings=settings)
    created = manager.authenticate(
        user_id=777,
        chat_id=888,
        password="bot-password",
    )
    assert created is not None

    assert manager.get_session_for_chat(777, 888) == created
    assert manager.get_session_for_chat(777, 999) is None
    assert manager.touch_for_chat(777, 999) is None

    touched = manager.touch_for_chat(777, 888)

    assert touched is not None
    assert touched.chat_id == 888
    assert touched.last_activity_at >= created.last_activity_at


def test_authenticate_raises_when_session_persistence_is_unavailable(
    tmp_path: Path,
    store: TradeStore,
) -> None:
    settings = build_settings(tmp_path)
    manager = SecurityManager(store=store, settings=settings)
    store.db = None

    with pytest.raises(PersistenceWriteError):
        manager.authenticate(
            user_id=111,
            chat_id=222,
            password="bot-password",
        )


def test_admin_detection_uses_configured_ids(tmp_path: Path) -> None:
    settings = build_settings(tmp_path, TELEGRAM_ADMIN_IDS="111, 999")
    manager = SecurityManager(store=TradeStore(db_path=tmp_path / "admins.json"), settings=settings)

    try:
        assert manager.is_admin(111) is True
        assert manager.is_admin(999) is True
        assert manager.is_admin(222) is False
    finally:
        manager.store.close()


@freeze_time("2026-03-21T08:00:00Z")
def test_session_persists_without_expiry_after_long_inactivity(
    tmp_path: Path,
    store: TradeStore,
) -> None:
    settings = build_settings(tmp_path)
    manager = SecurityManager(store=store, settings=settings)
    session = manager.authenticate(
        user_id=111,
        chat_id=222,
        password="bot-password",
    )

    with freeze_time("2026-04-20T08:00:00Z"):
        persisted = manager.get_session(111)

    assert session is not None
    assert persisted is not None
    assert persisted.user_id == 111


def test_password_rotation_does_not_invalidate_existing_sessions(
    tmp_path: Path,
    store: TradeStore,
) -> None:
    initial_settings = build_settings(tmp_path, TELEGRAM_BOT_PASSWORD="old-password")
    manager = SecurityManager(store=store, settings=initial_settings)
    created = manager.authenticate(
        user_id=111,
        chat_id=222,
        password="old-password",
    )

    rotated_settings = build_settings(tmp_path, TELEGRAM_BOT_PASSWORD="new-password")
    rotated_manager = SecurityManager(store=store, settings=rotated_settings)

    assert created is not None
    assert rotated_manager.get_session_for_chat(111, 222) is not None
