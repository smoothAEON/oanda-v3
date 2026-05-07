from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pandas as pd
import pytest

from data.correlation_service import CorrelationService


class FakeAccountClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, int]] = []

    async def get_candles(self, instrument: str, granularity: str, count: int) -> pd.DataFrame:
        self.calls.append((instrument, granularity, count))
        base = pd.Timestamp("2026-04-01T00:00:00Z")
        return pd.DataFrame(
            {
                "time": pd.to_datetime([base + timedelta(days=index) for index in range(4)], utc=True),
                "close": [100.0, 110.0, 121.0, 133.1],
            }
        )


class FakeYFinanceService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None, str, str | None, str | None, bool, bool, bool, int]] = []

    def get_history(
        self,
        symbol: str,
        *,
        period: str | None,
        interval: str,
        start: str | None,
        end: str | None,
        prepost: bool = False,
        actions: bool = False,
        auto_adjust: bool = True,
        max_rows: int = 250,
    ) -> dict[str, object]:
        self.calls.append((symbol, period, interval, start, end, prepost, actions, auto_adjust, max_rows))
        base = pd.Timestamp("2026-04-01T00:00:00Z")
        return {
            "symbol": symbol,
            "history": [
                {"time": base + timedelta(days=0), "close": 50.0},
                {"time": base + timedelta(days=1), "close": 55.0},
                {"time": base + timedelta(days=2), "close": 60.5},
                {"time": base + timedelta(days=3), "close": 66.55},
            ],
        }


@pytest.mark.asyncio
async def test_correlation_service_aligns_mixed_sources() -> None:
    account_client = FakeAccountClient()
    yfinance_service = FakeYFinanceService()
    service = CorrelationService(
        account_client=account_client,
        yfinance_service=yfinance_service,
        settings=SimpleNamespace(),
    )

    result = await service.get_correlation("XAU_USD", "SPY", lookback=3)

    assert result.primary == "XAU_USD"
    assert result.secondary == "SPY"
    assert result.primary_source == "oanda"
    assert result.secondary_source == "yfinance"
    assert result.aligned_observations == 3
    assert result.correlation == pytest.approx(1.0)
    assert account_client.calls == [("XAU_USD", "D", 4)]
    assert yfinance_service.calls[0][0] == "SPY"
    assert yfinance_service.calls[0][2] == "1d"


@pytest.mark.asyncio
async def test_correlation_service_supports_inverse_secondary_transform() -> None:
    service = CorrelationService(
        account_client=FakeAccountClient(),
        yfinance_service=FakeYFinanceService(),
        settings=SimpleNamespace(),
    )

    result = await service.get_correlation("XAU_USD", "SPY", lookback=3, secondary_transform="inverse")

    assert result.secondary_transform == "inverse"
    assert result.correlation == pytest.approx(-1.0)
