"""Shared command parsing and validation helpers."""

from __future__ import annotations

from datetime import date
import re

from charting.renderer import ChartRequest
from core.candle_policy import get_timeframe_delta
from core.enums import ChartMode, ChartRenderStyle, IndicatorKind
from core.instrument_registry import SCAN_INSTRUMENTS, get_instrument_spec, normalize_instrument

DEFAULT_COMMAND_INSTRUMENT = "XAU_USD"

SUPPORTED_INSTRUMENTS_HELP = "Supported: " + ", ".join(SCAN_INSTRUMENTS)
DEFAULT_COMMAND_TIMEFRAME = "H1"
DEFAULT_EXTRACTOR_TIMEFRAMES: tuple[str, ...] = ("M15", "H1", "H4", "D")
CALENDAR_SCOPES: tuple[str, ...] = ("today", "week")

TIMEFRAME_ALIASES: dict[str, str] = {
    "m1": "M1",
    "1m": "M1",
    "m5": "M5",
    "5m": "M5",
    "m15": "M15",
    "15m": "M15",
    "m30": "M30",
    "30m": "M30",
    "h1": "H1",
    "1h": "H1",
    "h4": "H4",
    "4h": "H4",
    "d": "D",
    "1d": "D",
    "day": "D",
    "daily": "D",
    "w": "W",
    "1w": "W",
    "weekly": "W",
}
TRADE_HISTORY_PERIODS: tuple[str, ...] = ("day", "week", "month", "today", "thisweek", "thismonth")
TRADE_HISTORY_VIEWS: tuple[str, ...] = ("all", "opened", "closed")
TRADE_HISTORY_INSTRUMENT_RE = re.compile(r"^[A-Z]{3,}_[A-Z]{3,}$")
TRADE_HISTORY_USAGE = (
    "Usage: /tradehistory [day|week|month|today|thisweek|thismonth|custom:YYYY-MM-DD:YYYY-MM-DD] "
    "[all|opened|closed] [INSTRUMENT] [page]"
)
TRADE_HISTORY_BACKFILL_USAGE = "Usage: /tradehistory_backfill <YYYY-MM-DD> <YYYY-MM-DD>"


def normalize_command_instrument(
    value: str | None,
    *,
    default: str = DEFAULT_COMMAND_INSTRUMENT,
) -> str:
    """Normalize and validate one supported instrument."""

    raw = default if value is None else value
    instrument = normalize_instrument(raw)
    try:
        get_instrument_spec(instrument)
    except KeyError as exc:
        raise ValueError(str(exc)) from exc
    return instrument


def normalize_command_timeframe(
    value: str | None,
    *,
    default: str = DEFAULT_COMMAND_TIMEFRAME,
) -> str:
    """Normalize and validate one supported timeframe."""

    raw = default if value is None else value
    normalized = TIMEFRAME_ALIASES.get(str(raw).strip().lower(), str(raw).strip().upper())
    if normalized == "W":
        raise ValueError("Unsupported timeframe 'W'. Supported values: ['D', 'H1', 'H4', 'M1', 'M15', 'M30', 'M5'].")
    try:
        get_timeframe_delta(normalized)
    except ValueError as exc:
        raise ValueError(str(exc)) from exc
    return normalized


def parse_chart_args(
    args: list[str],
    *,
    default_style: ChartRenderStyle,
    default_mode: ChartMode = ChartMode.BALANCED,
) -> ChartRequest:
    """Parse `/chart` args into the Stage 12 validated request contract."""

    tokens = list(args)
    if not tokens:
        raise ValueError(
            "Usage: /chart <symbol> [timeframe] [--count N] [--overlays X] [--smc X]\n\n"
            + SUPPORTED_INSTRUMENTS_HELP
        )
    instrument = normalize_command_instrument(tokens.pop(0))
    timeframe = normalize_command_timeframe(
        tokens.pop(0) if tokens and not tokens[0].strip().startswith("-") else None
    )
    payload: dict[str, object] = {
        "instrument": instrument,
        "timeframe": timeframe,
        "style": default_style,
        "mode": default_mode,
    }

    index = 0
    while index < len(tokens):
        token = tokens[index].strip().lower()
        if token in {"--count", "-n"}:
            if index + 1 >= len(tokens):
                raise ValueError(f"Flag {token} requires a value.")
            payload["count"] = int(tokens[index + 1])
            index += 2
            continue
        if token == "--mode":
            if index + 1 >= len(tokens):
                raise ValueError("Flag --mode requires a value.")
            payload["mode"] = tokens[index + 1]
            index += 2
            continue
        if token in {"--overlays", "--smc", "--trade", "--alert", "--indicator"}:
            if index + 1 >= len(tokens):
                raise ValueError(f"Flag {token} requires a value.")
            field_name = token.removeprefix("--")
            payload[field_name] = _split_selector_values(tokens[index + 1])
            index += 2
            continue
        raise ValueError(f"Unsupported chart flag '{tokens[index]}'.")

    return ChartRequest.model_validate(payload)


