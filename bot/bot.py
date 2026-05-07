"""Stage 13 Telegram runtime and command handlers."""

from __future__ import annotations

import asyncio
import json
import signal
from datetime import date, datetime, time, timedelta, timezone
from io import BytesIO
from zoneinfo import ZoneInfo

from pydantic import ValidationError
from telegram import Message, Update
from telegram.error import Conflict, TelegramError
from telegram.ext import Application, CommandHandler, ContextTypes
import uvicorn

from alerts.alert_repository import AlertRepository
from alerts.time_alert_engine import (
    DEFAULT_TIME_ALERT_TIMEZONE,
    next_fixed_alert_fire_at,
    next_session_fire_at,
)
from bot.formatting import (
    format_calendar_output,
    format_day_range,
    format_journal_detail,
    format_journal_list,
    format_maemfe_detail,
    format_maemfe_list,
    format_orders_grouped,
    format_positions,
    format_price_alert_list,
    format_previous_day_level,
    format_sessions,
    format_time_alert_list,
    format_trade_history_page,
    format_vwap_summary,
)
from bot.pricing import DEFAULT_LIVE_PRICE_MAX_AGE_SECONDS, resolve_price_quote
from bot.parsing import (
    DEFAULT_COMMAND_INSTRUMENT,
    DEFAULT_COMMAND_TIMEFRAME,
    ORDER_BLOCK_USAGE,
    SUPPORTED_INSTRUMENTS_HELP,
    TRACKED_CALENDAR_CURRENCIES,
    OrderBlockMitigationFilter,
    normalize_command_instrument,
    normalize_command_timeframe,
    parse_calendar_args,
    parse_chart_args,
    parse_extractor_args,
    parse_indicator_alert_args,
    parse_journal_args,
    parse_order_block_args,
    parse_price_args,
    parse_price_alert_args,
    parse_tradehistory_args,
    parse_tradehistory_backfill_args,
    parse_time_alert_args,
    parse_vwap_args,
)
from bot.runtime import (
    ACCOUNT_CLIENT_KEY,
    ALERT_REPOSITORY_KEY,
    BOT_RUNTIME_KEY,
    CHART_RENDERER_KEY,
    EXCURSION_REPOSITORY_KEY,
    MARKET_DATA_PROVIDER_KEY,
    MARKET_STATE_KEY,
    RUNTIME_CONFIG_MANAGER_KEY,
    SCAN_ORCHESTRATOR_KEY,
    SCHEDULER_KEY,
    SECURITY_MANAGER_KEY,
    SETTINGS_KEY,
    STARTED_AT_KEY,
    TASK_SUPERVISOR_KEY,
    TRADE_HISTORY_SERVICE_KEY,
    TRADE_REPOSITORY_KEY,
    BotRuntime,
    build_runtime,
)
from bot.runtime_config import RuntimeConfigManager
from bot.security_manager import SecurityManager
from config.settings import Settings, get_settings
from core.enums import AlertStatus, ChartMode, IndicatorKind, RuntimeConfigKey, TimeAlertKind, TradeState
from core.instrument_registry import SCAN_INSTRUMENTS
from core.logging_setup import configure_logging, get_logger, log_failure
from core.models import (
    BackgroundTaskStatus,
    MacroContextStatus,
    MacroIndicatorStatus,
    OrderBlockSummary,
    TimeAlert,
    TimeAlertDefinition,
    TimeAlertExportDocument,
    TimeframeSnapshot,
    TradeRecord,
    is_time_alert_local_datetime_text,
)
from data.market_hours import coerce_market_hours_overview
from data.persistence.trade_store import PersistenceWriteError
from indicators import build_vwap_read_result, resolve_vwap_candle_count
from journal.excursion_repository import ExcursionRepository
from journal.mae_mfe_service import MaeMfeService
from journal.trade_repository import TradeRepository
from orchestration.scheduler import TRADE_POLLER_JOB_ID

AUTH_REQUIRED_TEXT = "Authenticate first with /start <password>."
CHAT_SCOPE_TEXT = "This session is bound to a different chat. Authenticate here with /start <password>."
SGT = ZoneInfo("Asia/Singapore")
LOGGER = get_logger(__name__)
LIVE_PRICE_MAX_AGE_SECONDS = DEFAULT_LIVE_PRICE_MAX_AGE_SECONDS
POLLING_CONFLICT_ACTIVE_KEY = "_telegram_polling_conflict_active"
TELEGRAM_TEXT_MESSAGE_LIMIT = 4096
IMPORT_TIME_ALERTS_USAGE = "Usage: /importtimealerts (reply to a JSON export file)"

COMMAND_REGISTRY: tuple[str, ...] = (
    "start",
    "help",
    "logout",
    "status",
    "marketstatus",
    "price",
    "account",
    "positions",
    "orders",
    "session",
    "dayrange",
    "pdh",
    "pdl",
    "calendar",
    "scan",
    "smc",
    "structure",
    "indicators",
    "vwap",
    "ob",
    "chart",
    "extractor",
    "config",
    "journal",
    "tradehistory",
    "label",
    "maemfe",
    "indicatoralert",
    "listindicators",
    "clearindicator",
    "pricealert",
    "listpricealerts",
    "clearpricealert",
    "timealert",
    "listtimealerts",
    "cleartimealert",
    "exporttimealerts",
    "importtimealerts",
    "tradehistory_backfill",
)


def build_application(
    *,
    settings: Settings | None = None,
    runtime: BotRuntime | None = None,
) -> Application:
    """Build the PTB application with injected runtime services."""

    resolved_settings = settings or get_settings()
    resolved_runtime = runtime or build_runtime(settings=resolved_settings)
    application = (
        Application.builder()
        .token(resolved_settings.telegram_bot_token.get_secret_value())
        .post_init(_on_startup)
        .post_shutdown(_on_shutdown)
        .build()
    )
    application.bot_data.update(resolved_runtime.bot_data())
    register_handlers(application)
    application.add_error_handler(_handle_application_error)
    return application


def register_handlers(application: Application) -> None:
    """Register the full Stage 13 command surface."""

    handlers = (
        ("start", start_command),
        ("help", help_command),
        ("logout", logout_command),
        ("status", status_command),
        ("marketstatus", marketstatus_command),
        ("price", price_command),
        ("account", account_command),
        ("positions", positions_command),
        ("orders", orders_command),
        ("session", session_command),
        ("dayrange", dayrange_command),
        ("pdh", pdh_command),
        ("pdl", pdl_command),
        ("calendar", calendar_command),
        ("scan", scan_command),
        ("smc", smc_command),
        ("structure", structure_command),
        ("indicators", indicators_command),
        ("vwap", vwap_command),
        ("ob", order_blocks_command),
        ("chart", chart_command),
        ("extractor", extractor_command),
        ("config", config_command),
        ("journal", journal_command),
        ("tradehistory", tradehistory_command),
        ("label", label_command),
        ("maemfe", maemfe_command),
        ("indicatoralert", indicator_alert_command),
        ("listindicators", list_indicator_alerts_command),
        ("clearindicator", clear_indicator_alert_command),
        ("pricealert", price_alert_command),
        ("listpricealerts", list_price_alerts_command),
        ("clearpricealert", clear_price_alert_command),
        ("timealert", time_alert_command),
        ("listtimealerts", list_time_alerts_command),
        ("cleartimealert", clear_time_alert_command),
        ("exporttimealerts", export_time_alerts_command),
        ("importtimealerts", import_time_alerts_command),
        ("tradehistory_backfill", tradehistory_backfill_command),
    )
    for command_name, handler in handlers:
        application.add_handler(CommandHandler(command_name, handler))


async def _on_startup(application: Application) -> None:
    runtime = _runtime_from_bot_data(application.bot_data)
    runtime.configure_notifications(application.bot)
    await runtime.start()


async def _on_shutdown(application: Application) -> None:
    runtime = _runtime_from_bot_data(application.bot_data)
    await runtime.stop()


async def _handle_application_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    error = getattr(context, "error", None)
    if error is None:
        return

    if isinstance(error, Conflict):
        _handle_polling_conflict(context, error)
        return

    effective_message = getattr(update, "effective_message", None)
    effective_user = getattr(update, "effective_user", None)
    effective_chat = getattr(update, "effective_chat", None)
    log_failure(
        LOGGER,
        "telegram_update_failed",
        error,
        update_type=type(update).__name__ if update is not None else None,
        user_id=getattr(effective_user, "id", None),
        chat_id=getattr(effective_chat, "id", None),
        message_text=getattr(effective_message, "text", None),
    )


def _handle_polling_conflict(context: ContextTypes.DEFAULT_TYPE, error: Conflict) -> None:
    bot_data = context.bot_data
    if bot_data.get(POLLING_CONFLICT_ACTIVE_KEY):
        return

    bot_data[POLLING_CONFLICT_ACTIVE_KEY] = True
    LOGGER.warning(
        "telegram_polling_conflict_detected",
        error=str(error),
        action="retrying_until_other_getupdates_client_releases_the_token",
    )


def _mark_polling_healthy(bot_data: dict[str, object]) -> None:
    if not bot_data.pop(POLLING_CONFLICT_ACTIVE_KEY, False):
        return

    LOGGER.info("telegram_polling_conflict_cleared")


def _build_polling_error_callback(application: Application):
    def error_callback(exc: TelegramError) -> None:
        application.create_task(application.process_error(error=exc, update=None))

    return error_callback


def _chunk_plain_text(text: str, *, limit: int = TELEGRAM_TEXT_MESSAGE_LIMIT) -> list[str]:
    """Split a plain-text Telegram reply into safe chunks, preferring line boundaries."""

    if limit <= 0:
        raise ValueError("limit must be positive.")
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    current = ""
    for line in text.split("\n"):
        candidate = line if not current else f"{current}\n{line}"
        if len(candidate) <= limit:
            current = candidate
            continue

        if current:
            chunks.append(current)

        while len(line) > limit:
            chunks.append(line[:limit])
            line = line[limit:]
        current = line

    if current or not chunks:
        chunks.append(current)
    return chunks


async def _reply_text_in_chunks(
    message: Message,
    text: str,
    *,
    limit: int = TELEGRAM_TEXT_MESSAGE_LIMIT,
) -> None:
    for chunk in _chunk_plain_text(text, limit=limit):
        await message.reply_text(chunk)


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    chat = update.effective_chat
    if message is None or user is None or chat is None:
        return
    _mark_polling_healthy(context.bot_data)

    if len(context.args) != 1:
        await message.reply_text("Usage: /start <password>")
        return

    security = _security_manager(context)
    try:
        session = await asyncio.to_thread(
            security.authenticate,
            user_id=user.id,
            chat_id=chat.id,
            password=context.args[0],
            username=user.username,
            first_name=user.first_name,
        )
    except PersistenceWriteError as exc:
        await _reply_persistence_error(
            update,
            context,
            "start",
            exc,
            "Authentication failed because the session could not be saved.",
        )
        return
    if session is None:
        await message.reply_text("Authentication failed.")
        return

    role = "admin" if session.is_admin else "user"
    await message.reply_text(f"Authenticated as {role}. Use /help for commands.")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    if message is None:
        return
    if await _reject_unexpected_args(update, context, "Usage: /help"):
        return
    # Fix #4: /help now requires authentication so the command surface is not
    # visible to unauthenticated users.  is_admin is only meaningful for an
    # authenticated session, so we keep the admin-tier check after the gate.
    session = await _require_session(update, context)
    if session is None:
        return

    is_admin = bool(user and _security_manager(context).is_admin(user.id))
    await message.reply_text(_build_help_text(is_admin))


