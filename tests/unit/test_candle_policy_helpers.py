"""Unit tests for candle_policy helper functions beyond trim/validate."""

from __future__ import annotations

import pandas as pd
import pytest
from freezegun import freeze_time

from core.candle_policy import (
    OANDA_CANDLE_GRANULARITIES,
    TIMEFRAME_DELTAS,
    calculate_candle_staleness_seconds,
    floor_time_to_boundary,
    get_current_candle_start,
    get_last_completed_candle_start,
    get_timeframe_delta,
    normalize_oanda_candle_granularity,
)


class TestGetTimeframeDelta:
    @pytest.mark.parametrize(
        ("timeframe", "expected"),
        [
            ("S5", pd.Timedelta(seconds=5)),
            ("S10", pd.Timedelta(seconds=10)),
            ("S15", pd.Timedelta(seconds=15)),
            ("S30", pd.Timedelta(seconds=30)),
            ("M1", pd.Timedelta(minutes=1)),
            ("M2", pd.Timedelta(minutes=2)),
            ("M4", pd.Timedelta(minutes=4)),
            ("M5", pd.Timedelta(minutes=5)),
            ("M10", pd.Timedelta(minutes=10)),
            ("M15", pd.Timedelta(minutes=15)),
            ("M30", pd.Timedelta(minutes=30)),
            ("H1", pd.Timedelta(hours=1)),
            ("H2", pd.Timedelta(hours=2)),
            ("H3", pd.Timedelta(hours=3)),
            ("H4", pd.Timedelta(hours=4)),
            ("H6", pd.Timedelta(hours=6)),
            ("H8", pd.Timedelta(hours=8)),
            ("H12", pd.Timedelta(hours=12)),
            ("D", pd.Timedelta(days=1)),
            ("W", pd.Timedelta(weeks=1)),
        ],
    )
    def test_returns_correct_delta_for_each_supported_timeframe(
        self,
        timeframe: str,
        expected: pd.Timedelta,
    ) -> None:
        assert get_timeframe_delta(timeframe) == expected

    def test_rejects_unsupported_timeframe(self) -> None:
        with pytest.raises(ValueError, match="Unsupported timeframe"):
            get_timeframe_delta("M")

    def test_rejects_lowercase_timeframe(self) -> None:
        with pytest.raises(ValueError, match="Unsupported timeframe"):
            get_timeframe_delta("h1")

    def test_all_timeframes_are_covered(self) -> None:
        assert tuple(TIMEFRAME_DELTAS) == OANDA_CANDLE_GRANULARITIES


class TestNormalizeOandaCandleGranularity:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("5s", "S5"),
            ("s10", "S10"),
            ("1m", "M1"),
            ("2m", "M2"),
            ("4m", "M4"),
            ("10m", "M10"),
            ("2h", "H2"),
            ("12h", "H12"),
            ("daily", "D"),
            ("1w", "W"),
        ],
    )
    def test_accepts_oanda_aliases(self, value: str, expected: str) -> None:
        assert normalize_oanda_candle_granularity(value) == expected

    @pytest.mark.parametrize("value", ["M", "monthly", "S1"])
    def test_rejects_monthly_and_unknown_granularities(self, value: str) -> None:
        with pytest.raises(ValueError, match="Unsupported OANDA candle granularity"):
            normalize_oanda_candle_granularity(value)


class TestFloorTimeToBoundary:
    @freeze_time("2026-03-20T10:37:00Z")
    def test_floors_h1_to_hour_start(self) -> None:
        ts = pd.Timestamp("2026-03-20T10:37:00Z")
        result = floor_time_to_boundary(ts, "H1")
        assert result == pd.Timestamp("2026-03-20T10:00:00Z")

    def test_floors_h4_to_four_hour_boundary(self) -> None:
        ts = pd.Timestamp("2026-03-20T05:30:00Z")
        result = floor_time_to_boundary(ts, "H4")
        assert result == pd.Timestamp("2026-03-20T05:00:00Z")

    def test_floors_m15_to_quarter_hour(self) -> None:
        ts = pd.Timestamp("2026-03-20T10:22:00Z")
        result = floor_time_to_boundary(ts, "M15")
        assert result == pd.Timestamp("2026-03-20T10:15:00Z")

    def test_floors_daily_to_oanda_new_york_alignment(self) -> None:
        ts = pd.Timestamp("2026-03-20T18:00:00Z")
        result = floor_time_to_boundary(ts, "D")
        assert result == pd.Timestamp("2026-03-19T21:00:00Z")

    def test_floors_weekly_to_oanda_friday_alignment(self) -> None:
        ts = pd.Timestamp("2026-03-21T02:00:00Z")
        result = floor_time_to_boundary(ts, "W")
        assert result == pd.Timestamp("2026-03-20T21:00:00Z")

    def test_floors_seconds_to_second_boundary(self) -> None:
        ts = pd.Timestamp("2026-03-20T10:00:17Z")
        result = floor_time_to_boundary(ts, "S5")
        assert result == pd.Timestamp("2026-03-20T10:00:15Z")

    def test_exact_boundary_returns_same_time(self) -> None:
        ts = pd.Timestamp("2026-03-20T10:00:00Z")
        result = floor_time_to_boundary(ts, "H1")
        assert result == ts

    def test_accepts_datetime_input(self) -> None:
        from datetime import datetime, timezone

        dt = datetime(2026, 3, 20, 10, 37, tzinfo=timezone.utc)
        result = floor_time_to_boundary(dt, "H1")
        assert result == pd.Timestamp("2026-03-20T10:00:00Z")

    def test_coerces_naive_datetime_to_utc(self) -> None:
        from datetime import datetime

        dt = datetime(2026, 3, 20, 10, 37)
        result = floor_time_to_boundary(dt, "H1")
        assert result == pd.Timestamp("2026-03-20T10:00:00+00:00")


