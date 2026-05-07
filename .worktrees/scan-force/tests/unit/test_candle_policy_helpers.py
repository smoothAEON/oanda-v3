"""Unit tests for candle_policy helper functions beyond trim/validate."""

from __future__ import annotations

import pandas as pd
import pytest
from freezegun import freeze_time

from core.candle_policy import (
    TIMEFRAME_DELTAS,
    calculate_candle_staleness_seconds,
    floor_time_to_boundary,
    get_current_candle_start,
    get_last_completed_candle_start,
    get_timeframe_delta,
)


class TestGetTimeframeDelta:
    def test_returns_correct_delta_for_each_supported_timeframe(self) -> None:
        assert get_timeframe_delta("M1") == pd.Timedelta(minutes=1)
        assert get_timeframe_delta("M5") == pd.Timedelta(minutes=5)
        assert get_timeframe_delta("M15") == pd.Timedelta(minutes=15)
        assert get_timeframe_delta("M30") == pd.Timedelta(minutes=30)
        assert get_timeframe_delta("H1") == pd.Timedelta(hours=1)
        assert get_timeframe_delta("H4") == pd.Timedelta(hours=4)
        assert get_timeframe_delta("D") == pd.Timedelta(days=1)

    def test_rejects_unsupported_timeframe(self) -> None:
        with pytest.raises(ValueError, match="Unsupported timeframe"):
            get_timeframe_delta("W")

    def test_rejects_lowercase_timeframe(self) -> None:
        with pytest.raises(ValueError, match="Unsupported timeframe"):
            get_timeframe_delta("h1")

    def test_all_timeframes_are_covered(self) -> None:
        assert len(TIMEFRAME_DELTAS) == 7


class TestFloorTimeToBoundary:
    @freeze_time("2026-03-20T10:37:00Z")
    def test_floors_h1_to_hour_start(self) -> None:
        ts = pd.Timestamp("2026-03-20T10:37:00Z")
        result = floor_time_to_boundary(ts, "H1")
        assert result == pd.Timestamp("2026-03-20T10:00:00Z")

    def test_floors_h4_to_four_hour_boundary(self) -> None:
        ts = pd.Timestamp("2026-03-20T05:30:00Z")
        result = floor_time_to_boundary(ts, "H4")
        assert result == pd.Timestamp("2026-03-20T04:00:00Z")

    def test_floors_m15_to_quarter_hour(self) -> None:
        ts = pd.Timestamp("2026-03-20T10:22:00Z")
        result = floor_time_to_boundary(ts, "M15")
        assert result == pd.Timestamp("2026-03-20T10:15:00Z")

    def test_floors_daily_to_midnight(self) -> None:
        ts = pd.Timestamp("2026-03-20T18:00:00Z")
        result = floor_time_to_boundary(ts, "D")
        assert result == pd.Timestamp("2026-03-20T00:00:00Z")

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
        assert result == pd.Timestamp("2026-03-20T00:00:00Z")


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
