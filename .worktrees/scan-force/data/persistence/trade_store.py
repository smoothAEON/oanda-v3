"""TinyDB-backed persistence helpers used by runtime support layers."""

from __future__ import annotations

from decimal import Decimal
import json
import os
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any, Callable, Mapping, TypeVar

import portalocker
from config.settings import Settings, get_settings
from core.enums import AlertStatus, CloseReason, TimeAlertStatus, TradeState
from core.logging_setup import get_logger, log_failure
from core.models import (
    BotSessionRecord,
    ExcursionSample,
    FinancingEvent,
    IndicatorAlert,
    IndicatorAlertEvaluationCursor,
    PriceAlert,
    RuntimeConfigRecord,
    TimeAlert,
    TradeHistoryEvent,
    TradeHistorySyncState,
    TradeRecord,
)
from tinydb import Query, TinyDB
from tinydb.storages import Storage

T = TypeVar("T")
_PROCESS_LOCKS_GUARD = RLock()


@dataclass
class _ProcessRuntimeLock:
    handle: Any
    refcount: int = 1


_PROCESS_RUNTIME_LOCKS: dict[Path, _ProcessRuntimeLock] = {}


def _runtime_lock_path_for(db_path: Path) -> Path:
    return db_path.with_name(f"{db_path.name}.lock")


def _acquire_runtime_lock(db_path: Path) -> Path:
    lock_path = _runtime_lock_path_for(db_path)
    with _PROCESS_LOCKS_GUARD:
        existing = _PROCESS_RUNTIME_LOCKS.get(lock_path)
        if existing is not None:
            existing.refcount += 1
            return lock_path

        lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = lock_path.open("a+", encoding="utf-8")
        try:
            portalocker.lock(handle, portalocker.LOCK_EX | portalocker.LOCK_NB)
        except Exception as exc:
            handle.close()
            raise RuntimeError(
                f"Persistence runtime lock unavailable for {db_path}. Another process is already using it."
            ) from exc
        _PROCESS_RUNTIME_LOCKS[lock_path] = _ProcessRuntimeLock(handle=handle)
        return lock_path


def _release_runtime_lock(lock_path: Path | None) -> None:
    if lock_path is None:
        return
    with _PROCESS_LOCKS_GUARD:
        existing = _PROCESS_RUNTIME_LOCKS.get(lock_path)
        if existing is None:
            return
        existing.refcount -= 1
        if existing.refcount > 0:
            return
        try:
            portalocker.unlock(existing.handle)
        finally:
            existing.handle.close()
            _PROCESS_RUNTIME_LOCKS.pop(lock_path, None)


class PersistenceWriteError(RuntimeError):
    """Raised when a durable persistence write could not be committed."""

    def __init__(self, action: str, db_path: Path, reason: str) -> None:
        self.action = action
        self.db_path = db_path
        super().__init__(f"Persistence write failed during {action}: {reason}")


class AtomicJSONStorage(Storage):
    """TinyDB JSON storage that replaces the database file atomically."""

    def __init__(
        self,
        path: str | Path,
        create_dirs: bool = False,
        encoding: str = "utf-8",
        access_mode: str = "r+",
        **kwargs: Any,
    ) -> None:
        self._path = Path(path)
        self._encoding = encoding
        self._mode = access_mode
        self.kwargs = kwargs

        if create_dirs:
            self._path.parent.mkdir(parents=True, exist_ok=True)
        if any(character in self._mode for character in ("+", "w", "a")):
            self._path.touch(exist_ok=True)

    def read(self) -> dict[str, dict[str, Any]] | None:
        if not self._path.exists():
            return None
        if self._path.stat().st_size == 0:
            return None
        with self._path.open("r", encoding=self._encoding) as handle:
            return json.load(handle)

    def write(self, data: dict[str, dict[str, Any]]) -> None:
        serialized = json.dumps(data, **self.kwargs)
        temp_fd, temp_name = tempfile.mkstemp(
            prefix=f"{self._path.name}.",
            suffix=".tmp",
            dir=str(self._path.parent),
        )
        temp_path = Path(temp_name)
        try:
            with os.fdopen(temp_fd, mode="w", encoding=self._encoding) as temp_handle:
                temp_handle.write(serialized)
                temp_handle.flush()
                os.fsync(temp_handle.fileno())

            attempts = 5
            for attempt in range(attempts):
                try:
                    os.replace(temp_path, self._path)
                    break
                except PermissionError:
                    if attempt >= attempts - 1:
                        raise
                    time.sleep(0.05 * (attempt + 1))
        finally:
            if temp_path.exists():
                temp_path.unlink()

    def close(self) -> None:
        return None


