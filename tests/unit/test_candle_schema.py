from __future__ import annotations

import pandas as pd
import pandas.testing as pdt
import pytest

from core.candle_policy import CANONICAL_COLUMNS, validate_candle_df


def make_candles() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "close": [1.3, 1.1],
            "tick_volume": [30, 20],
            "time": ["2026-03-20 10:00:00", "2026-03-20 09:00:00"],
            "high": [1.4, 1.2],
            "open": [1.2, 1.0],
            "low": [1.1, 0.9],
        }
    )


def test_validate_candle_df_returns_canonical_columns_sorted_ascending() -> None:
    result = validate_candle_df(make_candles())

    assert list(result.columns) == list(CANONICAL_COLUMNS)
    assert result["time"].tolist() == [
        pd.Timestamp("2026-03-20 09:00:00+00:00"),
        pd.Timestamp("2026-03-20 10:00:00+00:00"),
    ]
    assert str(result["time"].dt.tz) == "UTC"
    assert result["open"].dtype == "float64"
    assert result["tick_volume"].dtype == "int64"


def test_validate_candle_df_rejects_missing_columns() -> None:
    df = make_candles().drop(columns=["low"])

    with pytest.raises(ValueError) as excinfo:
        validate_candle_df(df)

    assert "Missing candle columns" in str(excinfo.value)


def test_validate_candle_df_rejects_volume_column() -> None:
    df = make_candles().rename(columns={"tick_volume": "volume"})

    with pytest.raises(ValueError) as excinfo:
        validate_candle_df(df)

    assert "tick_volume" in str(excinfo.value)


def test_validate_candle_df_resets_time_index_to_column() -> None:
    df = make_candles().set_index(pd.DatetimeIndex(pd.to_datetime(make_candles()["time"]), name="time"))
    df = df.drop(columns=["time"])

    result = validate_candle_df(df)

    assert "time" in result.columns
    assert result.index.name is None
    assert isinstance(result.index, pd.RangeIndex)


def test_validate_candle_df_coerces_naive_time_to_utc() -> None:
    result = validate_candle_df(make_candles())

    assert str(result["time"].dt.tz) == "UTC"


def test_validate_candle_df_rejects_extra_columns() -> None:
    df = make_candles().assign(extra=1)

    with pytest.raises(ValueError) as excinfo:
        validate_candle_df(df)

    assert "Unexpected candle columns" in str(excinfo.value)


def test_validate_candle_df_rejects_invalid_numeric_conversions() -> None:
    df = make_candles().astype({"open": "object"})
    df.loc[0, "open"] = "bad"

    with pytest.raises(ValueError) as excinfo:
        validate_candle_df(df)

    assert "open" in str(excinfo.value)


def test_validate_candle_df_does_not_mutate_input() -> None:
    df = make_candles()
    original = df.copy(deep=True)

    validate_candle_df(df)

    pdt.assert_frame_equal(df, original)
