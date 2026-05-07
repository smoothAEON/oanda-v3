"""CSV-backed candle persistence for the Stage 04 cache layer."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from threading import RLock

import pandas as pd

from config.settings import Settings, get_settings
from core.candle_policy import validate_candle_df
from core.instrument_registry import normalize_instrument, validate_live_instrument
from core.logging_setup import get_logger, log_failure


class CandleCsvStore:
    """Persist canonical candle frames under the derived cache directory."""

    def __init__(
        self,
        *,
        root_dir: Path | None = None,
        settings: Settings | None = None,
    ) -> None:
        resolved_settings = settings or get_settings()
        derived_root = resolved_settings.tinydb_path.parent / "cache"
        self.root_dir = (root_dir or derived_root).resolve()
        self.logger = get_logger(__name__)
        self._path_lock_guard = RLock()
        self._path_locks: dict[Path, RLock] = {}

    def path_for(self, instrument: str, timeframe: str) -> Path:
        """Return the canonical on-disk CSV location for a cache key."""

        resolved_instrument = validate_live_instrument(normalize_instrument(instrument))
        return self.root_dir / resolved_instrument / f"{timeframe}.csv"

    def load_candles(self, instrument: str, timeframe: str) -> pd.DataFrame | None:
        """Load and revalidate a cached candle frame if it exists."""

        path = self.path_for(instrument, timeframe)
        if not path.exists():
            return None

        try:
            loaded = pd.read_csv(path)
            return validate_candle_df(loaded)
        except Exception as exc:
            log_failure(
                self.logger,
                "csv_candle_load_failed",
                exc,
                instrument=instrument,
                timeframe=timeframe,
                path=str(path),
            )
            raise

    def save_candles(
        self,
        instrument: str,
        timeframe: str,
        candles: pd.DataFrame,
    ) -> Path:
        """Persist canonical candle data and return the written path."""

        path = self.path_for(instrument, timeframe)
        path_lock = self._path_lock(path)
        try:
            validated = validate_candle_df(candles)
            with path_lock:
                path.parent.mkdir(parents=True, exist_ok=True)
                temp_fd, temp_name = tempfile.mkstemp(
                    prefix=f"{path.name}.",
                    suffix=".tmp",
                    dir=str(path.parent),
                )
                temp_path = Path(temp_name)
                try:
                    os.close(temp_fd)
                    validated.to_csv(temp_path, index=False)
                    os.replace(temp_path, path)
                finally:
                    if temp_path.exists():
                        temp_path.unlink()
            return path
        except Exception as exc:
            log_failure(
                self.logger,
                "csv_candle_save_failed",
                exc,
                instrument=instrument,
                timeframe=timeframe,
                path=str(path),
            )
            raise

    def _path_lock(self, path: Path) -> RLock:
        with self._path_lock_guard:
            lock = self._path_locks.get(path)
            if lock is None:
                lock = RLock()
                self._path_locks[path] = lock
            return lock


__all__ = ["CandleCsvStore"]
