"""Stage 02 settings contract for the V3 runtime."""

from __future__ import annotations

from functools import lru_cache
import os
from pathlib import Path
from typing import Annotated, Final, Literal
from zoneinfo import ZoneInfo

from pydantic import AliasChoices, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from core.instrument_registry import SCAN_INSTRUMENTS, get_instrument_spec, normalize_instrument

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ENV_FILE = REPO_ROOT / ".env"

REQUIRED_ENV_KEYS: Final[tuple[str, ...]] = (
    "OANDA_API_KEY",
    "OANDA_ACCOUNT_ID",
    "OANDA_ENVIRONMENT",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
    "TELEGRAM_BOT_PASSWORD",
    "TELEGRAM_ADMIN_IDS",
)

RUNTIME_DEFAULTS: Final[dict[str, object]] = {
    "LOG_LEVEL": "INFO",
    "LOG_JSON": False,
    "DEFAULT_CANDLE_COUNT": 500,
    "DEFAULT_SWING_LENGTH": 10,
    "SCAN_INTERVAL_MINUTES": 5,
    "POLL_INTERVAL_SECONDS": 30,
    "STREAM_INSTRUMENTS": SCAN_INSTRUMENTS,
    "MAE_MFE_MIN_PIP_MOVE": 0.5,
    "ACCOUNT_CURRENCY": "USD",
    "JOURNAL_TIMEZONE": "Asia/Singapore",
    "CALENDAR_REFRESH_HOURS": 1,
    "MACRO_REFRESH_HOURS": 1,
    "TINYDB_PATH": Path("data/bot.json"),
    "MCP_HTTP_ENABLED": False,
    "MCP_HTTP_HOST": "0.0.0.0",
    "MCP_HTTP_PORT": 8080,
    "MCP_HTTP_PATH": "/mcp",
    "MCP_HTTP_API_KEY": None,
    "MCP_DEFAULT_CHAT_ID": None,
}

PORT_ENV: Final[str] = "PORT"
RAILWAY_ENVIRONMENT_ENV: Final[str] = "RAILWAY_ENVIRONMENT"
RAILWAY_PROJECT_ID_ENV: Final[str] = "RAILWAY_PROJECT_ID"
RAILWAY_SERVICE_ID_ENV: Final[str] = "RAILWAY_SERVICE_ID"
RAILWAY_VOLUME_MOUNT_PATH_ENV: Final[str] = "RAILWAY_VOLUME_MOUNT_PATH"
RAILWAY_RUNTIME_ENV_KEYS: Final[tuple[str, ...]] = (
    RAILWAY_ENVIRONMENT_ENV,
    RAILWAY_PROJECT_ID_ENV,
    RAILWAY_SERVICE_ID_ENV,
    RAILWAY_VOLUME_MOUNT_PATH_ENV,
)

RUNTIME_MUTABLE_KEYS: Final[frozenset[str]] = frozenset(
    {
        "LOG_LEVEL",
        "SCAN_INTERVAL_MINUTES",
    }
)

ALL_SETTING_KEYS: Final[frozenset[str]] = frozenset(
    REQUIRED_ENV_KEYS
    + tuple(RUNTIME_DEFAULTS)
)

STARTUP_ONLY_KEYS: Final[frozenset[str]] = ALL_SETTING_KEYS - RUNTIME_MUTABLE_KEYS

SUPPORTED_LOG_LEVELS: Final[frozenset[str]] = frozenset(
    {
        "CRITICAL",
        "ERROR",
        "WARNING",
        "INFO",
        "DEBUG",
    }
)

_ENV_FILE_SENTINEL = object()


def _default_tinydb_path() -> Path:
    mount_path = os.getenv(RAILWAY_VOLUME_MOUNT_PATH_ENV, "").strip()
    if mount_path:
        return Path(mount_path) / "bot.json"
    return Path(RUNTIME_DEFAULTS["TINYDB_PATH"])


def _local_default_tinydb_path() -> Path:
    return (REPO_ROOT / Path(RUNTIME_DEFAULTS["TINYDB_PATH"])).resolve()


def _repo_data_dir() -> Path:
    return (REPO_ROOT / "data").resolve()


def _path_is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _is_local_default_tinydb_path(value: object) -> bool:
    if value is None:
        return False
    try:
        candidate = Path(value).expanduser()
    except TypeError:
        return False
    resolved = (
        candidate.resolve()
        if candidate.is_absolute()
        else (REPO_ROOT / candidate).resolve()
    )
    return resolved == _local_default_tinydb_path()