async def logout_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await _reject_unexpected_args(update, context, "Usage: /logout"):
        return
    session = await _require_session(update, context)
    if session is None:
        return
    try:
        removed = await asyncio.to_thread(_security_manager(context).logout, session.user_id)
    except PersistenceWriteError as exc:
        await _reply_persistence_error(
            update,
            context,
            "logout",
            exc,
            "Logout failed because the session could not be removed.",
        )
        return
    if removed is None:
        await update.effective_message.reply_text("No active session found.")
        return
    duration = _format_duration(removed.authenticated_at, removed.last_activity_at)
    await update.effective_message.reply_text(f"Logged out. Session duration: {duration}.")


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    session = await _require_session(update, context)
    if session is None:
        return

    if context.args and context.args[0].lower() == "help":
        await update.effective_message.reply_text(
            "/status fields explained:\n\n"
            "Uptime: Time since bot process started.\n"
            "Scheduler: RUNNING = jobs firing normally. PAUSED = paused by config. STOPPED = error or not started.\n"
            "Stream: RUNNING = price stream connected. STOPPED = disconnected. DEGRADED = reconnecting.\n"
            "Last scan: Most recent scan type — full, instrument_refresh, or snapshot_refresh. 'none' = no scan has run yet.\n"
            "Macro: fresh or cached bounded VIX/DXY context from yfinance.\n"
            "Active sessions: Number of currently authenticated users."
        )
        return

    if len(context.args) > 1:
        await update.effective_message.reply_text("Usage: /status [help]")
        return

    runtime = _runtime(context)
    health = await asyncio.to_thread(
        _build_runtime_health,
        context.bot_data,
    )
    active_sessions = await asyncio.to_thread(_active_session_count, _security_manager(context))
    lines = [
        "Runtime Status",
        f"Uptime: {_format_duration(context.bot_data[STARTED_AT_KEY], datetime.now(timezone.utc))}",
        f"Scheduler: {health.scheduler.state if health.scheduler else 'UNKNOWN'}",
        f"Stream: {health.stream.state if health.stream else 'UNKNOWN'}",
        f"Last scan: {health.last_scan.run_kind if health.last_scan else 'none'}",
        _macro_summary_line(health.macro),
        f"Active sessions: {active_sessions}",
        "",
        "Use /status help for field explanations.",
    ]
    await update.effective_message.reply_text("\n".join(lines))


async def marketstatus_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await _reject_unexpected_args(update, context, "Usage: /marketstatus"):
        return
    session = await _require_session(update, context)
    if session is None:
        return

    runtime = _runtime(context)
    market_status = await asyncio.to_thread(_current_market_hours_overview, runtime)
    macro_status = await asyncio.to_thread(_current_macro_status, runtime)
    stream_status = runtime.stream_task.stream_status()
    lines = [
        "Market Status",
        _market_status_line("Overall", market_status.overall),
        _market_status_line("FX", market_status.fx),
        _market_status_line("Metals", market_status.metals),
        f"Stream state: {stream_status.state}",
        f"Reconnects: {stream_status.reconnect_count}",
        f"Last tick: {_format_optional_time(stream_status.last_tick_at)}",
        f"Macro source: {_macro_source_label(macro_status)}",
        _format_macro_indicator_line("VIX", macro_status.vix),
        _format_macro_indicator_line("DXY", macro_status.dxy),
    ]
    if macro_status.last_error:
        lines.append(f"Macro note: {macro_status.last_error}")
    await update.effective_message.reply_text("\n".join(lines))


async def price_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    session = await _require_session(update, context)
    if session is None:
        return

    runtime = _runtime(context)

    def record_spread_quote(quote) -> None:
        trade_store = getattr(runtime, "trade_store", None)
        if trade_store is None:
            return
        trade_store.record_spread(
            quote.instrument,
            quote.spread_pips,
            recorded_at=quote.fetched_at,
            metadata={
                "source": quote.source,
                "reason": "telegram_price_command",
                "bid": quote.bid,
                "ask": quote.ask,
                "spread_price": quote.ask - quote.bid,
            },
        )

    try:
        instrument, prefer_live = parse_price_args(context.args)
    except ValueError as exc:
        await _reply_command_error(
            update,
            context,
            "price",
            exc,
            str(exc),
            event="telegram_command_validation_failed",
            level="warning",
        )
        return

    try:
        quote = await resolve_price_quote(
            instrument=instrument,
            account_client=context.bot_data[ACCOUNT_CLIENT_KEY],
            stream_task=runtime.stream_task,
            prefer_live=prefer_live,
            live_max_age_seconds=LIVE_PRICE_MAX_AGE_SECONDS,
            on_resolved=record_spread_quote,
        )
    except Exception as exc:
        await _reply_command_error(
            update,
            context,
            "price",
            exc,
            f"Failed to fetch price for {instrument}: {exc}",
            instrument=instrument,
        )
        return
    lines = [instrument]
    if quote.fallback_note is not None:
        lines.append("Source: live stream unavailable or stale, falling back to REST.")
    lines.extend(
        [
            f"Bid: {quote.bid:.5f}",
            f"Ask: {quote.ask:.5f}",
            f"Spread: {quote.spread_pips:.1f} pips",
            f"As of: {_format_optional_time(quote.fetched_at)}",
            "Source: live stream" if quote.source == "live_stream" else "Source: REST pricing",
        ]
    )
    await update.effective_message.reply_text("\n".join(lines))


async def account_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await _reject_unexpected_args(update, context, "Usage: /account"):
        return
    session = await _require_session(update, context)
    if session is None:
        return

    # Fix #2: guard OANDA REST call.
    try:
        summary = await context.bot_data[ACCOUNT_CLIENT_KEY].get_account_summary()
    except Exception as exc:
        await _reply_command_error(
            update,
            context,
            "account",
            exc,
            f"Failed to fetch account summary: {exc}",
        )
        return
    lines = [
        f"Account {summary.account_id} ({summary.environment})",
        f"Money Base: {summary.currency}",
        f"Balance: {summary.balance:.2f} {summary.currency}",
        f"NAV: {summary.nav:.2f} {summary.currency}",
        f"Unrealized P/L: {summary.unrealized_pl:.2f} {summary.currency}",
        f"Realized P/L: {summary.realized_pl:.2f} {summary.currency}",
        f"Margin used: {summary.margin_used:.2f} {summary.currency}",
        f"Margin available: {summary.margin_available:.2f} {summary.currency}",
        f"Open trades: {summary.open_trade_count}",
        f"Open positions: {summary.open_position_count}",
        f"Pending orders: {summary.pending_order_count}",
    ]
    await update.effective_message.reply_text("\n".join(lines))


async def positions_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await _reject_unexpected_args(update, context, "Usage: /positions"):
        return
    session = await _require_session(update, context)
    if session is None:
        return

    # Fix #2: guard OANDA REST call.
    try:
        positions = await context.bot_data[ACCOUNT_CLIENT_KEY].get_open_positions()
    except Exception as exc:
        await _reply_command_error(
            update,
            context,
            "positions",
            exc,
            f"Failed to fetch open positions: {exc}",
        )
        return
    if not positions:
        await update.effective_message.reply_text("No open trades.")
        return

    # Fetch mid prices per unique instrument for pip distance display.
    mid_prices: dict[str, float] = {}
    for inst in {pos.instrument for pos in positions}:
        try:
            snap = await context.bot_data[ACCOUNT_CLIENT_KEY].get_pricing(inst)
            mid_prices[inst] = (snap.bid + snap.ask) / 2
        except Exception as exc:
            _log_command_failure(
                update,
                context,
                "positions",
                exc,
                event="telegram_command_nonfatal_failure",
                level="warning",
                instrument=inst,
                phase="mid_price_lookup",
            )

    await update.effective_message.reply_text(
        format_positions(positions, mid_prices=mid_prices)
    )


async def orders_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await _reject_unexpected_args(update, context, "Usage: /orders"):
        return
    session = await _require_session(update, context)
    if session is None:
        return

    # Fix #2: guard OANDA REST call.
    try:
        orders = await context.bot_data[ACCOUNT_CLIENT_KEY].get_open_orders()
    except Exception as exc:
        await _reply_command_error(
            update,
            context,
            "orders",
            exc,
            f"Failed to fetch open orders: {exc}",
        )
        return
    if not orders:
        await update.effective_message.reply_text("No open orders.")
        return

    # Fetch mid prices per unique instrument for pip distance display.
    mid_prices: dict[str, float] = {}
    for inst in {o.instrument for o in orders if o.instrument}:
        try:
            snap = await context.bot_data[ACCOUNT_CLIENT_KEY].get_pricing(inst)
            mid_prices[inst] = (snap.bid + snap.ask) / 2
        except Exception as exc:
            _log_command_failure(
                update,
                context,
                "orders",
                exc,
                event="telegram_command_nonfatal_failure",
                level="warning",
                instrument=inst,
                phase="mid_price_lookup",
            )

    await update.effective_message.reply_text(format_orders_grouped(orders, mid_prices))


async def session_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    session = await _require_session(update, context)
    if session is None:
        return

    if not context.args:
        await update.effective_message.reply_text(
            "Usage: /session <symbol> [timeframe]\n"
            "Shows trading session windows (Sydney, Tokyo, London, New York).\n\n"
            + SUPPORTED_INSTRUMENTS_HELP
        )
        return
    if len(context.args) > 2:
        await update.effective_message.reply_text(
            "Usage: /session <symbol> [timeframe]\n\n" + SUPPORTED_INSTRUMENTS_HELP
        )
        return
    try:
        instrument = normalize_command_instrument(context.args[0])
        timeframe = normalize_command_timeframe(context.args[1] if len(context.args) > 1 else None)
    except ValueError as exc:
        await _reply_command_error(
            update,
            context,
            "session",
            exc,
            str(exc),
            event="telegram_command_validation_failed",
            level="warning",
        )
        return

    snapshot = await _snapshot_first(update, context, instrument, timeframe)
    if snapshot is None:
        return
    summaries = snapshot.smc_context.sessions.sessions
    lines = [format_sessions(instrument, timeframe, summaries)]
    warning = _snapshot_warning_line(snapshot)
    if warning is not None:
        lines.extend(("", warning))
    await update.effective_message.reply_text("\n".join(lines))


async def dayrange_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    session = await _require_session(update, context)
    if session is None:
        return
    if len(context.args) != 1:
        await update.effective_message.reply_text(
            "Usage: /dayrange <symbol>\n\n" + SUPPORTED_INSTRUMENTS_HELP
        )
        return
    try:
        instrument = normalize_command_instrument(context.args[0])
    except ValueError as exc:
        await _reply_command_error(
            update,
            context,
            "dayrange",
            exc,
            str(exc),
            event="telegram_command_validation_failed",
            level="warning",
        )
        return
    snapshot = await _snapshot_first(update, context, instrument, "H1")
    if snapshot is None:
        return
    lines = [format_day_range(instrument, "H1", snapshot.smc_context.previous_high_low)]
    warning = _snapshot_warning_line(snapshot)
    if warning is not None:
        lines.extend(("", warning))
    await update.effective_message.reply_text("\n".join(lines))


