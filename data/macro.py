"""Bounded macro context fetches for the Stage 18 runtime."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Callable, Iterable

import pandas as pd

from config.settings import Settings, get_settings
from core.logging_setup import get_logger
from core.models import MacroContextStatus, MacroIndicatorStatus


MACRO_SYMBOLS = {
    "VIX": "^VIX",
    "DXY": "DX-Y.NYB",
    "CL": "CL=F",
    "SPX": "^GSPC",
    "US10Y": "^TNX",
}
_DOWNLOAD_PERIOD = "5d"
_DOWNLOAD_INTERVAL = "1d"


class MacroContextService:
    """Fetch and cache a bounded macro surface without persisting state."""

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        refresh_interval_hours: int | None = None,
        now_fn=None,
        download_fn: Callable[[tuple[str, ...]], pd.DataFrame] | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.refresh_interval = timedelta(
            hours=refresh_interval_hours or self.settings.macro_refresh_hours
        )
        self._now_fn = now_fn or (lambda: datetime.now(timezone.utc))
        self._download_fn = download_fn or self._download
        self.logger = get_logger(__name__)
        self._last_status: MacroContextStatus | None = None
        self._last_good_status: MacroContextStatus | None = None

    @property
    def last_status(self) -> MacroContextStatus | None:
        """Return the most recent macro status, including degraded cached reads."""

        return self._last_status

    def get_status(self, *, force: bool = False) -> MacroContextStatus:
        """Return the cached status or refresh it when the cache is stale."""

        now = self._ensure_utc(self._now_fn())
        if not force and self._last_good_status is None:
            return self._last_status or MacroContextStatus()
        if (
            not force
            and self._last_good_status is not None
            and self._last_good_status.last_refreshed_at is not None
            and now - self._last_good_status.last_refreshed_at < self.refresh_interval
        ):
            status = self._last_good_status.model_copy(
                update={
                    "last_attempted_at": now,
                    "used_cached": True,
                    "last_error": None,
                }
            )
            self._last_status = status
            return status
        return self.refresh(force=True)

    def refresh(self, *, force: bool = True) -> MacroContextStatus:
        """Refresh the bounded macro set or return a degraded cached status."""

        if not force:
            return self.get_status(force=False)

        now = self._ensure_utc(self._now_fn())
        try:
            frame = self._download_fn(tuple(MACRO_SYMBOLS.values()))
            status = MacroContextStatus(
                last_attempted_at=now,
                last_refreshed_at=now,
                used_cached=False,
                last_error=None,
                vix=self._extract_indicator(frame, "VIX", MACRO_SYMBOLS["VIX"]),
                dxy=self._extract_indicator(frame, "DXY", MACRO_SYMBOLS["DXY"]),
                cl=self._extract_indicator(frame, "CL", MACRO_SYMBOLS["CL"]),
                spx=self._extract_indicator(frame, "SPX", MACRO_SYMBOLS["SPX"]),
                us10y=self._extract_indicator(frame, "US10Y", MACRO_SYMBOLS["US10Y"]),
            )
        except Exception as exc:
            status = self._status_from_failure(now, exc)
            self.logger.warning(
                "macro_refresh_failed",
                last_error=status.last_error,
                used_cached=status.used_cached,
            )
            self._last_status = status
            return status

        self._last_good_status = status
        self._last_status = status
        self.logger.info(
            "macro_refreshed",
            used_cached=False,
            vix=status.vix.value,
            dxy=status.dxy.value,
            cl=status.cl.value,
            spx=status.spx.value,
            us10y=status.us10y.value,
        )
        return status

    def _status_from_failure(self, now: datetime, exc: Exception) -> MacroContextStatus:
        error_text = str(exc)
        if self._last_good_status is not None:
            return self._last_good_status.model_copy(
                update={
                    "last_attempted_at": now,
                    "used_cached": True,
                    "last_error": error_text,
                }
            )
        return MacroContextStatus(
            last_attempted_at=now,
            last_refreshed_at=None,
            used_cached=False,
            last_error=error_text,
        )

    def _extract_indicator(
        self,
        frame: pd.DataFrame,
        name: str,
        symbol: str,
    ) -> MacroIndicatorStatus:
        if frame.empty:
            raise RuntimeError("Macro download returned no rows.")

        for field in ("Adj Close", "Close"):
            series = self._extract_series(frame, field, symbol)
            if series is None:
                continue
            cleaned = series.dropna()
            if cleaned.empty:
                continue
            last_index = cleaned.index[-1]
            last_value = float(cleaned.iloc[-1])
            return MacroIndicatorStatus(
                name=name,
                symbol=symbol,
                value=last_value,
                as_of=self._ensure_utc(last_index),
            )

        raise RuntimeError(f"Macro download did not include close data for {symbol}.")

    @staticmethod
    def _extract_series(
        frame: pd.DataFrame,
        field: str,
        symbol: str,
    ) -> pd.Series | None:
        if isinstance(frame.columns, pd.MultiIndex):
            if (field, symbol) in frame.columns:
                return frame[(field, symbol)]
            if (symbol, field) in frame.columns:
                return frame[(symbol, field)]
            try:
                by_field = frame[field]
            except KeyError:
                by_field = None
            if isinstance(by_field, pd.DataFrame) and symbol in by_field.columns:
                return by_field[symbol]
            try:
                by_symbol = frame[symbol]
            except KeyError:
                by_symbol = None
            if isinstance(by_symbol, pd.DataFrame) and field in by_symbol.columns:
                return by_symbol[field]
            return None

        if field in frame.columns:
            return frame[field]
        if symbol in frame.columns:
            return frame[symbol]
        return None

    @staticmethod
    def _download(symbols: tuple[str, ...]) -> pd.DataFrame:
        try:
            import yfinance as yf
        except ImportError as exc:  # pragma: no cover - import smoke covers availability
            raise RuntimeError("yfinance is required for macro-context checks.") from exc

        return yf.download(
            list(symbols),
            period=_DOWNLOAD_PERIOD,
            interval=_DOWNLOAD_INTERVAL,
            progress=False,
            auto_adjust=False,
            threads=False,
        )

    @staticmethod
    def _ensure_utc(value: datetime | pd.Timestamp) -> datetime:
        timestamp = pd.Timestamp(value)
        if timestamp.tzinfo is None:
            return timestamp.tz_localize("UTC").to_pydatetime()
        return timestamp.tz_convert("UTC").to_pydatetime()


__all__ = ["MACRO_SYMBOLS", "MacroContextService"]
