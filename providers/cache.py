"""Three-level candle cache with candle-boundary freshness semantics."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import RLock

import pandas as pd

from core.candle_policy import (
    calculate_candle_staleness_seconds,
    get_timeframe_delta,
    validate_candle_df,
)
from core.instrument_registry import validate_live_instrument
from core.logging_setup import get_logger, log_failure
from data.csv_persistence import CandleCsvStore
from data.persistence.trade_store import TradeStore
from providers.base import CandleFreshness

CacheFetcher = Callable[[str, str, int, pd.Timestamp | None], pd.DataFrame]


@dataclass(frozen=True)
class CacheEntry:
    """Canonical candle cache entry shared across memory, CSV, and API paths."""

    candles: pd.DataFrame
    last_completed_candle: datetime
    fetched_at: datetime
    source: str
    candle_count: int


def is_cache_fresh(
    cached_last_candle: datetime | pd.Timestamp,
    timeframe: str,
    *,
    now_utc: datetime | pd.Timestamp | None = None,
) -> bool:
    """Return True when cached data contains the latest completed candle."""

    return (
        calculate_candle_staleness_seconds(
            cached_last_candle,
            timeframe,
            now_utc=now_utc,
        )
        == 0.0
    )


class CandleCache:
    """Resolve candles through memory, CSV, then API with one freshness policy."""

    def __init__(
        self,
        *,
        csv_store: CandleCsvStore | None = None,
        trade_store: TradeStore | None = None,
    ) -> None:
        self.csv_store = csv_store or CandleCsvStore()
        self.trade_store = trade_store or TradeStore()
        self._memory_cache: dict[tuple[str, str], CacheEntry] = {}
        self._lock = RLock()
        self.logger = get_logger(__name__)

    def get_candles(
        self,
        instrument: str,
        timeframe: str,
        count: int,
        fetcher: CacheFetcher,
    ) -> pd.DataFrame:
        """Return canonical closed candles, fetching and persisting as needed."""

        resolved_instrument = validate_live_instrument(instrument)
        get_timeframe_delta(timeframe)
        if count <= 0:
            self.logger.warning(
                "cache_request_invalid",
                instrument=resolved_instrument,
                timeframe=timeframe,
                requested_count=count,
            )
            raise ValueError("count must be a positive integer.")

        key = (resolved_instrument, timeframe)
        with self._lock:
            memory_entry = self._memory_cache.get(key)

        if memory_entry is not None:
            self._log_cache_lookup("memory", resolved_instrument, timeframe, memory_entry)
            if is_cache_fresh(memory_entry.last_completed_candle, timeframe):
                if memory_entry.candle_count >= count:
                    return self._tail(memory_entry.candles, count)
                return self._replace_from_api(resolved_instrument, timeframe, count, fetcher)
            return self._append_refresh(resolved_instrument, timeframe, count, memory_entry, fetcher)

        self._log_cache_lookup("memory", resolved_instrument, timeframe, None)
        csv_entry = self._load_csv_entry(resolved_instrument, timeframe)
        if csv_entry is not None:
            self._log_cache_lookup("csv", resolved_instrument, timeframe, csv_entry)
            with self._lock:
                self._memory_cache[key] = csv_entry
            if is_cache_fresh(csv_entry.last_completed_candle, timeframe):
                if csv_entry.candle_count >= count:
                    return self._tail(csv_entry.candles, count)
                return self._replace_from_api(resolved_instrument, timeframe, count, fetcher)
            return self._append_refresh(resolved_instrument, timeframe, count, csv_entry, fetcher)

        self._log_cache_lookup("csv", resolved_instrument, timeframe, None)
        return self._replace_from_api(resolved_instrument, timeframe, count, fetcher)

    def get_candle_freshness(self, instrument: str, timeframe: str) -> CandleFreshness:
        """Return freshness metadata without forcing an API fetch."""

        resolved_instrument = validate_live_instrument(instrument)
        get_timeframe_delta(timeframe)

        key = (resolved_instrument, timeframe)
        with self._lock:
            memory_entry = self._memory_cache.get(key)
        if memory_entry is not None:
            return self._freshness_from_entry(resolved_instrument, timeframe, memory_entry)

        metadata = self.trade_store.get_cache_metadata(resolved_instrument, timeframe)
        if metadata is not None:
            return self._freshness_from_metadata(resolved_instrument, timeframe, metadata)

        return CandleFreshness(
            instrument=resolved_instrument,
            timeframe=timeframe,
            last_completed_candle=None,
            fetched_at=None,
            source=None,
            candle_count=0,
            is_fresh=False,
            staleness_seconds=None,
        )

    def get_cached_candles(
        self,
        instrument: str,
        timeframe: str,
        count: int,
    ) -> pd.DataFrame | None:
        """Return cached candles only when cache data and metadata already exist."""

        resolved_instrument = validate_live_instrument(instrument)
        get_timeframe_delta(timeframe)
        if count <= 0:
            self.logger.warning(
                "cache_request_invalid",
                instrument=resolved_instrument,
                timeframe=timeframe,
                requested_count=count,
            )
            raise ValueError("count must be a positive integer.")

        key = (resolved_instrument, timeframe)
        with self._lock:
            memory_entry = self._memory_cache.get(key)
        if memory_entry is not None:
            self._log_cache_lookup("memory", resolved_instrument, timeframe, memory_entry)
            return self._tail(memory_entry.candles, count)

        self._log_cache_lookup("memory", resolved_instrument, timeframe, None)
        csv_entry = self._load_csv_entry(
            resolved_instrument,
            timeframe,
            bootstrap_metadata=False,
        )
        if csv_entry is None:
            self._log_cache_lookup("csv", resolved_instrument, timeframe, None)
            return None
        self._log_cache_lookup("csv", resolved_instrument, timeframe, csv_entry)
        with self._lock:
            self._memory_cache[key] = csv_entry
        return self._tail(csv_entry.candles, count)

    def clear_memory(self) -> None:
        """Drop all in-memory entries while preserving CSV/TinyDB state."""

        with self._lock:
            self._memory_cache.clear()

    def _replace_from_api(
        self,
        instrument: str,
        timeframe: str,
        count: int,
        fetcher: CacheFetcher,
    ) -> pd.DataFrame:
        try:
            candles = fetcher(instrument, timeframe, count, None)
        except Exception as exc:
            log_failure(
                self.logger,
                "cache_replace_fetch_failed",
                exc,
                instrument=instrument,
                timeframe=timeframe,
                requested_count=count,
            )
            raise
        if candles.empty:
            self.logger.error(
                "cache_replace_empty",
                instrument=instrument,
                timeframe=timeframe,
                requested_count=count,
            )
            raise RuntimeError(
                f"Market-data fetch for {instrument} {timeframe} returned no closed candles."
            )

        entry = self._persist_entry(
            instrument=instrument,
            timeframe=timeframe,
            candles=candles,
            source="oanda_api",
            fetched_at=datetime.now(timezone.utc),
        )
        return self._tail(entry.candles, count)

    def _append_refresh(
        self,
        instrument: str,
        timeframe: str,
        count: int,
        existing: CacheEntry,
        fetcher: CacheFetcher,
    ) -> pd.DataFrame:
        since = pd.Timestamp(existing.last_completed_candle) + get_timeframe_delta(timeframe)
        try:
            newer = fetcher(instrument, timeframe, count, since)
        except Exception as exc:
            log_failure(
                self.logger,
                "cache_append_refresh_failed",
                exc,
                instrument=instrument,
                timeframe=timeframe,
                requested_count=count,
                since=since.to_pydatetime(),
                cached_last_candle=existing.last_completed_candle,
            )
            raise
        if newer.empty:
            # Weekend/holiday gap: no new complete candles available yet.
            # Return the best available data from the existing cache.
            self.logger.info(
                "append_refresh_no_new_candles",
                instrument=instrument,
                timeframe=timeframe,
            )
            return self._tail(existing.candles, count)

        combined = pd.concat([existing.candles, newer], ignore_index=True)
        combined = (
            combined.drop_duplicates(subset=["time"], keep="last")
            .sort_values("time", kind="mergesort")
            .reset_index(drop=True)
        )

        entry = self._persist_entry(
            instrument=instrument,
            timeframe=timeframe,
            candles=combined,
            source="oanda_api",
            fetched_at=datetime.now(timezone.utc),
        )
        return self._tail(entry.candles, count)

    def _load_csv_entry(
        self,
        instrument: str,
        timeframe: str,
        *,
        bootstrap_metadata: bool = True,
    ) -> CacheEntry | None:
        candles = self.csv_store.load_candles(instrument, timeframe)
        if candles is None:
            return None

        metadata = self.trade_store.get_cache_metadata(instrument, timeframe)
        if metadata is None:
            if not bootstrap_metadata:
                return None
            fetched_at = datetime.fromtimestamp(
                self.csv_store.path_for(instrument, timeframe).stat().st_mtime,
                tz=timezone.utc,
            )
            source = "csv"
            candle_count = len(candles)
            self.trade_store.upsert_cache_metadata(
                instrument=instrument,
                timeframe=timeframe,
                last_completed_candle=candles["time"].iloc[-1].to_pydatetime(),
                fetched_at=fetched_at,
                candle_count=candle_count,
                source=source,
            )
        else:
            fetched_at = metadata["fetched_at"]
            source = str(metadata.get("source", "csv"))
            candle_count = int(metadata.get("candle_count", len(candles)))

        return CacheEntry(
            candles=candles,
            last_completed_candle=candles["time"].iloc[-1].to_pydatetime(),
            fetched_at=fetched_at,
            source=source,
            candle_count=candle_count,
        )

    def _persist_entry(
        self,
        *,
        instrument: str,
        timeframe: str,
        candles: pd.DataFrame,
        source: str,
        fetched_at: datetime,
    ) -> CacheEntry:
        validated = validate_candle_df(candles)
        if validated.empty:
            self.logger.error(
                "cache_persist_empty",
                instrument=instrument,
                timeframe=timeframe,
                source=source,
            )
            raise RuntimeError(
                f"Cannot persist an empty candle cache for {instrument} {timeframe}."
            )

        entry = CacheEntry(
            candles=validated,
            last_completed_candle=validated["time"].iloc[-1].to_pydatetime(),
            fetched_at=fetched_at,
            source=source,
            candle_count=len(validated),
        )

        try:
            self.csv_store.save_candles(instrument, timeframe, validated)
            self.trade_store.upsert_cache_metadata(
                instrument=instrument,
                timeframe=timeframe,
                last_completed_candle=entry.last_completed_candle,
                fetched_at=entry.fetched_at,
                candle_count=entry.candle_count,
                source=entry.source,
            )
        except Exception as exc:
            log_failure(
                self.logger,
                "cache_persist_failed",
                exc,
                instrument=instrument,
                timeframe=timeframe,
                source=source,
                candle_count=len(validated),
            )
            raise
        with self._lock:
            self._memory_cache[(instrument, timeframe)] = entry
        return entry

    def _freshness_from_entry(
        self,
        instrument: str,
        timeframe: str,
        entry: CacheEntry,
    ) -> CandleFreshness:
        staleness = calculate_candle_staleness_seconds(entry.last_completed_candle, timeframe)
        return CandleFreshness(
            instrument=instrument,
            timeframe=timeframe,
            last_completed_candle=entry.last_completed_candle,
            fetched_at=entry.fetched_at,
            source=entry.source,
            candle_count=entry.candle_count,
            is_fresh=staleness == 0.0,
            staleness_seconds=staleness,
        )

    def _freshness_from_metadata(
        self,
        instrument: str,
        timeframe: str,
        metadata: dict[str, object],
    ) -> CandleFreshness:
        last_completed = metadata.get("last_completed_candle")
        if not isinstance(last_completed, datetime):
            return CandleFreshness(
                instrument=instrument,
                timeframe=timeframe,
                last_completed_candle=None,
                fetched_at=None,
                source=None,
                candle_count=0,
                is_fresh=False,
                staleness_seconds=None,
            )

        staleness = calculate_candle_staleness_seconds(last_completed, timeframe)
        fetched_at = metadata.get("fetched_at")
        return CandleFreshness(
            instrument=instrument,
            timeframe=timeframe,
            last_completed_candle=last_completed,
            fetched_at=fetched_at if isinstance(fetched_at, datetime) else None,
            source=str(metadata.get("source")) if metadata.get("source") is not None else None,
            candle_count=int(metadata.get("candle_count", 0)),
            is_fresh=staleness == 0.0,
            staleness_seconds=staleness,
        )

    def _log_cache_lookup(
        self,
        cache_level: str,
        instrument: str,
        timeframe: str,
        entry: CacheEntry | None,
    ) -> None:
        if entry is None:
            self.logger.info(
                "cache_lookup",
                instrument=instrument,
                timeframe=timeframe,
                cache_level=cache_level,
                hit=False,
                cached_last_candle=None,
                staleness_seconds=None,
            )
            return

        self.logger.info(
            "cache_lookup",
            instrument=instrument,
            timeframe=timeframe,
            cache_level=cache_level,
            hit=True,
            cached_last_candle=entry.last_completed_candle,
            staleness_seconds=calculate_candle_staleness_seconds(
                entry.last_completed_candle,
                timeframe,
            ),
        )

    @staticmethod
    def _tail(candles: pd.DataFrame, count: int) -> pd.DataFrame:
        return candles.tail(count).reset_index(drop=True)


__all__ = ["CacheEntry", "CandleCache", "is_cache_fresh"]
