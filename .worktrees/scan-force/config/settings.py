"""Stage 02 settings contract for the V3 runtime."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Final, Literal
from zoneinfo import ZoneInfo

from pydantic import Field, SecretStr, field_validator
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
    "RUPTURES_PENALTY": 10.0,
    "HTF_BIAS_WEIGHT_D": 0.50,
    "HTF_BIAS_WEIGHT_H4": 0.30,
    "HTF_BIAS_WEIGHT_H1": 0.20,
    "HTF_BIAS_NEUTRAL_BAND": 0.15,
    "HTF_TRANSITION_WINDOW_D": 3,
    "HTF_TRANSITION_WINDOW_H4": 4,
    "HTF_TRANSITION_WINDOW_H1": 6,
    "SCAN_INTERVAL_MINUTES": 5,
    "POLL_INTERVAL_SECONDS": 30,
    "STREAM_INSTRUMENTS": SCAN_INSTRUMENTS,
    "MAE_MFE_MIN_PIP_MOVE": 0.5,
    "ACCOUNT_CURRENCY": "USD",
    "JOURNAL_TIMEZONE": "Asia/Singapore",
    "CALENDAR_REFRESH_HOURS": 1,
    "MACRO_REFRESH_HOURS": 1,
    "TINYDB_PATH": Path("data/bot.json"),
}

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
    ruptures_penalty: float = Field(
        default=RUNTIME_DEFAULTS["RUPTURES_PENALTY"],
        gt=0,
        validation_alias="RUPTURES_PENALTY",
    )
    htf_bias_weight_d: float = Field(
        default=RUNTIME_DEFAULTS["HTF_BIAS_WEIGHT_D"],
        gt=0,
        validation_alias="HTF_BIAS_WEIGHT_D",
    )
    htf_bias_weight_h4: float = Field(
        default=RUNTIME_DEFAULTS["HTF_BIAS_WEIGHT_H4"],
        gt=0,
        validation_alias="HTF_BIAS_WEIGHT_H4",
    )
    htf_bias_weight_h1: float = Field(
        default=RUNTIME_DEFAULTS["HTF_BIAS_WEIGHT_H1"],
        gt=0,
        validation_alias="HTF_BIAS_WEIGHT_H1",
    )
    htf_bias_neutral_band: float = Field(
        default=RUNTIME_DEFAULTS["HTF_BIAS_NEUTRAL_BAND"],
        ge=0,
        lt=1,
        validation_alias="HTF_BIAS_NEUTRAL_BAND",
    )
    htf_transition_window_d: int = Field(
        default=RUNTIME_DEFAULTS["HTF_TRANSITION_WINDOW_D"],
        gt=0,
        validation_alias="HTF_TRANSITION_WINDOW_D",
    )
    htf_transition_window_h4: int = Field(
        default=RUNTIME_DEFAULTS["HTF_TRANSITION_WINDOW_H4"],
        gt=0,
        validation_alias="HTF_TRANSITION_WINDOW_H4",
    )
    htf_transition_window_h1: int = Field(
        default=RUNTIME_DEFAULTS["HTF_TRANSITION_WINDOW_H1"],
        gt=0,
        validation_alias="HTF_TRANSITION_WINDOW_H1",
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
        default=RUNTIME_DEFAULTS["TINYDB_PATH"],
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

    @field_validator(
        "htf_bias_weight_d",
        "htf_bias_weight_h4",
        "htf_bias_weight_h1",
    )
    @classmethod
    def validate_htf_bias_weight(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("HTF bias weights must be positive.")
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