TRACKED_CALENDAR_CURRENCIES: tuple[str, ...] = (
    "AUD", "CAD", "CHF", "EUR", "GBP", "JPY", "NZD", "USD"
)


def parse_calendar_scope(args: list[str]) -> str:
    """Parse `/calendar` scope args."""

    if not args:
        return "today"
    if len(args) > 1:
        raise ValueError("Usage: /calendar [today|week]")

    scope = str(args[0]).strip().lower()
    if scope not in CALENDAR_SCOPES:
        raise ValueError("Usage: /calendar [today|week]")
    return scope


def parse_calendar_args(
    args: list[str],
) -> tuple[str, tuple[str, ...] | None, bool]:
    """Parse /calendar [today|week] [CURR...] [force] args.

    Returns (scope, currencies, force_refresh).
    - scope: "today" or "week"
    - currencies: tuple of 3-letter uppercase codes if provided, else None
      (None means caller should use TRACKED_CALENDAR_CURRENCIES as default)
    - force_refresh: True if "force" or "refresh" was present

    Any 3-letter alpha token not matching a scope/force keyword is treated
    as a currency code and passed through as-is. Unknown codes (e.g. "FOO")
    are accepted silently — filter_events() will return zero events for them,
    which is surfaced to the user as "No events: FOO".

    Raises ValueError with usage string for tokens that are not scope/force
    keywords and not 3-letter alpha strings.
    """
    scope = "today"
    currencies: list[str] = []
    force = False

    for token in args:
        t = token.strip().lower()
        if t in CALENDAR_SCOPES:
            scope = t
        elif t in ("force", "refresh"):
            force = True
        elif len(token.strip()) == 3 and token.strip().isalpha():
            currencies.append(token.strip().upper())
        else:
            raise ValueError(
                "Usage: /calendar [today|week] [USD EUR GBP...] [force]"
            )

    return scope, tuple(currencies) if currencies else None, force


def parse_extractor_args(
    args: list[str],
    *,
    default_count: int,
) -> tuple[tuple[str, ...], int, tuple[str, ...]]:
    """Parse `/extractor` args."""

    tokens = list(args)
    instruments: tuple[str, ...]
    if not tokens:
        raise ValueError(
            "Usage: /extractor <symbol|all> [count] [timeframes...]\n\n"
            + SUPPORTED_INSTRUMENTS_HELP
        )
    if tokens[0].strip().lower() == "all":
        tokens.pop(0)
        instruments = SCAN_INSTRUMENTS
    else:
        instruments = (normalize_command_instrument(tokens.pop(0)),)

    if tokens and tokens[0].strip().isdigit():
        count = int(tokens.pop(0))
        if count < 1 or count > 5000:
            raise ValueError("count must be between 1 and 5000.")
    else:
        count = default_count

    timeframes = (
        tuple(normalize_command_timeframe(token) for token in tokens)
        if tokens
        else DEFAULT_EXTRACTOR_TIMEFRAMES
    )
    return instruments, count, timeframes


def parse_journal_args(
    args: list[str],
) -> tuple[str | None, str | None, date | None, date | None]:
    """Parse `/journal` filter args."""

    tokens = list(args)
    trade_id: str | None = None
    instrument: str | None = None
    start_date: date | None = None
    end_date: date | None = None

    if tokens and not tokens[0].startswith("--"):
        trade_id = tokens.pop(0).strip()

    index = 0
    while index < len(tokens):
        token = tokens[index].strip().lower()
        if token == "--instrument":
            if index + 1 >= len(tokens):
                raise ValueError("Flag --instrument requires a value.")
            instrument = normalize_command_instrument(tokens[index + 1])
            index += 2
            continue
        if token == "--from":
            if index + 1 >= len(tokens):
                raise ValueError("Flag --from requires a date (YYYY-MM-DD).")
            start_date = date.fromisoformat(tokens[index + 1])
            index += 2
            continue
        if token == "--to":
            if index + 1 >= len(tokens):
                raise ValueError("Flag --to requires a date (YYYY-MM-DD).")
            end_date = date.fromisoformat(tokens[index + 1])
            index += 2
            continue
        raise ValueError(f"Unsupported journal flag '{tokens[index]}'.")

    return trade_id, instrument, start_date, end_date


