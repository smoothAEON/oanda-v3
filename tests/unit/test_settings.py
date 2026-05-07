from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

import config.settings as settings_module
from bot.bot import main
from config.settings import (
    PORT_ENV,
    REPO_ROOT,
    REQUIRED_ENV_KEYS,
    RAILWAY_RUNTIME_ENV_KEYS,
    RAILWAY_VOLUME_MOUNT_PATH_ENV,
    RUNTIME_MUTABLE_KEYS,
    STARTUP_ONLY_KEYS,
    get_settings,
    load_settings,
)
from core.instrument_registry import SCAN_INSTRUMENTS


def write_env_file(path: Path, **overrides: str) -> Path:
    values = {
        "OANDA_API_KEY": "api-key",
        "OANDA_ACCOUNT_ID": "account-id",
        "OANDA_ENVIRONMENT": "practice",
        "TELEGRAM_BOT_TOKEN": "telegram-token",
        "TELEGRAM_CHAT_ID": "123456789",
        "TELEGRAM_BOT_PASSWORD": "bot-password",
        "TELEGRAM_ADMIN_IDS": "111,222",
        "LOG_LEVEL": "INFO",
        "LOG_JSON": "false",
        "DEFAULT_CANDLE_COUNT": "500",
        "DEFAULT_SWING_LENGTH": "10",
        "SCAN_INTERVAL_MINUTES": "5",
        "POLL_INTERVAL_SECONDS": "30",
        "STREAM_INSTRUMENTS": ",".join(SCAN_INSTRUMENTS),
        "MAE_MFE_MIN_PIP_MOVE": "0.5",
        "ACCOUNT_CURRENCY": "USD",
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
    keys.add(RAILWAY_VOLUME_MOUNT_PATH_ENV)
    keys.add(PORT_ENV)
    keys.update(RAILWAY_RUNTIME_ENV_KEYS)
    for key in keys:
        monkeypatch.delenv(key, raising=False)


def test_load_settings_requires_required_env_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear_settings_env(monkeypatch)

    with pytest.raises(ValidationError) as excinfo:
        load_settings(env_file=None)

    missing_fields = {error["loc"][0] for error in excinfo.value.errors()}
    assert {
        "OANDA_API_KEY",
        "OANDA_ACCOUNT_ID",
        "OANDA_ENVIRONMENT",
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_CHAT_ID",
        "TELEGRAM_BOT_PASSWORD",
        "TELEGRAM_ADMIN_IDS",
    }.issubset(missing_fields)


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
    assert settings.scan_interval_minutes == 5


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("DEFAULT_CANDLE_COUNT", "0"),
        ("DEFAULT_SWING_LENGTH", "-1"),
        ("SCAN_INTERVAL_MINUTES", "-5"),
        ("POLL_INTERVAL_SECONDS", "9"),
        ("MAE_MFE_MIN_PIP_MOVE", "0"),
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


def test_load_settings_parses_admin_ids_and_resolves_tinydb_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear_settings_env(monkeypatch)
    env_file = write_env_file(tmp_path / ".env", TELEGRAM_ADMIN_IDS="123, 456,789")

    settings = load_settings(env_file=env_file)

    assert settings.telegram_admin_ids == (123, 456, 789)
    assert settings.tinydb_path == (REPO_ROOT / "data" / "bot.json").resolve()


def test_load_settings_uses_railway_volume_mount_path_when_tinydb_path_is_unset(
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
                "TELEGRAM_BOT_TOKEN=telegram-token",
                "TELEGRAM_CHAT_ID=123456789",
                "TELEGRAM_BOT_PASSWORD=bot-password",
                "TELEGRAM_ADMIN_IDS=111,222",
            )
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv(RAILWAY_VOLUME_MOUNT_PATH_ENV, str(tmp_path))

    settings = load_settings(env_file=env_file)

    assert settings.tinydb_path == (tmp_path / "bot.json").resolve()


def test_load_settings_prefers_explicit_tinydb_path_over_railway_volume_mount_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear_settings_env(monkeypatch)
    explicit_parent = tmp_path / "custom-store"
    explicit_parent.mkdir()
    env_file = write_env_file(
        tmp_path / ".env",
        TINYDB_PATH=str(explicit_parent / "bot.json"),
    )
    monkeypatch.setenv(RAILWAY_VOLUME_MOUNT_PATH_ENV, str(tmp_path / "railway-volume"))

    settings = load_settings(env_file=env_file)

    assert settings.tinydb_path == (explicit_parent / "bot.json").resolve()


def test_load_settings_uses_railway_volume_when_env_file_has_local_tinydb_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear_settings_env(monkeypatch)
    env_file = write_env_file(tmp_path / ".env", TINYDB_PATH="data/bot.json")
    mount_path = tmp_path / "railway-volume"
    mount_path.mkdir()
    monkeypatch.setenv(RAILWAY_VOLUME_MOUNT_PATH_ENV, str(mount_path))

    settings = load_settings(env_file=env_file)

    assert settings.tinydb_path == (mount_path / "bot.json").resolve()


def test_load_settings_uses_railway_volume_when_env_var_has_local_tinydb_default(
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
                "TELEGRAM_BOT_TOKEN=telegram-token",
                "TELEGRAM_CHAT_ID=123456789",
                "TELEGRAM_BOT_PASSWORD=bot-password",
                "TELEGRAM_ADMIN_IDS=111,222",
            )
        ),
        encoding="utf-8",
    )
    mount_path = tmp_path / "railway-volume"
    mount_path.mkdir()
    monkeypatch.setenv(RAILWAY_VOLUME_MOUNT_PATH_ENV, str(mount_path))
    monkeypatch.setenv("TINYDB_PATH", "data/bot.json")

    settings = load_settings(env_file=env_file)

    assert settings.tinydb_path == (mount_path / "bot.json").resolve()


