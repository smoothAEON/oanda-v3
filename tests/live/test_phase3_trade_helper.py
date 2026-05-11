"""Phase 3 live cohesiveness tests: local trade-helper runtime.

Tests REST account reads, one-shot open-trade sync, journal persistence, and
MAE/MFE replay against the real OANDA API.  All tests are auto-marked
``@pytest.mark.live`` by conftest.py.

Run with:  pytest tests/live/test_phase3_trade_helper.py -m live -v
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from core.enums import TradeState
from core.events import TradeOpenedEvent
from core.instrument_registry import get_pip_size, validate_live_instrument
from core.models import TradeRecord
from mcp_server.adapters import BotMcpService
from providers.base import PriceSnapshot


# ---------------------------------------------------------------------------
# 1. Account client round-trip
# ---------------------------------------------------------------------------


class TestAccountClientLiveRoundtrip:
    """Validate that the account client returns well-formed trade and pricing data."""

    def test_open_trades_returns_nonempty_list(self, account_client) -> None:
        trades = asyncio.run(account_client.get_open_trades())
        assert isinstance(trades, list)
        assert len(trades) > 0, "Expected at least one open trade in the live account"

    def test_first_trade_has_required_fields(self, account_client) -> None:
        trades = asyncio.run(account_client.get_open_trades())
        trade = trades[0]

        assert isinstance(trade["id"], str)
        assert validate_live_instrument(trade["instrument"]) == trade["instrument"]
        assert isinstance(trade["currentUnits"], float)
        assert trade["currentUnits"] != 0
        assert isinstance(trade["price"], float)
        assert trade["price"] > 0
        assert isinstance(trade["openTime"], datetime)

    def test_get_trade_detail_matches(self, account_client) -> None:
        trades = asyncio.run(account_client.get_open_trades())
        first = trades[0]
        trade_id = first["id"]
        instrument = first["instrument"]

        detail = asyncio.run(account_client.get_trade_detail(trade_id))
        assert detail["id"] == trade_id
        assert detail["instrument"] == instrument

    def test_get_pricing_returns_valid_snapshot(self, account_client) -> None:
        trades = asyncio.run(account_client.get_open_trades())
        instrument = trades[0]["instrument"]

        snap = asyncio.run(account_client.get_pricing(instrument))
        assert isinstance(snap, PriceSnapshot)
        assert snap.bid > 0
        assert snap.ask >= snap.bid

    def test_trade_price_near_current_mid(self, account_client) -> None:
        """The trade's open price should be within a plausible range of the current mid."""
        trades = asyncio.run(account_client.get_open_trades())
        first = trades[0]
        instrument = first["instrument"]
        open_price = first["price"]

        snap = asyncio.run(account_client.get_pricing(instrument))
        current_mid = (snap.bid + snap.ask) / 2.0

        pip_size = get_pip_size(instrument)
        # Allow up to 5000 pips difference (generous for any instrument/duration)
        max_distance = 5000.0 * pip_size
        distance = abs(open_price - current_mid)
        assert distance < max_distance, (
            f"open_price={open_price} vs mid={current_mid}, "
            f"distance={distance / pip_size:.1f} pips exceeds 5000-pip limit"
        )


# ---------------------------------------------------------------------------
# 2. Poller detects open trade
# ---------------------------------------------------------------------------


class TestPollerDetectsOpenTrade:
    """TradePollerTask must detect at least one open trade and emit a TradeOpenedEvent."""

    def test_first_poll_emits_open_event(
        self, account_client, trade_repository, journal_service, live_settings
    ) -> None:
        from background.poller_task import TradePollerTask

        poller = TradePollerTask(
            account_client,
            trade_repository,
            journal_service,
            settings=live_settings,
        )

        events = asyncio.run(poller.poll_once())
        open_events = [e for e in events if isinstance(e, TradeOpenedEvent)]
        assert len(open_events) >= 1, "Expected at least one TradeOpenedEvent on first poll"

        event = open_events[0]
        assert isinstance(event.trade_id, str) and event.trade_id.strip()
        assert validate_live_instrument(event.instrument) == event.instrument
        assert event.units != 0
        assert event.open_price > 0

        open_trades = trade_repository.list_open()
        ids = {t.trade_id for t in open_trades}
        assert event.trade_id in ids

        matching = [t for t in open_trades if t.trade_id == event.trade_id]
        assert matching[0].state == TradeState.OPEN

    def test_second_poll_emits_no_new_opens(
        self, account_client, trade_repository, journal_service, live_settings
    ) -> None:
        from background.poller_task import TradePollerTask

        poller = TradePollerTask(
            account_client,
            trade_repository,
            journal_service,
            settings=live_settings,
        )

        # First poll — seeds the journal
        asyncio.run(poller.poll_once())

        # Second poll — no new trades expected
        events = asyncio.run(poller.poll_once())
        open_events = [e for e in events if isinstance(e, TradeOpenedEvent)]
        assert len(open_events) == 0, (
            "Second poll should emit zero TradeOpenedEvent (trade already known)"
        )


