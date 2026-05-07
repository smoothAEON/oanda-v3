"""Shared MCP adapters over the live Gold Signal Bot runtime."""

from __future__ import annotations

import asyncio
import json
from datetime import date, datetime, timedelta, timezone
from typing import Any, Literal

from pydantic import TypeAdapter

from alerts.alert_repository import EVALUATED_INDICATOR_ALERT_TIMEFRAMES
from alerts.defaults import INDICATOR_ALERT_DEFAULTS
from alerts.time_alert_engine import DEFAULT_TIME_ALERT_TIMEZONE, next_fixed_alert_fire_at, next_session_fire_at
from bot.pricing import resolve_price_quote
from bot.parsing import (
    TRACKED_CALENDAR_CURRENCIES,
    OrderBlockMitigationFilter,
    normalize_broker_instrument,
    normalize_command_instrument,
    normalize_command_timeframe,
    normalize_order_block_mitigation_status,
)
from bot.runtime import BotRuntime
from bot.runtime_views import (
    build_runtime_health,
    calendar_window_bounds,
    current_macro_status,
    current_market_hours_overview,
)
from config.settings import Settings
from core.candle_policy import (
    OANDA_CANDLE_GRANULARITIES,
    OANDA_MAX_CANDLE_COUNT,
    normalize_oanda_candle_granularity,
)
from core.enums import AlertStatus, IndicatorKind, TimeAlertKind, TradeState
from core.models import AlertHistoryPage, SpreadHistoryEntry, SpreadResult, SpreadSnapshot
from core.instrument_registry import (
    INSTRUMENT_ALIASES,
    INSTRUMENT_REGISTRY,
    SCAN_INSTRUMENTS,
    get_instrument_spec,
    get_oanda_instrument_catalog,
    get_pip_size,
)
from data.correlation_service import CorrelationService
from data.yfinance_service import YFinanceService
from indicators import build_vwap_read_result, normalize_vwap_bands, resolve_vwap_candle_count, validate_vwap_timeframe
from journal.mae_mfe_service import MaeMfeService
from journal.trade_stats_service import TradeStatsService
from providers.base import PriceSnapshot

RefreshPolicy = Literal["never", "if_missing", "always"]
CalendarScope = Literal["today", "week"]
IndicatorCondition = Literal["above", "below", "cross_up", "cross_down"]
TimeAlertCreateKind = Literal["at", "session"]
OhlcPriceComponent = Literal["mid", "bid_ask"]

_JSON_ADAPTER = TypeAdapter(Any)
_COMPACT_INDICATOR_NAMES = frozenset({"rsi", "atr", "ema_20", "ema_50", "adx"})
_SUPPORTED_INDICATOR_CONDITIONS = frozenset({"above", "below", "cross_up", "cross_down"})
_SUPPORTED_TIME_ALERT_SCHEDULES = frozenset({"daily", "once"})
_SUPPORTED_TIME_ALERT_SESSIONS = frozenset({"london", "newyork", "market_open"})
_PUBLISHED_SNAPSHOT_TIMEFRAMES = frozenset(EVALUATED_INDICATOR_ALERT_TIMEFRAMES)