def parse_indicator_alert_args(
    args: list[str],
) -> tuple[str, str, IndicatorKind, str, float | None, str | None]:
    """Parse `/indicatoralert` args."""

    if len(args) < 4:
        raise ValueError(
            "Usage: /indicatoralert <symbol> <timeframe> <indicator> <condition> [threshold] [note]"
        )

    instrument = normalize_command_instrument(args[0])
    timeframe = normalize_command_timeframe(args[1])
    try:
        indicator = IndicatorKind(str(args[2]).strip().upper())
    except ValueError as exc:
        raise ValueError("indicator must be RSI, STOCH, MACD, or SMA_CROSS.") from exc

    condition = str(args[3]).strip().lower()
    threshold: float | None = None
    note: str | None = None

    if len(args) >= 5:
        try:
            threshold = float(args[4])
            note_tokens = args[5:]
        except ValueError:
            note_tokens = args[4:]
        else:
            note = " ".join(token.strip() for token in note_tokens if token.strip()) or None
            return instrument, timeframe, indicator, condition, threshold, note

    note = " ".join(token.strip() for token in args[4:] if token.strip()) or None
    return instrument, timeframe, indicator, condition, threshold, note


def parse_price_alert_args(
    args: list[str],
) -> tuple[str, float, str, str | None]:
    """Parse ``/pricealert`` args -> (instrument, price, direction, note)."""

    if len(args) < 3:
        raise ValueError(
            "Usage: /pricealert <symbol> <price> <above|below> [note]\n\n"
            + SUPPORTED_INSTRUMENTS_HELP
        )
    instrument = normalize_command_instrument(args[0])
    try:
        price = float(args[1])
    except ValueError:
        raise ValueError("Price must be a number.") from None
    if price <= 0:
        raise ValueError("Price must be positive.")
    direction = args[2].strip().lower()
    if direction not in ("above", "below"):
        raise ValueError("Direction must be 'above' or 'below'.")
    note = " ".join(t.strip() for t in args[3:] if t.strip()) or None
    return instrument, price, direction, note


def parse_price_args(args: list[str]) -> tuple[str, bool]:
    """Parse `/price` args -> (instrument, prefer_live)."""

    if not args:
        raise ValueError("Usage: /price <symbol> [--live]\n\n" + SUPPORTED_INSTRUMENTS_HELP)

    instrument: str | None = None
    prefer_live = False
    for token in args:
        normalized = token.strip()
        if normalized.lower() == "--live":
            prefer_live = True
            continue
        if normalized.startswith("-"):
            raise ValueError("Usage: /price <symbol> [--live]\n\n" + SUPPORTED_INSTRUMENTS_HELP)
        if instrument is not None:
            raise ValueError("Usage: /price <symbol> [--live]\n\n" + SUPPORTED_INSTRUMENTS_HELP)
        instrument = normalize_command_instrument(normalized)

    if instrument is None:
        raise ValueError("Usage: /price <symbol> [--live]\n\n" + SUPPORTED_INSTRUMENTS_HELP)
    return instrument, prefer_live


def parse_time_alert_args(
    args: list[str],
) -> tuple[str, str, str | None, str | None, str | None]:
    """Parse `/timealert` args.

    Returns `(kind, schedule, local_time, session_name, note)`.
    """

    if len(args) < 2:
        raise ValueError(
            "Usage: /timealert at <HH:MM> [daily|once] [note]\n"
            "   or: /timealert session <london|newyork|market_open> [note]"
        )

    mode = args[0].strip().lower()
    if mode == "at":
        local_time = args[1].strip()
        schedule = "once"
        note_tokens = args[2:]
        if note_tokens and note_tokens[0].strip().lower() in {"daily", "once"}:
            schedule = note_tokens[0].strip().lower()
            note_tokens = note_tokens[1:]
        if schedule not in {"daily", "once"}:
            raise ValueError("Fixed time alerts support daily or once.")
        note = " ".join(token.strip() for token in note_tokens if token.strip()) or None
        return "at", schedule, local_time, None, note

    if mode == "session":
        session_name = args[1].strip().lower()
        if session_name not in {"london", "newyork", "market_open"}:
            raise ValueError("Session alerts support london, newyork, or market_open.")
        note = " ".join(token.strip() for token in args[2:] if token.strip()) or None
        return "session", "session", None, session_name, note

    raise ValueError(
        "Usage: /timealert at <HH:MM> [daily|once] [note]\n"
        "   or: /timealert session <london|newyork|market_open> [note]"
    )


