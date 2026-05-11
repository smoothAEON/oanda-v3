"""Stage 02 settings contract for the V3 runtime."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Final, Literal
from zoneinfo import ZoneInfo

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ENV_FILE = REPO_ROOT / ".env"

REQUIRED_ENV_KEYS: Final[tuple[str, ...]] = (
    "OANDA_API_KEY",
    "OANDA_ACCOUNT_ID",
    "OANDA_ENVIRONMENT",
)

RUNTIME_DEFAULTS: Final[dict[str, object]] = {
    "LOG_LEVEL": "INFO",
    "LOG_JSON": False,
    "DEFAULT_CANDLE_COUNT": 500,
    "DEFAULT_SWING_LENGTH": 10,
    "ACCOUNT_CURRENCY": "USD",
    "JOURNAL_TIMEZONE": "Asia/Singapore",
    "CALENDAR_REFRESH_HOURS": 1,
    "MACRO_REFRESH_HOURS": 1,
    "TINYDB_PATH": Path("data/bot.json"),
}

RUNTIME_MUTABLE_KEYS: Final[frozenset[str]] = frozenset(
    {
        "LOG_LEVEL",
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
    return Path(RUNTIME_DEFAULTS["TINYDB_PATH"])


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

        if not resolved.parent.exists() or not resolved.parent.is_dir():
            raise ValueError(
                "TINYDB_PATH must resolve inside an existing parent directory."
            )

        return resolved


def load_settings(*, env_file: Path | str | None | object = _ENV_FILE_SENTINEL) -> Settings:
    """Build a fresh settings instance.

    Defaults in code are overridden by the repo-root `.env`, which is then
    overridden by explicit process environment variables.
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
    "REPO_ROOT",
    "REQUIRED_ENV_KEYS",
    "RUNTIME_DEFAULTS",
    "RUNTIME_MUTABLE_KEYS",
    "STARTUP_ONLY_KEYS",
    "SUPPORTED_LOG_LEVELS",
    "Settings",
    "get_settings",
    "load_settings",
]
