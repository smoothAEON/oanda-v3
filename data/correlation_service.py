"""Daily close-return correlation helpers for MCP workflows."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd

from bot.parsing import normalize_command_instrument, normalize_command_timeframe
from config.settings import Settings, get_settings
from core.instrument_registry import INSTRUMENT_REGISTRY, INSTRUMENT_ALIASES
from core.models import CorrelationResult
from data.yfinance_service import YFinanceService


@dataclass
class CorrelationService:
    """Compute a bounded two-series return correlation."""

    account_client: Any
    yfinance_service: YFinanceService
    settings: Settings

    def __init__(
        self,
        *,
        account_client: Any,
        yfinance_service: YFinanceService,
        settings: Settings | None = None,
    ) -> None:
        self.account_client = account_client
        self.yfinance_service = yfinance_service
        self.settings = settings or get_settings()

    async def get_correlation(
        self,
        primary: str,
        secondary: str,
        *,
        timeframe: str = "D",
        lookback: int = 60,
        secondary_transform: str = "raw",
    ) -> CorrelationResult:
        resolved_timeframe = normalize_command_timeframe(timeframe)
        if resolved_timeframe != "D":
            raise ValueError("Correlation supports timeframe D only in v1.")
        if lookback < 2:
            raise ValueError("lookback must be at least 2.")
        normalized_transform = str(secondary_transform).strip().lower()
        if normalized_transform not in {"raw", "inverse"}:
            raise ValueError("secondary_transform must be 'raw' or 'inverse'.")

        primary_series, primary_source, primary_label = await self._load_series(primary, lookback=lookback)
        secondary_series, secondary_source, secondary_label = await self._load_series(secondary, lookback=lookback)
        if normalized_transform == "inverse":
            secondary_series = secondary_series * -1.0

        aligned = pd.concat(
            [primary_series.rename("primary"), secondary_series.rename("secondary")],
            axis=1,
            join="inner",
        ).dropna()
        correlation = None if len(aligned) < 2 else aligned["primary"].corr(aligned["secondary"])
        start_utc = None if aligned.empty else pd.Timestamp(aligned.index.min()).to_pydatetime().astimezone(timezone.utc)
        end_utc = None if aligned.empty else pd.Timestamp(aligned.index.max()).to_pydatetime().astimezone(timezone.utc)
        return CorrelationResult(
            primary=primary_label,
            secondary=secondary_label,
            timeframe=resolved_timeframe,
            lookback=lookback,
            secondary_transform=normalized_transform,
            correlation=None if correlation is None or pd.isna(correlation) else float(correlation),
            aligned_observations=len(aligned),
            primary_source=primary_source,
            secondary_source=secondary_source,
            start_utc=start_utc,
            end_utc=end_utc,
        )

    async def _load_series(self, symbol: str, *, lookback: int) -> tuple[pd.Series, str, str]:
        if self._is_oanda_instrument(symbol):
            instrument = normalize_command_instrument(symbol)
            frame = await self.account_client.get_candles(instrument, "D", lookback + 1)
            close_series = pd.Series(frame["close"].astype(float).to_list(), index=pd.to_datetime(frame["time"], utc=True))
            returns = close_series.pct_change().dropna().tail(lookback)
            returns.index = returns.index.normalize()
            return returns, "oanda", instrument

        end_day = datetime.now(timezone.utc).date() + timedelta(days=1)
        start_day = end_day - timedelta(days=max(lookback * 3, 30))
        payload = self.yfinance_service.get_history(
            symbol,
            period=None,
            interval="1d",
            start=start_day.isoformat(),
            end=end_day.isoformat(),
            auto_adjust=True,
            max_rows=lookback + 1,
        )
        history = payload["history"]
        if not history:
            raise RuntimeError(f"No yfinance history available for {symbol}.")
        close_series = pd.Series(
            [float(item["close"]) for item in history],
            index=pd.to_datetime([item["time"] for item in history], utc=True),
        )
        returns = close_series.pct_change().dropna().tail(lookback)
        returns.index = returns.index.normalize()
        return returns, "yfinance", str(payload["symbol"])

    @staticmethod
    def _is_oanda_instrument(symbol: str) -> bool:
        alias = INSTRUMENT_ALIASES.get(symbol.strip().casefold())
        if alias is not None:
            return True
        try:
            normalized = normalize_command_instrument(symbol)
        except Exception:
            return False
        return normalized in INSTRUMENT_REGISTRY


__all__ = ["CorrelationService"]
