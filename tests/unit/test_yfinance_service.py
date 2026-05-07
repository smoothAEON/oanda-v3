from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import pytest

from data.yfinance_service import YFinanceService


class FakeSearchResult:
    def __init__(self) -> None:
        self.quotes = [
            {
                "symbol": "SPY",
                "shortname": "SPDR S&P 500 ETF Trust",
                "longname": "SPDR S&P 500 ETF Trust",
                "exchange": "PCX",
                "exchDisp": "NYSEArca",
                "quoteType": "ETF",
                "typeDisp": "etf",
                "score": 10694000.0,
                "isYahooFinance": True,
            }
        ]
        self.news = [
            {
                "uuid": "query-story-1",
                "title": "Search headline",
                "publisher": "Yahoo Finance",
                "link": "https://finance.yahoo.com/story-1",
                "providerPublishTime": 1775387400,
                "type": "STORY",
            }
        ]


class FakeTicker:
    def __init__(self) -> None:
        self.info = {
            "shortName": "SPDR S&P 500 ETF Trust",
            "longName": "SPDR S&P 500 ETF Trust",
            "quoteType": "ETF",
            "currency": "USD",
            "market": "us_market",
            "exchange": "PCX",
            "fullExchangeName": "NYSE Arca",
            "exchangeTimezoneName": "America/New_York",
            "marketState": "REGULAR",
            "fundFamily": "State Street",
            "category": "Large Blend",
            "website": "https://www.ssga.com",
            "longBusinessSummary": "A" * 900,
            "regularMarketPrice": 655.83,
            "previousClose": 655.92,
        }
        self.fast_info = {
            "currency": "USD",
            "quoteType": "ETF",
            "exchange": "PCX",
            "timezone": "America/New_York",
            "lastPrice": 655.83,
            "previousClose": 655.92,
            "regularMarketPreviousClose": 655.24,
            "open": 646.42,
            "dayHigh": 658.20,
            "dayLow": 645.11,
            "lastVolume": 68358700,
            "marketCap": None,
            "fiftyDayAverage": 676.45,
            "twoHundredDayAverage": 662.57,
            "tenDayAverageVolume": 110368550,
            "threeMonthAverageVolume": 89192829,
            "yearHigh": 697.84,
            "yearLow": 481.80,
            "yearChange": 0.3002,
        }
        self.options = tuple(f"2026-05-{day:02d}" for day in range(1, 31))
        self.calendar = {
            "Dividend Date": datetime(2026, 2, 12, tzinfo=timezone.utc).date(),
            "Earnings Date": [datetime(2026, 5, 1, tzinfo=timezone.utc).date()],
            "Revenue Average": 109116811940,
        }
        self.news = [
            {
                "id": "ticker-story-1",
                "content": {
                    "id": "ticker-story-1",
                    "contentType": "STORY",
                    "title": "Ticker headline",
                    "summary": "Ticker summary",
                    "pubDate": "2026-04-05T11:10:10Z",
                    "provider": {"displayName": "24/7 Wall St."},
                    "canonicalUrl": {"url": "https://247wallst.com/story-1"},
                },
            }
        ]
        self.history_calls: list[dict[str, object]] = []

    def history(self, **kwargs):
        self.history_calls.append(kwargs)
        index = pd.DatetimeIndex(
            [
                "2026-04-01 00:00:00-04:00",
                "2026-04-02 00:00:00-04:00",
                "2026-04-03 00:00:00-04:00",
            ],
            name="Date",
        )
        return pd.DataFrame(
            {
                "Open": [653.90, 646.42, 640.10],
                "High": [658.52, 658.20, 642.00],
                "Low": [653.00, 645.11, 638.50],
                "Close": [655.24, 655.83, 639.10],
                "Adj Close": [655.24, 655.83, 639.10],
                "Volume": [97841500, 68358700, 99275900],
                "Dividends": [0.0, 0.0, 0.0],
                "Stock Splits": [0.0, 0.0, 0.0],
                "Capital Gains": [0.0, 0.0, 0.0],
            },
            index=index,
        )