def test_load_settings_rejects_custom_repo_data_dir_when_railway_volume_is_attached(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear_settings_env(monkeypatch)
    env_file = write_env_file(tmp_path / ".env", TINYDB_PATH="data/custom-bot.json")
    monkeypatch.setenv(RAILWAY_VOLUME_MOUNT_PATH_ENV, str(tmp_path / "railway-volume"))

    with pytest.raises(ValidationError) as excinfo:
        load_settings(env_file=env_file)

    assert "Railway volume" in str(excinfo.value)


def test_load_settings_uses_railway_bind_host_and_port_for_mcp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear_settings_env(monkeypatch)
    env_file = write_env_file(
        tmp_path / ".env",
        MCP_HTTP_ENABLED="true",
        MCP_HTTP_HOST="127.0.0.1",
        MCP_HTTP_PORT="8001",
        MCP_HTTP_API_KEY="secret-key",
    )
    monkeypatch.setenv("RAILWAY_ENVIRONMENT", "production")
    monkeypatch.setenv(PORT_ENV, "54321")

    settings = load_settings(env_file=env_file)

    assert settings.mcp_http_host == "0.0.0.0"
    assert settings.mcp_http_port == 54321


def test_load_settings_rejects_blank_mcp_api_key_when_mcp_enabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear_settings_env(monkeypatch)
    env_file = write_env_file(
        tmp_path / ".env",
        MCP_HTTP_ENABLED="true",
        MCP_HTTP_API_KEY="   ",
    )

    with pytest.raises(ValidationError) as excinfo:
        load_settings(env_file=env_file)

    assert "MCP_HTTP_API_KEY is required" in str(excinfo.value)


def test_load_settings_uses_trade_helper_runtime_defaults(
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
                "TELEGRAM_BOT_TOKEN=telegram-token",
                "TELEGRAM_CHAT_ID=123456789",
                "TELEGRAM_BOT_PASSWORD=bot-password",
                "TELEGRAM_ADMIN_IDS=111,222",
            )
        ),
        encoding="utf-8",
    )

    settings = load_settings(env_file=env_file)

    assert settings.poll_interval_seconds == 30
    assert settings.stream_instruments == SCAN_INSTRUMENTS
    assert settings.mae_mfe_min_pip_move == 0.5
    assert settings.account_currency == "USD"
    assert settings.journal_timezone == "Asia/Singapore"
    assert settings.macro_refresh_hours == 1
    assert settings.mcp_http_host == "0.0.0.0"
    assert settings.mcp_http_port == 8080