class TradeStore:
    """Wrap TinyDB tables with narrow helper methods."""

    def __init__(
        self,
        *,
        db_path: Path | None = None,
        settings: Settings | None = None,
    ) -> None:
        resolved_settings = settings or get_settings()
        self.db_path = (db_path or resolved_settings.tinydb_path).resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.logger = get_logger(__name__)
        self._lock = RLock()
        self._runtime_lock_path: Path | None = None
        self.db: TinyDB | None = None
        self.trades = None
        self.signals = None
        self.spread_history = None
        self.cache_metadata = None
        self.excursion_samples = None
        self.price_alerts = None
        self.indicator_alerts = None
        self.time_alerts = None
        self.indicator_alert_cursors = None
        self.sessions = None
        self.runtime_config = None
        self.raw_transactions = None
        self.trade_history_events = None
        self.trade_history_sync = None

        try:
            self._runtime_lock_path = _acquire_runtime_lock(self.db_path)
            self.db = TinyDB(
                self.db_path,
                storage=AtomicJSONStorage,
                create_dirs=True,
                encoding="utf-8",
            )
            self.db.storage.read()
            self.trades = self.db.table("trades")
            self.signals = self.db.table("signals")
            self.spread_history = self.db.table("spread_history")
            self.cache_metadata = self.db.table("cache_metadata")
            self.excursion_samples = self.db.table("excursion_samples")
            self.price_alerts = self.db.table("price_alerts")
            self.indicator_alerts = self.db.table("indicator_alerts")
            self.time_alerts = self.db.table("time_alerts")
            self.indicator_alert_cursors = self.db.table("indicator_alert_cursors")
            self.sessions = self.db.table("sessions")
            self.runtime_config = self.db.table("runtime_config")
            self.raw_transactions = self.db.table("raw_transactions")
            self.trade_history_events = self.db.table("trade_history_events")
            self.trade_history_sync = self.db.table("trade_history_sync")
        except Exception as exc:
            self.close()
            self._warn_persistence_failure("open", exc)
            if isinstance(exc, RuntimeError) and "runtime lock unavailable" in str(exc).lower():
                raise

    def upsert_session(
        self,
        session: BotSessionRecord | Mapping[str, Any],
    ) -> BotSessionRecord:
        """Upsert one active Telegram session keyed by user_id."""

        validated = self._validate_session(session)
        serialized = validated.model_dump(mode="json")

        def write() -> None:
            assert self.sessions is not None
            self.sessions.upsert(serialized, Query().user_id == validated.user_id)

        self._strict_write_operation("upsert_session", write)
        return validated

    def get_session(self, user_id: int) -> BotSessionRecord | None:
        """Return one persisted Telegram session by user id."""

        return self._read_operation(
            "get_session",
            None,
            lambda: self._deserialize_session(
                None if self.sessions is None else self.sessions.get(Query().user_id == int(user_id))
            ),
        )

    def list_sessions(self) -> list[BotSessionRecord]:
        """Return persisted sessions ordered by last activity descending."""

        def read() -> list[BotSessionRecord]:
            assert self.sessions is not None
            sessions = [
                BotSessionRecord.model_validate(dict(record))
                for record in self.sessions.all()
            ]
            sessions.sort(key=lambda item: item.last_activity_at, reverse=True)
            return sessions

        return self._read_operation("list_sessions", [], read)

    def delete_session(self, user_id: int) -> BotSessionRecord | None:
        """Delete one persisted Telegram session and return the removed record."""

        existing = self.get_session(user_id)
        if existing is None:
            return None

        def write() -> None:
            assert self.sessions is not None
            self.sessions.remove(Query().user_id == int(user_id))

        self._strict_write_operation("delete_session", write)
        return existing

    def upsert_runtime_config(
        self,
        record: RuntimeConfigRecord | Mapping[str, Any],
    ) -> RuntimeConfigRecord:
        """Upsert one runtime-config override keyed by config key."""

        validated = self._validate_runtime_config(record)
        serialized = validated.model_dump(mode="json")

        def write() -> None:
            assert self.runtime_config is not None
            self.runtime_config.upsert(serialized, Query().key == validated.key.value)

        self._strict_write_operation("upsert_runtime_config", write)
        return validated

    def get_runtime_config(self, key: str) -> RuntimeConfigRecord | None:
        """Return one runtime-config override by key."""

        return self._read_operation(
            "get_runtime_config",
            None,
            lambda: self._deserialize_runtime_config(
                None if self.runtime_config is None else self.runtime_config.get(Query().key == key)
            ),
        )

    def list_runtime_configs(self) -> list[RuntimeConfigRecord]:
        """Return persisted runtime-config overrides ordered by key."""

        def read() -> list[RuntimeConfigRecord]:
            assert self.runtime_config is not None
            records = [
                RuntimeConfigRecord.model_validate(dict(record))
                for record in self.runtime_config.all()
            ]
            records.sort(key=lambda item: item.key.value)
            return records

        return self._read_operation("list_runtime_configs", [], read)

    def delete_runtime_config(self, key: str) -> RuntimeConfigRecord | None:
        """Delete one runtime-config override and return the removed record."""

        existing = self.get_runtime_config(key)
        if existing is None:
            return None

        def write() -> None:
            assert self.runtime_config is not None
            self.runtime_config.remove(Query().key == key)

        self._strict_write_operation("delete_runtime_config", write)
        return existing

    def get_cache_metadata(self, instrument: str, timeframe: str) -> dict[str, Any] | None:
        """Return cache metadata for a key with datetimes restored."""

        return self._read_operation(
            "get_cache_metadata",
            None,
            lambda: self._get_cache_metadata_record(instrument, timeframe),
        )

    def update_cache_metadata(
        self,
        instrument: str,
        timeframe: str,
        metadata: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Upsert cache metadata for a key and return the normalized record."""

        serialized = self._serialize_cache_metadata(instrument, timeframe, metadata)

        def write() -> dict[str, Any]:
            assert self.cache_metadata is not None
            self.cache_metadata.upsert(serialized, self._cache_query(instrument, timeframe))
            return self._get_cache_metadata_record(instrument, timeframe) or {}

        return self._write_operation("update_cache_metadata", {}, write)

    def upsert_cache_metadata(
        self,
        *,
        instrument: str,
        timeframe: str,
        last_completed_candle: datetime,
        fetched_at: datetime,
        candle_count: int,
        source: str,
    ) -> dict[str, Any]:
        """Convenience helper for the Stage 04 cache metadata contract."""

        return self.update_cache_metadata(
            instrument,
            timeframe,
            {
                "last_completed_candle": last_completed_candle,
                "fetched_at": fetched_at,
                "candle_count": candle_count,
                "source": source,
            },
        )

    def record_signal(self, signal: Mapping[str, Any]) -> int:
        """Persist an analysis-signal record and return its document id."""

        payload = dict(signal)
        payload.setdefault("recorded_at", self._serialize_datetime(self._now()))
        serialized = self._serialize_document(payload)
        return self._write_operation(
            "record_signal",
            0,
            lambda: self.signals.insert(serialized),
        )

    def record_spread(
        self,
        instrument: str,
        spread_pips: float,
        is_spiking: bool,
        *,
        recorded_at: datetime | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> int:
        """Persist a spread observation and return its document id."""

        payload: dict[str, Any] = {
            "instrument": instrument,
            "spread_pips": float(spread_pips),
            "is_spiking": bool(is_spiking),
            "recorded_at": self._serialize_datetime(recorded_at or self._now()),
        }
        if metadata:
            payload.update(self._serialize_document(dict(metadata)))

        return self._write_operation(
            "record_spread",
            0,
            lambda: self.spread_history.insert(payload),
        )

    def get_recent_spreads(self, instrument: str, limit: int = 100) -> list[dict[str, Any]]:
        """Return recent spread observations for an instrument, newest first."""

        if limit <= 0:
            raise ValueError("limit must be a positive integer.")

        def read() -> list[dict[str, Any]]:
            assert self.spread_history is not None
            query = Query()
            records = [
                self._deserialize_spread_record(dict(record))
                for record in self.spread_history.search(query.instrument == instrument)
            ]
            records.sort(
                key=lambda item: item.get("recorded_at") or datetime.min.replace(tzinfo=timezone.utc),
                reverse=True,
            )
            return records[:limit]

        return self._read_operation("get_recent_spreads", [], read)

    def upsert_trade(self, trade: TradeRecord | Mapping[str, Any]) -> TradeRecord:
        """Upsert a trade record keyed by trade_id."""

        validated = self._validate_trade(trade)
        serialized = validated.model_dump(mode="json")

        def write() -> None:
            existing = self._find_trade_document(validated.trade_id)
            assert self.trades is not None
            if existing is None:
                self.trades.insert(serialized)
            else:
                self.trades.update(serialized, doc_ids=[existing.doc_id])

        self._strict_write_operation("upsert_trade", write)
        return validated

    def record_trade(self, trade: TradeRecord | Mapping[str, Any]) -> TradeRecord:
        """Compatibility alias for upserting a trade journal record."""

        return self.upsert_trade(trade)

    def upsert_trade_journal(self, trade: TradeRecord | Mapping[str, Any]) -> TradeRecord:
        """Compatibility alias for upserting a trade journal record."""

        return self.upsert_trade(trade)

    def get_trade(self, trade_id: str) -> TradeRecord | None:
        """Return one trade record by id."""

        return self._read_operation(
            "get_trade",
            None,
            lambda: self._deserialize_trade(self._find_trade_document(trade_id)),
        )

    def list_open_trades(self) -> list[TradeRecord]:
        """Return open trades ordered by opened_at descending."""

        return self._list_trades_by_state(TradeState.OPEN)

    def list_closed_trades(self) -> list[TradeRecord]:
        """Return closed trades ordered by closed_at descending."""

        return self._list_trades_by_state(TradeState.CLOSED)

    def close_trade(
        self,
        trade_id: str,
        *,
        close_price: float,
        close_reason: CloseReason,
        pips: float,
        instrument_pnl: float,
        instrument_pnl_currency: str,
        account_pnl: float,
        account_currency: str,
        closed_at: datetime,
    ) -> TradeRecord | None:
        """Transition an existing open trade to CLOSED."""

        existing = self.get_trade(trade_id)
        if existing is None:
            return None

        payload = existing.model_dump(mode="python")
        payload.update(
            {
                "close_price": close_price,
                "state": TradeState.CLOSED,
                "close_reason": close_reason,
                "pips": pips,
                "instrument_pnl": instrument_pnl,
                "instrument_pnl_currency": instrument_pnl_currency,
                "account_pnl": account_pnl,
                "account_currency": account_currency,
                "closed_at": closed_at,
            }
        )
        closed_trade = TradeRecord.model_validate(payload)
        return self.upsert_trade(closed_trade)

    def set_trade_notes(self, trade_id: str, notes: str | None) -> TradeRecord | None:
        """Update the notes attached to a trade record."""

        existing = self.get_trade(trade_id)
        if existing is None:
            return None

        payload = existing.model_dump(mode="python")
        payload["notes"] = notes
        updated = TradeRecord.model_validate(payload)
        return self.upsert_trade(updated)

    def upsert_raw_transactions(
        self,
        transactions: list[Mapping[str, Any]],
    ) -> tuple[int, int, int]:
        """Persist raw OANDA transaction payloads keyed by transaction id."""

        serialized_records = [self._serialize_raw_transaction_record(transaction) for transaction in transactions]

        def write() -> tuple[int, int, int]:
            assert self.raw_transactions is not None
            query = Query()
            seen = inserted = updated = 0
            for record in serialized_records:
                existing = self.raw_transactions.get(query.transaction_id == record["transaction_id"])
                seen += 1
                if existing is None:
                    self.raw_transactions.insert(record)
                    inserted += 1
                    continue
                if dict(existing) != record:
                    self.raw_transactions.update(record, doc_ids=[existing.doc_id])
                    updated += 1
            return seen, inserted, updated

        return self._strict_write_operation("upsert_raw_transactions", write)

    def list_raw_transactions(
        self,
        *,
        transaction_type: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return persisted raw transaction records ordered by transaction id ascending."""

        def read() -> list[dict[str, Any]]:
            assert self.raw_transactions is not None
            records = [self._deserialize_raw_transaction_record(dict(record)) for record in self.raw_transactions.all()]
            if transaction_type is not None:
                records = [
                    record
                    for record in records
                    if str(record.get("transaction_type") or "").upper() == transaction_type.upper()
                ]
            records.sort(key=lambda item: self._transaction_sort_key(item.get("transaction_id")))
            return records

        return self._read_operation("list_raw_transactions", [], read)

    def upsert_trade_history_events(
        self,
        events: list[TradeHistoryEvent | FinancingEvent],
    ) -> tuple[int, int, int]:
        """Persist normalized trade-history events keyed by stable event id."""

        serialized_records = [self._serialize_trade_history_record(event) for event in events]

        def write() -> tuple[int, int, int]:
            assert self.trade_history_events is not None
            query = Query()
            seen = inserted = updated = 0
            for record in serialized_records:
                existing = self.trade_history_events.get(query.event_id == record["event_id"])
                seen += 1
                if existing is None:
                    self.trade_history_events.insert(record)
                    inserted += 1
                    continue
                if dict(existing) != record:
                    self.trade_history_events.update(record, doc_ids=[existing.doc_id])
                    updated += 1
            return seen, inserted, updated

        return self._strict_write_operation("upsert_trade_history_events", write)

    def list_trade_history_records(
        self,
        *,
        start_utc: datetime | None = None,
        end_utc: datetime | None = None,
        instrument: str | None = None,
        event_types: tuple[str, ...] | None = None,
        trade_id: str | None = None,
        descending: bool = False,
    ) -> list[TradeHistoryEvent | FinancingEvent]:
        """Return normalized trade-history records filtered by time and identity."""

        normalized_start = self._deserialize_datetime(start_utc) if start_utc is not None else None
        normalized_end = self._deserialize_datetime(end_utc) if end_utc is not None else None
        normalized_event_types = {event_type.upper() for event_type in event_types or ()}

        def read() -> list[TradeHistoryEvent | FinancingEvent]:
            assert self.trade_history_events is not None
            records: list[TradeHistoryEvent | FinancingEvent] = []
            for raw_record in self.trade_history_events.all():
                record = self._deserialize_trade_history_record(raw_record)
                if record is None:
                    continue
                if normalized_start is not None and record.time_utc < normalized_start:
                    continue
                if normalized_end is not None and record.time_utc >= normalized_end:
                    continue
                if instrument is not None and getattr(record, "instrument", None) != instrument:
                    continue
                if normalized_event_types and record.event_type.upper() not in normalized_event_types:
                    continue
                if trade_id is not None:
                    if not isinstance(record, TradeHistoryEvent) or record.trade_id != trade_id:
                        continue
                records.append(record)

            records.sort(
                key=lambda item: (
                    item.time_utc,
                    self._transaction_sort_key(item.transaction_id),
                    item.event_id,
                ),
                reverse=descending,
            )
            return records

        return self._read_operation("list_trade_history_records", [], read)

    def list_trade_history_trade_events(
        self,
        *,
        start_utc: datetime | None = None,
        end_utc: datetime | None = None,
        instrument: str | None = None,
        event_types: tuple[str, ...] | None = None,
        trade_id: str | None = None,
        descending: bool = False,
    ) -> list[TradeHistoryEvent]:
        """Return normalized OPEN/CLOSE/PARTIAL_CLOSE events only."""

        records = self.list_trade_history_records(
            start_utc=start_utc,
            end_utc=end_utc,
            instrument=instrument,
            event_types=event_types,
            trade_id=trade_id,
            descending=descending,
        )
        return [record for record in records if isinstance(record, TradeHistoryEvent)]

    def list_trade_history_financing_events(
        self,
        *,
        start_utc: datetime | None = None,
        end_utc: datetime | None = None,
        instrument: str | None = None,
        descending: bool = False,
    ) -> list[FinancingEvent]:
        """Return normalized DAILY_FINANCING events only."""

        records = self.list_trade_history_records(
            start_utc=start_utc,
            end_utc=end_utc,
            instrument=instrument,
            event_types=("DAILY_FINANCING",),
            descending=descending,
        )
        return [record for record in records if isinstance(record, FinancingEvent)]

    def has_trade_history_data(self) -> bool:
        """Return True when any normalized trade-history record exists."""

        def read() -> bool:
            assert self.trade_history_events is not None
            return bool(self.trade_history_events.all())

        return self._read_operation("has_trade_history_data", False, read)

    def upsert_trade_history_sync_state(
        self,
        state: TradeHistorySyncState | Mapping[str, Any],
    ) -> TradeHistorySyncState:
        """Persist the incremental trade-history sync watermark for one account."""

        validated = self._validate_trade_history_sync_state(state)
        serialized = validated.model_dump(mode="json")

        def write() -> None:
            assert self.trade_history_sync is not None
            self.trade_history_sync.upsert(serialized, Query().account_id == validated.account_id)

        self._strict_write_operation("upsert_trade_history_sync_state", write)
        return validated

    def get_trade_history_sync_state(self, account_id: str) -> TradeHistorySyncState | None:
        """Return the last transaction watermark for one account."""

        return self._read_operation(
            "get_trade_history_sync_state",
            None,
            lambda: self._deserialize_trade_history_sync_state(
                None
                if self.trade_history_sync is None
                else self.trade_history_sync.get(Query().account_id == str(account_id))
            ),
        )

    def list_trade_history_sync_states(self) -> list[TradeHistorySyncState]:
        """Return all persisted trade-history sync watermarks."""

        def read() -> list[TradeHistorySyncState]:
            assert self.trade_history_sync is not None
            records = [
                TradeHistorySyncState.model_validate(dict(record))
                for record in self.trade_history_sync.all()
            ]
            records.sort(key=lambda item: item.account_id)
            return records

        return self._read_operation("list_trade_history_sync_states", [], read)

    def insert_excursion_sample(
        self,
        sample: ExcursionSample | Mapping[str, Any],
    ) -> ExcursionSample:
        """Persist one excursion sample."""

        validated = self._validate_excursion_sample(sample)
        serialized = validated.model_dump(mode="json")
        self._strict_write_operation(
            "insert_excursion_sample",
            lambda: self.excursion_samples.insert(serialized),
        )
        return validated

    def list_excursion_samples(self, trade_id: str) -> list[ExcursionSample]:
        """Return excursion samples for a trade ordered oldest-first."""

        def read() -> list[ExcursionSample]:
            assert self.excursion_samples is not None
            query = Query()
            samples = [
                ExcursionSample.model_validate(dict(record))
                for record in self.excursion_samples.search(query.trade_id == trade_id)
            ]
            samples.sort(key=lambda sample: sample.sampled_at)
            return samples

        return self._read_operation("list_excursion_samples", [], read)

    def get_trade_mae_mfe(self, trade_id: str) -> dict[str, Any] | None:
        """Aggregate MAE/MFE from stored excursion samples for a trade."""

        samples = self.list_excursion_samples(trade_id)
        if not samples:
            return None

        return {
            "trade_id": trade_id,
            "sample_count": len(samples),
            "mae_pips": max(sample.adverse_pips for sample in samples),
            "mfe_pips": max(sample.favorable_pips for sample in samples),
            "last_sampled_at": samples[-1].sampled_at,
        }

    def upsert_price_alert(
        self,
        alert: PriceAlert | Mapping[str, Any],
    ) -> PriceAlert:
        """Upsert a price alert by its id."""

        payload = dict(alert) if isinstance(alert, Mapping) else alert.model_dump(mode="python")
        payload.setdefault("status", AlertStatus.PENDING)
        payload.setdefault("armed", False)
        payload.setdefault("created_at", self._now())
        payload.setdefault("fired_at", None)

        def write() -> PriceAlert:
            assert self.price_alerts is not None
            resolved = dict(payload)
            resolved["id"] = self._resolve_alert_id_locked(
                resolved.get("id"),
                table=self.price_alerts,
            )
            validated = PriceAlert.model_validate(resolved)
            serialized = validated.model_dump(mode="json")
            existing = self._find_alert_document(self.price_alerts, validated.id)
            if existing is None:
                self.price_alerts.insert(serialized)
            else:
                self.price_alerts.update(serialized, doc_ids=[existing.doc_id])
            return validated

        return self._strict_write_operation("upsert_price_alert", write)

    def get_price_alert(self, alert_id: int) -> PriceAlert | None:
        """Return one price alert by id."""

        return self._read_operation(
            "get_price_alert",
            None,
            lambda: self._deserialize_price_alert(self._find_alert_document(self.price_alerts, alert_id)),
        )

    def list_pending_price_alerts(self) -> list[PriceAlert]:
        """Return pending price alerts ordered by id ascending."""

        return self.list_price_alerts(statuses=(AlertStatus.PENDING,))

    def list_pending_price_alerts_for_chat(self, chat_id: int) -> list[PriceAlert]:
        """Return pending price alerts for one Telegram chat."""

        return self.list_price_alerts(statuses=(AlertStatus.PENDING,), chat_id=chat_id)

    def list_price_alerts(
        self,
        *,
        statuses: tuple[AlertStatus, ...] | None = None,
        chat_id: int | None = None,
    ) -> list[PriceAlert]:
        """Return price alerts filtered by status, ordered by id ascending."""

        def read() -> list[PriceAlert]:
            assert self.price_alerts is not None
            if statuses is None:
                records = self.price_alerts.all()
            else:
                status_values = {status.value for status in statuses}
                records = [
                    record
                    for record in self.price_alerts.all()
                    if record.get("status") in status_values
                ]
            if chat_id is not None:
                records = [record for record in records if int(record.get("chat_id", -1)) == int(chat_id)]

            alerts = [PriceAlert.model_validate(dict(record)) for record in records]
            alerts.sort(key=lambda alert: alert.id)
            return alerts

        return self._read_operation("list_price_alerts", [], read)

    def mark_price_alert_fired(
        self,
        alert_id: int,
        *,
        fired_at: datetime | None = None,
    ) -> PriceAlert | None:
        """Transition a price alert to FIRED."""

        existing = self.get_price_alert(alert_id)
        if existing is None:
            return None

        payload = existing.model_dump(mode="python")
        payload.update(
            {
                "status": AlertStatus.FIRED,
                "armed": False,
                "fired_at": fired_at or self._now(),
            }
        )
        return self.upsert_price_alert(PriceAlert.model_validate(payload))

    def mark_price_alert_armed(self, alert_id: int) -> PriceAlert | None:
        """Persist that a pending price alert has observed the safe side."""

        existing = self.get_price_alert(alert_id)
        if existing is None or existing.status != AlertStatus.PENDING:
            return None
        if existing.armed:
            return existing

        payload = existing.model_dump(mode="python")
        payload.update({"armed": True})
        return self.upsert_price_alert(PriceAlert.model_validate(payload))

    def cancel_price_alert(self, alert_id: int) -> PriceAlert | None:
        """Transition a price alert to CANCELLED."""

        existing = self.get_price_alert(alert_id)
        if existing is None:
            return None

        payload = existing.model_dump(mode="python")
        payload.update({"status": AlertStatus.CANCELLED, "armed": False, "fired_at": None})
        return self.upsert_price_alert(PriceAlert.model_validate(payload))

    def cancel_price_alert_for_chat(self, alert_id: int, chat_id: int) -> PriceAlert | None:
        """Cancel one price alert only when it belongs to the requesting chat."""

        existing = self.get_price_alert(alert_id)
        if existing is None or existing.chat_id != int(chat_id):
            return None
        return self.cancel_price_alert(alert_id)

    def upsert_indicator_alert(
        self,
        alert: IndicatorAlert | Mapping[str, Any],
    ) -> IndicatorAlert:
        """Upsert an indicator alert by its id."""

        payload = dict(alert) if isinstance(alert, Mapping) else alert.model_dump(mode="python")
        payload.setdefault("status", AlertStatus.PENDING)
        payload.setdefault("created_at", self._now())
        payload.setdefault("fired_at", None)
        payload.setdefault("repeat", False)
        payload.setdefault("cooloff_minutes", None)

        def write() -> IndicatorAlert:
            assert self.indicator_alerts is not None
            resolved = dict(payload)
            resolved["id"] = self._resolve_alert_id_locked(
                resolved.get("id"),
                table=self.indicator_alerts,
            )
            validated = IndicatorAlert.model_validate(resolved)
            serialized = validated.model_dump(mode="json")
            existing = self._find_alert_document(self.indicator_alerts, validated.id)
            if existing is None:
                self.indicator_alerts.insert(serialized)
            else:
                self.indicator_alerts.update(serialized, doc_ids=[existing.doc_id])
            return validated

        return self._strict_write_operation("upsert_indicator_alert", write)

    def get_indicator_alert(self, alert_id: int) -> IndicatorAlert | None:
        """Return one indicator alert by id."""

        return self._read_operation(
            "get_indicator_alert",
            None,
            lambda: self._deserialize_indicator_alert(
                self._find_alert_document(self.indicator_alerts, alert_id)
            ),
        )

    def list_pending_indicator_alerts(self) -> list[IndicatorAlert]:
        """Return pending indicator alerts ordered by id ascending."""

        return self.list_indicator_alerts(statuses=(AlertStatus.PENDING,))

    def list_pending_indicator_alerts_for_chat(self, chat_id: int) -> list[IndicatorAlert]:
        """Return pending indicator alerts for one Telegram chat."""

        return self.list_indicator_alerts(statuses=(AlertStatus.PENDING,), chat_id=chat_id)

    def list_indicator_alerts(
        self,
        *,
        statuses: tuple[AlertStatus, ...] | None = None,
        chat_id: int | None = None,
    ) -> list[IndicatorAlert]:
        """Return indicator alerts filtered by status, ordered by id ascending."""

        def read() -> list[IndicatorAlert]:
            assert self.indicator_alerts is not None
            if statuses is None:
                records = self.indicator_alerts.all()
            else:
                status_values = {status.value for status in statuses}
                records = [
                    record
                    for record in self.indicator_alerts.all()
                    if record.get("status") in status_values
                ]
            if chat_id is not None:
                records = [record for record in records if int(record.get("chat_id", -1)) == int(chat_id)]

            alerts = [IndicatorAlert.model_validate(dict(record)) for record in records]
            alerts.sort(key=lambda alert: alert.id)
            return alerts

        return self._read_operation("list_indicator_alerts", [], read)

    def mark_indicator_alert_fired(
        self,
        alert_id: int,
        *,
        fired_at: datetime | None = None,
    ) -> IndicatorAlert | None:
        """Transition an indicator alert to FIRED."""

        existing = self.get_indicator_alert(alert_id)
        if existing is None:
            return None

        payload = existing.model_dump(mode="python")
        payload.update({"status": AlertStatus.FIRED, "fired_at": fired_at or self._now()})
        return self.upsert_indicator_alert(IndicatorAlert.model_validate(payload))

    def cancel_indicator_alert(self, alert_id: int) -> IndicatorAlert | None:
        """Transition an indicator alert to CANCELLED."""

        existing = self.get_indicator_alert(alert_id)
        if existing is None:
            return None

        payload = existing.model_dump(mode="python")
        payload.update({"status": AlertStatus.CANCELLED, "fired_at": None})
        return self.upsert_indicator_alert(IndicatorAlert.model_validate(payload))

    def cancel_indicator_alert_for_chat(self, alert_id: int, chat_id: int) -> IndicatorAlert | None:
        """Cancel one indicator alert only when it belongs to the requesting chat."""

        existing = self.get_indicator_alert(alert_id)
        if existing is None or existing.chat_id != int(chat_id):
            return None
        return self.cancel_indicator_alert(alert_id)

    def upsert_time_alert(
        self,
        alert: TimeAlert | Mapping[str, Any],
    ) -> TimeAlert:
        """Upsert a time alert by its id."""

        payload = dict(alert) if isinstance(alert, Mapping) else alert.model_dump(mode="python")
        payload.setdefault("status", TimeAlertStatus.ACTIVE)
        payload.setdefault("created_at", self._now())
        payload.setdefault("last_fired_at", None)

        def write() -> TimeAlert:
            assert self.time_alerts is not None
            resolved = dict(payload)
            resolved["id"] = self._resolve_alert_id_locked(
                resolved.get("id"),
                table=self.time_alerts,
            )
            validated = TimeAlert.model_validate(resolved)
            serialized = validated.model_dump(mode="json")
            existing = self._find_alert_document(self.time_alerts, validated.id)
            if existing is None:
                self.time_alerts.insert(serialized)
            else:
                self.time_alerts.update(serialized, doc_ids=[existing.doc_id])
            return validated

        return self._strict_write_operation("upsert_time_alert", write)

    def get_time_alert(self, alert_id: int) -> TimeAlert | None:
        """Return one time alert by id."""

        return self._read_operation(
            "get_time_alert",
            None,
            lambda: self._deserialize_time_alert(self._find_alert_document(self.time_alerts, alert_id)),
        )

    def list_time_alerts(
        self,
        *,
        statuses: tuple[TimeAlertStatus, ...] | None = None,
        chat_id: int | None = None,
    ) -> list[TimeAlert]:
        """Return time alerts filtered by status and chat, ordered by id ascending."""

        def read() -> list[TimeAlert]:
            assert self.time_alerts is not None
            if statuses is None:
                records = self.time_alerts.all()
            else:
                status_values = {status.value for status in statuses}
                records = [
                    record
                    for record in self.time_alerts.all()
                    if record.get("status") in status_values
                ]
            if chat_id is not None:
                records = [record for record in records if int(record.get("chat_id", -1)) == int(chat_id)]

            alerts = [TimeAlert.model_validate(dict(record)) for record in records]
            alerts.sort(key=lambda item: item.id)
            return alerts

        return self._read_operation("list_time_alerts", [], read)

    def list_active_time_alerts(self) -> list[TimeAlert]:
        """Return active time alerts."""

        return self.list_time_alerts(statuses=(TimeAlertStatus.ACTIVE,))

    def list_active_time_alerts_for_chat(self, chat_id: int) -> list[TimeAlert]:
        """Return active time alerts scoped to one Telegram chat."""

        return self.list_time_alerts(statuses=(TimeAlertStatus.ACTIVE,), chat_id=chat_id)

    def mark_time_alert_triggered(
        self,
        alert_id: int,
        *,
        fired_at: datetime | None = None,
        next_fire_at: datetime | None = None,
    ) -> TimeAlert | None:
        """Advance a time alert after one reminder fires."""

        existing = self.get_time_alert(alert_id)
        if existing is None:
            return None

        payload = existing.model_dump(mode="python")
        payload.update(
            {
                "status": TimeAlertStatus.ACTIVE if next_fire_at is not None else TimeAlertStatus.COMPLETED,
                "last_fired_at": fired_at or self._now(),
                "next_fire_at": next_fire_at,
            }
        )
        return self.upsert_time_alert(TimeAlert.model_validate(payload))

    def cancel_time_alert(self, alert_id: int) -> TimeAlert | None:
        """Transition a time alert to CANCELLED."""

        existing = self.get_time_alert(alert_id)
        if existing is None:
            return None

        payload = existing.model_dump(mode="python")
        payload.update({"status": TimeAlertStatus.CANCELLED, "next_fire_at": None})
        return self.upsert_time_alert(TimeAlert.model_validate(payload))

    def cancel_time_alert_for_chat(self, alert_id: int, chat_id: int) -> TimeAlert | None:
        """Cancel one time alert only when it belongs to the requesting chat."""

        existing = self.get_time_alert(alert_id)
        if existing is None or existing.chat_id != int(chat_id):
            return None
        return self.cancel_time_alert(alert_id)

    def upsert_indicator_alert_evaluation_cursor(
        self,
        record: IndicatorAlertEvaluationCursor | Mapping[str, Any],
    ) -> IndicatorAlertEvaluationCursor:
        """Persist the last evaluated closed candle for one alert timeframe."""

        validated = self._validate_indicator_alert_evaluation_cursor(record)
        serialized = validated.model_dump(mode="json")

        def write() -> None:
            assert self.indicator_alert_cursors is not None
            self.indicator_alert_cursors.upsert(
                serialized,
                self._indicator_alert_cursor_query(
                    validated.instrument,
                    validated.granularity,
                ),
            )

        self._strict_write_operation("upsert_indicator_alert_evaluation_cursor", write)
        return validated

    def get_indicator_alert_evaluation_cursor(
        self,
        instrument: str,
        granularity: str,
    ) -> IndicatorAlertEvaluationCursor | None:
        """Return the last evaluated candle cursor for one alert timeframe."""

        return self._read_operation(
            "get_indicator_alert_evaluation_cursor",
            None,
            lambda: self._deserialize_indicator_alert_evaluation_cursor(
                None
                if self.indicator_alert_cursors is None
                else self.indicator_alert_cursors.get(
                    self._indicator_alert_cursor_query(instrument, granularity)
                )
            ),
        )

    def list_indicator_alert_evaluation_cursors(self) -> list[IndicatorAlertEvaluationCursor]:
        """Return all persisted indicator alert evaluation cursors."""

        def read() -> list[IndicatorAlertEvaluationCursor]:
            assert self.indicator_alert_cursors is not None
            records = [
                IndicatorAlertEvaluationCursor.model_validate(dict(record))
                for record in self.indicator_alert_cursors.all()
            ]
            records.sort(key=lambda item: (item.instrument, item.granularity))
            return records

        return self._read_operation("list_indicator_alert_evaluation_cursors", [], read)

    def close(self) -> None:
        """Close the underlying TinyDB handle."""

        with self._lock:
            if self.db is not None:
                self.db.close()
            self.db = None
            self.trades = None
            self.signals = None
            self.spread_history = None
            self.cache_metadata = None
            self.excursion_samples = None
            self.price_alerts = None
            self.indicator_alerts = None
            self.time_alerts = None
            self.indicator_alert_cursors = None
            self.sessions = None
            self.runtime_config = None
            self.raw_transactions = None
            self.trade_history_events = None
            self.trade_history_sync = None
            runtime_lock_path = self._runtime_lock_path
            self._runtime_lock_path = None
        _release_runtime_lock(runtime_lock_path)

    def _list_trades_by_state(self, state: TradeState) -> list[TradeRecord]:
        def read() -> list[TradeRecord]:
            assert self.trades is not None
            query = Query()
            trades = [
                TradeRecord.model_validate(dict(record))
                for record in self.trades.search(query.state == state.value)
            ]
            if state == TradeState.OPEN:
                trades.sort(key=lambda trade: trade.opened_at, reverse=True)
            else:
                trades.sort(
                    key=lambda trade: trade.closed_at or datetime.min.replace(tzinfo=timezone.utc),
                    reverse=True,
                )
            return trades

        return self._read_operation(f"list_{state.value.casefold()}_trades", [], read)

    def _get_cache_metadata_record(
        self,
        instrument: str,
        timeframe: str,
    ) -> dict[str, Any] | None:
        assert self.cache_metadata is not None
        record = self.cache_metadata.get(self._cache_query(instrument, timeframe))
        if record is None:
            return None
        return self._deserialize_cache_metadata(record)

    def _find_trade_document(self, trade_id: str) -> Any:
        assert self.trades is not None
        query = Query()
        return self.trades.get(query.trade_id == trade_id)

    @staticmethod
    def _find_alert_document(table: Any, alert_id: int) -> Any:
        if table is None:
            return None
        query = Query()
        return table.get(query.id == alert_id)

    @staticmethod
    def _deserialize_trade(record: Any) -> TradeRecord | None:
        if record is None:
            return None
        return TradeRecord.model_validate(dict(record))

    @staticmethod
    def _deserialize_price_alert(record: Any) -> PriceAlert | None:
        if record is None:
            return None
        return PriceAlert.model_validate(dict(record))

    @staticmethod
    def _deserialize_indicator_alert(record: Any) -> IndicatorAlert | None:
        if record is None:
            return None
        return IndicatorAlert.model_validate(dict(record))

    @staticmethod
    def _deserialize_time_alert(record: Any) -> TimeAlert | None:
        if record is None:
            return None
        return TimeAlert.model_validate(dict(record))

    @staticmethod
    def _deserialize_indicator_alert_evaluation_cursor(
        record: Any,
    ) -> IndicatorAlertEvaluationCursor | None:
        if record is None:
            return None
        return IndicatorAlertEvaluationCursor.model_validate(dict(record))

    @staticmethod
    def _deserialize_session(record: Any) -> BotSessionRecord | None:
        if record is None:
            return None
        return BotSessionRecord.model_validate(dict(record))

    @staticmethod
    def _deserialize_runtime_config(record: Any) -> RuntimeConfigRecord | None:
        if record is None:
            return None
        return RuntimeConfigRecord.model_validate(dict(record))

    @staticmethod
    def _deserialize_trade_history_record(record: Any) -> TradeHistoryEvent | FinancingEvent | None:
        if record is None:
            return None
        payload = dict(record)
        if payload.get("event_type") == "DAILY_FINANCING":
            return FinancingEvent.model_validate(payload)
        return TradeHistoryEvent.model_validate(payload)

    @staticmethod
    def _deserialize_trade_history_sync_state(record: Any) -> TradeHistorySyncState | None:
        if record is None:
            return None
        return TradeHistorySyncState.model_validate(dict(record))

    @staticmethod
    def _serialize_trade_history_record(
        record: TradeHistoryEvent | FinancingEvent,
    ) -> dict[str, Any]:
        return record.model_dump(mode="json")

    @staticmethod
    def _serialize_raw_transaction_record(transaction: Mapping[str, Any]) -> dict[str, Any]:
        payload = TradeStore._serialize_document(dict(transaction))
        transaction_id = payload.get("id") or payload.get("transaction_id")
        if transaction_id is None or not str(transaction_id).strip():
            raise ValueError("Raw transaction payloads must include a non-empty id.")

        transaction_time = payload.get("time")
        if isinstance(transaction_time, datetime):
            serialized_time = TradeStore._serialize_datetime(transaction_time)
        else:
            serialized_time = None if transaction_time is None else str(transaction_time)

        return {
            "transaction_id": str(transaction_id),
            "account_id": None
            if payload.get("accountID") is None
            else str(payload.get("accountID")),
            "transaction_type": None
            if payload.get("type") is None
            else str(payload.get("type")),
            "time_utc": serialized_time,
            "payload": payload,
        }

    @staticmethod
    def _deserialize_raw_transaction_record(record: Mapping[str, Any]) -> dict[str, Any]:
        normalized = dict(record)
        normalized["time_utc"] = TradeStore._deserialize_datetime(normalized.get("time_utc"))
        return normalized

    @staticmethod
    def _transaction_sort_key(value: Any) -> tuple[int, str]:
        if value is None:
            return (0, "")
        try:
            return (0, f"{int(value):020d}")
        except (TypeError, ValueError):
            return (1, str(value))

    def _resolve_alert_id(self, value: Any, *, table_name: str) -> int:
        if value is not None:
            return int(value)
        return self._read_operation(
            f"next_{table_name}_id",
            1,
            lambda: self._next_numeric_id(getattr(self, table_name)),
        )

    @staticmethod
    def _resolve_alert_id_locked(value: Any, *, table: Any) -> int:
        if value is not None:
            return int(value)
        return TradeStore._next_numeric_id(table)

    @staticmethod
    def _next_numeric_id(table: Any) -> int:
        if table is None:
            return 1
        max_id = 0
        for record in table.all():
            try:
                max_id = max(max_id, int(record.get("id", 0)))
            except (TypeError, ValueError):
                continue
        return max_id + 1

    def _read_operation(self, action: str, default: T, fn: Callable[[], T]) -> T:
        with self._lock:
            if self.db is None:
                return default
            try:
                return fn()
            except Exception as exc:
                self._warn_persistence_failure(action, exc)
                return default

    def _write_operation(self, action: str, default: T, fn: Callable[[], T]) -> T:
        with self._lock:
            if self.db is None:
                return default
            try:
                return fn()
            except Exception as exc:
                self._warn_persistence_failure(action, exc)
                return default

    def _strict_write_operation(self, action: str, fn: Callable[[], T]) -> T:
        with self._lock:
            if self.db is None:
                exc = RuntimeError("database unavailable")
                self._warn_persistence_failure(action, exc)
                raise PersistenceWriteError(action, self.db_path, "database unavailable") from exc
            try:
                return fn()
            except PersistenceWriteError:
                raise
            except Exception as exc:
                self._warn_persistence_failure(action, exc)
                raise PersistenceWriteError(action, self.db_path, str(exc) or repr(exc)) from exc

    def _warn_persistence_failure(self, action: str, exc: Exception) -> None:
        log_failure(
            self.logger,
            "persistence_degraded",
            exc,
            level="warning",
            action=action,
            db_path=str(self.db_path),
        )

    @staticmethod
    def _cache_query(instrument: str, timeframe: str) -> Any:
        query = Query()
        return (query.instrument == instrument) & (query.timeframe == timeframe)

    @staticmethod
    def _indicator_alert_cursor_query(instrument: str, granularity: str) -> Any:
        query = Query()
        return (query.instrument == instrument) & (query.granularity == granularity)

    @staticmethod
    def _serialize_cache_metadata(
        instrument: str,
        timeframe: str,
        metadata: Mapping[str, Any],
    ) -> dict[str, Any]:
        record = {
            "instrument": instrument,
            "timeframe": timeframe,
            "last_completed_candle": TradeStore._serialize_datetime(
                metadata["last_completed_candle"]
            ),
            "fetched_at": TradeStore._serialize_datetime(metadata["fetched_at"]),
            "candle_count": int(metadata["candle_count"]),
            "source": str(metadata["source"]),
        }

        for key, value in metadata.items():
            if key not in record:
                record[key] = TradeStore._serialize_document(value)

        return record

    @staticmethod
    def _deserialize_cache_metadata(record: Mapping[str, Any]) -> dict[str, Any]:
        normalized = dict(record)
        normalized["last_completed_candle"] = TradeStore._deserialize_datetime(
            normalized.get("last_completed_candle")
        )
        normalized["fetched_at"] = TradeStore._deserialize_datetime(
            normalized.get("fetched_at")
        )
        return normalized

    @staticmethod
    def _serialize_document(value: Any) -> Any:
        if isinstance(value, Mapping):
            return {
                str(key): TradeStore._serialize_document(inner_value)
                for key, inner_value in value.items()
            }
        if isinstance(value, (list, tuple)):
            return [TradeStore._serialize_document(item) for item in value]
        if isinstance(value, datetime):
            return TradeStore._serialize_datetime(value)
        if isinstance(value, Decimal):
            return str(value)
        return value

    @staticmethod
    def _deserialize_spread_record(record: Mapping[str, Any]) -> dict[str, Any]:
        normalized = dict(record)
        normalized["recorded_at"] = TradeStore._deserialize_datetime(normalized.get("recorded_at"))
        return normalized

    @staticmethod
    def _serialize_datetime(value: Any) -> str:
        timestamp = datetime.fromisoformat(str(value)) if isinstance(value, str) else value
        if not isinstance(timestamp, datetime):
            raise TypeError("Cache metadata datetimes must be datetime instances.")
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        else:
            timestamp = timestamp.astimezone(timezone.utc)
        return timestamp.isoformat()

    @staticmethod
    def _deserialize_datetime(value: Any) -> datetime | None:
        if value is None:
            return None
        if isinstance(value, datetime):
            timestamp = value
        else:
            timestamp = datetime.fromisoformat(str(value))
        if timestamp.tzinfo is None:
            return timestamp.replace(tzinfo=timezone.utc)
        return timestamp.astimezone(timezone.utc)

    @staticmethod
    def _validate_trade(trade: TradeRecord | Mapping[str, Any]) -> TradeRecord:
        return trade if isinstance(trade, TradeRecord) else TradeRecord.model_validate(trade)

    @staticmethod
    def _validate_excursion_sample(
        sample: ExcursionSample | Mapping[str, Any],
    ) -> ExcursionSample:
        return (
            sample
            if isinstance(sample, ExcursionSample)
            else ExcursionSample.model_validate(sample)
        )

    @staticmethod
    def _validate_session(
        session: BotSessionRecord | Mapping[str, Any],
    ) -> BotSessionRecord:
        return (
            session
            if isinstance(session, BotSessionRecord)
            else BotSessionRecord.model_validate(session)
        )

    @staticmethod
    def _validate_runtime_config(
        record: RuntimeConfigRecord | Mapping[str, Any],
    ) -> RuntimeConfigRecord:
        return (
            record
            if isinstance(record, RuntimeConfigRecord)
            else RuntimeConfigRecord.model_validate(record)
        )

    @staticmethod
    def _validate_indicator_alert_evaluation_cursor(
        record: IndicatorAlertEvaluationCursor | Mapping[str, Any],
    ) -> IndicatorAlertEvaluationCursor:
        return (
            record
            if isinstance(record, IndicatorAlertEvaluationCursor)
            else IndicatorAlertEvaluationCursor.model_validate(record)
        )

    @staticmethod
    def _validate_trade_history_sync_state(
        record: TradeHistorySyncState | Mapping[str, Any],
    ) -> TradeHistorySyncState:
        return (
            record
            if isinstance(record, TradeHistorySyncState)
            else TradeHistorySyncState.model_validate(record)
        )

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)


__all__ = ["PersistenceWriteError", "TradeStore"]