class FakeBackend:
    def __init__(self) -> None:
        self.search_calls: list[tuple[str, int, int, bool]] = []
        self.ticker_calls: list[str] = []
        self._ticker = FakeTicker()

    def search(
        self,
        query: str,
        *,
        max_results: int,
        news_count: int,
        enable_fuzzy_query: bool,
    ) -> FakeSearchResult:
        self.search_calls.append((query, max_results, news_count, enable_fuzzy_query))
        return FakeSearchResult()

    def ticker(self, symbol: str) -> FakeTicker:
        self.ticker_calls.append(symbol)
        return self._ticker


def test_yfinance_service_search_and_ticker_summary_are_sanitized() -> None:
    backend = FakeBackend()
    service = YFinanceService(backend=backend)

    search_result = service.search_tickers("spy", limit=2, news_count=1, enable_fuzzy=True)
    ticker_result = service.get_ticker("spy", include_news=True, news_limit=1)

    assert backend.search_calls == [("spy", 2, 1, True)]
    assert backend.ticker_calls == ["SPY"]
    assert search_result["quotes"][0]["short_name"] == "SPDR S&P 500 ETF Trust"
    assert search_result["news"][0]["publisher"] == "Yahoo Finance"
    assert search_result["news"][0]["published_at"] == datetime(2026, 4, 5, 11, 10, tzinfo=timezone.utc)
    assert ticker_result["symbol"] == "SPY"
    assert ticker_result["quote"]["last_price"] == 655.83
    assert round(ticker_result["quote"]["day_change"], 2) == -0.09
    assert "business_summary_excerpt" in ticker_result["profile"]
    assert ticker_result["profile"]["business_summary_excerpt"].endswith("...")
    assert ticker_result["available_option_expiration_count"] == 30
    assert len(ticker_result["options_expirations"]) == 24
    assert ticker_result["options_expirations_truncated"] is True
    assert ticker_result["calendar"]["earnings_date"][0].isoformat() == "2026-05-01"
    assert ticker_result["news"][0]["publisher"] == "24/7 Wall St."
    assert ticker_result["news"][0]["link"] == "https://247wallst.com/story-1"
    assert ticker_result["warnings"] == []


def test_yfinance_service_history_and_news_are_bounded_and_normalized() -> None:
    backend = FakeBackend()
    service = YFinanceService(backend=backend)

    history_result = service.get_history(
        "SPY",
        period="5d",
        interval="1d",
        actions=True,
        auto_adjust=False,
        max_rows=2,
    )
    news_result = service.get_news("SPY", limit=1)

    assert backend.ticker_calls == ["SPY", "SPY"]
    assert backend._ticker.history_calls == [
        {
            "interval": "1d",
            "prepost": False,
            "actions": True,
            "auto_adjust": False,
            "period": "5d",
        }
    ]
    assert history_result["returned_count"] == 2
    assert history_result["available_count"] == 3
    assert history_result["truncated"] is True
    assert history_result["history"][0]["time"] == datetime(2026, 4, 2, 4, 0, tzinfo=timezone.utc)
    assert history_result["history"][0]["adj_close"] == 655.83
    assert history_result["history"][0]["stock_splits"] == 0.0
    assert history_result["history"][0]["capital_gains"] == 0.0
    assert news_result["returned_count"] == 1
    assert news_result["news"][0]["title"] == "Ticker headline"


def test_yfinance_service_validates_bounded_inputs() -> None:
    service = YFinanceService(backend=FakeBackend())

    with pytest.raises(ValueError, match="query must be a non-empty string"):
        service.search_tickers("   ")

    with pytest.raises(ValueError, match="limit must be between 1 and 10"):
        service.get_news("SPY", limit=0)

    with pytest.raises(ValueError, match="max_rows must be between 1 and 1000"):
        service.get_history("SPY", max_rows=1001)