def test_settings_schema_exposes_poll_interval_minimum() -> None:
    schema = settings_module.Settings.model_json_schema()

    assert schema["properties"]["POLL_INTERVAL_SECONDS"]["minimum"] == 10


def test_load_settings_normalizes_stream_instruments_and_account_currency(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear_settings_env(monkeypatch)
    env_file = write_env_file(
        tmp_path / ".env",
        STREAM_INSTRUMENTS="gold, eurusd, EUR_USD, gbpjpy",
        ACCOUNT_CURRENCY="usd",
    )

    settings = load_settings(env_file=env_file)

    assert settings.stream_instruments == ("XAU_USD", "EUR_USD", "GBP_JPY")
    assert settings.account_currency == "USD"


def test_load_settings_rejects_invalid_stream_instrument(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear_settings_env(monkeypatch)
    env_file = write_env_file(tmp_path / ".env", STREAM_INSTRUMENTS="EUR_USD, BTC_USD")

    with pytest.raises(ValidationError) as excinfo:
        load_settings(env_file=env_file)

    assert "Unsupported instrument" in str(excinfo.value)


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
    assert RUNTIME_MUTABLE_KEYS == {"LOG_LEVEL", "SCAN_INTERVAL_MINUTES"}
    assert "LOG_LEVEL" not in STARTUP_ONLY_KEYS
    assert "SCAN_INTERVAL_MINUTES" not in STARTUP_ONLY_KEYS
    assert "OANDA_API_KEY" in STARTUP_ONLY_KEYS
    assert "TELEGRAM_BOT_TOKEN" in STARTUP_ONLY_KEYS
    assert "POLL_INTERVAL_SECONDS" in STARTUP_ONLY_KEYS
    assert "STREAM_INSTRUMENTS" in STARTUP_ONLY_KEYS
    assert "MAE_MFE_MIN_PIP_MOVE" in STARTUP_ONLY_KEYS
    assert "ACCOUNT_CURRENCY" in STARTUP_ONLY_KEYS
    assert "JOURNAL_TIMEZONE" in STARTUP_ONLY_KEYS
    assert "MCP_HTTP_API_KEY" in STARTUP_ONLY_KEYS


def test_bot_main_loads_settings_before_returning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = {"settings": 0, "logging": 0, "build_application": 0, "run_polling": 0}
    settings = object()

    def fake_get_settings() -> object:
        called["settings"] += 1
        return settings

    def fake_configure_logging(value: object) -> None:
        called["logging"] += 1
        assert value is settings

    class FakeApplication:
        def run_polling(self) -> None:
            called["run_polling"] += 1

    def fake_build_application(*, settings: object, runtime=None):
        called["build_application"] += 1
        assert settings is settings_obj
        assert runtime is None
        return FakeApplication()

    settings_obj = settings

    monkeypatch.setattr("bot.bot.get_settings", fake_get_settings)
    monkeypatch.setattr("bot.bot.configure_logging", fake_configure_logging)
    monkeypatch.setattr("bot.bot.build_application", fake_build_application)
    get_settings.cache_clear()

    assert main() == 0
    assert called["settings"] == 1
    assert called["logging"] == 1
    assert called["build_application"] == 1
    assert called["run_polling"] == 1