# ---------------------------------------------------------------------------
# 3. Poller to journal round-trip
# ---------------------------------------------------------------------------


class TestPollerToJournalRoundtrip:
    """After the poller creates a trade, it must be readable with all fields populated."""

    def test_journal_trade_record_fully_populated(
        self, account_client, trade_repository, journal_service, live_settings
    ) -> None:
        from background.poller_task import TradePollerTask

        poller = TradePollerTask(
            account_client,
            trade_repository,
            journal_service,
            settings=live_settings,
        )

        events = asyncio.run(poller.poll_once())
        open_events = [e for e in events if isinstance(e, TradeOpenedEvent)]
        assert len(open_events) >= 1

        trade_id = open_events[0].trade_id
        record = trade_repository.get(trade_id)
        assert record is not None, f"Trade {trade_id} not found in repository"

        assert record.trade_id == trade_id
        assert validate_live_instrument(record.instrument) == record.instrument
        assert record.units != 0
        assert record.open_price > 0
        assert record.state == TradeState.OPEN
        assert isinstance(record.opened_at, datetime)
        assert record.opened_at.tzinfo is not None

        # Pydantic re-validation must succeed
        roundtrip = TradeRecord.model_validate(record.model_dump())
        assert roundtrip.trade_id == record.trade_id
        assert roundtrip.state == record.state


# ---------------------------------------------------------------------------
# 4. MAE/MFE replay from bid/ask candles
# ---------------------------------------------------------------------------


class TestMaeMfeReplayOnDemand:
    """MCP MAE/MFE reads should sync open trades and replay M1 bid/ask candles."""

    @pytest.mark.asyncio
    async def test_mcp_maemfe_matches_direct_m1_bid_ask_replay(
        self,
        account_client,
        trade_repository,
        excursion_repository,
        journal_service,
        live_settings,
    ) -> None:
        from background.poller_task import TradePollerTask

        poller = TradePollerTask(
            account_client,
            trade_repository,
            journal_service,
            settings=live_settings,
        )
        await poller.poll_once()
        candidate_trades = [
            trade
            for trade in trade_repository.list_open()
            if (datetime.now(timezone.utc) - trade.opened_at).total_seconds() >= 120
        ]
        if not candidate_trades:
            pytest.skip("No open trades older than 2 minutes for M1 replay verification.")

        runtime = SimpleNamespace(
            settings=live_settings,
            trade_repository=trade_repository,
            excursion_repository=excursion_repository,
            account_client=account_client,
        )
        service = BotMcpService(runtime=runtime, settings=live_settings)
        result = await service.get_mae_mfe()
        summaries = {
            item["trade"]["trade_id"]: item["summary"]
            for item in result["open_trades"]
        }

        for trade in candidate_trades:
            summary = summaries.get(trade.trade_id)
            assert summary is not None, f"Expected replay summary for open trade {trade.trade_id}"

            frame = await account_client.get_bid_ask_candles_range(
                trade.instrument,
                "M1",
                trade.opened_at,
                datetime.now(timezone.utc),
            )
            opened_minute = trade.opened_at.replace(second=0, microsecond=0)
            frame = frame.loc[frame["time"] >= opened_minute].reset_index(drop=True)
            assert not frame.empty, f"Expected completed M1 candles for {trade.trade_id}"

            pip_size = get_pip_size(trade.instrument)
            if trade.units > 0:
                expected_mae = (trade.open_price - float(frame["bid_low"].min())) / pip_size
                expected_mfe = (float(frame["bid_high"].max()) - trade.open_price) / pip_size
            else:
                expected_mae = (float(frame["ask_high"].max()) - trade.open_price) / pip_size
                expected_mfe = (trade.open_price - float(frame["ask_low"].min())) / pip_size

            assert summary["summary_source"] == "m1_bid_ask_replay"
            assert float(summary["mae_pips"]) == pytest.approx(expected_mae, abs=1e-6)
            assert float(summary["mfe_pips"]) == pytest.approx(expected_mfe, abs=1e-6)