def parse_tradehistory_args(
    args: list[str],
) -> tuple[str, str, str | None, int]:
    """Parse `/tradehistory` args."""

    period = "day"
    view = "all"
    instrument: str | None = None
    page = 1

    seen_period = False
    seen_view = False
    seen_instrument = False
    seen_page = False

    for token in args:
        raw = token.strip()
        if not raw:
            continue
        lowered = raw.lower()

        if lowered in TRADE_HISTORY_PERIODS or lowered.startswith("custom:"):
            if seen_period:
                raise ValueError(TRADE_HISTORY_USAGE)
            period = _parse_tradehistory_period(lowered)
            seen_period = True
            continue

        if lowered in TRADE_HISTORY_VIEWS:
            if seen_view:
                raise ValueError(TRADE_HISTORY_USAGE)
            view = lowered
            seen_view = True
            continue

        if TRADE_HISTORY_INSTRUMENT_RE.fullmatch(raw):
            if seen_instrument:
                raise ValueError(TRADE_HISTORY_USAGE)
            instrument = normalize_command_instrument(raw)
            seen_instrument = True
            continue

        if raw.isdigit():
            if seen_page:
                raise ValueError(TRADE_HISTORY_USAGE)
            page = int(raw)
            if page <= 0:
                raise ValueError(TRADE_HISTORY_USAGE)
            seen_page = True
            continue

        raise ValueError(TRADE_HISTORY_USAGE)

    return period, view, instrument, page


def parse_tradehistory_backfill_args(args: list[str]) -> tuple[date, date]:
    """Parse `/tradehistory_backfill` date args."""

    if len(args) != 2:
        raise ValueError(TRADE_HISTORY_BACKFILL_USAGE)
    try:
        start_date = date.fromisoformat(args[0])
        end_date = date.fromisoformat(args[1])
    except ValueError as exc:
        raise ValueError(TRADE_HISTORY_BACKFILL_USAGE) from exc
    if end_date < start_date:
        raise ValueError("End date must be greater than or equal to start date.")
    return start_date, end_date


def _parse_tradehistory_period(value: str) -> str:
    if value in TRADE_HISTORY_PERIODS:
        return value
    parts = value.split(":")
    if len(parts) != 3 or parts[0] != "custom":
        raise ValueError(TRADE_HISTORY_USAGE)
    try:
        start_date = date.fromisoformat(parts[1])
        end_date = date.fromisoformat(parts[2])
    except ValueError as exc:
        raise ValueError(TRADE_HISTORY_USAGE) from exc
    if end_date < start_date:
        raise ValueError(TRADE_HISTORY_USAGE)
    return value


def _split_selector_values(value: str) -> tuple[str, ...]:
    parts = []
    for raw in value.replace("|", ",").split(","):
        token = raw.strip()
        if token:
            parts.append(token)
    return tuple(parts)


__all__ = [
    "CALENDAR_SCOPES",
    "DEFAULT_COMMAND_INSTRUMENT",
    "DEFAULT_COMMAND_TIMEFRAME",
    "DEFAULT_EXTRACTOR_TIMEFRAMES",
    "SUPPORTED_INSTRUMENTS_HELP",
    "TRACKED_CALENDAR_CURRENCIES",
    "normalize_command_instrument",
    "normalize_command_timeframe",
    "parse_calendar_args",
    "parse_calendar_scope",
    "parse_chart_args",
    "parse_extractor_args",
    "parse_indicator_alert_args",
    "parse_journal_args",
    "parse_price_args",
    "parse_price_alert_args",
    "parse_tradehistory_args",
    "parse_tradehistory_backfill_args",
    "parse_time_alert_args",
]