async def pdh_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    session = await _require_session(update, context)
    if session is None:
        return
    if len(context.args) != 1:
        await update.effective_message.reply_text(
            "Usage: /pdh <symbol>\n\n" + SUPPORTED_INSTRUMENTS_HELP
        )
        return
    try:
        instrument = normalize_command_instrument(context.args[0])
    except ValueError as exc:
        await _reply_command_error(
            update,
            context,
            "pdh",
            exc,
            str(exc),
            event="telegram_command_validation_failed",
            level="warning",
        )
        return
    snapshot = await _snapshot_first(update, context, instrument, "H1")
    if snapshot is None:
        return
    previous = snapshot.smc_context.previous_high_low
    lines = [
        format_previous_day_level(
            instrument,
            "PDH",
            None if previous is None else previous.previous_high,
            swept=False if previous is None else previous.broken_high,
        )
    ]
    warning = _snapshot_warning_line(snapshot)
    if warning is not None:
        lines.extend(("", warning))
    await update.effective_message.reply_text("\n".join(lines))


async def pdl_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    session = await _require_session(update, context)
    if session is None:
        return
    if len(context.args) != 1:
        await update.effective_message.reply_text(
            "Usage: /pdl <symbol>\n\n" + SUPPORTED_INSTRUMENTS_HELP
        )
        return
    try:
        instrument = normalize_command_instrument(context.args[0])
    except ValueError as exc:
        await _reply_command_error(
            update,
            context,
            "pdl",
            exc,
            str(exc),
            event="telegram_command_validation_failed",
            level="warning",
        )
        return
    snapshot = await _snapshot_first(update, context, instrument, "H1")
    if snapshot is None:
        return
    previous = snapshot.smc_context.previous_high_low
    lines = [
        format_previous_day_level(
            instrument,
            "PDL",
            None if previous is None else previous.previous_low,
            swept=False if previous is None else previous.broken_low,
        )
    ]
    warning = _snapshot_warning_line(snapshot)
    if warning is not None:
        lines.extend(("", warning))
    await update.effective_message.reply_text("\n".join(lines))


async def calendar_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    session = await _require_session(update, context)
    if session is None:
        return

    try:
        scope, req_currencies, force_refresh = parse_calendar_args(list(context.args or []))
    except ValueError as exc:
        await _reply_command_error(
            update,
            context,
            "calendar",
            exc,
            str(exc),
            event="telegram_command_validation_failed",
            level="warning",
        )
        return

    runtime = _runtime(context)
    try:
        if runtime.scan_orchestrator.calendar_status.calendar_version == 0 or force_refresh:
            status = await asyncio.to_thread(runtime.scan_orchestrator.refresh_calendar, force=True)
        else:
            status = runtime.scan_orchestrator.calendar_status
    except Exception as exc:
        await _reply_command_error(
            update,
            context,
            "calendar",
            exc,
            f"Calendar refresh failed: {exc}\nTry /calendar force to retry.",
            phase="refresh",
        )
        return

    last_error = getattr(status, "last_error", None)
    if last_error and status.event_count == 0:
        await update.effective_message.reply_text(
            f"Calendar unavailable: {last_error}\nTry /calendar force to retry."
        )
        return

    display_currencies = req_currencies or TRACKED_CALENDAR_CURRENCIES
    window_start, window_end = _calendar_window_bounds(datetime.now(timezone.utc), scope=scope)
    try:
        events = await asyncio.to_thread(
            runtime.scan_orchestrator.calendar_provider.filter_events,
            impacts=("HIGH", "MEDIUM"),
            currencies=display_currencies,
            window_start=window_start,
            window_end=window_end,
        )
    except Exception as exc:
        await _reply_command_error(
            update,
            context,
            "calendar",
            exc,
            f"Calendar filter failed: {exc}",
            phase="filter",
        )
        return

    text = format_calendar_output(
        status=status,
        events=events,
        scope=scope,
        requested_currencies=req_currencies,
        sgt=SGT,
    )
    await update.effective_message.reply_text(text)


async def scan_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    session = await _require_session(update, context)
    if session is None:
        return

    orchestrator = context.bot_data[SCAN_ORCHESTRATOR_KEY]
    args = list(context.args or [])
    force_tokens = [arg for arg in args if arg.lower() == "force"]
    force = bool(force_tokens)
    positional_args = [arg for arg in args if arg.lower() != "force"]
    if len(force_tokens) > 1 or len(positional_args) > 1:
        await update.effective_message.reply_text(
            "Usage:\n  /scan [force] - Full scan of all instruments\n  /scan <symbol> [force] - Single instrument\n\n"
            + SUPPORTED_INSTRUMENTS_HELP
        )
        return

    scan_start = datetime.now(timezone.utc)

    if positional_args:
        try:
            instrument = normalize_command_instrument(positional_args[0])
        except ValueError as exc:
            await _reply_command_error(
                update,
                context,
                "scan",
                exc,
                str(exc),
                event="telegram_command_validation_failed",
                level="warning",
            )
            return
        try:
            snapshots = await asyncio.to_thread(
                orchestrator.refresh_instrument,
                instrument,
                force=force,
            )
        except Exception as exc:
            await _reply_command_error(
                update,
                context,
                "scan",
                exc,
                f"Scan failed for {instrument}: {exc}",
                instrument=instrument,
            )
            return
        elapsed = _format_duration(scan_start, datetime.now(timezone.utc))
        status = orchestrator.last_scan_status
        lines = [
            f"Scan complete for {instrument} ({elapsed})",
            f"Snapshots ready: {'yes' if snapshots is not None else 'no'}",
            f"Snapshots: {status.snapshots_published}",
            f"Errors: {len(status.errors)}",
        ]
    else:
        try:
            status = await asyncio.to_thread(orchestrator.scan_all, force=force)
        except Exception as exc:
            await _reply_command_error(
                update,
                context,
                "scan",
                exc,
                f"Full scan failed: {exc}",
                scope="all",
            )
            return
        elapsed = _format_duration(scan_start, datetime.now(timezone.utc))
        scanned = ", ".join(status.scanned_instruments) if status.scanned_instruments else "none"
        lines = [
            f"Full scan complete ({elapsed})",
            f"Instruments: {scanned}",
            f"Snapshots: {status.snapshots_published}",
            f"Errors: {len(status.errors)}",
        ]
        skipped_reason = getattr(status, "skipped_reason", None)
        if skipped_reason:
            lines.append(f"Skipped: {skipped_reason}")
    if status.errors:
        lines.append("")
        lines.append("Error detail:")
        lines.extend(status.errors[:5])
    if force and getattr(status, "forced_market_fetch", False):
        lines.append("Note: forced scan used live fetch (market closed).")
    await update.effective_message.reply_text("\n".join(lines))


async def smc_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    session = await _require_session(update, context)
    if session is None:
        return
    if not context.args:
        await update.effective_message.reply_text(
            "Usage: /smc <symbol> [timeframe]\n\n" + SUPPORTED_INSTRUMENTS_HELP
        )
        return
    if len(context.args) > 2:
        await update.effective_message.reply_text(
            "Usage: /smc <symbol> [timeframe]\n\n" + SUPPORTED_INSTRUMENTS_HELP
        )
        return
    try:
        instrument = normalize_command_instrument(context.args[0])
        timeframe = normalize_command_timeframe(context.args[1] if len(context.args) > 1 else None)
    except ValueError as exc:
        await _reply_command_error(
            update,
            context,
            "smc",
            exc,
            str(exc),
            event="telegram_command_validation_failed",
            level="warning",
        )
        return
    snapshot = await _snapshot_first(update, context, instrument, timeframe)
    if snapshot is None:
        return
    latest_break = snapshot.structure.latest_break
    lines = [
        f"SMC {instrument} {timeframe}",
        f"Structure: {latest_break.kind if latest_break else 'NONE'} {latest_break.direction if latest_break else ''}".strip(),
        f"Order blocks: {len(snapshot.zones.order_blocks)}",
        f"Liquidity levels: {len(snapshot.liquidity.levels)}",
        f"Spread: {snapshot.spread.spread_pips:.1f} pips",
    ]
    warning = _snapshot_warning_line(snapshot)
    if warning is not None:
        lines.extend(("", warning))
    await update.effective_message.reply_text("\n".join(lines))


async def structure_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    session = await _require_session(update, context)
    if session is None:
        return
    if not context.args:
        await update.effective_message.reply_text(
            "Usage: /structure <symbol> [timeframe]\n\n" + SUPPORTED_INSTRUMENTS_HELP
        )
        return
    if len(context.args) > 2:
        await update.effective_message.reply_text(
            "Usage: /structure <symbol> [timeframe]\n\n" + SUPPORTED_INSTRUMENTS_HELP
        )
        return
    try:
        instrument = normalize_command_instrument(context.args[0])
        timeframe = normalize_command_timeframe(context.args[1] if len(context.args) > 1 else None)
    except ValueError as exc:
        await _reply_command_error(
            update,
            context,
            "structure",
            exc,
            str(exc),
            event="telegram_command_validation_failed",
            level="warning",
        )
        return
    snapshot = await _snapshot_first(update, context, instrument, timeframe)
    if snapshot is None:
        return
    lines = [f"Structure {instrument} {timeframe}"]
    warning = _snapshot_warning_line(snapshot)
    if warning is not None:
        lines.append(warning)
        lines.append("")
    for structure in snapshot.structure.recent_breaks:
        lines.append(
            f"{structure.kind} {structure.direction} level={_format_optional_number(structure.level)} at {_format_optional_time(structure.occurred_at)}"
        )
    if len(lines) == 1:
        lines.append("No recent structure breaks.")
    await update.effective_message.reply_text("\n".join(lines))


async def indicators_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    session = await _require_session(update, context)
    if session is None:
        return
    if not context.args:
        await update.effective_message.reply_text(
            "Usage: /indicators <symbol> [timeframe] [compact|full]\n\n" + SUPPORTED_INSTRUMENTS_HELP
        )
        return
    if len(context.args) > 3:
        await update.effective_message.reply_text(
            "Usage: /indicators <symbol> [timeframe] [compact|full]\n\n" + SUPPORTED_INSTRUMENTS_HELP
        )
        return
    try:
        instrument = normalize_command_instrument(context.args[0])
        timeframe = normalize_command_timeframe(context.args[1] if len(context.args) > 1 else None)
    except ValueError as exc:
        await _reply_command_error(
            update,
            context,
            "indicators",
            exc,
            str(exc),
            event="telegram_command_validation_failed",
            level="warning",
        )
        return
    mode = context.args[2].strip().lower() if len(context.args) > 2 else "compact"
    if mode not in {"compact", "full"}:
        await update.effective_message.reply_text(
            "Usage: /indicators [symbol] [timeframe] [compact|full]"
        )
        return
    snapshot = await _snapshot_first(update, context, instrument, timeframe)
    if snapshot is None:
        return
    metrics = list(snapshot.indicators.metrics)
    if mode != "full":
        metrics = [
            metric
            for metric in metrics
            if metric.name in {"rsi", "atr", "ema_20", "ema_50", "adx"}
        ][:8]
    lines = [f"Indicators {instrument} {timeframe}"]
    warning = _snapshot_warning_line(snapshot)
    if warning is not None:
        lines.append(warning)
        lines.append("")
    if not metrics:
        lines.append("No indicator metrics available.")
    else:
        for metric in metrics:
            lines.append(f"{metric.name}: {_format_optional_number(metric.value)}")
    await update.effective_message.reply_text("\n".join(lines))