def _railway_volume_attached() -> bool:
    return bool(os.getenv(RAILWAY_VOLUME_MOUNT_PATH_ENV, "").strip())


def _railway_runtime_detected() -> bool:
    return any(os.getenv(key, "").strip() for key in RAILWAY_RUNTIME_ENV_KEYS)


def _default_mcp_http_host() -> str:
    if _railway_runtime_detected() and os.getenv(PORT_ENV, "").strip():
        return "0.0.0.0"
    return str(RUNTIME_DEFAULTS["MCP_HTTP_HOST"])


class Settings(BaseSettings):
    """Validated runtime configuration with deterministic precedence."""

    model_config = SettingsConfigDict(
        env_file=str(DEFAULT_ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    oanda_api_key: SecretStr = Field(validation_alias="OANDA_API_KEY")
    oanda_account_id: SecretStr = Field(validation_alias="OANDA_ACCOUNT_ID")
    oanda_environment: Literal["practice", "live"] = Field(
        validation_alias="OANDA_ENVIRONMENT"
    )
    telegram_bot_token: SecretStr = Field(validation_alias="TELEGRAM_BOT_TOKEN")
    telegram_chat_id: int = Field(validation_alias="TELEGRAM_CHAT_ID")
    telegram_bot_password: SecretStr = Field(validation_alias="TELEGRAM_BOT_PASSWORD")
    telegram_admin_ids: Annotated[tuple[int, ...], NoDecode] = Field(
        validation_alias="TELEGRAM_ADMIN_IDS"
    )

    log_level: str = Field(
        default=RUNTIME_DEFAULTS["LOG_LEVEL"],
        validation_alias="LOG_LEVEL",
    )
    log_json: bool = Field(
        default=RUNTIME_DEFAULTS["LOG_JSON"],
        validation_alias="LOG_JSON",
    )
    default_candle_count: int = Field(
        default=RUNTIME_DEFAULTS["DEFAULT_CANDLE_COUNT"],
        gt=0,
        validation_alias="DEFAULT_CANDLE_COUNT",
    )
    default_swing_length: int = Field(
        default=RUNTIME_DEFAULTS["DEFAULT_SWING_LENGTH"],
        gt=0,
        validation_alias="DEFAULT_SWING_LENGTH",
    )
    scan_interval_minutes: int = Field(
        default=RUNTIME_DEFAULTS["SCAN_INTERVAL_MINUTES"],
        gt=0,
        validation_alias="SCAN_INTERVAL_MINUTES",
    )
    poll_interval_seconds: int = Field(
        default=RUNTIME_DEFAULTS["POLL_INTERVAL_SECONDS"],
        ge=10,
        validation_alias="POLL_INTERVAL_SECONDS",
    )
    stream_instruments: Annotated[tuple[str, ...], NoDecode] = Field(
        default=RUNTIME_DEFAULTS["STREAM_INSTRUMENTS"],
        validation_alias="STREAM_INSTRUMENTS",
    )
    mae_mfe_min_pip_move: float = Field(
        default=RUNTIME_DEFAULTS["MAE_MFE_MIN_PIP_MOVE"],
        gt=0,
        validation_alias="MAE_MFE_MIN_PIP_MOVE",
    )
    account_currency: str = Field(
        default=RUNTIME_DEFAULTS["ACCOUNT_CURRENCY"],
        validation_alias="ACCOUNT_CURRENCY",
    )
    journal_timezone: str = Field(
        default=RUNTIME_DEFAULTS["JOURNAL_TIMEZONE"],
        validation_alias="JOURNAL_TIMEZONE",
    )
    calendar_refresh_hours: int = Field(
        default=RUNTIME_DEFAULTS["CALENDAR_REFRESH_HOURS"],
        gt=0,
        validation_alias="CALENDAR_REFRESH_HOURS",
    )
    macro_refresh_hours: int = Field(
        default=RUNTIME_DEFAULTS["MACRO_REFRESH_HOURS"],
        gt=0,
        validation_alias="MACRO_REFRESH_HOURS",
    )
    tinydb_path: Path = Field(
        default_factory=_default_tinydb_path,
        validation_alias="TINYDB_PATH",
    )
    mcp_http_enabled: bool = Field(
        default=RUNTIME_DEFAULTS["MCP_HTTP_ENABLED"],
        validation_alias="MCP_HTTP_ENABLED",
    )
    mcp_http_host: str = Field(
        default_factory=_default_mcp_http_host,
        validation_alias="MCP_HTTP_HOST",
    )
    mcp_http_port: int = Field(
        default=RUNTIME_DEFAULTS["MCP_HTTP_PORT"],
        gt=0,
        lt=65536,
        validation_alias=AliasChoices("MCP_HTTP_PORT", "PORT"),
    )
    mcp_http_path: str = Field(
        default=RUNTIME_DEFAULTS["MCP_HTTP_PATH"],
        validation_alias="MCP_HTTP_PATH",
    )
    mcp_http_api_key: SecretStr | None = Field(
        default=RUNTIME_DEFAULTS["MCP_HTTP_API_KEY"],
        validation_alias="MCP_HTTP_API_KEY",
    )
    mcp_default_chat_id: int | None = Field(
        default=RUNTIME_DEFAULTS["MCP_DEFAULT_CHAT_ID"],
        validation_alias="MCP_DEFAULT_CHAT_ID",
    )

    @field_validator("oanda_environment", mode="before")
    @classmethod
    def normalize_environment(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().lower()
        return value

    @field_validator("log_level", mode="before")
    @classmethod
    def normalize_log_level(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().upper()
        return value

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, value: str) -> str:
        if value not in SUPPORTED_LOG_LEVELS:
            raise ValueError(
                f"LOG_LEVEL must be one of {sorted(SUPPORTED_LOG_LEVELS)}."
            )
        return value

    @field_validator("telegram_admin_ids", mode="before")
    @classmethod
    def parse_admin_ids(cls, value: object) -> object:
        if isinstance(value, str):
            parts = tuple(part.strip() for part in value.split(",") if part.strip())
            if not parts:
                raise ValueError("TELEGRAM_ADMIN_IDS must contain at least one admin ID.")
            return parts
        return value

    @field_validator("stream_instruments", mode="before")
    @classmethod
    def parse_stream_instruments(cls, value: object) -> object:
        if isinstance(value, str):
            parts = [part.strip() for part in value.split(",") if part.strip()]
            if not parts:
                raise ValueError("STREAM_INSTRUMENTS must contain at least one instrument.")
            return parts
        return value

    @field_validator("telegram_admin_ids")
    @classmethod
    def validate_admin_ids(cls, value: tuple[int, ...]) -> tuple[int, ...]:
        if not value:
            raise ValueError("TELEGRAM_ADMIN_IDS must contain at least one admin ID.")
        if any(admin_id <= 0 for admin_id in value):
            raise ValueError("TELEGRAM_ADMIN_IDS must contain positive integer IDs.")
        return value

    @field_validator("poll_interval_seconds")
    @classmethod
    def validate_poll_interval_seconds(cls, value: int) -> int:
        if value < 10:
            raise ValueError("POLL_INTERVAL_SECONDS must be greater than or equal to 10.")
        return value

    @field_validator("stream_instruments")
    @classmethod
    def validate_stream_instruments(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            raise ValueError("STREAM_INSTRUMENTS must contain at least one instrument.")

        normalized: list[str] = []
        seen: set[str] = set()
        for raw in value:
            instrument = normalize_instrument(str(raw))
            try:
                get_instrument_spec(instrument)
            except KeyError as exc:
                raise ValueError(str(exc)) from exc
            if instrument in seen:
                continue
            seen.add(instrument)
            normalized.append(instrument)
        return tuple(normalized)

    @field_validator("account_currency", mode="before")
    @classmethod
    def normalize_account_currency(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().upper()
        return value

    @field_validator("account_currency")
    @classmethod
    def validate_account_currency(cls, value: str) -> str:
        if len(value) != 3 or not value.isalpha():
            raise ValueError("ACCOUNT_CURRENCY must be a 3-letter currency code.")
        return value

    @field_validator("journal_timezone", mode="before")
    @classmethod
    def normalize_journal_timezone(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("journal_timezone")
    @classmethod
    def validate_journal_timezone(cls, value: str) -> str:
        if not value:
            raise ValueError("JOURNAL_TIMEZONE must be a non-empty timezone name.")
        try:
            ZoneInfo(value)
        except Exception as exc:
            raise ValueError(f"Unsupported JOURNAL_TIMEZONE {value!r}.") from exc
        return value

    @field_validator("tinydb_path")
    @classmethod
    def resolve_tinydb_path(cls, value: Path) -> Path:
        candidate = value.expanduser()
        resolved = (
            candidate.resolve()
            if candidate.is_absolute()
            else (REPO_ROOT / candidate).resolve()
        )

        if _railway_volume_attached() and _path_is_relative_to(
            resolved.parent, _repo_data_dir()
        ):
            raise ValueError(
                "TINYDB_PATH must not resolve inside the repo data package when a Railway "
                "volume is attached. Mount the Railway volume to /data and set "
                "TINYDB_PATH=/data/bot.json, or leave TINYDB_PATH unset so "
                "RAILWAY_VOLUME_MOUNT_PATH is used automatically."
            )

        if not resolved.parent.exists() or not resolved.parent.is_dir():
            raise ValueError(
                "TINYDB_PATH must resolve inside an existing parent directory."
            )

        return resolved

    @field_validator("tinydb_path", mode="before")
    @classmethod
    def prefer_railway_volume_for_local_tinydb_default(cls, value: object) -> object:
        if _railway_volume_attached() and _is_local_default_tinydb_path(value):
            return _default_tinydb_path()
        return value

    @field_validator("mcp_http_host")
    @classmethod
    def validate_mcp_http_host(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("MCP_HTTP_HOST must be a non-empty string.")
        if _railway_runtime_detected() and os.getenv(PORT_ENV, "").strip():
            if normalized in {"127.0.0.1", "localhost", "::1"}:
                return "0.0.0.0"
        return normalized

    @field_validator("mcp_http_port", mode="before")
    @classmethod
    def prefer_railway_port(cls, value: object) -> object:
        railway_port = os.getenv(PORT_ENV, "").strip()
        if _railway_runtime_detected() and railway_port:
            return railway_port
        return value

    @field_validator("mcp_http_path")
    @classmethod
    def validate_mcp_http_path(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized.startswith("/"):
            raise ValueError("MCP_HTTP_PATH must start with '/'.")
        return normalized.rstrip("/") or "/"

    @field_validator("mcp_http_api_key", mode="before")
    @classmethod
    def normalize_mcp_http_api_key(cls, value: object) -> object:
        if isinstance(value, SecretStr):
            value = value.get_secret_value()
        if isinstance(value, str):
            normalized = value.strip()
            return normalized or None
        return value

    @field_validator("mcp_default_chat_id")
    @classmethod
    def validate_mcp_default_chat_id(cls, value: int | None) -> int | None:
        if value is not None and value == 0:
            raise ValueError("MCP_DEFAULT_CHAT_ID must be non-zero when provided.")
        return value

    @model_validator(mode="after")
    def validate_mcp_settings(self) -> "Settings":
        if self.mcp_http_enabled and self.mcp_http_api_key is None:
            raise ValueError("MCP_HTTP_API_KEY is required when MCP_HTTP_ENABLED is true.")
        return self


def load_settings(*, env_file: Path | str | None | object = _ENV_FILE_SENTINEL) -> Settings:
    """Build a fresh settings instance.

    Defaults in code are overridden by the repo-root `.env`, which is then
    overridden by explicit process environment variables. When `TINYDB_PATH`
    is unset and Railway provides `RAILWAY_VOLUME_MOUNT_PATH`, persistence
    defaults to `<mount-path>/bot.json`.
    """

    init_kwargs: dict[str, object] = {}

    if env_file is not _ENV_FILE_SENTINEL:
        init_kwargs["_env_file"] = env_file

    return Settings(**init_kwargs)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide cached settings instance."""

    return load_settings()


__all__ = [
    "ALL_SETTING_KEYS",
    "DEFAULT_ENV_FILE",
    "PORT_ENV",
    "REPO_ROOT",
    "REQUIRED_ENV_KEYS",
    "RAILWAY_ENVIRONMENT_ENV",
    "RAILWAY_PROJECT_ID_ENV",
    "RAILWAY_RUNTIME_ENV_KEYS",
    "RAILWAY_SERVICE_ID_ENV",
    "RAILWAY_VOLUME_MOUNT_PATH_ENV",
    "RUNTIME_DEFAULTS",
    "RUNTIME_MUTABLE_KEYS",
    "STARTUP_ONLY_KEYS",
    "SUPPORTED_LOG_LEVELS",
    "Settings",
    "get_settings",
    "load_settings",
]