class BotMcpService:
    """Expose the live bot runtime through JSON-first MCP-friendly methods."""

    def __init__(
        self,
        *,
        runtime: BotRuntime,
        settings: Settings | None = None,
        yfinance_service: YFinanceService | None = None,
    ) -> None:
        self.runtime = runtime
        self.settings = settings or runtime.settings
        self.yfinance_service = yfinance_service or YFinanceService()
        self._mae_mfe_service: MaeMfeService | None = None
        self._trade_stats_service: TradeStatsService | None = None
        self._correlation_service: CorrelationService | None = None
        self.default_chat_id = (
            self.settings.telegram_chat_id
            if self.settings.mcp_default_chat_id is None
            else self.settings.mcp_default_chat_id
        )

    async def get_runtime_status(self) -> dict[str, Any]:
        status = await asyncio.to_thread(build_runtime_health, self.runtime)
        return self._jsonable(status)

    async def get_market_status(self) -> dict[str, Any]:
        market_hours = await asyncio.to_thread(current_market_hours_overview, self.runtime)
        macro_status = await asyncio.to_thread(current_macro_status, self.runtime)
        stream_status = self.runtime.stream_task.stream_status()
        return self._jsonable(
            {
                "market_hours": market_hours,
                "stream": stream_status,
                "macro": macro_status,
                "calendar": self.runtime.scan_orchestrator.calendar_status,
            }
        )

    async def get_macro_context(self, force: bool = False) -> dict[str, Any]:
        status = await asyncio.to_thread(self.runtime.scan_orchestrator.refresh_macro, force=force)
        return self._jsonable(status)

    async def search_yfinance_tickers(
        self,
        query: str,
        limit: int = 8,
        news_count: int = 0,
        enable_fuzzy: bool = False,
    ) -> dict[str, Any]:
        result = await asyncio.to_thread(
            self.yfinance_service.search_tickers,
            query,
            limit=limit,
            news_count=news_count,
            enable_fuzzy=enable_fuzzy,
        )
        return self._jsonable(result)

    async def get_yfinance_ticker(
        self,
        symbol: str,
        include_news: bool = False,
        news_limit: int = 5,
    ) -> dict[str, Any]:
        result = await asyncio.to_thread(
            self.yfinance_service.get_ticker,
            symbol,
            include_news=include_news,
            news_limit=news_limit,
        )
        return self._jsonable(result)

    async def get_yfinance_history(
        self,
        symbol: str,
        period: str | None = "1mo",
        interval: str = "1d",
        start: str | None = None,
        end: str | None = None,
        prepost: bool = False,
        actions: bool = False,
        auto_adjust: bool = True,
        max_rows: int = 250,
    ) -> dict[str, Any]:
        result = await asyncio.to_thread(
            self.yfinance_service.get_history,
            symbol,
            period=period,
            interval=interval,
            start=start,
            end=end,
            prepost=prepost,
            actions=actions,
            auto_adjust=auto_adjust,
            max_rows=max_rows,
        )
        return self._jsonable(result)

    async def get_yfinance_news(self, symbol: str, limit: int = 8) -> dict[str, Any]:
        result = await asyncio.to_thread(
            self.yfinance_service.get_news,
            symbol,
            limit=limit,
        )
        return self._jsonable(result)

    async def get_calendar(
        self,
        scope: CalendarScope = "today",
        currencies: list[str] | None = None,
        force: bool = False,
    ) -> dict[str, Any]:
        resolved_scope = self._normalize_calendar_scope(scope)
        requested_currencies = self._normalize_currencies(currencies)
        orchestrator = self.runtime.scan_orchestrator

        if force or orchestrator.calendar_status.calendar_version == 0:
            status = await asyncio.to_thread(orchestrator.refresh_calendar, force=True)
        else:
            status = orchestrator.calendar_status

        window_start_utc, window_end_utc = calendar_window_bounds(
            datetime.now(timezone.utc),
            scope=resolved_scope,
        )
        events = await asyncio.to_thread(
            orchestrator.calendar_provider.filter_events,
            impacts=("HIGH", "MEDIUM"),
            currencies=requested_currencies,
            window_start=window_start_utc,
            window_end=window_end_utc,
        )
        return self._jsonable(
            {
                "scope": resolved_scope,
                "requested_currencies": requested_currencies,
                "window_start_utc": window_start_utc,
                "window_end_utc": window_end_utc,
                "status": status,
                "events": events,
            }
        )

    async def scan_all(self, force: bool = False) -> dict[str, Any]:
        status = await asyncio.to_thread(self.runtime.scan_orchestrator.scan_all, force=force)
        return self._jsonable(status)

    async def scan_instrument(self, instrument: str, force: bool = False) -> dict[str, Any]:
        resolved_instrument = normalize_command_instrument(instrument)
        snapshots = await asyncio.to_thread(
            self.runtime.scan_orchestrator.refresh_instrument,
            resolved_instrument,
            force=force,
        )
        return self._jsonable(
            self._sanitize_mcp_payload(
                {
                    "snapshots": {
                        timeframe: self._snapshot_payload(snapshot)
                        for timeframe, snapshot in (snapshots or {}).items()
                    },
                    "force": force,
                    "last_scan": self.runtime.scan_orchestrator.last_scan_status,
                }
            )
        )

    async def refresh_snapshot(
        self,
        instrument: str,
        timeframe: str = "H1",
        force: bool = False,
    ) -> dict[str, Any]:
        resolved_instrument = normalize_command_instrument(instrument)
        resolved_timeframe = self._normalize_snapshot_timeframe(timeframe)
        snapshot = await asyncio.to_thread(
            self.runtime.scan_orchestrator.refresh_snapshot,
            resolved_instrument,
            resolved_timeframe,
            force=force,
        )
        if snapshot is None:
            raise ValueError(f"Data unavailable for {resolved_instrument} {resolved_timeframe}.")
        payload = self._snapshot_payload(snapshot)
        payload["force"] = force
        return self._jsonable(payload)

    async def get_candles(
        self,
        instrument: str,
        timeframe: str = "H1",
        count: int | None = None,
        force: bool = False,
    ) -> dict[str, Any]:
        return await self._mid_ohlc_payload(
            instrument=instrument,
            timeframe=timeframe,
            count=count,
            force=force,
        )

    async def get_ohlc(
        self,
        instrument: str,
        timeframe: str = "H1",
        count: int | None = None,
        price_component: OhlcPriceComponent = "mid",
        force: bool = False,
    ) -> dict[str, Any]:
        resolved_price_component = self._normalize_price_component(price_component)
        if resolved_price_component == "mid":
            return await self._mid_ohlc_payload(
                instrument=instrument,
                timeframe=timeframe,
                count=count,
                force=force,
            )
        return await self._bid_ask_ohlc_payload(
            instrument=instrument,
            timeframe=timeframe,
            count=count,
            force=force,
        )

    async def get_price(self, instrument: str, prefer_live: bool = False) -> dict[str, Any]:
        resolved_instrument = normalize_broker_instrument(instrument)
        quote = await resolve_price_quote(
            instrument=resolved_instrument,
            account_client=self.runtime.account_client,
            stream_task=self.runtime.stream_task,
            prefer_live=prefer_live,
            on_resolved=self._quote_recorder_callback(reason="mcp_get_price"),
        )
        return self._jsonable(
            {
                "instrument": resolved_instrument,
                "bid": quote.bid,
                "ask": quote.ask,
                "spread_pips": quote.spread_pips,
                "fetched_at": quote.fetched_at,
                "source": quote.source,
                "fallback_note": quote.fallback_note,
            }
        )

    async def get_account_summary(self) -> dict[str, Any]:
        summary = await self.runtime.account_client.get_account_summary()
        return self._jsonable(summary)

    async def list_transfers(
        self,
        start_date: str | None = None,
        end_date: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        if limit <= 0:
            raise ValueError("limit must be a positive integer.")

        end_day = date.fromisoformat(end_date) if end_date is not None else datetime.now(timezone.utc).date()
        start_day = (
            date.fromisoformat(start_date)
            if start_date is not None
            else end_day - timedelta(days=364)
        )
        if end_day < start_day:
            raise ValueError("end_date must be greater than or equal to start_date.")

        window_start_utc = datetime.combine(start_day, datetime.min.time(), tzinfo=timezone.utc)
        window_end_utc = datetime.combine(end_day + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)
        transfers = await asyncio.to_thread(
            self.runtime.trade_history_service.history_client.fetch_transactions_for_window_sync,
            window_start_utc,
            window_end_utc,
            "TRANSFER_FUNDS",
        )
        newest_first = list(reversed(transfers))
        bounded = newest_first[:limit]
        return self._jsonable(
            {
                "window_start_utc": window_start_utc,
                "window_end_utc": window_end_utc,
                "limit": limit,
                "returned_count": len(bounded),
                "transfers": bounded,
            }
        )

    async def list_open_positions(self) -> dict[str, Any]:
        positions = await self.runtime.account_client.get_open_positions()
        mid_prices = await self._mid_prices_for({position.instrument for position in positions})
        enriched = [
            self._enrich_position(position, mid_prices.get(position.instrument))
            for position in positions
        ]
        return self._jsonable({"positions": enriched, "mid_prices": mid_prices})

    async def list_open_orders(self) -> dict[str, Any]:
        orders = await self.runtime.account_client.get_open_orders()
        mid_prices = await self._mid_prices_for(
            {order.instrument for order in orders if order.instrument is not None}
        )
        enriched = [
            self._enrich_order(order, None if order.instrument is None else mid_prices.get(order.instrument))
            for order in orders
        ]
        return self._jsonable({"orders": enriched, "mid_prices": mid_prices})

    async def get_session_context(
        self,
        instrument: str,
        timeframe: str = "H1",
        refresh_policy: RefreshPolicy = "if_missing",
    ) -> dict[str, Any]:
        snapshot = await self._resolve_snapshot(instrument, timeframe, refresh_policy=refresh_policy)
        return self._jsonable(
            {
                **self._snapshot_metadata(snapshot),
                "sessions": snapshot.smc_context.sessions,
            }
        )

    async def get_day_range(
        self,
        instrument: str,
        refresh_policy: RefreshPolicy = "if_missing",
    ) -> dict[str, Any]:
        snapshot = await self._resolve_snapshot(instrument, "H1", refresh_policy=refresh_policy)
        previous = snapshot.smc_context.previous_high_low
        pip_size = get_instrument_spec(snapshot.instrument).pip_size
        range_pips = None
        if previous is not None and previous.previous_high is not None and previous.previous_low is not None:
            range_pips = (previous.previous_high - previous.previous_low) / pip_size
        return self._jsonable(
            {
                **self._snapshot_metadata(snapshot),
                "previous_high_low": previous,
                "range_pips": range_pips,
            }
        )

    async def get_previous_day_levels(
        self,
        instrument: str,
        refresh_policy: RefreshPolicy = "if_missing",
    ) -> dict[str, Any]:
        snapshot = await self._resolve_snapshot(instrument, "H1", refresh_policy=refresh_policy)
        previous = snapshot.smc_context.previous_high_low
        return self._jsonable(
            {
                **self._snapshot_metadata(snapshot),
                "previous_high": None if previous is None else previous.previous_high,
                "previous_low": None if previous is None else previous.previous_low,
                "broken_high": False if previous is None else previous.broken_high,
                "broken_low": False if previous is None else previous.broken_low,
            }
        )

    async def get_smc_snapshot(
        self,
        instrument: str,
        timeframe: str = "H1",
        refresh_policy: RefreshPolicy = "if_missing",
    ) -> dict[str, Any]:
        snapshot = await self._resolve_snapshot(instrument, timeframe, refresh_policy=refresh_policy)
        return self._snapshot_payload(snapshot)

    async def get_structure(
        self,
        instrument: str,
        timeframe: str = "H1",
        refresh_policy: RefreshPolicy = "if_missing",
    ) -> dict[str, Any]:
        snapshot = await self._resolve_snapshot(instrument, timeframe, refresh_policy=refresh_policy)
        return self._jsonable(self._sanitize_mcp_payload({**self._snapshot_metadata(snapshot), **self._structure_evidence(snapshot)}))

    async def get_indicators(
        self,
        instrument: str,
        timeframe: str = "H1",
        mode: Literal["compact", "full"] = "compact",
        refresh_policy: RefreshPolicy = "if_missing",
    ) -> dict[str, Any]:
        snapshot = await self._resolve_snapshot(instrument, timeframe, refresh_policy=refresh_policy)
        resolved_mode = str(mode).strip().lower()
        if resolved_mode not in {"compact", "full"}:
            raise ValueError("mode must be 'compact' or 'full'.")
        metrics = list(snapshot.indicators.metrics)
        if resolved_mode == "compact":
            metrics = [metric for metric in metrics if metric.name in _COMPACT_INDICATOR_NAMES][:8]
        return self._jsonable(
            {
                **self._snapshot_metadata(snapshot),
                "mode": resolved_mode,
                "metrics": metrics,
                "tick_volume_metrics": snapshot.indicators.tick_volume_metrics,
            }
        )

    async def get_vwap(
        self,
        instrument: str,
        timeframe: str = "H1",
        anchor: str = "D",
        bands: list[float] | None = None,
    ) -> dict[str, Any]:
        resolved_instrument = normalize_command_instrument(instrument)
        resolved_timeframe = validate_vwap_timeframe(normalize_command_timeframe(timeframe))
        resolved_bands = normalize_vwap_bands(bands)
        count = resolve_vwap_candle_count(resolved_timeframe, anchor)
        candles = await asyncio.to_thread(
            self.runtime.market_data_provider.get_candles,
            resolved_instrument,
            resolved_timeframe,
            count,
        )
        freshness = await asyncio.to_thread(
            self.runtime.market_data_provider.get_candle_freshness,
            resolved_instrument,
            resolved_timeframe,
        )
        result = await asyncio.to_thread(
            build_vwap_read_result,
            candles,
            instrument=resolved_instrument,
            timeframe=resolved_timeframe,
            anchor=anchor,
            bands=resolved_bands,
            source=None if freshness.source is None else str(freshness.source),
        )
        return self._jsonable(result)

    async def get_order_blocks(
        self,
        instrument: str,
        timeframe: str = "H1",
        refresh_policy: RefreshPolicy = "if_missing",
        mitigation_status: OrderBlockMitigationFilter = "all",
    ) -> dict[str, Any]:
        snapshot = await self._resolve_snapshot(instrument, timeframe, refresh_policy=refresh_policy)
        resolved_status = normalize_order_block_mitigation_status(mitigation_status)
        return self._jsonable(
            self._sanitize_mcp_payload(
                {
                    **self._snapshot_metadata(snapshot),
                    "mitigation_status_filter": resolved_status,
                    "order_block_counts": self._order_block_counts(snapshot),
                    "order_blocks": self._zone_evidence(
                        snapshot,
                        mitigation_status=resolved_status,
                    ),
                }
            )
        )

    async def list_journal_trades(
        self,
        instrument: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        limit: int = 10,
    ) -> dict[str, Any]:
        if limit <= 0:
            raise ValueError("limit must be a positive integer.")
        resolved_instrument = None if instrument is None else normalize_broker_instrument(instrument)
        start_day = None if start_date is None else date.fromisoformat(start_date)
        end_day = None if end_date is None else date.fromisoformat(end_date)
        trades = await asyncio.to_thread(
            self._filtered_journal_trades,
            resolved_instrument,
            start_day,
            end_day,
        )
        return self._jsonable(
            {
                "filters": {
                    "instrument": resolved_instrument,
                    "start_date": start_day,
                    "end_date": end_day,
                    "limit": limit,
                },
                "trades": trades[:limit],
            }
        )

    async def get_journal_trade(self, trade_id: str) -> dict[str, Any]:
        trade = await asyncio.to_thread(self.runtime.trade_repository.get, trade_id)
        if trade is None:
            raise ValueError(f"Trade {trade_id} not found.")
        samples = await asyncio.to_thread(self.runtime.excursion_repository.list_for_trade, trade.trade_id)
        mae_mfe = await self._mae_mfe_service_for_runtime().summary_for_trade(trade, samples=samples)
        current_price = trade.close_price or trade.open_price
        current_price_source = "stored_close_price" if trade.close_price is not None else "entry_price"
        current_price_fallback_note: str | None = None
        if trade.state == TradeState.OPEN:
            quote = await self._resolve_trade_price_quote(trade.instrument)
            current_price = quote["bid"] if trade.units > 0 else quote["ask"]
            current_price_source = str(quote["source"])
            current_price_fallback_note = (
                None if quote.get("fallback_note") is None else str(quote["fallback_note"])
            )
        return self._jsonable(
            {
                "trade": trade,
                "samples": samples,
                "mae_mfe": mae_mfe,
                "current_price": current_price,
                "current_price_source": current_price_source,
                "current_price_fallback_note": current_price_fallback_note,
            }
        )

    async def get_mae_mfe(self, trade_id: str | None = None) -> dict[str, Any]:
        if trade_id is not None:
            return await self.get_journal_trade(trade_id)

        open_trades = await asyncio.to_thread(self.runtime.trade_repository.list_open)
        summaries = await self._mae_mfe_service_for_runtime().summary_map_for_open_trades(open_trades)
        current_prices: dict[str, float] = {}
        current_price_sources: dict[str, str] = {}
        current_price_fallback_notes: dict[str, str | None] = {}
        pricing_by_instrument = await self._pricing_by_instrument(
            {trade.instrument for trade in open_trades}
        )
        for instrument, pricing in pricing_by_instrument.items():
            for trade in open_trades:
                if trade.instrument != instrument:
                    continue
                current_prices[trade.trade_id] = pricing["bid"] if trade.units > 0 else pricing["ask"]
                current_price_sources[trade.trade_id] = str(pricing["source"])
                current_price_fallback_notes[trade.trade_id] = (
                    None
                    if pricing.get("fallback_note") is None
                    else str(pricing["fallback_note"])
                )
        records = []
        for trade in open_trades:
            records.append(
                {
                    "trade": trade,
                    "summary": summaries.get(trade.trade_id),
                    "current_price": current_prices.get(trade.trade_id),
                    "current_price_source": current_price_sources.get(trade.trade_id),
                    "current_price_fallback_note": current_price_fallback_notes.get(trade.trade_id),
                }
            )
        return self._jsonable({"open_trades": records})

    async def get_trade_history(
        self,
        period: str = "day",
        view: Literal["all", "opened", "closed"] = "all",
        instrument: str | None = None,
        page: int = 1,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> dict[str, Any]:
        resolved_instrument = None if instrument is None else normalize_broker_instrument(instrument)
        result = await asyncio.to_thread(
            self.runtime.trade_history_service.get_trade_history,
            period,
            view,
            resolved_instrument,
            page,
            start_date,
            end_date,
        )
        return self._jsonable(result)

    async def create_price_alert(
        self,
        instrument: str,
        target_price: float,
        direction: Literal["above", "below"],
        note: str | None = None,
    ) -> dict[str, Any]:
        resolved_instrument = normalize_broker_instrument(instrument)
        payload = {
            "id": None,
            "instrument": resolved_instrument,
            "target_price": target_price,
            "direction": self._normalize_price_direction(direction),
            "status": AlertStatus.PENDING,
            "chat_id": self.default_chat_id,
            "notes": note,
            "created_at": datetime.now(timezone.utc),
            "fired_at": None,
        }
        created = await asyncio.to_thread(self.runtime.alert_repository.upsert_price_alert, payload)
        await self._refresh_price_alert_stream_watchlist()
        return self._jsonable(created)

    async def list_price_alerts(self) -> dict[str, Any]:
        alerts = await asyncio.to_thread(
            self.runtime.alert_repository.list_pending_price_alerts_for_chat,
            self.default_chat_id,
        )
        return self._jsonable({"chat_id": self.default_chat_id, "alerts": alerts})

    async def clear_price_alert(self, alert_id: int) -> dict[str, Any]:
        cleared = await asyncio.to_thread(
            self.runtime.alert_repository.cancel_price_alert_for_chat,
            alert_id,
            self.default_chat_id,
        )
        if cleared is None:
            raise ValueError(f"Price alert {alert_id} not found for the default MCP chat.")
        await self._refresh_price_alert_stream_watchlist()
        return self._jsonable(cleared)

    async def clear_all_price_alerts(
        self,
        confirm: bool,
        instrument: str | None = None,
    ) -> dict[str, Any]:
        if not confirm:
            raise ValueError("clear_all_price_alerts requires confirm=true.")
        resolved_instrument = None if instrument is None else normalize_broker_instrument(instrument)
        cleared = await asyncio.to_thread(
            self.runtime.alert_repository.cancel_price_alerts_for_chat,
            self.default_chat_id,
            instrument=resolved_instrument,
        )
        await self._refresh_price_alert_stream_watchlist()
        return self._jsonable(
            {
                "chat_id": self.default_chat_id,
                "instrument": resolved_instrument,
                "cleared_count": len(cleared),
                "cleared_alerts": cleared,
            }
        )

    async def replace_alert_grid(
        self,
        instrument: str,
        alerts: list[dict[str, Any]],
        confirm: bool,
    ) -> dict[str, Any]:
        if not confirm:
            raise ValueError("replace_alert_grid requires confirm=true.")
        resolved_instrument = normalize_broker_instrument(instrument)
        existing = await asyncio.to_thread(
            self.runtime.alert_repository.list_pending_price_alerts_for_chat,
            self.default_chat_id,
        )
        cleared_count = len([alert for alert in existing if alert.instrument == resolved_instrument])
        payloads = [self._normalize_price_grid_alert(alert) for alert in alerts]
        created = await asyncio.to_thread(
            self.runtime.alert_repository.replace_price_alert_grid_for_chat,
            self.default_chat_id,
            instrument=resolved_instrument,
            alerts=payloads,
        )
        await self._refresh_price_alert_stream_watchlist()
        return self._jsonable(
            {
                "chat_id": self.default_chat_id,
                "instrument": resolved_instrument,
                "cleared_count": cleared_count,
                "created_count": len(created),
                "alerts": created,
            }
        )

    async def create_indicator_alert(
        self,
        instrument: str,
        timeframe: str,
        indicator: str,
        condition: IndicatorCondition,
        threshold: float | None = None,
        note: str | None = None,
        repeat: bool = False,
        cooloff_minutes: int | None = None,
    ) -> dict[str, Any]:
        resolved_instrument = normalize_command_instrument(instrument)
        resolved_timeframe = normalize_command_timeframe(timeframe)
        resolved_indicator = self._normalize_indicator(indicator)
        resolved_condition = self._normalize_indicator_condition(condition)
        payload = {
            "id": None,
            "instrument": resolved_instrument,
            "granularity": resolved_timeframe,
            "indicator": resolved_indicator,
            "condition": resolved_condition,
            "threshold": threshold,
            "status": AlertStatus.PENDING,
            "repeat": repeat,
            "cooloff_minutes": cooloff_minutes,
            "chat_id": self.default_chat_id,
            "notes": note,
            "created_at": datetime.now(timezone.utc),
            "fired_at": None,
        }
        created = await asyncio.to_thread(self.runtime.alert_repository.upsert_indicator_alert, payload)
        return self._jsonable(created)

    async def seed_default_indicator_alerts(self) -> dict[str, Any]:
        chat_id = self.default_chat_id
        alert_repository = self.runtime.alert_repository
        now = datetime.now(timezone.utc)
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

        created: list[Any] = []
        defaults = [
            (IndicatorKind.RSI, "above", 70.0, "RSI overbought"),
            (IndicatorKind.RSI, "below", 30.0, "RSI oversold"),
            (IndicatorKind.STOCH, "above", 80.0, "STOCH overbought"),
            (IndicatorKind.STOCH, "below", 20.0, "STOCH oversold"),
        ]
        for instrument_symbol in SCAN_INSTRUMENTS:
            for indicator_kind, condition, threshold, note in defaults:
                key = (instrument_symbol, "H1", indicator_kind, condition, threshold)
                if key in existing_keys:
                    continue
                created.append(
                    await asyncio.to_thread(
                        alert_repository.upsert_indicator_alert,
                        {
                            "id": None,
                            "instrument": instrument_symbol,
                            "granularity": "H1",
                            "indicator": indicator_kind,
                            "condition": condition,
                            "threshold": threshold,
                            "status": AlertStatus.PENDING,
                            "repeat": False,
                            "cooloff_minutes": None,
                            "chat_id": chat_id,
                            "notes": note,
                            "created_at": now,
                            "fired_at": None,
                        },
                    )
                )
                existing_keys.add(key)

        for instrument_symbol in SCAN_INSTRUMENTS:
            for timeframe_name in EVALUATED_INDICATOR_ALERT_TIMEFRAMES:
                for condition, note in (("cross_up", "SMA golden cross"), ("cross_down", "SMA death cross")):
                    key = (instrument_symbol, timeframe_name, IndicatorKind.SMA_CROSS, condition, None)
                    if key in existing_keys:
                        continue
                    created.append(
                        await asyncio.to_thread(
                            alert_repository.upsert_indicator_alert,
                            {
                                "id": None,
                                "instrument": instrument_symbol,
                                "granularity": timeframe_name,
                                "indicator": IndicatorKind.SMA_CROSS,
                                "condition": condition,
                                "threshold": None,
                                "status": AlertStatus.PENDING,
                                "repeat": False,
                                "cooloff_minutes": None,
                                "chat_id": chat_id,
                                "notes": note,
                                "created_at": now,
                                "fired_at": None,
                            },
                        )
                    )
                    existing_keys.add(key)

        return self._jsonable({"chat_id": chat_id, "created_count": len(created), "created_alerts": created})

    async def list_indicator_alerts(self) -> dict[str, Any]:
        alerts = await asyncio.to_thread(
            self.runtime.alert_repository.list_active_indicator_alerts_for_chat,
            self.default_chat_id,
        )
        return self._jsonable({"chat_id": self.default_chat_id, "alerts": alerts})

    async def clear_indicator_alert(self, alert_id: int) -> dict[str, Any]:
        cleared = await asyncio.to_thread(
            self.runtime.alert_repository.cancel_indicator_alert_for_chat,
            alert_id,
            self.default_chat_id,
        )
        if cleared is None:
            raise ValueError(f"Indicator alert {alert_id} not found for the default MCP chat.")
        return self._jsonable(cleared)

    async def clear_all_indicator_alerts(
        self,
        confirm: bool,
        instrument: str | None = None,
        timeframe: str | None = None,
        indicator: str | None = None,
    ) -> dict[str, Any]:
        if not confirm:
            raise ValueError("clear_all_indicator_alerts requires confirm=true.")
        resolved_instrument = None if instrument is None else normalize_broker_instrument(instrument)
        resolved_timeframe = None if timeframe is None else normalize_command_timeframe(timeframe)
        resolved_indicator = None if indicator is None else self._normalize_indicator(indicator).value
        cleared = await asyncio.to_thread(
            self.runtime.alert_repository.cancel_indicator_alerts_for_chat,
            self.default_chat_id,
            instrument=resolved_instrument,
            granularity=resolved_timeframe,
            indicator=resolved_indicator,
        )
        return self._jsonable(
            {
                "chat_id": self.default_chat_id,
                "instrument": resolved_instrument,
                "timeframe": resolved_timeframe,
                "indicator": resolved_indicator,
                "cleared_count": len(cleared),
                "cleared_alerts": cleared,
            }
        )

    async def create_time_alert(
        self,
        kind: TimeAlertCreateKind,
        local_time: str | None = None,
        schedule: str | None = None,
        session_name: str | None = None,
        note: str | None = None,
    ) -> dict[str, Any]:
        resolved_kind = str(kind).strip().lower()
        now = datetime.now(timezone.utc)
        if resolved_kind == "at":
            if local_time is None:
                raise ValueError("local_time is required when kind='at'.")
            if " " in local_time.strip():
                resolved_schedule = "once"
            else:
                resolved_schedule = "daily" if schedule is None else str(schedule).strip().lower()
                if resolved_schedule not in _SUPPORTED_TIME_ALERT_SCHEDULES:
                    raise ValueError("Fixed time alerts support 'daily' or 'once' schedules only.")
            next_fire_at = next_fixed_alert_fire_at(
                local_time,
                now_utc=now,
                timezone_name=DEFAULT_TIME_ALERT_TIMEZONE,
            )
            payload = {
                "id": None,
                "chat_id": self.default_chat_id,
                "kind": TimeAlertKind.FIXED_TIME,
                "status": "ACTIVE",
                "schedule": resolved_schedule,
                "timezone_name": DEFAULT_TIME_ALERT_TIMEZONE,
                "local_time": local_time,
                "session_name": None,
                "note": note,
                "created_at": now,
                "next_fire_at": next_fire_at,
                "last_fired_at": None,
            }
        elif resolved_kind == "session":
            if session_name is None:
                raise ValueError("session_name is required when kind='session'.")
            resolved_session = str(session_name).strip().lower()
            if resolved_session not in _SUPPORTED_TIME_ALERT_SESSIONS:
                raise ValueError("session_name must be london, newyork, or market_open.")
            next_fire_at = next_session_fire_at(resolved_session, now_utc=now)
            payload = {
                "id": None,
                "chat_id": self.default_chat_id,
                "kind": TimeAlertKind.SESSION,
                "status": "ACTIVE",
                "schedule": "session",
                "timezone_name": DEFAULT_TIME_ALERT_TIMEZONE,
                "local_time": None,
                "session_name": resolved_session,
                "note": note,
                "created_at": now,
                "next_fire_at": next_fire_at,
                "last_fired_at": None,
            }
        else:
            raise ValueError("kind must be 'at' or 'session'.")
        created = await asyncio.to_thread(self.runtime.alert_repository.upsert_time_alert, payload)
        return self._jsonable(created)

    async def list_time_alerts(self) -> dict[str, Any]:
        alerts = await asyncio.to_thread(
            self.runtime.alert_repository.list_active_time_alerts_for_chat,
            self.default_chat_id,
        )
        return self._jsonable({"chat_id": self.default_chat_id, "alerts": alerts})

    async def clear_time_alert(self, alert_id: int) -> dict[str, Any]:
        cleared = await asyncio.to_thread(
            self.runtime.alert_repository.cancel_time_alert_for_chat,
            alert_id,
            self.default_chat_id,
        )
        if cleared is None:
            raise ValueError(f"Time alert {alert_id} not found for the default MCP chat.")
        return self._jsonable(cleared)

    async def list_alert_history(
        self,
        alert_type: Literal["all", "price", "indicator", "time"] = "all",
        instrument: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        resolved_alert_type = self._normalize_alert_history_type(alert_type)
        resolved_instrument = None if instrument is None else normalize_broker_instrument(instrument)
        window_start_utc, window_end_utc = self._resolve_optional_date_window(start_date, end_date)
        entries = await asyncio.to_thread(
            self.runtime.alert_repository.list_alert_history,
            chat_id=self.default_chat_id,
            alert_type=resolved_alert_type,
            instrument=resolved_instrument,
            start_utc=window_start_utc,
            end_utc=window_end_utc,
            limit=limit,
        )
        page = AlertHistoryPage(
            alert_type=resolved_alert_type,
            instrument=resolved_instrument,
            window_start_utc=window_start_utc,
            window_end_utc=window_end_utc,
            returned_count=len(entries),
            limit=limit,
            entries=tuple(entries),
        )
        return self._jsonable(page)

    async def get_trade_stats(
        self,
        period: str = "day",
        start_date: str | None = None,
        end_date: str | None = None,
        instrument: str | None = None,
    ) -> dict[str, Any]:
        resolved_instrument = None if instrument is None else normalize_broker_instrument(instrument)
        report = await asyncio.to_thread(
            self._trade_stats_service_for_runtime().get_trade_stats,
            period,
            start_date=start_date,
            end_date=end_date,
            instrument=resolved_instrument,
        )
        return self._jsonable(report)

    async def get_spread_snapshot(
        self,
        instrument: str,
        include_history: bool = False,
        history_limit: int = 20,
        prefer_live: bool = True,
        require_live: bool = True,
    ) -> dict[str, Any]:
        if history_limit <= 0:
            raise ValueError("history_limit must be a positive integer.")
        resolved_instrument = normalize_command_instrument(instrument)
        quote = await resolve_price_quote(
            instrument=resolved_instrument,
            account_client=self.runtime.account_client,
            stream_task=self.runtime.stream_task,
            prefer_live=prefer_live,
        )
        if require_live and quote.source != "live_stream":
            raise ValueError("Live stream quote unavailable or stale for spread snapshot.")
        price_snapshot = PriceSnapshot(
            instrument=resolved_instrument,
            bid=quote.bid,
            ask=quote.ask,
            spread_price=quote.ask - quote.bid,
            spread_pips=quote.spread_pips,
            fetched_at=quote.fetched_at,
        )
        spread_result = self._spread_result_from_snapshot(price_snapshot)
        self._record_spread_observation(
            resolved_instrument,
            quote,
            reason="mcp_get_spread_snapshot",
        )
        history_entries: tuple[SpreadHistoryEntry, ...] = ()
        if include_history:
            records = await asyncio.to_thread(
                self.runtime.trade_store.get_recent_spreads,
                resolved_instrument,
                history_limit,
            )
            history_entries = tuple(
                SpreadHistoryEntry.model_validate(record)
                for record in records
            )
        snapshot = SpreadSnapshot(
            instrument=resolved_instrument,
            bid=quote.bid,
            ask=quote.ask,
            fetched_at=quote.fetched_at,
            quote_source=quote.source,
            fallback_note=quote.fallback_note,
            require_live=require_live,
            current=spread_result,
            include_history=include_history,
            history_limit=history_limit if include_history else 0,
            history=history_entries,
        )
        return self._jsonable(snapshot)

    async def get_correlation(
        self,
        primary: str,
        secondary: str,
        timeframe: str = "D",
        lookback: int = 60,
        secondary_transform: Literal["raw", "inverse"] = "raw",
    ) -> dict[str, Any]:
        result = await self._correlation_service_for_runtime().get_correlation(
            primary,
            secondary,
            timeframe=timeframe,
            lookback=lookback,
            secondary_transform=secondary_transform,
        )
        return self._jsonable(result)

    @staticmethod
    def capabilities_payload() -> dict[str, Any]:
        return {
            "name": "gold-signal-bot-v3-mcp",
            "transport": "streamable-http",
            "raw_oanda_candle_granularities": list(OANDA_CANDLE_GRANULARITIES),
            "raw_oanda_candle_max_count": OANDA_MAX_CANDLE_COUNT,
            "published_snapshot_timeframes": list(EVALUATED_INDICATOR_ALERT_TIMEFRAMES),
            "surfaces": {
                "reads": [
                    "runtime_status",
                    "market_status",
                    "macro_context",
                    "yfinance",
                    "calendar",
                    "pricing",
                    "spread_snapshot",
                    "raw_oanda_candles",
                    "vwap",
                    "account",
                    "transfers",
                    "positions",
                    "orders",
                    "analysis_snapshots",
                    "journal",
                    "trade_history",
                    "trade_stats",
                    "alert_history",
                    "correlation",
                ],
                "writes": [
                    "scan_all",
                    "scan_instrument",
                    "refresh_snapshot",
                    "price_alerts",
                    "price_alert_grids",
                    "indicator_alerts",
                    "time_alerts",
                ],
                "deferred": [
                    "chart_rendering",
                    "csv_export",
                    "runtime_config_mutation",
                    "tradehistory_backfill",
                    "trade_label_write",
                    "telegram_session_auth",
                ],
            },
        }

    @staticmethod
    def supported_instruments_payload() -> dict[str, Any]:
        live_catalog = get_oanda_instrument_catalog()
        return {
            "aliases": dict(INSTRUMENT_ALIASES),
            "scan_instruments": list(SCAN_INSTRUMENTS),
            "raw_oanda_candle_granularities": list(OANDA_CANDLE_GRANULARITIES),
            "raw_oanda_candle_instrument_scope": "live_oanda_catalog",
            "registry": {
                instrument: spec.__dict__
                for instrument, spec in INSTRUMENT_REGISTRY.items()
            },
            "live_catalog_count": len(live_catalog),
            "live_instruments": list(sorted(live_catalog)),
            "live_catalog": {
                instrument: definition.__dict__
                for instrument, definition in live_catalog.items()
            },
        }

    @staticmethod
    def alert_defaults_payload() -> dict[str, Any]:
        return {
            "indicator_defaults": {
                f"{indicator.value}:{condition}": threshold
                for (indicator, condition), threshold in INDICATOR_ALERT_DEFAULTS.items()
            },
            "default_seed_plan": {
                "momentum_h1": [
                    "RSI above 70",
                    "RSI below 30",
                    "STOCH above 80",
                    "STOCH below 20",
                ],
                "sma_cross_timeframes": list(EVALUATED_INDICATOR_ALERT_TIMEFRAMES),
            },
            "time_alert_timezone": DEFAULT_TIME_ALERT_TIMEZONE,
        }

    @staticmethod
    def tool_surface_payload(tool_specs: list[dict[str, str]]) -> dict[str, Any]:
        return {"tools": tool_specs}

    async def _resolve_snapshot(
        self,
        instrument: str,
        timeframe: str,
        *,
        refresh_policy: RefreshPolicy,
    ):
        resolved_instrument = normalize_command_instrument(instrument)
        resolved_timeframe = self._normalize_snapshot_timeframe(timeframe)
        policy = self._normalize_refresh_policy(refresh_policy)
        snapshot = self.runtime.market_state.get_snapshot(resolved_instrument, resolved_timeframe)
        if policy == "always" or (snapshot is None and policy == "if_missing"):
            refreshed = await asyncio.to_thread(
                self.runtime.scan_orchestrator.refresh_snapshot,
                resolved_instrument,
                resolved_timeframe,
            )
            if refreshed is not None:
                snapshot = refreshed
        if snapshot is None:
            raise ValueError(f"Data unavailable for {resolved_instrument} {resolved_timeframe}.")
        return snapshot

    async def _mid_prices_for(self, instruments: set[str]) -> dict[str, float]:
        mid_prices: dict[str, float] = {}
        for instrument in sorted(instruments):
            pricing = await self.runtime.account_client.get_pricing(instrument)
            mid_prices[instrument] = (pricing.bid + pricing.ask) / 2.0
        return mid_prices

    async def _pricing_by_instrument(self, instruments: set[str]) -> dict[str, dict[str, float | str | None]]:
        pricing_map: dict[str, dict[str, float | str | None]] = {}
        for instrument in sorted(instruments):
            pricing = await self._resolve_trade_price_quote(instrument)
            pricing_map[instrument] = {
                "bid": pricing["bid"],
                "ask": pricing["ask"],
                "mid": (float(pricing["bid"]) + float(pricing["ask"])) / 2.0,
                "source": str(pricing["source"]),
                "fallback_note": None
                if pricing.get("fallback_note") is None
                else str(pricing["fallback_note"]),
            }
        return pricing_map

    async def _resolve_trade_price_quote(self, instrument: str) -> dict[str, Any]:
        quote = await resolve_price_quote(
            instrument=instrument,
            account_client=self.runtime.account_client,
            stream_task=self.runtime.stream_task,
            prefer_live=True,
        )
        return {
            "bid": quote.bid,
            "ask": quote.ask,
            "source": quote.source,
            "fallback_note": quote.fallback_note,
        }

    async def _refresh_price_alert_stream_watchlist(self) -> None:
        stream_task = getattr(self.runtime, "stream_task", None)
        refresh = getattr(stream_task, "refresh_price_alert_instruments", None)
        if callable(refresh):
            await asyncio.to_thread(refresh)

    def _enrich_position(self, position, current_mid_price: float | None) -> dict[str, Any]:
        pip_size = get_pip_size(position.instrument)
        payload = self._jsonable(position)
        payload.pop("direction", None)
        payload["position_side"] = position.direction
        payload["current_mid_price"] = current_mid_price
        payload["entry_distance_pips"] = self._distance_pips(position.open_price, current_mid_price, pip_size)
        payload["stop_loss_distance_pips"] = self._distance_pips(position.stop_loss_price, current_mid_price, pip_size)
        payload["take_profit_distance_pips"] = self._distance_pips(position.take_profit_price, current_mid_price, pip_size)
        payload["gslo_distance_pips"] = self._distance_pips(position.gslo_price, current_mid_price, pip_size)
        return payload

    def _enrich_order(self, order, current_mid_price: float | None) -> dict[str, Any]:
        payload = self._jsonable(order)
        payload.pop("direction", None)
        payload["order_side"] = order.direction
        payload["current_mid_price"] = current_mid_price
        if order.instrument is None:
            payload["price_distance_pips"] = None
            payload["stop_loss_distance_pips"] = None
            payload["take_profit_distance_pips"] = None
            payload["gslo_distance_pips"] = None
            return payload
        pip_size = get_pip_size(order.instrument)
        payload["price_distance_pips"] = self._distance_pips(order.price, current_mid_price, pip_size)
        payload["stop_loss_distance_pips"] = self._distance_pips(order.stop_loss_price, current_mid_price, pip_size)
        payload["take_profit_distance_pips"] = self._distance_pips(order.take_profit_price, current_mid_price, pip_size)
        payload["gslo_distance_pips"] = self._distance_pips(order.gslo_price, current_mid_price, pip_size)
        return payload

    @staticmethod
    def _distance_pips(level: float | None, current_mid_price: float | None, pip_size: float) -> float | None:
        if level is None or current_mid_price is None:
            return None
        return (level - current_mid_price) / pip_size

    def _structure_evidence(self, snapshot) -> dict[str, Any]:
        return {
            "latest_break": self._structure_break_evidence(snapshot.structure.latest_break),
            "recent_breaks": [
                self._structure_break_evidence(item)
                for item in snapshot.structure.recent_breaks
            ],
            "latest_swing_high": self._jsonable(snapshot.structure.latest_swing_high),
            "latest_swing_low": self._jsonable(snapshot.structure.latest_swing_low),
        }

    def _structure_break_evidence(self, event) -> dict[str, Any] | None:
        if event is None:
            return None
        payload = self._jsonable(event)
        payload["break_side"] = payload.pop("direction")
        return payload

    def _zone_evidence(
        self,
        snapshot,
        *,
        mitigation_status: OrderBlockMitigationFilter = "all",
    ) -> list[dict[str, Any]]:
        resolved_status = normalize_order_block_mitigation_status(mitigation_status)
        zones: list[dict[str, Any]] = []
        for zone in snapshot.zones.order_blocks:
            if not self._order_block_matches_filter(zone, resolved_status):
                continue
            payload = self._jsonable(zone)
            payload["zone_side"] = payload.pop("direction")
            payload["mitigation_status"] = self._order_block_mitigation_status(zone)
            zones.append(payload)
        return zones

    @staticmethod
    def _order_block_mitigation_status(zone) -> str:
        return "MITIGATED" if zone.is_mitigated is True else "UNMITIGATED"

    @classmethod
    def _order_block_matches_filter(
        cls,
        zone,
        mitigation_status: OrderBlockMitigationFilter,
    ) -> bool:
        if mitigation_status == "all":
            return True
        if mitigation_status == "mitigated":
            return cls._order_block_mitigation_status(zone) == "MITIGATED"
        return cls._order_block_mitigation_status(zone) == "UNMITIGATED"

    def _order_block_counts(self, snapshot) -> dict[str, int]:
        mitigated = sum(
            1
            for zone in snapshot.zones.order_blocks
            if self._order_block_mitigation_status(zone) == "MITIGATED"
        )
        unmitigated = len(snapshot.zones.order_blocks) - mitigated
        return {
            "all": len(snapshot.zones.order_blocks),
            "mitigated": mitigated,
            "unmitigated": unmitigated,
        }

    def _liquidity_evidence(self, snapshot) -> list[dict[str, Any]]:
        levels: list[dict[str, Any]] = []
        for level in snapshot.liquidity.levels:
            payload = self._jsonable(level)
            payload["liquidity_side"] = payload.pop("side")
            levels.append(payload)
        return levels

    def _session_evidence(self, snapshot) -> list[dict[str, Any]]:
        return list(self._jsonable(snapshot.smc_context.sessions.sessions))

    def _previous_day_evidence(self, snapshot) -> dict[str, Any] | None:
        return self._jsonable(snapshot.smc_context.previous_high_low)

    def _retracement_evidence(self, snapshot) -> dict[str, Any] | None:
        retracement = snapshot.smc_context.retracement
        if retracement is None:
            return None
        payload = self._jsonable(retracement)
        if "direction" in payload:
            payload["retracement_side"] = payload.pop("direction")
        return payload

    def _indicator_evidence(self, snapshot) -> dict[str, Any]:
        return {
            "metrics": list(self._jsonable(snapshot.indicators.metrics)),
            "tick_volume_metrics": list(self._jsonable(snapshot.indicators.tick_volume_metrics)),
        }

    def _spread_evidence(self, spread: SpreadResult | None) -> dict[str, Any] | None:
        return None if spread is None else self._jsonable(spread)

    def _sanitize_mcp_payload(self, value: Any) -> Any:
        disallowed = {
            "bias",
            "direction",
            "valid",
            "setup",
            "entry",
            "target",
            "invalidation",
            "reward_risk",
            "score",
            "confidence",
            "recommendation",
            "trade_plan",
        }
        if hasattr(value, "model_dump"):
            value = value.model_dump(mode="python")
        if isinstance(value, dict):
            sanitized: dict[str, Any] = {}
            for key, item in value.items():
                if key == "direction":
                    sanitized["side"] = self._sanitize_mcp_payload(item)
                elif key in disallowed:
                    continue
                else:
                    sanitized[key] = self._sanitize_mcp_payload(item)
            return sanitized
        if isinstance(value, (list, tuple)):
            return [self._sanitize_mcp_payload(item) for item in value]
        return value

    async def _mid_ohlc_payload(
        self,
        *,
        instrument: str,
        timeframe: str,
        count: int | None,
        force: bool,
    ) -> dict[str, Any]:
        resolved_instrument = normalize_broker_instrument(instrument)
        resolved_timeframe = normalize_oanda_candle_granularity(timeframe)
        resolved_count = self._resolve_raw_candle_count(count)
        frame = await self.runtime.account_client.get_candles(
            resolved_instrument,
            resolved_timeframe,
            resolved_count,
        )
        warning = (
            "mid OHLC is fetched directly from OANDA and does not use candle-cache freshness metadata."
        )
        if force:
            warning = f"{warning} force=true has no additional effect for this mode."

        return self._jsonable(
            {
                "instrument": resolved_instrument,
                "timeframe": resolved_timeframe,
                "requested_count": resolved_count,
                "returned_count": len(frame),
                "price_component": "mid",
                "force": force,
                "source": "oanda_api_direct",
                "fetched_at": datetime.now(timezone.utc),
                "last_completed_candle": None if frame.empty else frame["time"].iloc[-1].to_pydatetime(),
                "freshness": None,
                "warning": warning,
                "bars": self._frame_records(frame),
            }
        )

    async def _bid_ask_ohlc_payload(
        self,
        *,
        instrument: str,
        timeframe: str,
        count: int | None,
        force: bool,
    ) -> dict[str, Any]:
        resolved_instrument = normalize_broker_instrument(instrument)
        resolved_timeframe = normalize_oanda_candle_granularity(timeframe)
        resolved_count = self._resolve_raw_candle_count(count)
        frame = await self.runtime.account_client.get_bid_ask_candles(
            resolved_instrument,
            resolved_timeframe,
            resolved_count,
        )
        warning = (
            "bid_ask OHLC is fetched directly from OANDA and does not use candle-cache freshness metadata."
        )
        if force:
            warning = f"{warning} force=true has no additional effect for this mode."

        return self._jsonable(
            {
                "instrument": resolved_instrument,
                "timeframe": resolved_timeframe,
                "requested_count": resolved_count,
                "returned_count": len(frame),
                "price_component": "bid_ask",
                "force": force,
                "source": "oanda_api_bid_ask_direct",
                "fetched_at": datetime.now(timezone.utc),
                "last_completed_candle": None if frame.empty else frame["time"].iloc[-1].to_pydatetime(),
                "freshness": None,
                "warning": warning,
                "bars": self._frame_records(frame),
            }
        )

    def _filtered_journal_trades(
        self,
        instrument: str | None,
        start_date: date | None,
        end_date: date | None,
    ):
        trades = self.runtime.trade_repository.list_open() + self.runtime.trade_repository.list_closed()
        if instrument is not None:
            trades = [trade for trade in trades if trade.instrument == instrument]
        if start_date is not None:
            trades = [trade for trade in trades if trade.opened_at.date() >= start_date]
        if end_date is not None:
            trades = [trade for trade in trades if trade.opened_at.date() <= end_date]
        trades.sort(key=lambda trade: trade.closed_at or trade.opened_at, reverse=True)
        return trades

    def _mae_mfe_summary_map(self, trades: list[Any]) -> dict[str, dict[str, Any] | None]:
        return {
            trade.trade_id: self.runtime.excursion_repository.get_mae_mfe(trade.trade_id)
            for trade in trades
        }

    def _mae_mfe_service_for_runtime(self) -> MaeMfeService:
        if self._mae_mfe_service is None:
            self._mae_mfe_service = MaeMfeService(
                excursion_repository=self.runtime.excursion_repository,
                account_client=self.runtime.account_client,
            )
        return self._mae_mfe_service

    def _trade_stats_service_for_runtime(self) -> TradeStatsService:
        if self._trade_stats_service is None:
            self._trade_stats_service = TradeStatsService(
                trade_history_service=self.runtime.trade_history_service,
                trade_repository=self.runtime.trade_repository,
                excursion_repository=self.runtime.excursion_repository,
                settings=self.settings,
            )
        return self._trade_stats_service

    def _correlation_service_for_runtime(self) -> CorrelationService:
        if self._correlation_service is None:
            self._correlation_service = CorrelationService(
                account_client=self.runtime.account_client,
                yfinance_service=self.yfinance_service,
                settings=self.settings,
            )
        return self._correlation_service

    def _quote_recorder_callback(self, *, reason: str):
        def recorder(quote) -> None:
            if quote.instrument not in INSTRUMENT_REGISTRY:
                return
            self._record_spread_observation(
                quote.instrument,
                quote,
                reason=reason,
            )

        return recorder

    def _record_spread_observation(
        self,
        instrument: str,
        quote,
        *,
        reason: str,
    ) -> None:
        store = getattr(self.runtime, "trade_store", None)
        if store is None:
            return
        store.record_spread(
            instrument,
            quote.spread_pips,
            recorded_at=quote.fetched_at,
            metadata={
                "source": quote.source,
                "reason": reason,
                "bid": quote.bid,
                "ask": quote.ask,
                "spread_price": quote.ask - quote.bid,
            },
        )

    @staticmethod
    def _spread_result_from_snapshot(price_snapshot: PriceSnapshot) -> SpreadResult:
        spec = get_instrument_spec(price_snapshot.instrument)
        raw_spread = price_snapshot.ask - price_snapshot.bid
        return SpreadResult(
            instrument=price_snapshot.instrument,
            bid=price_snapshot.bid,
            ask=price_snapshot.ask,
            raw_spread=raw_spread,
            spread_pips=raw_spread / spec.pip_size,
            pip_size=spec.pip_size,
            fetched_at=price_snapshot.fetched_at,
        )

    def _resolve_optional_date_window(
        self,
        start_date: str | None,
        end_date: str | None,
    ) -> tuple[datetime | None, datetime | None]:
        if start_date is None and end_date is None:
            return None, None
        resolved_period = self.runtime.trade_history_service.resolve_period_selector(
            "day",
            start_date=start_date,
            end_date=end_date,
        )
        window = self.runtime.trade_history_service.resolve_period_window(
            resolved_period,
            tz_name=self.settings.journal_timezone,
        )
        return window.start_utc, window.end_utc

    @staticmethod
    def _normalize_alert_history_type(value: str) -> str:
        normalized = str(value).strip().lower()
        if normalized not in {"all", "price", "indicator", "time"}:
            raise ValueError("alert_type must be 'all', 'price', 'indicator', or 'time'.")
        return normalized

    def _normalize_price_grid_alert(self, alert: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(alert, dict):
            raise ValueError("replace_alert_grid alerts must be objects.")
        try:
            target_price = float(alert["target_price"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("Each grid alert must define a numeric target_price.") from exc
        if target_price <= 0:
            raise ValueError("Each grid alert target_price must be positive.")
        direction = self._normalize_price_direction(str(alert.get("direction", "")))
        note = alert.get("note")
        if note is not None:
            note = str(note).strip() or None
        return {
            "id": None,
            "target_price": target_price,
            "direction": direction,
            "notes": note,
        }

    @staticmethod
    def _normalize_refresh_policy(value: RefreshPolicy | str) -> RefreshPolicy:
        normalized = str(value).strip().lower()
        if normalized not in {"never", "if_missing", "always"}:
            raise ValueError("refresh_policy must be 'never', 'if_missing', or 'always'.")
        return normalized  # type: ignore[return-value]

    @staticmethod
    def _normalize_calendar_scope(scope: CalendarScope | str) -> CalendarScope:
        normalized = str(scope).strip().lower()
        if normalized not in {"today", "week"}:
            raise ValueError("scope must be 'today' or 'week'.")
        return normalized  # type: ignore[return-value]

    @staticmethod
    def _normalize_currencies(currencies: list[str] | None) -> tuple[str, ...]:
        if not currencies:
            return TRACKED_CALENDAR_CURRENCIES
        normalized: list[str] = []
        seen: set[str] = set()
        for currency in currencies:
            candidate = str(currency).strip().upper()
            if len(candidate) != 3 or not candidate.isalpha():
                raise ValueError(f"Unsupported currency filter {currency!r}.")
            if candidate in seen:
                continue
            seen.add(candidate)
            normalized.append(candidate)
        return tuple(normalized)

    @staticmethod
    def _normalize_price_direction(direction: str) -> Literal["above", "below"]:
        normalized = str(direction).strip().lower()
        if normalized not in {"above", "below"}:
            raise ValueError("direction must be 'above' or 'below'.")
        return normalized  # type: ignore[return-value]

    @staticmethod
    def _normalize_indicator(indicator: str) -> IndicatorKind:
        try:
            return IndicatorKind(str(indicator).strip().upper())
        except ValueError as exc:
            raise ValueError("indicator must be RSI, STOCH, MACD, or SMA_CROSS.") from exc

    @staticmethod
    def _normalize_indicator_condition(condition: IndicatorCondition | str) -> IndicatorCondition:
        normalized = str(condition).strip().lower()
        if normalized not in _SUPPORTED_INDICATOR_CONDITIONS:
            raise ValueError("condition must be above, below, cross_up, or cross_down.")
        return normalized  # type: ignore[return-value]

    @staticmethod
    def _normalize_price_component(value: OhlcPriceComponent | str) -> OhlcPriceComponent:
        normalized = str(value).strip().lower()
        if normalized not in {"mid", "bid_ask"}:
            raise ValueError("price_component must be 'mid' or 'bid_ask'.")
        return normalized  # type: ignore[return-value]

    @staticmethod
    def _normalize_snapshot_timeframe(timeframe: str) -> str:
        resolved = normalize_command_timeframe(timeframe)
        if resolved not in _PUBLISHED_SNAPSHOT_TIMEFRAMES:
            supported = ", ".join(EVALUATED_INDICATOR_ALERT_TIMEFRAMES)
            raise ValueError(
                f"Published snapshot timeframe must be one of {supported}; got {resolved}."
            )
        return resolved

    def _resolve_raw_candle_count(self, count: int | None) -> int:
        resolved = self.settings.default_candle_count if count is None else int(count)
        if resolved <= 0:
            raise ValueError("count must be a positive integer.")
        if resolved > OANDA_MAX_CANDLE_COUNT:
            raise ValueError(f"count must be less than or equal to {OANDA_MAX_CANDLE_COUNT}.")
        return resolved

    @staticmethod
    def _snapshot_warning(snapshot) -> str | None:
        if snapshot.freshness.is_fresh:
            return None
        last_candle = snapshot.freshness.last_completed_candle or snapshot.last_completed_candle
        age_seconds = snapshot.freshness.staleness_seconds
        age_text = "unknown" if age_seconds is None else f"{int(age_seconds)}"
        return (
            f"snapshot is stale (last_candle={last_candle.isoformat() if last_candle else None}, "
            f"age_seconds={age_text})"
        )

    def _snapshot_metadata(self, snapshot) -> dict[str, Any]:
        return {
            "instrument": snapshot.instrument,
            "timeframe": snapshot.timeframe,
            "snapshot_version": snapshot.version,
            "last_completed_candle": snapshot.last_completed_candle,
            "freshness": snapshot.freshness,
            "warning": self._snapshot_warning(snapshot),
        }

    def _snapshot_payload(self, snapshot) -> dict[str, Any]:
        return self._jsonable(
            self._sanitize_mcp_payload(
                {
                    **self._snapshot_metadata(snapshot),
                    "structure": self._structure_evidence(snapshot),
                    "zones": self._zone_evidence(snapshot),
                    "liquidity": self._liquidity_evidence(snapshot),
                    "sessions": self._session_evidence(snapshot),
                    "previous_day": self._previous_day_evidence(snapshot),
                    "retracement": self._retracement_evidence(snapshot),
                    "indicators": self._indicator_evidence(snapshot),
                    "spread": self._spread_evidence(snapshot.spread),
                }
            )
        )

    @staticmethod
    def _frame_records(frame) -> list[dict[str, Any]]:
        if frame.empty:
            return []
        return json.loads(frame.to_json(orient="records", date_format="iso"))

    @staticmethod
    def _jsonable(value: Any) -> Any:
        return _JSON_ADAPTER.dump_python(value, mode="json")


__all__ = ["BotMcpService", "CalendarScope", "RefreshPolicy"]