async def vwap_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    session = await _require_session(update, context)
    if session is None:
        return

    try:
        instrument, timeframe, anchor, bands = parse_vwap_args(context.args)
    except ValueError as exc:
        await _reply_command_error(
            update,
            context,
            "vwap",
            exc,
            str(exc),
            event="telegram_command_validation_failed",
            level="warning",
        )
        return

    market_data_provider = context.bot_data[MARKET_DATA_PROVIDER_KEY]
    try:
        candle_count = resolve_vwap_candle_count(timeframe, anchor)
        candles = await asyncio.to_thread(
            market_data_provider.get_candles,
            instrument,
            timeframe,
            candle_count,
        )
        freshness = await asyncio.to_thread(
            market_data_provider.get_candle_freshness,
            instrument,
            timeframe,
        )
        result = await asyncio.to_thread(
            build_vwap_read_result,
            candles,
            instrument=instrument,
            timeframe=timeframe,
            anchor=anchor,
            bands=bands,
            source=None if freshness.source is None else str(freshness.source),
        )
    except Exception as exc:
        await _reply_command_error(
            update,
            context,
            "vwap",
            exc,
            f"VWAP lookup failed: {exc}",
            event="telegram_command_failed",
            instrument=instrument,
            timeframe=timeframe,
            anchor=anchor,
        )
        return

    await update.effective_message.reply_text(format_vwap_summary(result))


async def order_blocks_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    session = await _require_session(update, context)
    if session is None:
        return
    if not context.args:
        await update.effective_message.reply_text(ORDER_BLOCK_USAGE)
        return
    try:
        instrument, timeframe, mitigation_status = parse_order_block_args(context.args)
    except ValueError as exc:
        await _reply_command_error(
            update,
            context,
            "ob",
            exc,
            str(exc),
            event="telegram_command_validation_failed",
            level="warning",
        )
        return
    snapshot = await _snapshot_first(update, context, instrument, timeframe)
    if snapshot is None:
        return
    order_blocks = _filter_order_blocks(snapshot.zones.order_blocks, mitigation_status)
    counts = _order_block_counts(snapshot.zones.order_blocks)
    lines = [f"Order Blocks {instrument} {timeframe}"]
    lines.append(
        "Filter: "
        f"{mitigation_status} "
        f"(all={counts['all']}, mitigated={counts['mitigated']}, unmitigated={counts['unmitigated']})"
    )
    warning = _snapshot_warning_line(snapshot)
    if warning is not None:
        lines.append(warning)
        lines.append("")
    if not order_blocks:
        lines.append(f"No {mitigation_status} order blocks.")
    else:
        for block in order_blocks:
            distance = (
                ""
                if block.distance_pips is None
                else f" distance={block.distance_pips:.1f}p"
            )
            lines.append(
                f"{_order_block_mitigation_label(block)} {block.direction} "
                f"{block.lower_price:.5f}-{block.upper_price:.5f}{distance}"
            )
    await update.effective_message.reply_text("\n".join(lines))


async def chart_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    session = await _require_session(update, context)
    if session is None:
        return

    default_style = await asyncio.to_thread(_runtime_config_manager(context).effective_chart_style)
    default_mode = await asyncio.to_thread(_runtime_config_manager(context).effective_chart_mode)
    try:
        request = parse_chart_args(
            context.args,
            default_style=default_style,
            default_mode=default_mode,
        )
    except (IndexError, ValueError) as exc:
        await _reply_command_error(
            update,
            context,
            "chart",
            exc,
            str(exc),
            event="telegram_command_validation_failed",
            level="warning",
        )
        return
    if hasattr(request, "model_copy"):
        request = request.model_copy(update={"chat_id": update.effective_chat.id})
    else:
        setattr(request, "chat_id", update.effective_chat.id)
    renderer = context.bot_data[CHART_RENDERER_KEY]
    try:
        result = await asyncio.to_thread(renderer.render, request)
    except Exception as exc:
        await _reply_command_error(
            update,
            context,
            "chart",
            exc,
            f"Chart render failed: {exc}",
            instrument=request.instrument,
            timeframe=request.timeframe,
        )
        return
    try:
        with open(result.artifact.path, "rb") as file_handle:
            reply_kwargs = {"filename": result.artifact.path.name}
            warning_text = getattr(result, "warning_text", None)
            if warning_text is not None:
                reply_kwargs["caption"] = warning_text
            await update.effective_message.reply_document(
                document=file_handle,
                **reply_kwargs,
            )
    finally:
        result.close()


async def extractor_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    session = await _require_session(update, context)
    if session is None:
        return

    settings = context.bot_data[SETTINGS_KEY]
    try:
        instruments, count, timeframes = parse_extractor_args(
            context.args,
            default_count=settings.default_candle_count,
        )
    except ValueError as exc:
        await _reply_command_error(
            update,
            context,
            "extractor",
            exc,
            str(exc),
            event="telegram_command_validation_failed",
            level="warning",
        )
        return
    account_client = context.bot_data[ACCOUNT_CLIENT_KEY]
    sent = 0
    errors: list[str] = []
    for instrument in instruments:
        for timeframe in timeframes:
            try:
                frame = await account_client.get_bid_ask_candles(instrument, timeframe, count)
                buffer = BytesIO(frame.to_csv(index=False).encode("utf-8"))
                buffer.name = f"{instrument}_{timeframe}_{count}.csv"
                await update.effective_message.reply_document(document=buffer, filename=buffer.name)
                sent += 1
            except Exception as exc:
                _log_command_failure(
                    update,
                    context,
                    "extractor",
                    exc,
                    event="telegram_command_nonfatal_failure",
                    level="warning",
                    instrument=instrument,
                    timeframe=timeframe,
                    requested_count=count,
                )
                errors.append(f"{instrument} {timeframe}: {exc}")
    summary = f"Extractor complete. Sent {sent} CSV file(s)."
    if errors:
        summary += "\nErrors:\n" + "\n".join(errors[:5])
    await update.effective_message.reply_text(summary)


async def config_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    session = await _require_session(update, context)
    if session is None:
        return

    manager = _runtime_config_manager(context)
    if not context.args:
        snapshot = await asyncio.to_thread(manager.snapshot)
        lines = [
            "Runtime Config",
            f"chart: {snapshot.chart.value}",
            f"chart_mode: {snapshot.chart_mode.value}",
            f"scan_interval: {snapshot.scan_interval}",
            f"trade_push: {'on' if snapshot.trade_push else 'off'}",
            f"session_alerts: {'on' if snapshot.session_alerts else 'off'}",
        ]
        await update.effective_message.reply_text("\n".join(lines))
        return

    if len(context.args) != 2:
        await update.effective_message.reply_text("Usage: /config <key> <value>")
        return

    try:
        key = RuntimeConfigKey(context.args[0].strip().lower())
        record = await asyncio.to_thread(manager.set_value, key, context.args[1])
    except PersistenceWriteError as exc:
        await _reply_persistence_error(
            update,
            context,
            "config",
            exc,
            "Config update failed because the change could not be saved.",
            key=context.args[0].strip().lower(),
        )
        return
    except ValueError as exc:
        await _reply_command_error(
            update,
            context,
            "config",
            exc,
            str(exc),
            event="telegram_command_validation_failed",
            level="warning",
        )
        return
    if key == RuntimeConfigKey.SCAN_INTERVAL:
        try:
            interval_minutes = await asyncio.to_thread(manager.effective_scan_interval_minutes)
            await asyncio.to_thread(
                context.bot_data[SCHEDULER_KEY].reschedule_auto_scan,
                interval_minutes,
            )
        except Exception as exc:
            await _reply_command_error(
                update,
                context,
                "config",
                exc,
                f"Config update applied but scheduler reschedule failed: {exc}",
                key=key.value,
            )
            return
    await update.effective_message.reply_text(f"Config updated: {record.key.value}={record.value}")


async def journal_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    session = await _require_session(update, context)
    if session is None:
        return

    try:
        trade_id, instrument, start_date, end_date = parse_journal_args(context.args)
    except (IndexError, ValueError) as exc:
        await _reply_command_error(
            update,
            context,
            "journal",
            exc,
            str(exc),
            event="telegram_command_validation_failed",
            level="warning",
        )
        return
    trade_repository = _trade_repository(context)
    excursion_repository = _excursion_repository(context)
    settings = context.bot_data[SETTINGS_KEY]

    if trade_id:
        try:
            trade = await asyncio.to_thread(trade_repository.get, trade_id)
        except Exception as exc:
            await _reply_command_error(
                update,
                context,
                "journal",
                exc,
                f"Failed to load trade {trade_id}: {exc}",
                trade_id=trade_id,
            )
            return
        if trade is None:
            await update.effective_message.reply_text(f"Trade {trade_id} not found.")
            return
        try:
            samples = await asyncio.to_thread(excursion_repository.list_for_trade, trade.trade_id)
        except Exception as exc:
            await _reply_command_error(
                update,
                context,
                "journal",
                exc,
                f"Failed to load excursion samples for {trade.trade_id}: {exc}",
                trade_id=trade.trade_id,
            )
            return
        await update.effective_message.reply_text(
            format_journal_detail(
                trade,
                account_currency=settings.account_currency,
                mae_samples=samples,
            )
        )
        return

    try:
        trades = await asyncio.to_thread(
            _filtered_journal_trades,
            trade_repository,
            instrument,
            start_date,
            end_date,
        )
    except Exception as exc:
        await _reply_command_error(
            update,
            context,
            "journal",
            exc,
            f"Failed to load journal trades: {exc}",
            instrument=instrument,
        )
        return
    await update.effective_message.reply_text(
        format_journal_list(
            trades,
            filter_summary=_journal_filter_summary(instrument, start_date, end_date),
            account_currency=settings.account_currency,
        )
    )


async def tradehistory_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    session = await _require_session(update, context)
    if session is None:
        return

    try:
        period, view, instrument, page = parse_tradehistory_args(context.args)
    except ValueError as exc:
        await _reply_command_error(
            update,
            context,
            "tradehistory",
            exc,
            str(exc),
            event="telegram_command_validation_failed",
            level="warning",
        )
        return

    service = _trade_history_service(context)
    try:
        result = await asyncio.to_thread(
            service.get_trade_history,
            period,
            view,
            instrument,
            page,
        )
    except Exception as exc:
        await _reply_command_error(
            update,
            context,
            "tradehistory",
            exc,
            f"Failed to load trade history: {exc}",
            instrument=instrument,
            period=period,
            view=view,
            page=page,
        )
        return

    await update.effective_message.reply_text(format_trade_history_page(result))


