from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

import mcp_server.main as mcp_main
from config.settings import (
    REPO_ROOT,
    REQUIRED_ENV_KEYS,
    RUNTIME_MUTABLE_KEYS,
    STARTUP_ONLY_KEYS,
    get_settings,
    load_settings,
)


def write_env_file(path: Path, **overrides: str) -> Path:
    values = {
        "OANDA_API_KEY": "api-key",
        "OANDA_ACCOUNT_ID": "account-id",
        "OANDA_ENVIRONMENT": "practice",
        "LOG_LEVEL": "INFO",
        "LOG_JSON": "false",
        "DEFAULT_CANDLE_COUNT": "500",
        "DEFAULT_SWING_LENGTH": "10",
        "ACCOUNT_CURRENCY": "USD",
        "JOURNAL_TIMEZONE": "Asia/Singapore",
        "CALENDAR_REFRESH_HOURS": "1",
        "MACRO_REFRESH_HOURS": "1",
        "TINYDB_PATH": "data/bot.json",
    }
    values.update(overrides)
    path.write_text(
        "\n".join(f"{key}={value}" for key, value in values.items()),
        encoding="utf-8",
    )
    return path


def clear_settings_env(monkeypatch: pytest.MonkeyPatch) -> None:
    keys = set(REQUIRED_ENV_KEYS) | set(STARTUP_ONLY_KEYS) | set(RUNTIME_MUTABLE_KEYS)
    for key in keys:
        monkeypatch.delenv(key, raising=False)


def test_load_settings_requires_required_env_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    clear_settings_env(monkeypatch)

    with pytest.raises(ValidationError) as excinfo:
        load_settings(env_file=None)

    missing_fields = {error["loc"][0] for error in excinfo.value.errors()}
    assert set(REQUIRED_ENV_KEYS).issubset(missing_fields)


def test_load_settings_rejects_invalid_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear_settings_env(monkeypatch)
    env_file = write_env_file(tmp_path / ".env", OANDA_ENVIRONMENT="paper")

    with pytest.raises(ValidationError) as excinfo:
        load_settings(env_file=env_file)

    assert "OANDA_ENVIRONMENT" in str(excinfo.value)


def test_load_settings_prefers_environment_variables_over_env_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear_settings_env(monkeypatch)
    env_file = write_env_file(tmp_path / ".env", OANDA_ACCOUNT_ID="from-file")
    monkeypatch.setenv("OANDA_ACCOUNT_ID", "from-env")

    settings = load_settings(env_file=env_file)

    assert settings.oanda_account_id.get_secret_value() == "from-env"
    assert settings.default_candle_count == 500


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("DEFAULT_CANDLE_COUNT", "0"),
        ("DEFAULT_SWING_LENGTH", "-1"),
        ("CALENDAR_REFRESH_HOURS", "0"),
        ("MACRO_REFRESH_HOURS", "0"),
    ],
)
def test_load_settings_rejects_non_positive_numeric_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    key: str,
    value: str,
) -> None:
    clear_settings_env(monkeypatch)
    env_file = write_env_file(tmp_path / ".env", **{key: value})

    with pytest.raises(ValidationError):
        load_settings(env_file=env_file)


def test_load_settings_rejects_nonexistent_tinydb_parent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear_settings_env(monkeypatch)
    env_file = write_env_file(tmp_path / ".env", TINYDB_PATH="missing-dir/bot.json")

    with pytest.raises(ValidationError) as excinfo:
        load_settings(env_file=env_file)

    assert "TINYDB_PATH" in str(excinfo.value)


def test_load_settings_resolves_local_tinydb_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear_settings_env(monkeypatch)
    env_file = write_env_file(tmp_path / ".env", TINYDB_PATH="data/bot.json")

    settings = load_settings(env_file=env_file)

    assert settings.tinydb_path == (REPO_ROOT / "data" / "bot.json").resolve()


def test_load_settings_prefers_explicit_absolute_tinydb_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear_settings_env(monkeypatch)
    env_file = write_env_file(
        tmp_path / ".env",
        TINYDB_PATH=str(tmp_path / "bot.json"),
    )

    settings = load_settings(env_file=env_file)

    assert settings.tinydb_path == (tmp_path / "bot.json").resolve()


def test_load_settings_uses_local_mcp_runtime_defaults(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear_settings_env(monkeypatch)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            (
                "OANDA_API_KEY=api-key",
                "OANDA_ACCOUNT_ID=account-id",
                "OANDA_ENVIRONMENT=practice",
            )
        ),
        encoding="utf-8",
    )

    settings = load_settings(env_file=env_file)

    assert settings.default_candle_count == 500
    assert settings.default_swing_length == 10
    assert settings.account_currency == "USD"
    assert settings.journal_timezone == "Asia/Singapore"
    assert settings.calendar_refresh_hours == 1
    assert settings.macro_refresh_hours == 1


def test_load_settings_normalizes_account_currency(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear_settings_env(monkeypatch)
    env_file = write_env_file(tmp_path / ".env", ACCOUNT_CURRENCY="usd")

    settings = load_settings(env_file=env_file)

    assert settings.account_currency == "USD"


def test_load_settings_rejects_invalid_account_currency(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear_settings_env(monkeypatch)
    env_file = write_env_file(tmp_path / ".env", ACCOUNT_CURRENCY="USDX")

    with pytest.raises(ValidationError) as excinfo:
        load_settings(env_file=env_file)

    assert "ACCOUNT_CURRENCY" in str(excinfo.value)


def test_runtime_mutable_key_allowlist_is_narrow() -> None:
    assert RUNTIME_MUTABLE_KEYS == {"LOG_LEVEL"}
    assert "LOG_LEVEL" not in STARTUP_ONLY_KEYS
    assert "OANDA_API_KEY" in STARTUP_ONLY_KEYS
    assert "ACCOUNT_CURRENCY" in STARTUP_ONLY_KEYS
    assert "TINYDB_PATH" in STARTUP_ONLY_KEYS


def test_mcp_main_builds_stdio_runtime_and_closes_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = {"settings": 0, "logging": 0, "build_runtime": 0, "server": 0, "run": 0, "close": 0}
    settings = object()

    def fake_get_settings() -> object:
        called["settings"] += 1
        return settings

    def fake_configure_logging(value: object) -> None:
        called["logging"] += 1
        assert value is settings

    class FakeRuntime:
        def close(self) -> None:
            called["close"] += 1

    runtime = FakeRuntime()

    def fake_build_runtime(*, settings: object) -> FakeRuntime:
        called["build_runtime"] += 1
        assert settings is settings_obj
        return runtime

    class FakeServer:
        def run(self, transport: str) -> None:
            called["run"] += 1
            assert transport == "stdio"

    def fake_build_mcp_server(*, runtime: FakeRuntime, settings: object) -> FakeServer:
        called["server"] += 1
        assert runtime is runtime_obj
        assert settings is settings_obj
        return FakeServer()

    settings_obj = settings
    runtime_obj = runtime

    monkeypatch.setattr(mcp_main, "get_settings", fake_get_settings)
    monkeypatch.setattr(mcp_main, "configure_logging", fake_configure_logging)
    monkeypatch.setattr(mcp_main, "build_runtime", fake_build_runtime)
    monkeypatch.setattr(mcp_main, "build_mcp_server", fake_build_mcp_server)
    get_settings.cache_clear()

    assert mcp_main.main() == 0
    assert called == {
        "settings": 1,
        "logging": 1,
        "build_runtime": 1,
        "server": 1,
        "run": 1,
        "close": 1,
    }