class TestGetCurrentCandleStart:
    @freeze_time("2026-03-20T10:37:00Z")
    def test_returns_current_h1_boundary(self) -> None:
        result = get_current_candle_start("H1")
        assert result == pd.Timestamp("2026-03-20T10:00:00Z")

    @freeze_time("2026-03-20T10:00:00Z")
    def test_exact_boundary_returns_that_boundary(self) -> None:
        result = get_current_candle_start("H1")
        assert result == pd.Timestamp("2026-03-20T10:00:00Z")

    def test_accepts_explicit_now_utc(self) -> None:
        now = pd.Timestamp("2026-03-20T10:37:00Z")
        result = get_current_candle_start("H1", now_utc=now)
        assert result == pd.Timestamp("2026-03-20T10:00:00Z")

    def test_m5_boundary(self) -> None:
        now = pd.Timestamp("2026-03-20T10:08:00Z")
        result = get_current_candle_start("M5", now_utc=now)
        assert result == pd.Timestamp("2026-03-20T10:05:00Z")


class TestGetLastCompletedCandleStart:
    @freeze_time("2026-03-20T10:37:00Z")
    def test_returns_previous_h1_boundary(self) -> None:
        result = get_last_completed_candle_start("H1")
        assert result == pd.Timestamp("2026-03-20T09:00:00Z")

    @freeze_time("2026-03-20T10:00:00Z")
    def test_at_exact_boundary_returns_previous_period(self) -> None:
        result = get_last_completed_candle_start("H1")
        assert result == pd.Timestamp("2026-03-20T09:00:00Z")

    def test_accepts_explicit_now_utc(self) -> None:
        now = pd.Timestamp("2026-03-20T10:37:00Z")
        result = get_last_completed_candle_start("H1", now_utc=now)
        assert result == pd.Timestamp("2026-03-20T09:00:00Z")

    def test_h4_last_completed(self) -> None:
        now = pd.Timestamp("2026-03-20T05:30:00Z")
        result = get_last_completed_candle_start("H4", now_utc=now)
        assert result == pd.Timestamp("2026-03-20T01:00:00Z")


class TestCalculateCandleStalenessSeconds:
    def test_fresh_cache_returns_zero(self) -> None:
        now = pd.Timestamp("2026-03-20T10:37:00Z")
        last = pd.Timestamp("2026-03-20T09:00:00Z")
        assert calculate_candle_staleness_seconds(last, "H1", now_utc=now) == 0.0

    def test_stale_by_one_period(self) -> None:
        now = pd.Timestamp("2026-03-20T11:15:00Z")
        last = pd.Timestamp("2026-03-20T09:00:00Z")
        assert calculate_candle_staleness_seconds(last, "H1", now_utc=now) == 3600.0

    def test_stale_by_two_periods(self) -> None:
        now = pd.Timestamp("2026-03-20T12:15:00Z")
        last = pd.Timestamp("2026-03-20T09:00:00Z")
        assert calculate_candle_staleness_seconds(last, "H1", now_utc=now) == 7200.0

    def test_future_cache_returns_zero(self) -> None:
        now = pd.Timestamp("2026-03-20T10:37:00Z")
        last = pd.Timestamp("2026-03-20T10:00:00Z")
        # last is ahead of expected (09:00) — still zero, not negative
        assert calculate_candle_staleness_seconds(last, "H1", now_utc=now) == 0.0

    def test_m5_staleness(self) -> None:
        now = pd.Timestamp("2026-03-20T10:12:00Z")
        last = pd.Timestamp("2026-03-20T10:00:00Z")
        # At 10:12, last completed M5 is 10:05. Cache has 10:00 → stale by 300s
        assert calculate_candle_staleness_seconds(last, "M5", now_utc=now) == 300.0
