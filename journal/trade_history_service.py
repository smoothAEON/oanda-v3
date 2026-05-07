"""Transaction-backed trade history service and CLI helpers."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from math import ceil
from typing import Any, Iterable, Sequence
from zoneinfo import ZoneInfo

from config.settings import Settings, get_settings
from core.enums import CloseReason, TradeState
from core.instrument_registry import get_instrument_spec, get_pip_size
from core.logging_setup import get_logger, log_failure
from core.models import FinancingEvent, RealizedPnLSummary, TradeHistoryEvent, TradeHistoryPage, TradeHistorySyncState, TradeRecord
from data.persistence.trade_store import TradeStore
from journal.close_reasons import infer_close_reason
from journal.trade_normalizer import normalize_transactions
from journal.trade_repository import TradeRepository
from providers.oanda_history import OandaHistoryClient

TRADE_HISTORY_TYPE_FILTER = "ORDER_FILL,DAILY_FINANCING"
TRADE_HISTORY_PAGE_SIZE = 20
SAFE_BACKFILL_CHUNK_DAYS = 30


@dataclass(frozen=True)
class WindowBounds:
    """Resolved half-open local and UTC time bounds for a trade-history query."""

    period: str
    start_local: datetime
    end_local: datetime
    start_utc: datetime
    end_utc: datetime


@dataclass
class TradeHistoryService:
    """Sync, query, and project transaction-backed trade history."""

    store: TradeStore
    trade_repository: TradeRepository
    history_client: OandaHistoryClient
    settings: Settings

    def __init__(
        self,
        *,
        store: TradeStore | None = None,
        trade_repository: TradeRepository | None = None,
        history_client: OandaHistoryClient | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.store = store or TradeStore(settings=self.settings)
        self.trade_repository = trade_repository or TradeRepository(store=self.store, settings=self.settings)
        self.history_client = history_client or OandaHistoryClient(settings=self.settings)
        self.logger = get_logger(__name__)

    def incremental_sync(self) -> dict[str, Any]:
        """Persist transactions newer than the stored watermark."""

        account_id = self.settings.oanda_account_id.get_secret_value()
        state = self.store.get_trade_history_sync_state(account_id)
        now_utc = self._now_utc()
        self.logger.info(
            "trade_history_sync_started",
            mode="initial" if state is None else "incremental",
            account_id=account_id,
            last_transaction_id=None if state is None else state.last_transaction_id,
        )

        if state is None:
            account = self.history_client.get_account_details_sync()
            resolved_account_id = str(account.get("id") or account_id)
            last_transaction_id = str(account.get("lastTransactionID") or "").strip()
            if not last_transaction_id:
                raise RuntimeError("OANDA account details did not include lastTransactionID.")
            self.store.upsert_trade_history_sync_state(
                TradeHistorySyncState(
                    account_id=resolved_account_id,
                    last_transaction_id=last_transaction_id,
                    last_sync_utc=now_utc,
                )
            )
            self.logger.info(
                "trade_history_sync_initialized",
                account_id=resolved_account_id,
                last_transaction_id=last_transaction_id,
            )
            return {
                "mode": "initialized",
                "account_id": resolved_account_id,
                "last_transaction_id": last_transaction_id,
                "raw_seen": 0,
                "raw_inserted": 0,
                "raw_updated": 0,
                "seen": 0,
                "inserted": 0,
                "updated": 0,
                "projected_trades": 0,
            }

        transactions, new_last_transaction_id = self.history_client.fetch_transactions_since_sync(
            state.last_transaction_id,
            TRADE_HISTORY_TYPE_FILTER,
        )
        raw_seen = raw_inserted = raw_updated = 0
        if transactions:
            raw_seen, raw_inserted, raw_updated = self.store.upsert_raw_transactions(transactions)

        normalized = normalize_transactions(
            transactions,
            journal_timezone=self.settings.journal_timezone,
        )
        seen = inserted = updated = 0
        if normalized:
            seen, inserted, updated = self.store.upsert_trade_history_events(normalized)
        projected_trades = self._project_trade_records_from_events(normalized)
        repaired_trades = self._repair_manual_mit_closes_from_history()

        self.store.upsert_trade_history_sync_state(
            TradeHistorySyncState(
                account_id=state.account_id,
                last_transaction_id=str(new_last_transaction_id),
                last_sync_utc=now_utc,
            )
        )
        self.logger.info(
            "trade_history_sync_completed",
            account_id=state.account_id,
            transaction_count=len(transactions),
            raw_inserted=raw_inserted,
            raw_updated=raw_updated,
            event_inserted=inserted,
            event_updated=updated,
            projected_trades=projected_trades,
            repaired_trades=repaired_trades,
            last_transaction_id=str(new_last_transaction_id),
        )
        return {
            "mode": "incremental",
            "account_id": state.account_id,
            "last_transaction_id": str(new_last_transaction_id),
            "raw_seen": raw_seen,
            "raw_inserted": raw_inserted,
            "raw_updated": raw_updated,
            "seen": seen,
            "inserted": inserted,
            "updated": updated,
            "projected_trades": projected_trades,
        }

    def backfill_history(
        self,
        start_date: date | str,
        end_date: date | str,
        tz_name: str | None = None,
    ) -> dict[str, Any]:
        """Backfill historical transactions into the normalized journal."""

        timezone_name = tz_name or self.settings.journal_timezone
        start_day = self._coerce_date(start_date)
        end_day = self._coerce_date(end_date)
        if end_day < start_day:
            raise ValueError("end_date must be greater than or equal to start_date.")

        zone = ZoneInfo(timezone_name)
        start_local = datetime.combine(start_day, time.min, tzinfo=zone)
        end_local = datetime.combine(end_day + timedelta(days=1), time.min, tzinfo=zone)
        start_utc = start_local.astimezone(timezone.utc)
        end_utc = end_local.astimezone(timezone.utc)
        chunks = list(self._split_utc_window(start_utc, end_utc, timedelta(days=SAFE_BACKFILL_CHUNK_DAYS)))

        self.logger.info(
            "trade_history_backfill_started",
            start_date=start_day.isoformat(),
            end_date=end_day.isoformat(),
            timezone_name=timezone_name,
            chunk_count=len(chunks),
        )

        total_raw_seen = total_raw_inserted = total_raw_updated = 0
        total_seen = total_inserted = total_updated = 0
        total_projected_trades = 0
        max_transaction_id: str | None = None

        for chunk_start, chunk_end in chunks:
            self.logger.info(
                "trade_history_backfill_chunk_started",
                start_utc=chunk_start.isoformat(),
                end_utc=chunk_end.isoformat(),
            )
            transactions = self.history_client.fetch_transactions_for_window_sync(
                chunk_start,
                chunk_end,
                TRADE_HISTORY_TYPE_FILTER,
            )
            if transactions:
                max_chunk_transaction_id = max(
                    (str(transaction.get("id")) for transaction in transactions if transaction.get("id") is not None),
                    key=self._transaction_sort_key,
                    default=None,
                )
                if max_chunk_transaction_id is not None and (
                    max_transaction_id is None
                    or self._transaction_sort_key(max_chunk_transaction_id) > self._transaction_sort_key(max_transaction_id)
                ):
                    max_transaction_id = max_chunk_transaction_id

            raw_seen, raw_inserted, raw_updated = self.store.upsert_raw_transactions(transactions) if transactions else (0, 0, 0)
            normalized = normalize_transactions(transactions, journal_timezone=timezone_name)
            seen, inserted, updated = self.store.upsert_trade_history_events(normalized) if normalized else (0, 0, 0)
            projected_trades = self._project_trade_records_from_events(normalized)
            repaired_trades = self._repair_manual_mit_closes_from_history()

            total_raw_seen += raw_seen
            total_raw_inserted += raw_inserted
            total_raw_updated += raw_updated
            total_seen += seen
            total_inserted += inserted
            total_updated += updated
            total_projected_trades += projected_trades

            self.logger.info(
                "trade_history_backfill_chunk_completed",
                start_utc=chunk_start.isoformat(),
                end_utc=chunk_end.isoformat(),
                transaction_count=len(transactions),
                raw_inserted=raw_inserted,
                raw_updated=raw_updated,
                event_inserted=inserted,
                event_updated=updated,
                projected_trades=projected_trades,
                repaired_trades=repaired_trades,
            )

        if max_transaction_id is not None:
            self._advance_sync_state_watermark(max_transaction_id)

        self.logger.info(
            "trade_history_backfill_completed",
            start_date=start_day.isoformat(),
            end_date=end_day.isoformat(),
            chunk_count=len(chunks),
            raw_inserted=total_raw_inserted,
            raw_updated=total_raw_updated,
            event_inserted=total_inserted,
            event_updated=total_updated,
            projected_trades=total_projected_trades,
        )
        return {
            "start": start_day.isoformat(),
            "end": end_day.isoformat(),
            "timezone_name": timezone_name,
            "chunks": len(chunks),
            "raw_seen": total_raw_seen,
            "raw_inserted": total_raw_inserted,
            "raw_updated": total_raw_updated,
            "seen": total_seen,
            "inserted": total_inserted,
            "updated": total_updated,
            "projected_trades": total_projected_trades,
        }

    def get_trade_history(
        self,
        period: str,
        view: str,
        instrument: str | None = None,
        page: int = 1,
        start_date: date | str | None = None,
        end_date: date | str | None = None,
    ) -> TradeHistoryPage:
        """Return paged trade-history rows from the normalized journal."""

        resolved_period = self.resolve_period_selector(
            period,
            start_date=start_date,
            end_date=end_date,
        )
        stale_warning = self._try_sync_for_read()
        pnl = self.compute_realized_pnl(
            resolved_period,
            instrument=instrument,
        )
        rows = self.store.list_trade_history_trade_events(
            start_utc=pnl.start_utc,
            end_utc=pnl.end_utc,
            instrument=instrument,
            descending=True,
        )
        filtered_rows = self._filter_rows_for_view(rows, view)
        total_rows = len(filtered_rows)
        total_pages = max(1, ceil(total_rows / TRADE_HISTORY_PAGE_SIZE))
        resolved_page = min(max(int(page), 1), total_pages)
        start_index = (resolved_page - 1) * TRADE_HISTORY_PAGE_SIZE
        end_index = start_index + TRADE_HISTORY_PAGE_SIZE
        page_rows = tuple(filtered_rows[start_index:end_index])
        page_date_local = page_rows[0].time_local.date() if page_rows else None
        page_date_summary = None
        if page_date_local is not None:
            page_date_period = f"custom:{page_date_local.isoformat()}:{page_date_local.isoformat()}"
            page_date_summary = self.compute_realized_pnl(page_date_period, instrument=instrument)

        return TradeHistoryPage(
            period=resolved_period,
            view=view,
            instrument=instrument,
            window_start_utc=pnl.start_utc,
            window_end_utc=pnl.end_utc,
            window_start_local=pnl.start_local,
            window_end_local=pnl.end_local,
            summary=pnl,
            page_date_local=page_date_local,
            page_date_summary=page_date_summary,
            rows=page_rows,
            page=resolved_page,
            page_size=TRADE_HISTORY_PAGE_SIZE,
            total_rows=total_rows,
            total_pages=total_pages,
            stale_warning=stale_warning,
        )

    def compute_realized_pnl(
        self,
        period: str,
        instrument: str | None = None,
        start_date: date | str | None = None,
        end_date: date | str | None = None,
    ) -> RealizedPnLSummary:
        """Aggregate realized PnL from normalized journal rows."""

        resolved_period = self.resolve_period_selector(
            period,
            start_date=start_date,
            end_date=end_date,
        )
        window = self.resolve_period_window(resolved_period, tz_name=self.settings.journal_timezone)
        records = self.store.list_trade_history_records(
            start_utc=window.start_utc,
            end_utc=window.end_utc,
            instrument=instrument,
            descending=False,
        )
        gross_realized_pl = Decimal("0")
        financing_total = Decimal("0")
        commission_total = Decimal("0")

        for record in records:
            if isinstance(record, TradeHistoryEvent):
                commission_total += record.commission
                if record.event_type in {"CLOSE", "PARTIAL_CLOSE"}:
                    gross_realized_pl += record.realized_pl
                    financing_total += record.financing
            elif isinstance(record, FinancingEvent):
                financing_total += record.financing

        return RealizedPnLSummary(
            period=resolved_period,
            instrument=instrument,
            start_utc=window.start_utc,
            end_utc=window.end_utc,
            start_local=window.start_local,
            end_local=window.end_local,
            gross_realized_pl=gross_realized_pl,
            financing=financing_total,
            commission=commission_total,
            net_realized_pl=gross_realized_pl + financing_total - commission_total,
        )

    def resolve_period_selector(
        self,
        period: str,
        *,
        start_date: date | str | None = None,
        end_date: date | str | None = None,
    ) -> str:
        """Resolve optional ISO date overrides into the canonical period selector."""

        if (start_date is None) != (end_date is None):
            raise ValueError("start_date and end_date must be provided together.")
        if start_date is None and end_date is None:
            return period

        resolved_start = self._coerce_date(start_date)
        resolved_end = self._coerce_date(end_date)
        if resolved_end < resolved_start:
            raise ValueError("end_date must be greater than or equal to start_date.")
        return f"custom:{resolved_start.isoformat()}:{resolved_end.isoformat()}"

    def resolve_period_window(
        self,
        period: str,
        *,
        tz_name: str,
        now_utc: datetime | None = None,
    ) -> WindowBounds:
        """Resolve a symbolic period into half-open local and UTC time bounds."""

        zone = ZoneInfo(tz_name)
        current_utc = now_utc or self._now_utc()
        current_local = current_utc.astimezone(zone)
        normalized_period = period.strip().lower()

        if normalized_period in {"day", "today"}:
            start_local = datetime.combine(current_local.date(), time.min, tzinfo=zone)
            end_local = current_local
        elif normalized_period in {"week", "thisweek"}:
            start_of_week = current_local.date() - timedelta(days=current_local.weekday())
            start_local = datetime.combine(start_of_week, time.min, tzinfo=zone)
            end_local = current_local
        elif normalized_period in {"month", "thismonth"}:
            start_local = datetime.combine(current_local.date().replace(day=1), time.min, tzinfo=zone)
            end_local = current_local
        elif normalized_period.startswith("custom:"):
            _, start_text, end_text = normalized_period.split(":", maxsplit=2)
            start_date = date.fromisoformat(start_text)
            end_date = date.fromisoformat(end_text)
            if end_date < start_date:
                raise ValueError("custom period end date must not be earlier than the start date.")
            start_local = datetime.combine(start_date, time.min, tzinfo=zone)
            end_local = datetime.combine(end_date + timedelta(days=1), time.min, tzinfo=zone)
        else:
            raise ValueError("Unsupported trade-history period.")

        return WindowBounds(
            period=period,
            start_local=start_local,
            end_local=end_local,
            start_utc=start_local.astimezone(timezone.utc),
            end_utc=end_local.astimezone(timezone.utc),
        )

    def _try_sync_for_read(self) -> str | None:
        try:
            self.incremental_sync()
        except Exception as exc:
            log_failure(
                self.logger,
                "trade_history_sync_for_read_failed",
                exc,
                level="warning",
            )
            if self.store.has_trade_history_data():
                return "Sync warning: using stored trade-history data; latest sync failed."
            raise RuntimeError(f"Trade history sync failed and no stored data is available: {exc}") from exc
        return None

    def _filter_rows_for_view(
        self,
        rows: Sequence[TradeHistoryEvent],
        view: str,
    ) -> list[TradeHistoryEvent]:
        normalized_view = view.strip().lower()
        if normalized_view == "all":
            return list(rows)
        if normalized_view == "opened":
            return [row for row in rows if row.event_type == "OPEN"]
        if normalized_view == "closed":
            return [row for row in rows if row.event_type in {"CLOSE", "PARTIAL_CLOSE"}]
        raise ValueError("Unsupported trade-history view.")

    def _project_trade_records_from_events(
        self,
        events: Iterable[TradeHistoryEvent | FinancingEvent],
    ) -> int:
        trade_ids = sorted(
            {
                event.trade_id
                for event in events
                if isinstance(event, TradeHistoryEvent)
            }
        )
        projected = 0
        for trade_id in trade_ids:
            if self._project_trade_record(trade_id):
                projected += 1
        return projected

    def _project_trade_record(self, trade_id: str) -> bool:
        events = self.store.list_trade_history_trade_events(trade_id=trade_id, descending=False)
        if not events:
            return False

        existing = self.trade_repository.get(trade_id)
        open_event = next((event for event in events if event.event_type == "OPEN"), None)
        base_units = Decimal(str(existing.units)) if existing is not None else (None if open_event is None else open_event.units)
        base_open_price = (
            Decimal(str(existing.open_price))
            if existing is not None
            else (None if open_event is None or open_event.price is None else open_event.price)
        )
        base_opened_at = existing.opened_at if existing is not None else (None if open_event is None else open_event.time_utc)
        base_instrument = existing.instrument if existing is not None else (open_event.instrument if open_event is not None else events[0].instrument)
        if base_units is None or base_open_price is None or base_opened_at is None:
            return False

        close_events = [event for event in events if event.event_type in {"CLOSE", "PARTIAL_CLOSE"}]
        is_fully_closed = any(event.event_type == "CLOSE" for event in close_events)

        if not is_fully_closed:
            open_record = TradeRecord(
                trade_id=trade_id,
                instrument=base_instrument,
                units=float(base_units),
                open_price=float(base_open_price),
                close_price=None,
                sl_price=None if existing is None else existing.sl_price,
                tp_price=None if existing is None else existing.tp_price,
                gslo_price=None if existing is None else existing.gslo_price,
                state=TradeState.OPEN,
                close_reason=None,
                pips=None,
                instrument_pnl=None,
                instrument_pnl_currency=None,
                account_pnl=None,
                account_currency=None,
                opened_at=base_opened_at,
                closed_at=None,
                notes=None if existing is None else existing.notes,
            )
            self.trade_repository.upsert(open_record)
            return True

        final_close_event = close_events[-1]
        close_price = float(final_close_event.price or base_open_price)
        account_pnl = sum(event.net_realized_pl for event in close_events)
        weighted_pips = self._compute_weighted_close_pips(
            instrument=base_instrument,
            base_units=base_units,
            base_open_price=base_open_price,
            close_events=close_events,
        )
        closed_record = TradeRecord(
            trade_id=trade_id,
            instrument=base_instrument,
            units=float(base_units),
            open_price=float(base_open_price),
            close_price=close_price,
            sl_price=None if existing is None else existing.sl_price,
            tp_price=None if existing is None else existing.tp_price,
            gslo_price=None if existing is None else existing.gslo_price,
            state=TradeState.CLOSED,
            close_reason=infer_close_reason(
                close_price=close_price,
                sl_price=None if existing is None else existing.sl_price,
                tp_price=None if existing is None else existing.tp_price,
                raw_reason=final_close_event.reason,
                evidence_sources=(final_close_event.raw_json,),
            ),
            pips=weighted_pips,
            instrument_pnl=float(account_pnl),
            instrument_pnl_currency=self.settings.account_currency,
            account_pnl=float(account_pnl),
            account_currency=self.settings.account_currency,
            opened_at=base_opened_at,
            closed_at=final_close_event.time_utc,
            notes=None if existing is None else existing.notes,
        )
        self.trade_repository.upsert(closed_record)
        return True

    def _repair_manual_mit_closes_from_history(self) -> int:
        repaired = 0
        for trade in self.trade_repository.list_closed():
            if trade.close_reason != CloseReason.MANUAL:
                continue
            events = self.store.list_trade_history_trade_events(trade_id=trade.trade_id, descending=False)
            close_events = [event for event in events if event.event_type in {"CLOSE", "PARTIAL_CLOSE"}]
            if not close_events:
                continue
            final_close_event = close_events[-1]
            inferred_reason = infer_close_reason(
                close_price=trade.close_price or trade.open_price,
                sl_price=trade.sl_price,
                tp_price=trade.tp_price,
                raw_reason=final_close_event.reason,
                evidence_sources=(final_close_event.raw_json,),
            )
            if inferred_reason != CloseReason.MIT:
                continue
            if self._project_trade_record(trade.trade_id):
                repaired += 1
        return repaired

    def _compute_weighted_close_pips(
        self,
        *,
        instrument: str,
        base_units: Decimal,
        base_open_price: Decimal,
        close_events: Sequence[TradeHistoryEvent],
    ) -> float | None:
        pip_size = self._resolve_pip_size(instrument)
        if pip_size is None or pip_size == 0:
            self.logger.warning(
                "trade_history_projection_missing_pip_size",
                instrument=instrument,
            )
            return None
        direction_multiplier = Decimal("1") if base_units > 0 else Decimal("-1")
        total_abs_units = sum(event.abs_units for event in close_events)
        if total_abs_units == 0:
            return 0.0

        weighted_sum = Decimal("0")
        for event in close_events:
            close_price = event.price or base_open_price
            pips = ((close_price - base_open_price) / pip_size) * direction_multiplier
            weighted_sum += pips * event.abs_units
        return float(weighted_sum / total_abs_units)

    @staticmethod
    def _resolve_pip_size(instrument: str) -> Decimal | None:
        try:
            return Decimal(str(get_pip_size(instrument)))
        except (KeyError, ValueError):
            pass

        if "_" not in instrument:
            return None

        base_currency, quote_currency = instrument.split("_", maxsplit=1)
        if base_currency == "XAU":
            return Decimal("0.01")
        if base_currency == "XAG":
            return Decimal("0.0001")
        if quote_currency in {"JPY", "HUF"}:
            return Decimal("0.01")
        return Decimal("0.0001")

    def _advance_sync_state_watermark(self, candidate_transaction_id: str) -> None:
        account_id = self.settings.oanda_account_id.get_secret_value()
        existing = self.store.get_trade_history_sync_state(account_id)
        if existing is not None and self._transaction_sort_key(candidate_transaction_id) <= self._transaction_sort_key(existing.last_transaction_id):
            return
        self.store.upsert_trade_history_sync_state(
            TradeHistorySyncState(
                account_id=account_id,
                last_transaction_id=candidate_transaction_id,
                last_sync_utc=self._now_utc(),
            )
        )

    @staticmethod
    def _coerce_date(value: date | str) -> date:
        if isinstance(value, date):
            return value
        return date.fromisoformat(str(value))

    @staticmethod
    def _split_utc_window(
        start_utc: datetime,
        end_utc: datetime,
        chunk_size: timedelta,
    ) -> Iterable[tuple[datetime, datetime]]:
        cursor = start_utc
        while cursor < end_utc:
            next_cursor = min(cursor + chunk_size, end_utc)
            yield cursor, next_cursor
            cursor = next_cursor

    @staticmethod
    def _transaction_sort_key(value: Any) -> tuple[int, str]:
        try:
            return (0, f"{int(str(value)):020d}")
        except (TypeError, ValueError):
            return (1, str(value))

    @staticmethod
    def _now_utc() -> datetime:
        return datetime.now(timezone.utc)


def build_cli_parser() -> argparse.ArgumentParser:
    """Build the trade-history CLI parser."""

    parser = argparse.ArgumentParser(description="Trade history maintenance utilities.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    backfill_parser = subparsers.add_parser(
        "backfill-trades",
        help="Backfill transaction-backed trade history into local storage.",
    )
    backfill_parser.add_argument("--start", required=True, help="Local start date in YYYY-MM-DD.")
    backfill_parser.add_argument("--end", required=True, help="Local end date in YYYY-MM-DD.")
    backfill_parser.add_argument("--tz", default=None, help="Journal timezone override.")
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for trade-history maintenance commands."""

    parser = build_cli_parser()
    args = parser.parse_args(argv)
    settings = get_settings()
    store = TradeStore(settings=settings)
    try:
        service = TradeHistoryService(
            store=store,
            trade_repository=TradeRepository(store=store, settings=settings),
            history_client=OandaHistoryClient(settings=settings),
            settings=settings,
        )
        if args.command == "backfill-trades":
            result = service.backfill_history(args.start, args.end, tz_name=args.tz)
            print(
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
            return 0
        parser.error(f"Unsupported command {args.command!r}.")
    finally:
        store.close()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "SAFE_BACKFILL_CHUNK_DAYS",
    "TRADE_HISTORY_PAGE_SIZE",
    "TRADE_HISTORY_TYPE_FILTER",
    "TradeHistoryService",
    "WindowBounds",
    "build_cli_parser",
    "main",
]
