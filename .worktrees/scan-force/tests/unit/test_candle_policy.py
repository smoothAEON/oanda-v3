from __future__ import annotations

import pandas as pd
import pandas.testing as pdt
import pytest
from freezegun import freeze_time

from core.candle_policy import CANONICAL_COLUMNS, trim_to_closed


def make_candles(times: list[str]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "time": times,
            "open": [1.0 + index for index in range(len(times))],
            "high": [1.1 + index for index in range(len(times))],
            "low": [0.9 + index for index in range(len(times))],
            "close": [1.05 + index for index in range(len(times))],
            "tick_volume": [100 + index for index in range(len(times))],
        }
    )


@freeze_time("2026-03-20T11:00:00Z")
def test_trim_to_closed_keeps_bar_at_exact_close_boundary() -> None:
    df = make_candles(
        [
            "2026-03-20T08:00:00Z",
            "2026-03-20T09:00:00Z",
            "2026-03-20T10:00:00Z",
        ]
    )

    result = trim_to_closed(df, "H1")

    assert result["time"].tolist() == [
        pd.Timestamp("2026-03-20T08:00:00Z"),
        pd.Timestamp("2026-03-20T09:00:00Z"),
        pd.Timestamp("2026-03-20T10:00:00Z"),
    ]


@freeze_time("2026-03-20T10:15:00Z")
def test_trim_to_closed_excludes_forming_bar() -> None:
    df = make_candles(
        [
            "2026-03-20T08:00:00Z",
            "2026-03-20T09:00:00Z",
            "2026-03-20T10:00:00Z",
        ]
    )

    result = trim_to_closed(df, "H1")

    assert result["time"].tolist() == [
        pd.Timestamp("2026-03-20T08:00:00Z"),
        pd.Timestamp("2026-03-20T09:00:00Z"),
    ]


@freeze_time("2026-03-20T10:15:00Z")
def test_trim_to_closed_is_idempotent() -> None:
    df = make_candles(
        [
            "2026-03-20T08:00:00Z",
            "2026-03-20T09:00:00Z",
            "2026-03-20T10:00:00Z",
        ]
    )

    first = trim_to_closed(df, "H1")
    second = trim_to_closed(first, "H1")

    pdt.assert_frame_equal(first, second)


@freeze_time("2026-03-20T10:15:00Z")
def test_trim_to_closed_handles_empty_input() -> None:
    df = pd.DataFrame(columns=CANONICAL_COLUMNS)

    result = trim_to_closed(df, "H1")

    assert result.empty
    assert list(result.columns) == list(CANONICAL_COLUMNS)


@freeze_time("2026-03-20T15:00:00Z")
def test_trim_to_closed_preserves_already_closed_input() -> None:
    df = make_candles(
        [
            "2026-03-20T08:00:00Z",
            "2026-03-20T09:00:00Z",
            "2026-03-20T10:00:00Z",
        ]
    )

    result = trim_to_closed(df, "H1")

    assert len(result) == 3


@freeze_time("2026-03-20T12:00:00Z")
def test_trim_to_closed_daily_timeframe_uses_provider_native_start_times() -> None:
    df = make_candles(
        [
            "2026-03-19T00:00:00Z",
            "2026-03-20T00:00:00Z",
        ]
    )

    result = trim_to_closed(df, "D")

    assert result["time"].tolist() == [pd.Timestamp("2026-03-19T00:00:00Z")]


def test_trim_to_closed_rejects_unsupported_timeframe() -> None:
    df = make_candles(["2026-03-20T10:00:00Z"])

    with pytest.raises(ValueError) as excinfo:
        trim_to_closed(df, "W")

    assert "Unsupported timeframe" in str(excinfo.value)


@freeze_time("2026-03-20T10:15:00Z")
def test_trim_to_closed_does_not_mutate_input() -> None:
    df = make_candles(
        [
            "2026-03-20T08:00:00Z",
            "2026-03-20T09:00:00Z",
            "2026-03-20T10:00:00Z",
        ]
    )
    original = df.copy(deep=True)

    trim_to_closed(df, "H1")

    pdt.assert_frame_equal(df, original)
