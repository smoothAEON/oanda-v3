from __future__ import annotations

from datetime import date
import re

import pytest

from bot.parsing import (
    TRADE_HISTORY_BACKFILL_USAGE,
    TRADE_HISTORY_USAGE,
    parse_tradehistory_args,
    parse_tradehistory_backfill_args,
)


def test_parse_tradehistory_day_defaults() -> None:
    assert parse_tradehistory_args(["day"]) == ("day", "all", None, 1)


def test_parse_tradehistory_month_closed_instrument_page() -> None:
    assert parse_tradehistory_args(["month", "closed", "SPX500_USD", "2"]) == (
        "month",
        "closed",
        "SPX500_USD",
        2,
    )


def test_parse_tradehistory_custom_period() -> None:
    assert parse_tradehistory_args(["custom:2026-03-01:2026-03-31", "opened"]) == (
        "custom:2026-03-01:2026-03-31",
        "opened",
        None,
        1,
    )


@pytest.mark.parametrize(
    "args",
    [
        ["yesterday"],
        ["custom:2026-03-31"],
        ["foo", "bar", "baz"],
        ["day", "opened", "EUR_USD", "0"],
        ["day", "closed", "EUR_USD", "2", "3"],
    ],
)
def test_parse_tradehistory_invalid_inputs_raise_usage(args: list[str]) -> None:
    with pytest.raises(ValueError, match=re.escape(TRADE_HISTORY_USAGE)):
        parse_tradehistory_args(args)


def test_parse_tradehistory_backfill_args() -> None:
    assert parse_tradehistory_backfill_args(["2025-01-01", "2026-04-01"]) == (
        date(2025, 1, 1),
        date(2026, 4, 1),
    )


def test_parse_tradehistory_backfill_args_rejects_invalid_dates() -> None:
    with pytest.raises(ValueError, match=re.escape(TRADE_HISTORY_BACKFILL_USAGE)):
        parse_tradehistory_backfill_args(["2025-01-01"])
