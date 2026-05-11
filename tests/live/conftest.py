"""Shared fixtures for live OANDA integration tests.

These tests require a valid .env with real OANDA credentials.
Run with:  pytest tests/live/ -m live -v
"""

from __future__ import annotations

from pathlib import Path

import pytest

from config.settings import load_settings, get_settings


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Auto-mark all tests in tests/live/ with the 'live' marker."""
    for item in items:
        if "tests/live" in str(item.fspath) or "tests\\live" in str(item.fspath):
            item.add_marker(pytest.mark.live)


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "live: tests that hit the real OANDA API")


@pytest.fixture(scope="session")
def live_settings():
    """Load settings from real .env — fails if credentials are missing."""
    get_settings.cache_clear()
    settings = load_settings()
    return settings


@pytest.fixture(scope="session")
def account_client(live_settings):
    """Session-scoped OandaAccountClient with real credentials."""
    from providers.account_client import OandaAccountClient

    return OandaAccountClient(settings=live_settings)


@pytest.fixture(scope="session")
def tmp_db_path(tmp_path):
    """Per-test TinyDB path for isolated persistence."""
    return tmp_path / "test_live.json"


@pytest.fixture()
def trade_store(tmp_db_path):
    """Per-test TradeStore backed by an isolated TinyDB file."""
    from data.persistence.trade_store import TradeStore

    return TradeStore(db_path=tmp_db_path)


@pytest.fixture()
def trade_repository(trade_store):
    """Per-test TradeRepository."""
    from journal.trade_repository import TradeRepository

    return TradeRepository(store=trade_store)


@pytest.fixture()
def excursion_repository(trade_store):
    """Per-test ExcursionRepository (shares TinyDB with trade_repository)."""
    from journal.excursion_repository import ExcursionRepository

    return ExcursionRepository(store=trade_store)


@pytest.fixture()
def market_state():
    """Per-test MarketStateStore."""
    from core.market_state import MarketStateStore

    return MarketStateStore()


@pytest.fixture()
def journal_service(trade_repository, live_settings):
    """Per-test JournalService."""
    from journal.journal_service import JournalService

    return JournalService(trade_repository, settings=live_settings)


@pytest.fixture()
def live_provider(live_settings, tmp_path):
    """Per-test OandaMarketDataProvider with a fresh temp cache."""
    from core.logging_setup import configure_logging
    from data.csv_persistence import CandleCsvStore
    from data.persistence.trade_store import TradeStore
    from providers.cache import CandleCache
    from providers.oanda import OandaMarketDataProvider

    configure_logging(live_settings)
    cache_dir = tmp_path / "live_cache"
    cache = CandleCache(
        csv_store=CandleCsvStore(root_dir=cache_dir / "cache"),
        trade_store=TradeStore(db_path=cache_dir / "live.json"),
    )
    return OandaMarketDataProvider(settings=live_settings, cache=cache)


@pytest.fixture()
def always_open_market_hours():
    """MarketHoursService that always reports market open (for weekend testing)."""
    from core.models import MarketHoursStatus
    from data.market_hours import MarketHoursService
    from datetime import datetime, timezone

    class AlwaysOpenMarketHours(MarketHoursService):
        def get_status(self, *, now_utc=None):
            return MarketHoursStatus(
                checked_at=datetime.now(timezone.utc),
                is_market_open=True,
                source="test_override",
                reason="forced_open_for_testing",
                next_open_at=None,
                next_close_at=None,
            )

        def is_market_open(self, *, now_utc=None):
            return True

    return AlwaysOpenMarketHours()


@pytest.fixture()
def scan_orchestrator(live_settings, live_provider, market_state, always_open_market_hours):
    """Per-test ScanOrchestrator wired to real provider with market-hours bypass."""
    from orchestration.scan_orchestrator import ScanOrchestrator

    return ScanOrchestrator(
        settings=live_settings,
        market_data_provider=live_provider,
        market_state=market_state,
        market_hours_service=always_open_market_hours,
    )