async def tradehistory_backfill_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    session = await _require_admin_session(update, context)
    if session is None:
        return

    try:
        start_date, end_date = parse_tradehistory_backfill_args(context.args)
    except ValueError as exc:
        await _reply_command_error(
            update,
            context,
            "tradehistory_backfill",
            exc,
            str(exc),
            event="telegram_command_validation_failed",
            level="warning",
        )
        return

    service = _trade_history_service(context)
    try:
        result = await asyncio.to_thread(
            service.backfill_history,
            start_date,
            end_date,
            None,
        )
    except Exception as exc:
        await _reply_command_error(
            update,
            context,
            "tradehistory_backfill",
            exc,
            f"Trade history backfill failed: {exc}",
            start_date=start_date.isoformat(),
            end_date=end_date.isoformat(),
        )
        return

    await update.effective_message.reply_text(
        "\n".join(
            (
                "Trade history backfill complete.",
                f"Range: {result['start']} -> {result['end']} ({result['timezone_name']})",
                f"Chunks: {result['chunks']}",
                f"Events seen/inserted/updated: {result['seen']}/{result['inserted']}/{result['updated']}",
                f"Raw seen/inserted/updated: {result['raw_seen']}/{result['raw_inserted']}/{result['raw_updated']}",
                f"Projected TradeRecord rows: {result['projected_trades']}",
            )
        )
    )


async def label_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    session = await _require_session(update, context)
    if session is None:
        return
    if len(context.args) < 2:
        await update.effective_message.reply_text("Usage: /label <trade_id> <text>")
        return
    trade_id = context.args[0].strip()
    note = " ".join(token.strip() for token in context.args[1:] if token.strip())
    if not note:
        await update.effective_message.reply_text("Usage: /label <trade_id> <text>")
        return
    try:
        updated = await asyncio.to_thread(_trade_repository(context).set_notes, trade_id, note)
    except PersistenceWriteError as exc:
        await _reply_persistence_error(
            update,
            context,
            "label",
            exc,
            "Label update failed because the trade note could not be saved.",
            trade_id=trade_id,
        )
        return
    if updated is None:
        await update.effective_message.reply_text(f"Trade {trade_id} not found.")
        return
    await update.effective_message.reply_text(f"Label updated for trade {trade_id}.")


async def maemfe_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    session = await _require_session(update, context)
    if session is None:
        return
    if len(context.args) > 1:
        await update.effective_message.reply_text("Usage: /maemfe [trade_id]")
        return
    trade_repository = _trade_repository(context)
    excursion_repository = _excursion_repository(context)
    account_client = context.bot_data[ACCOUNT_CLIENT_KEY]
    mae_mfe_service = MaeMfeService(
        excursion_repository=excursion_repository,
        account_client=account_client,
    )

    if context.args:
        trade_id = context.args[0].strip()
        trade = await asyncio.to_thread(trade_repository.get, trade_id)
        if trade is None:
            await update.effective_message.reply_text(f"Trade {trade_id} not found.")
            return
        samples = await asyncio.to_thread(excursion_repository.list_for_trade, trade.trade_id)
        summary = await mae_mfe_service.summary_for_trade(trade, samples=samples)
        if summary is None:
            await update.effective_message.reply_text(f"No MAE/MFE samples recorded for {trade.trade_id}.")
            return
        current_price = trade.close_price or trade.open_price
        if trade.state == TradeState.OPEN:
            # Fix #1 + #2: correct direction (bid=long, ask=short) + error handling.
            try:
                pricing = await account_client.get_pricing(trade.instrument)
                current_price = pricing.bid if trade.units > 0 else pricing.ask
            except Exception as exc:
                await _reply_command_error(
                    update,
                    context,
                    "maemfe",
                    exc,
                    f"Failed to fetch current price for {trade.instrument}: {exc}",
                    instrument=trade.instrument,
                )
                return
        await update.effective_message.reply_text(
            format_maemfe_detail(
                trade,
                current_price=current_price,
                summary=summary,
                sample_count=len(samples),
            )
        )
        return

    open_trades = await asyncio.to_thread(trade_repository.list_open)
    summaries = await mae_mfe_service.summary_map_for_open_trades(open_trades)

    # Fix #1 + #2: use bid for longs and ask for shorts; surface API errors.
    current_prices: dict[str, float] = {}
    instruments_needed = {t.instrument for t in open_trades}
    for inst in instruments_needed:
        try:
            pricing = await account_client.get_pricing(inst)
            for trade in open_trades:
                if trade.instrument != inst:
                    continue
                current_prices[trade.trade_id] = pricing.bid if trade.units > 0 else pricing.ask
        except Exception as exc:
            _log_command_failure(
                update,
                context,
                "maemfe",
                exc,
                event="telegram_command_nonfatal_failure",
                level="warning",
                instrument=inst,
                phase="current_price_lookup",
            )

    await update.effective_message.reply_text(
        format_maemfe_list(open_trades, summaries, current_prices=current_prices)
    )


async def indicator_alert_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    session = await _require_session(update, context)
    if session is None:
        return

    # Handle "defaults" subcommand
    if context.args and context.args[0].lower() == "defaults":
        await _create_default_indicator_alerts(update, context)
        return

    try:
        instrument, timeframe, indicator, condition, threshold, note = parse_indicator_alert_args(
            context.args
        )
    except ValueError as exc:
        await _reply_command_error(
            update,
            context,
            "indicatoralert",
            exc,
            str(exc),
            event="telegram_command_validation_failed",
            level="warning",
        )
        return

    alert_repository = _alert_repository(context)
    payload = {
        "id": None,
        "instrument": instrument,
        "granularity": timeframe,
        "indicator": indicator,
        "condition": condition,
        "threshold": threshold,
        "status": AlertStatus.PENDING,
        "repeat": False,
        "cooloff_minutes": None,
        "chat_id": update.effective_chat.id,
        "notes": note,
        "created_at": datetime.now(timezone.utc),
        "fired_at": None,
    }
    try:
        created = await asyncio.to_thread(alert_repository.upsert_indicator_alert, payload)
    except PersistenceWriteError as exc:
        await _reply_persistence_error(
            update,
            context,
            "indicatoralert",
            exc,
            "Indicator alert could not be saved.",
            instrument=instrument,
            timeframe=timeframe,
        )
        return
    except ValueError as exc:
        await _reply_command_error(
            update,
            context,
            "indicatoralert",
            exc,
            str(exc),
            event="telegram_command_validation_failed",
            level="warning",
            instrument=instrument,
            timeframe=timeframe,
        )
        return
    await update.effective_message.reply_text(
        f"Indicator alert #{created.id} created for {created.instrument} {created.granularity} {created.indicator.value} {created.condition}."
    )


async def list_indicator_alerts_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await _reject_unexpected_args(update, context, "Usage: /listindicators"):
        return
    session = await _require_session(update, context)
    if session is None:
        return
    alerts = await asyncio.to_thread(
        _alert_repository(context).list_active_indicator_alerts_for_chat,
        update.effective_chat.id,
    )
    if not alerts:
        await update.effective_message.reply_text("No active indicator alerts.")
        return
    lines = ["Active Indicator Alerts"]
    for alert in alerts:
        threshold = "" if alert.threshold is None else f" threshold={alert.threshold}"
        lines.append(
            f"#{alert.id} {alert.instrument} {alert.granularity} {alert.indicator.value} {alert.condition}{threshold} repeat={alert.repeat}"
        )
    await update.effective_message.reply_text("\n".join(lines))


async def clear_indicator_alert_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    session = await _require_session(update, context)
    if session is None:
        return
    if not context.args:
        await update.effective_message.reply_text("Usage: /clearindicator <id>")
        return
    if len(context.args) > 1:
        await update.effective_message.reply_text("Usage: /clearindicator <id>")
        return
    try:
        alert_id = int(context.args[0])
    except ValueError as exc:
        await _reply_command_error(
            update,
            context,
            "clearindicator",
            exc,
            "Usage: /clearindicator <id>",
            event="telegram_command_validation_failed",
            level="warning",
        )
        return
    try:
        cleared = await asyncio.to_thread(
            _alert_repository(context).cancel_indicator_alert_for_chat,
            alert_id,
            update.effective_chat.id,
        )
    except PersistenceWriteError as exc:
        await _reply_persistence_error(
            update,
            context,
            "clearindicator",
            exc,
            f"Indicator alert {alert_id} could not be cleared because the change could not be saved.",
            alert_id=alert_id,
        )
        return
    if cleared is None:
        await update.effective_message.reply_text(f"Indicator alert {alert_id} not found.")
        return
    await update.effective_message.reply_text(f"Indicator alert {alert_id} cleared.")


async def price_alert_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    session = await _require_session(update, context)
    if session is None:
        return
    try:
        instrument, price, direction, note = parse_price_alert_args(context.args)
    except ValueError as exc:
        await _reply_command_error(
            update,
            context,
            "pricealert",
            exc,
            str(exc),
            event="telegram_command_validation_failed",
            level="warning",
        )
        return

    alert_repository = _alert_repository(context)
    payload = {
        "id": None,
        "instrument": instrument,
        "target_price": price,
        "direction": direction,
        "status": AlertStatus.PENDING,
        "chat_id": update.effective_chat.id,
        "notes": note,
        "created_at": datetime.now(timezone.utc),
        "fired_at": None,
    }
    try:
        created = await asyncio.to_thread(alert_repository.upsert_price_alert, payload)
        await _refresh_price_alert_watchlist(context.bot_data.get(BOT_RUNTIME_KEY))
    except PersistenceWriteError as exc:
        await _reply_persistence_error(
            update,
            context,
            "pricealert",
            exc,
            "Price alert could not be saved.",
            instrument=instrument,
        )
        return
    except ValueError as exc:
        await _reply_command_error(
            update,
            context,
            "pricealert",
            exc,
            str(exc),
            event="telegram_command_validation_failed",
            level="warning",
            instrument=instrument,
        )
        return
    await update.effective_message.reply_text(
        f"Price alert #{created.id} created: {created.instrument} {created.direction} {created.target_price:.5f}"
    )


async def list_price_alerts_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await _reject_unexpected_args(update, context, "Usage: /listpricealerts"):
        return
    session = await _require_session(update, context)
    if session is None:
        return
    alerts = await asyncio.to_thread(
        _alert_repository(context).list_pending_price_alerts_for_chat,
        update.effective_chat.id,
    )
    await _reply_text_in_chunks(update.effective_message, format_price_alert_list(alerts))


async def clear_price_alert_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    session = await _require_session(update, context)
    if session is None:
        return
    if not context.args:
        await update.effective_message.reply_text("Usage: /clearpricealert <id>")
        return
    if len(context.args) > 1:
        await update.effective_message.reply_text("Usage: /clearpricealert <id>")
        return
    try:
        alert_id = int(context.args[0])
    except ValueError as exc:
        await _reply_command_error(
            update,
            context,
            "clearpricealert",
            exc,
            "Usage: /clearpricealert <id> (id must be a number)",
            event="telegram_command_validation_failed",
            level="warning",
        )
        return
    try:
        cleared = await asyncio.to_thread(
            _alert_repository(context).cancel_price_alert_for_chat,
            alert_id,
            update.effective_chat.id,
        )
        await _refresh_price_alert_watchlist(context.bot_data.get(BOT_RUNTIME_KEY))
    except PersistenceWriteError as exc:
        await _reply_persistence_error(
            update,
            context,
            "clearpricealert",
            exc,
            f"Price alert {alert_id} could not be cleared because the change could not be saved.",
            alert_id=alert_id,
        )
        return
    if cleared is None:
        await update.effective_message.reply_text(f"Price alert {alert_id} not found.")
        return
    await update.effective_message.reply_text(f"Price alert {alert_id} cleared.")


async def time_alert_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    session = await _require_session(update, context)
    if session is None:
        return
    try:
        kind, schedule, local_time, session_name, note = parse_time_alert_args(context.args)
    except ValueError as exc:
        await _reply_command_error(
            update,
            context,
            "timealert",
            exc,
            str(exc),
            event="telegram_command_validation_failed",
            level="warning",
        )
        return

    now = datetime.now(timezone.utc)
    try:
        next_fire_at = (
            next_fixed_alert_fire_at(local_time, now_utc=now, timezone_name=DEFAULT_TIME_ALERT_TIMEZONE)
            if kind == "at"
            else next_session_fire_at(session_name, now_utc=now)
        )
    except ValueError as exc:
        await _reply_command_error(
            update,
            context,
            "timealert",
            exc,
            str(exc),
            event="telegram_command_validation_failed",
            level="warning",
        )
        return

    payload = {
        "id": None,
        "chat_id": update.effective_chat.id,
        "kind": TimeAlertKind.FIXED_TIME if kind == "at" else TimeAlertKind.SESSION,
        "status": "ACTIVE",
        "schedule": schedule,
        "timezone_name": DEFAULT_TIME_ALERT_TIMEZONE,
        "local_time": local_time,
        "session_name": session_name,
        "note": note,
        "created_at": now,
        "next_fire_at": next_fire_at,
        "last_fired_at": None,
    }
    try:
        created = await asyncio.to_thread(_alert_repository(context).upsert_time_alert, payload)
    except PersistenceWriteError as exc:
        await _reply_persistence_error(
            update,
            context,
            "timealert",
            exc,
            "Time alert could not be saved.",
        )
        return
    await update.effective_message.reply_text(
        f"Time alert #{created.id} created for "
        f"{created.session_name if created.session_name is not None else created.local_time}."
    )


async def list_time_alerts_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await _reject_unexpected_args(update, context, "Usage: /listtimealerts"):
        return
    session = await _require_session(update, context)
    if session is None:
        return
    alerts = await asyncio.to_thread(
        _alert_repository(context).list_active_time_alerts_for_chat,
        update.effective_chat.id,
    )
    await update.effective_message.reply_text(format_time_alert_list(alerts))


async def clear_time_alert_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    session = await _require_session(update, context)
    if session is None:
        return
    if len(context.args) != 1:
        await update.effective_message.reply_text("Usage: /cleartimealert <id>")
        return
    try:
        alert_id = int(context.args[0])
    except ValueError as exc:
        await _reply_command_error(
            update,
            context,
            "cleartimealert",
            exc,
            "Usage: /cleartimealert <id> (id must be a number)",
            event="telegram_command_validation_failed",
            level="warning",
        )
        return
    try:
        cleared = await asyncio.to_thread(
            _alert_repository(context).cancel_time_alert_for_chat,
            alert_id,
            update.effective_chat.id,
        )
    except PersistenceWriteError as exc:
        await _reply_persistence_error(
            update,
            context,
            "cleartimealert",
            exc,
            f"Time alert {alert_id} could not be cleared because the change could not be saved.",
            alert_id=alert_id,
        )
        return
    if cleared is None:
        await update.effective_message.reply_text(f"Time alert {alert_id} not found.")
        return
    await update.effective_message.reply_text(f"Time alert {alert_id} cleared.")


async def export_time_alerts_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await _reject_unexpected_args(update, context, "Usage: /exporttimealerts"):
        return
    session = await _require_session(update, context)
    if session is None:
        return

    now = datetime.now(timezone.utc)
    alerts = await asyncio.to_thread(
        _alert_repository(context).list_active_time_alerts_for_chat,
        update.effective_chat.id,
    )
    export_document = TimeAlertExportDocument(
        exported_at=now,
        alerts=tuple(TimeAlertDefinition.from_time_alert(alert) for alert in alerts),
    )
    payload = json.dumps(export_document.model_dump(mode="json"), indent=2, sort_keys=True).encode("utf-8")
    artifact = BytesIO(payload)
    artifact.seek(0)
    artifact.name = f"time_alerts_{now.strftime('%Y%m%d_%H%M%S')}.json"
    await update.effective_message.reply_document(
        document=artifact,
        filename=artifact.name,
        caption=f"Exported {len(alerts)} active time alerts.",
    )


async def import_time_alerts_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await _reject_unexpected_args(update, context, IMPORT_TIME_ALERTS_USAGE):
        return
    session = await _require_session(update, context)
    if session is None:
        return

    reply_message = getattr(update.effective_message, "reply_to_message", None)
    document = None if reply_message is None else getattr(reply_message, "document", None)
    if document is None:
        await update.effective_message.reply_text(IMPORT_TIME_ALERTS_USAGE)
        return

    now = datetime.now(timezone.utc)
    try:
        export_document = await _load_time_alert_export_document(document)
        payloads, skipped_expired = _build_import_time_alert_payloads(
            export_document,
            chat_id=update.effective_chat.id,
            now_utc=now,
        )
        created = await asyncio.to_thread(_alert_repository(context).create_time_alerts, payloads)
    except PersistenceWriteError as exc:
        await _reply_persistence_error(
            update,
            context,
            "importtimealerts",
            exc,
            "Time alerts could not be imported because the change could not be saved.",
        )
        return
    except ValueError as exc:
        await _reply_command_error(
            update,
            context,
            "importtimealerts",
            exc,
            str(exc),
            event="telegram_command_validation_failed",
            level="warning",
        )
        return

    summary = [f"Imported {len(created)} time alerts."]
    if skipped_expired:
        label = "alert" if skipped_expired == 1 else "alerts"
        summary.append(f"Skipped {skipped_expired} expired dated {label}.")
    await update.effective_message.reply_text(" ".join(summary))


async def _create_default_indicator_alerts(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Create starter indicator alerts for all scan instruments on H1."""

    alert_repository = _alert_repository(context)
    chat_id = update.effective_chat.id
    now = datetime.now(timezone.utc)

    defaults = [
        (IndicatorKind.RSI, "above", 70.0, "RSI overbought"),
        (IndicatorKind.RSI, "below", 30.0, "RSI oversold"),
        (IndicatorKind.STOCH, "above", 80.0, "STOCH overbought"),
        (IndicatorKind.STOCH, "below", 20.0, "STOCH oversold"),
    ]

    existing_keys = {
        (
            alert.instrument,
            alert.granularity,
            alert.indicator,
            alert.condition,
            None if alert.threshold is None else float(alert.threshold),
        )
        for alert in await asyncio.to_thread(
            alert_repository.list_active_indicator_alerts_for_chat,
            chat_id,
        )
    }

    created_count = 0
    persistence_error: PersistenceWriteError | None = None
    for instrument in SCAN_INSTRUMENTS:
        if persistence_error is not None:
            break
        for indicator, condition, threshold, note in defaults:
            alert_key = (
                instrument,
                "H1",
                indicator,
                condition,
                None if threshold is None else float(threshold),
            )
            if alert_key in existing_keys:
                continue
            payload = {
                "id": None,
                "instrument": instrument,
                "granularity": "H1",
                "indicator": indicator,
                "condition": condition,
                "threshold": threshold,
                "status": AlertStatus.PENDING,
                "repeat": False,
                "cooloff_minutes": None,
                "chat_id": chat_id,
                "notes": note,
                "created_at": now,
                "fired_at": None,
            }
            try:
                await asyncio.to_thread(alert_repository.upsert_indicator_alert, payload)
                existing_keys.add(alert_key)
                created_count += 1
            except PersistenceWriteError as exc:
                persistence_error = exc
                break
            except ValueError as exc:
                _log_command_failure(
                    update,
                    context,
                    "indicatoralert",
                    exc,
                    event="telegram_command_nonfatal_failure",
                    level="warning",
                    instrument=instrument,
                    timeframe="H1",
                    indicator=indicator.value,
                    condition=condition,
                    phase="default_alert_create",
                )

    sma_cross_timeframes = ("M15", "H1", "H4", "D")
    sma_cross_defaults = [
        (IndicatorKind.SMA_CROSS, "cross_up", None, "SMA bullish cross"),
        (IndicatorKind.SMA_CROSS, "cross_down", None, "SMA bearish cross"),
    ]
    for instrument in SCAN_INSTRUMENTS:
        if persistence_error is not None:
            break
        for tf in sma_cross_timeframes:
            if persistence_error is not None:
                break
            for indicator, condition, threshold, note in sma_cross_defaults:
                alert_key = (
                    instrument,
                    tf,
                    indicator,
                    condition,
                    None if threshold is None else float(threshold),
                )
                if alert_key in existing_keys:
                    continue
                payload = {
                    "id": None,
                    "instrument": instrument,
                    "granularity": tf,
                    "indicator": indicator,
                    "condition": condition,
                    "threshold": threshold,
                    "status": AlertStatus.PENDING,
                    "repeat": False,
                    "cooloff_minutes": None,
                    "chat_id": chat_id,
                    "notes": note,
                    "created_at": now,
                    "fired_at": None,
                }
                try:
                    await asyncio.to_thread(alert_repository.upsert_indicator_alert, payload)
                    existing_keys.add(alert_key)
                    created_count += 1
                except PersistenceWriteError as exc:
                    persistence_error = exc
                    break
                except ValueError as exc:
                    _log_command_failure(
                        update,
                        context,
                        "indicatoralert",
                        exc,
                        event="telegram_command_nonfatal_failure",
                        level="warning",
                        instrument=instrument,
                        timeframe=tf,
                        indicator=indicator.value,
                        condition=condition,
                        phase="default_alert_create",
                    )

    if persistence_error is not None:
        await _reply_persistence_error(
            update,
            context,
            "indicatoralert",
            persistence_error,
            "Default indicator alerts could not be saved.",
            phase="default_alert_create",
        )
        return

    await update.effective_message.reply_text(
        f"Created {created_count} default indicator alerts.\n"
        "RSI 70/30, STOCH 80/20 on H1 per instrument.\n"
        "SMA bullish cross + bearish cross on M15/H1/H4/D per instrument.\n"
        "Use /listindicators to view."
    )


async def _require_session(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    user = update.effective_user
    chat = update.effective_chat
    if message is None or user is None or chat is None:
        return None
    _mark_polling_healthy(context.bot_data)
    session = await asyncio.to_thread(_security_manager(context).get_session, user.id)
    if session is None:
        _log_command_failure(
            update,
            context,
            "auth",
            PermissionError("Authentication required."),
            event="telegram_command_unauthorized",
            level="warning",
        )
        await message.reply_text(AUTH_REQUIRED_TEXT)
        return None
    if session.chat_id != chat.id:
        _log_command_failure(
            update,
            context,
            "auth",
            PermissionError("Session bound to a different chat."),
            event="telegram_command_unauthorized",
            level="warning",
            expected_chat_id=session.chat_id,
            actual_chat_id=chat.id,
        )
        await message.reply_text(CHAT_SCOPE_TEXT)
        return None
    try:
        await asyncio.to_thread(_security_manager(context).touch_for_chat, user.id, chat.id)
    except PersistenceWriteError as exc:
        _log_command_failure(
            update,
            context,
            "auth_touch",
            exc,
            event="telegram_command_persistence_failed",
            level="warning",
        )
    return session


async def _require_admin_session(update: Update, context: ContextTypes.DEFAULT_TYPE):
    session = await _require_session(update, context)
    if session is None:
        return None

    user = update.effective_user
    is_admin = bool(session.is_admin or (user and _security_manager(context).is_admin(user.id)))
    if is_admin:
        return session

    _log_command_failure(
        update,
        context,
        "admin_auth",
        PermissionError("Admin access required."),
        event="telegram_command_forbidden",
        level="warning",
    )
    if update.effective_message is not None:
        await update.effective_message.reply_text("Admin access required.")
    return None


async def _reject_unexpected_args(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    usage: str,
) -> bool:
    if context.args:
        _log_command_failure(
            update,
            context,
            "unexpected_args",
            ValueError("Unexpected command arguments."),
            event="telegram_command_usage_failed",
            level="warning",
            usage=usage,
        )
        message = update.effective_message
        if message is not None:
            await message.reply_text(usage)
        return True
    return False


def _command_log_fields(
    update: Update | object,
    context: ContextTypes.DEFAULT_TYPE | None,
    command: str,
    **fields: object,
) -> dict[str, object]:
    effective_user = getattr(update, "effective_user", None)
    effective_chat = getattr(update, "effective_chat", None)
    return {
        "command": command,
        "user_id": getattr(effective_user, "id", None),
        "chat_id": getattr(effective_chat, "id", None),
        "args": tuple(() if context is None else context.args or ()),
        **fields,
    }


def _log_command_failure(
    update: Update | object,
    context: ContextTypes.DEFAULT_TYPE | None,
    command: str,
    exc: BaseException,
    *,
    event: str = "telegram_command_failed",
    level: str = "error",
    **fields: object,
) -> None:
    log_failure(
        LOGGER,
        event,
        exc,
        level=level,
        **_command_log_fields(update, context, command, **fields),
    )


async def _reply_command_error(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    command: str,
    exc: BaseException,
    message: str,
    *,
    event: str = "telegram_command_failed",
    level: str = "error",
    **fields: object,
) -> None:
    _log_command_failure(
        update,
        context,
        command,
        exc,
        event=event,
        level=level,
        **fields,
    )
    if update.effective_message is not None:
        await update.effective_message.reply_text(message)


async def _reply_persistence_error(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    command: str,
    exc: PersistenceWriteError,
    message: str,
    **fields: object,
) -> None:
    await _reply_command_error(
        update,
        context,
        command,
        exc,
        message,
        event="telegram_command_persistence_failed",
        level="warning",
        **fields,
    )


async def _load_time_alert_export_document(document) -> TimeAlertExportDocument:  # type: ignore[no-untyped-def]
    try:
        telegram_file = await document.get_file()
        payload = await telegram_file.download_as_bytearray()
    except Exception as exc:
        raise ValueError("Could not download the replied time alert export file.") from exc

    try:
        parsed = json.loads(bytes(payload).decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise ValueError("Time alert import file must be UTF-8 encoded JSON.") from exc
    except json.JSONDecodeError as exc:
        raise ValueError("Time alert import file must be valid JSON.") from exc

    try:
        return TimeAlertExportDocument.model_validate(parsed)
    except ValidationError as exc:
        raise ValueError(_format_time_alert_import_validation_error(exc)) from exc


def _format_time_alert_import_validation_error(exc: ValidationError) -> str:
    error = exc.errors()[0]
    location = ".".join(str(part) for part in error.get("loc", ())) or "payload"
    message = str(error.get("msg", "invalid value"))
    return f"Invalid time alert import file at {location}: {message}"


def _build_import_time_alert_payloads(
    export_document: TimeAlertExportDocument,
    *,
    chat_id: int,
    now_utc: datetime,
) -> tuple[list[dict[str, object]], int]:
    payloads: list[dict[str, object]] = []
    skipped_expired = 0

    for definition in export_document.alerts:
        if definition.kind == TimeAlertKind.FIXED_TIME:
            assert definition.local_time is not None
            try:
                next_fire_at = next_fixed_alert_fire_at(
                    definition.local_time,
                    now_utc=now_utc,
                    timezone_name=definition.timezone_name,
                )
            except ValueError:
                if is_time_alert_local_datetime_text(definition.local_time):
                    skipped_expired += 1
                    continue
                raise
        else:
            assert definition.session_name is not None
            next_fire_at = next_session_fire_at(definition.session_name, now_utc=now_utc)

        payloads.append(
            {
                "id": None,
                "chat_id": chat_id,
                "kind": definition.kind,
                "status": "ACTIVE",
                "schedule": definition.schedule,
                "timezone_name": definition.timezone_name,
                "local_time": definition.local_time,
                "session_name": definition.session_name,
                "note": definition.note,
                "created_at": now_utc,
                "next_fire_at": next_fire_at,
                "last_fired_at": None,
            }
        )

    return payloads, skipped_expired


async def _snapshot_first(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    instrument: str,
    timeframe: str,
) -> TimeframeSnapshot | None:
    market_state = context.bot_data[MARKET_STATE_KEY]
    snapshot = market_state.get_snapshot(instrument, timeframe)
    if snapshot is None:
        await update.effective_message.reply_text(
            f"Data unavailable for {instrument} {timeframe}. Try /scan first."
        )
        return None
    return snapshot


def _snapshot_warning_line(snapshot: TimeframeSnapshot) -> str | None:
    if snapshot.freshness.is_fresh:
        return None

    last_candle = snapshot.freshness.last_completed_candle or snapshot.last_completed_candle
    age_seconds = snapshot.freshness.staleness_seconds
    age_text = f"{int(age_seconds)}s" if age_seconds is not None else "unknown"
    return (
        "Warning: snapshot is stale "
        f"(last candle: {_format_optional_time(last_candle)}, age: {age_text})."
    )


def _order_block_mitigation_label(block: OrderBlockSummary) -> str:
    return "MITIGATED" if block.is_mitigated is True else "UNMITIGATED"


def _order_block_counts(
    order_blocks: tuple[OrderBlockSummary, ...],
) -> dict[str, int]:
    mitigated = sum(1 for block in order_blocks if block.is_mitigated is True)
    unmitigated = len(order_blocks) - mitigated
    return {
        "all": len(order_blocks),
        "mitigated": mitigated,
        "unmitigated": unmitigated,
    }


def _filter_order_blocks(
    order_blocks: tuple[OrderBlockSummary, ...],
    mitigation_status: OrderBlockMitigationFilter,
) -> tuple[OrderBlockSummary, ...]:
    if mitigation_status == "all":
        return order_blocks
    if mitigation_status == "mitigated":
        return tuple(block for block in order_blocks if block.is_mitigated is True)
    return tuple(block for block in order_blocks if block.is_mitigated is not True)


def _runtime(context: ContextTypes.DEFAULT_TYPE) -> BotRuntime:
    return _runtime_from_bot_data(context.bot_data)


def _runtime_from_bot_data(bot_data: dict[str, object]) -> BotRuntime:
    return bot_data[BOT_RUNTIME_KEY]


async def _refresh_price_alert_watchlist(runtime: BotRuntime | object | None) -> None:
    if runtime is None:
        return
    stream_task = getattr(runtime, "stream_task", None)
    refresh = getattr(stream_task, "refresh_price_alert_instruments", None)
    if callable(refresh):
        await asyncio.to_thread(refresh)


def _security_manager(context: ContextTypes.DEFAULT_TYPE) -> SecurityManager:
    return context.bot_data[SECURITY_MANAGER_KEY]


def _runtime_config_manager(context: ContextTypes.DEFAULT_TYPE) -> RuntimeConfigManager:
    return context.bot_data[RUNTIME_CONFIG_MANAGER_KEY]


def _trade_repository(context: ContextTypes.DEFAULT_TYPE) -> TradeRepository:
    return context.bot_data[TRADE_REPOSITORY_KEY]


def _excursion_repository(context: ContextTypes.DEFAULT_TYPE) -> ExcursionRepository:
    return context.bot_data[EXCURSION_REPOSITORY_KEY]


def _alert_repository(context: ContextTypes.DEFAULT_TYPE) -> AlertRepository:
    return context.bot_data[ALERT_REPOSITORY_KEY]


def _trade_history_service(context: ContextTypes.DEFAULT_TYPE):
    return context.bot_data[TRADE_HISTORY_SERVICE_KEY]


def _filtered_journal_trades(
    trade_repository: TradeRepository,
    instrument: str | None,
    start_date: date | None,
    end_date: date | None,
) -> list[TradeRecord]:
    trades = trade_repository.list_open() + trade_repository.list_closed()
    if instrument is not None:
        trades = [trade for trade in trades if trade.instrument == instrument]
    if start_date is not None:
        trades = [trade for trade in trades if trade.opened_at.date() >= start_date]
    if end_date is not None:
        trades = [trade for trade in trades if trade.opened_at.date() <= end_date]
    trades.sort(key=lambda trade: trade.closed_at or trade.opened_at, reverse=True)
    return trades[:10]


def _journal_filter_summary(
    instrument: str | None,
    start_date: date | None,
    end_date: date | None,
) -> str:
    filters = []
    if instrument is not None:
        filters.append(f"instrument={instrument}")
    if start_date is not None:
        filters.append(f"from={start_date.isoformat()}")
    if end_date is not None:
        filters.append(f"to={end_date.isoformat()}")
    return "none" if not filters else ", ".join(filters)


def _format_optional_number(value: float | None) -> str:
    if value is None:
        return "None"
    return f"{value:.5f}"


def _format_optional_time(value: datetime | None) -> str:
    if value is None:
        return "None"
    return value.strftime("%Y-%m-%d %H:%M:%S UTC")


def _market_status_line(label: str, status) -> str:
    return (
        f"{label}: {'open' if status.is_market_open else 'closed'}"
        f" | Reason: {status.reason or 'n/a'}"
        f" | Next open: {_format_optional_time(status.next_open_at)}"
        f" | Next close: {_format_optional_time(status.next_close_at)}"
    )


def _format_macro_indicator_line(label: str, indicator: MacroIndicatorStatus) -> str:
    if indicator.value is None:
        return f"{label}: unavailable"
    return (
        f"{label}: {indicator.value:.5f}"
        f" | As of: {_format_optional_time(indicator.as_of)}"
    )


def _macro_source_label(status: MacroContextStatus | None) -> str:
    if status is None or status.last_refreshed_at is None:
        return "unavailable"
    return "cached" if status.used_cached else "fresh"


def _macro_summary_line(status: MacroContextStatus | None) -> str:
    if status is None or status.last_refreshed_at is None:
        if status is not None and status.last_error:
            return f"Macro: unavailable ({status.last_error})"
        return "Macro: unavailable"
    freshness = "cached" if status.used_cached else "fresh"
    if status.vix.value is None or status.dxy.value is None:
        return f"Macro: partial ({freshness})"
    return (
        f"Macro: VIX {status.vix.value:.2f} | DXY {status.dxy.value:.2f}"
        f" ({freshness})"
    )


def _current_market_hours_overview(runtime: BotRuntime):
    market_status = (
        runtime.scan_orchestrator.market_hours_status
        or runtime.scan_orchestrator.market_hours_service.get_status()
    )
    return coerce_market_hours_overview(market_status)


def _current_macro_status(runtime: BotRuntime) -> MacroContextStatus:
    orchestrator = runtime.scan_orchestrator
    status = getattr(orchestrator, "macro_status", None)
    if status is not None and (status.last_refreshed_at is not None or status.last_error is not None):
        return status

    refresh_macro = getattr(orchestrator, "refresh_macro", None)
    if callable(refresh_macro):
        try:
            return refresh_macro(force=False)
        except TypeError:
            return refresh_macro()
        except Exception as exc:
            return MacroContextStatus(last_error=str(exc))

    return status or MacroContextStatus()


def _build_runtime_health(bot_data: dict[str, object]):
    runtime = _runtime_from_bot_data(bot_data)
    scheduler = bot_data[SCHEDULER_KEY]
    supervisor = bot_data[TASK_SUPERVISOR_KEY]
    scheduler_status = scheduler.status()
    market_hours_status = _current_market_hours_overview(runtime)
    macro_status = _current_macro_status(runtime)
    return supervisor.health_snapshot(
        scheduler_status=scheduler_status,
        poller_status=_trade_poller_status_from_scheduler(scheduler_status),
        last_scan=runtime.scan_orchestrator.last_scan_status,
        calendar_status=runtime.scan_orchestrator.calendar_status,
        market_hours_status=market_hours_status,
        macro_status=macro_status,
    )


def _trade_poller_status_from_scheduler(scheduler_status) -> BackgroundTaskStatus:
    job = next((item for item in scheduler_status.jobs if item.job_id == TRADE_POLLER_JOB_ID), None)
    if job is None:
        return BackgroundTaskStatus(
            name="trade_poller",
            state="FAILED",
            restart_count=0,
            started_at=scheduler_status.started_at,
            last_heartbeat_at=None,
            last_error_at=scheduler_status.started_at,
            last_error="trade poller job not registered",
        )

    if scheduler_status.state != "RUNNING":
        state = "STOPPED"
    elif job.last_error is not None:
        state = "DEGRADED"
    else:
        state = "RUNNING"
    return BackgroundTaskStatus(
        name="trade_poller",
        state=state,
        restart_count=0,
        started_at=job.last_started_at or scheduler_status.started_at,
        last_heartbeat_at=job.last_completed_at or job.last_succeeded_at,
        last_error_at=job.last_failed_at,
        last_error=job.last_error,
    )



def _calendar_window_bounds(
    started_at_utc: datetime,
    *,
    scope: str,
) -> tuple[datetime, datetime]:
    started_at_sgt = started_at_utc.astimezone(SGT)
    if scope == "today":
        next_boundary_sgt = datetime.combine(
            started_at_sgt.date() + timedelta(days=1),
            time.min,
            tzinfo=SGT,
        )
    else:
        next_boundary_sgt = datetime.combine(
            started_at_sgt.date() + timedelta(days=7),
            time.max,
            tzinfo=SGT,
        )
    return started_at_utc, next_boundary_sgt.astimezone(timezone.utc)


def _active_session_count(security_manager: SecurityManager) -> int:
    return len(security_manager.list_sessions())


def _format_duration(started_at: datetime, completed_at: datetime) -> str:
    total_seconds = max(int((completed_at - started_at).total_seconds()), 0)
    total_minutes = total_seconds // 60
    hours, minutes = divmod(total_minutes, 60)
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def _build_help_text(is_admin: bool) -> str:
    """Build the expanded /help response."""

    sections = [
        "Market Signal Bot V3",
        "",
        "Auth:",
        "  /start <password> - Authenticate",
        "  /help - Show this reference",
        "  /logout - End session",
        "",
        "Operations:",
        "  /status - Runtime health",
        "  /status help - Explain status fields",
        "  /marketstatus - Market hours and stream state",
        "  /price <symbol> [--live] - Bid/ask/spread",
        "  /account - Balance and margin",
        "  /positions - Open trades with P/L and SL/TP",
        "  /orders - Pending orders grouped by type",
        "  /session <symbol> [tf] - Trading session windows",
        "  /dayrange <symbol> - Previous day range",
        "  /pdh <symbol> - Previous day high",
        "  /pdl <symbol> - Previous day low",
        "  /calendar [today|week] [USD EUR...] [force] - HIGH+MED news by currency",
        "",
        "Scanning:",
        "  /scan [force] - Full scan of all instruments",
        "  /scan <symbol> [force] - Single instrument refresh",
        "",
        "Analysis (require <symbol>, tf defaults to H1):",
        "  /smc <symbol> [tf] - Structure, OBs, liquidity, spread",
        "  /structure <symbol> [tf] - Recent structure breaks",
        "  /indicators <symbol> [tf] [compact|full]",
        "  /vwap <symbol> [tf] [--anchor D|W|M] [--bands 1,2]",
        "  /ob <symbol> [tf] [all|mitigated|unmitigated] - Order blocks by mitigation status",
        "  /chart <symbol> [tf] [compact|balanced|full] [count]",
        "  /extractor <symbol|all> [count] [timeframes]",
        "",
        "Trade Helper:",
        "  /journal [id] [--instrument X] [--from D] [--to D]",
        "  /tradehistory [period] [view] [instrument] [page] - Transaction-based trade history and realized PnL",
        "    period = day|week|month|today|thisweek|thismonth|custom:YYYY-MM-DD:YYYY-MM-DD",
        "    view = all|opened|closed, instrument = exact OANDA symbol, page = positive integer",
        "    Uses JOURNAL_TIMEZONE (default Asia/Singapore, UTC+8)",
        "  /label <trade_id> <text> - Add note to trade",
        "  /maemfe [trade_id] - MAE/MFE excursion data",
        "",
        "Alerts:",
        "  /pricealert <symbol> <price> <above|below> [note]",
        "  /listpricealerts - List pending price alerts",
        "  /clearpricealert <id> - Cancel price alert",
        "  /indicatoralert <symbol> <tf> <RSI|STOCH|MACD|SMA_CROSS> <cond> [threshold] [note]",
        "  /indicatoralert defaults - Create starter alerts",
        "  /listindicators - List active indicator alerts",
        "  /clearindicator <id> - Cancel indicator alert",
        "  /timealert at <HH:MM> [daily|once] [note]",
        "  /timealert at <YYYY-MM-DD> <HH:MM> [once] [note]",
        "  /timealert session <london|newyork|market_open> [note]",
        "  /listtimealerts - List active time alerts",
        "  /cleartimealert <id> - Cancel time alert",
        "  /exporttimealerts - Export active time alerts as JSON",
        "  /importtimealerts - Reply to a JSON export file to import time alerts",
        "",
        "Config:",
        "  /config - View current runtime config",
        "  /config <key> <value> - Set chart|chart_mode|scan_interval|trade_push|session_alerts",
    ]
    if is_admin:
        sections.extend(
            [
                "",
                "Admin:",
                "  /tradehistory_backfill <YYYY-MM-DD> <YYYY-MM-DD> - Backfill trade history for an inclusive local-date range",
                "    Example: /tradehistory_backfill 2025-01-01 2026-04-01",
            ]
        )
    return "\n".join(sections)


async def _run_application_with_mcp(
    application: Application,
    settings: Settings,
    *,
    stop_event: asyncio.Event | None = None,
    config_factory=uvicorn.Config,
    server_factory=uvicorn.Server,
) -> None:
    """Run Telegram polling and the embedded MCP HTTP server in one event loop."""

    from mcp_server.server import build_mcp_http_app

    if application.updater is None:
        raise RuntimeError("Application must have an updater to run polling with embedded MCP.")

    resolved_stop_event = stop_event or asyncio.Event()
    signal_handlers_registered = False
    if stop_event is None:
        loop = asyncio.get_running_loop()
        for handled_signal in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(handled_signal, resolved_stop_event.set)
                signal_handlers_registered = True
            except NotImplementedError:
                break
            except RuntimeError:
                break

    runtime = _runtime_from_bot_data(application.bot_data)
    http_app = build_mcp_http_app(runtime=runtime, settings=settings)
    server = server_factory(
        config_factory(
            http_app,
            host=settings.mcp_http_host,
            port=settings.mcp_http_port,
            log_level=settings.log_level.lower(),
            access_log=False,
        )
    )
    initialized = False
    started = False
    updater_started = False
    server_task: asyncio.Task | None = None
    stop_task: asyncio.Task | None = None

    try:
        await application.initialize()
        initialized = True
        if application.post_init:
            await application.post_init(application)

        await application.updater.start_polling(
            error_callback=_build_polling_error_callback(application),
        )
        updater_started = True
        await application.start()
        started = True

        server_task = asyncio.create_task(server.serve(), name="embedded_mcp_http_server")
        stop_task = asyncio.create_task(resolved_stop_event.wait(), name="embedded_stop_wait")
        done, _pending = await asyncio.wait(
            {server_task, stop_task},
            return_when=asyncio.FIRST_COMPLETED,
        )

        if server_task in done:
            server_error = server_task.exception()
            if server_error is not None:
                raise server_error
            if not resolved_stop_event.is_set():
                LOGGER.warning("embedded_mcp_http_server_stopped_unexpectedly")

        server.should_exit = True
        resolved_stop_event.set()
    finally:
        if stop_task is not None and not stop_task.done():
            stop_task.cancel()
            try:
                await stop_task
            except asyncio.CancelledError:
                pass

        if server_task is not None:
            server.should_exit = True
            try:
                await asyncio.wait_for(server_task, timeout=10)
            except asyncio.TimeoutError:
                server_task.cancel()
                try:
                    await server_task
                except asyncio.CancelledError:
                    pass

        if updater_started and application.updater.running:
            await application.updater.stop()
        if started and application.running:
            await application.stop()
            if application.post_stop:
                await application.post_stop(application)
        if initialized:
            await application.shutdown()
            if application.post_shutdown:
                await application.post_shutdown(application)

        if signal_handlers_registered:
            loop = asyncio.get_running_loop()
            for handled_signal in (signal.SIGINT, signal.SIGTERM):
                try:
                    loop.remove_signal_handler(handled_signal)
                except NotImplementedError:
                    break


def main() -> int:
    """Build and run the Telegram runtime with optional embedded MCP HTTP."""

    settings = get_settings()
    configure_logging(settings)
    application = build_application(settings=settings)
    if getattr(settings, "mcp_http_enabled", False):
        asyncio.run(_run_application_with_mcp(application, settings))
    else:
        application.run_polling()
    return 0


__all__ = [
    "AUTH_REQUIRED_TEXT",
    "COMMAND_REGISTRY",
    "build_application",
    "main",
    "register_handlers",
]
