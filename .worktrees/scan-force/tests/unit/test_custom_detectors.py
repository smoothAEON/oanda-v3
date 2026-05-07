from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from freezegun import freeze_time
import pandas as pd
import pandas.testing as pdt
import pytest

from core.models import ORBResult, SFPResult, TurtleSoupResult
from smc.orb import detect_orb
from smc.sfp import detect_sfp
from smc.turtle_soup import detect_turtle_soup


def build_candles(
    rows: list[tuple[datetime, float, float, float, float]],
) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "time": [row[0] for row in rows],
            "open": [row[1] for row in rows],
            "high": [row[2] for row in rows],
            "low": [row[3] for row in rows],
            "close": [row[4] for row in rows],
            "tick_volume": [100 + index for index in range(len(rows))],
        }
    )


def build_h1_sfp_fixture() -> pd.DataFrame:
    start = datetime(2026, 3, 20, 0, 0, tzinfo=timezone.utc)
    rows = [
        (start + timedelta(hours=0), 100.0, 103.0, 99.0, 101.0),
        (start + timedelta(hours=1), 101.0, 105.0, 100.0, 104.0),
        (start + timedelta(hours=2), 104.0, 110.0, 103.0, 108.0),
        (start + timedelta(hours=3), 108.0, 109.0, 101.0, 103.0),
        (start + timedelta(hours=4), 103.0, 107.0, 95.0, 104.0),
        (start + timedelta(hours=5), 104.0, 111.0, 100.0, 109.0),
    ]
    return build_candles(rows)


def build_m15_orb_fixture(*, breakout: bool) -> pd.DataFrame:
    start = datetime(2026, 3, 20, 8, 0, tzinfo=timezone.utc)
    if breakout:
        closes = (100.0, 101.0, 102.5, 102.7)
    else:
        closes = (100.0, 101.0, 101.5, 101.8)

    rows = [
        (start + timedelta(minutes=0), 99.8, 100.5, 99.5, closes[0]),
        (start + timedelta(minutes=15), 100.0, 102.0, 100.0, closes[1]),
        (start + timedelta(minutes=30), 101.0, 101.9, 100.5, closes[2]),
        (start + timedelta(minutes=45), 101.5, 101.8, 100.8, closes[3]),
    ]
    return build_candles(rows)


def build_m15_orb_first_break_fixture() -> pd.DataFrame:
    start = datetime(2026, 3, 20, 8, 0, tzinfo=timezone.utc)
    rows = [
        (start + timedelta(minutes=0), 99.8, 100.5, 99.5, 100.0),
        (start + timedelta(minutes=15), 100.0, 102.0, 100.0, 101.0),
        (start + timedelta(minutes=30), 101.0, 102.6, 100.5, 102.5),
        (start + timedelta(minutes=45), 100.5, 101.0, 99.0, 99.5),
    ]
    return build_candles(rows)


def build_m15_overlapping_orb_fixture() -> pd.DataFrame:
    start = datetime(2026, 3, 20, 8, 0, tzinfo=timezone.utc)
    rows = [
        (start + timedelta(minutes=0), 99.7, 100.2, 99.4, 100.0),
        (start + timedelta(minutes=15), 99.9, 100.8, 99.7, 100.3),
        (start + timedelta(minutes=30), 100.1, 102.0, 100.0, 101.0),
        (start + timedelta(minutes=45), 101.1, 102.4, 100.8, 102.3),
        (start + timedelta(minutes=60), 102.0, 102.2, 100.5, 100.7),
        (start + timedelta(minutes=75), 100.8, 101.1, 100.2, 100.4),
    ]
    return build_candles(rows)


def build_fake_swing_module() -> SimpleNamespace:
    def swing_highs_lows(frame: pd.DataFrame, swing_length: int) -> pd.DataFrame:
        size = len(frame)
        result = pd.DataFrame(
            {
                "HighLow": [0] * size,
                "Level": [None] * size,
            }
        )
        result.loc[2, "HighLow"] = 1
        result.loc[2, "Level"] = 110.0
        result.loc[4, "HighLow"] = -1
        result.loc[4, "Level"] = 95.0
        return result

    return SimpleNamespace(swing_highs_lows=swing_highs_lows)


def build_fake_session_module() -> SimpleNamespace:
    def sessions(frame: pd.DataFrame, session_name: str) -> pd.DataFrame:
        active = [0, 1, 1, 1] if session_name == "Sydney" else [0, 0, 0, 0]
        return pd.DataFrame({"Active": active}, index=frame.index)

    return SimpleNamespace(sessions=sessions)


def build_overlapping_session_module() -> SimpleNamespace:
    def sessions(frame: pd.DataFrame, session_name: str) -> pd.DataFrame:
        active_by_session = {
            "Sydney": [1, 1, 1, 1, 0, 0],
            "Tokyo": [0, 0, 1, 1, 1, 1],
            "London": [0, 0, 0, 0, 0, 0],
            "New York": [0, 0, 0, 0, 0, 0],
        }
        active = active_by_session.get(session_name, [0] * len(frame))
        return pd.DataFrame({"Active": active}, index=frame.index)

    return SimpleNamespace(sessions=sessions)


@freeze_time("2026-03-20T07:00:00Z")
def test_sfp_detector_is_deterministic_and_does_not_mutate_input(monkeypatch) -> None:
    candles = build_h1_sfp_fixture()
    original = candles.copy(deep=True)

    monkeypatch.setattr("smc.sfp._load_smc_module", build_fake_swing_module)

    first = detect_sfp(candles, "H1", swing_length=2)
    second = detect_sfp(candles, "H1", swing_length=2)

    pdt.assert_frame_equal(candles, original)
    assert first.model_dump() == second.model_dump()
    assert first == SFPResult(
        detected=True,
        direction="BEARISH",
        reference_level=110.0,
        reference_time=datetime(2026, 3, 20, 2, 0, tzinfo=timezone.utc),
        sweep_price=111.0,
        close_price=109.0,
        occurred_at=datetime(2026, 3, 20, 5, 0, tzinfo=timezone.utc),
    )


@freeze_time("2026-03-20T07:00:00Z")
def test_sfp_detector_returns_non_detected_for_ambiguous_dual_side_bar(monkeypatch) -> None:
    candles = build_h1_sfp_fixture()
    candles.loc[len(candles) - 1, "low"] = 94.0
    candles.loc[len(candles) - 1, "close"] = 100.0

    monkeypatch.setattr("smc.sfp._load_smc_module", build_fake_swing_module)

    result = detect_sfp(candles, "H1", swing_length=2)

    assert result == SFPResult()


@freeze_time("2026-03-20T07:00:00Z")
def test_sfp_detector_requires_a_true_sweep_not_a_touch(monkeypatch) -> None:
    candles = build_h1_sfp_fixture()
    candles.loc[len(candles) - 1, "high"] = 110.0

    monkeypatch.setattr("smc.sfp._load_smc_module", build_fake_swing_module)

    result = detect_sfp(candles, "H1", swing_length=2)

    assert result == SFPResult()


@freeze_time("2026-03-20T07:00:00Z")
def test_sfp_detector_requires_the_close_to_finish_back_through_the_swept_level(monkeypatch) -> None:
    candles = build_h1_sfp_fixture()
    candles.loc[len(candles) - 1, "close"] = 110.0

    monkeypatch.setattr("smc.sfp._load_smc_module", build_fake_swing_module)

    result = detect_sfp(candles, "H1", swing_length=2)

    assert result == SFPResult()


@freeze_time("2026-03-21T00:00:00Z")
def test_turtle_soup_detector_is_deterministic_and_does_not_mutate_input() -> None:
    start = datetime(2026, 3, 20, 0, 0, tzinfo=timezone.utc)
    rows = []
    for index in range(20):
        time = start + timedelta(hours=index)
        rows.append((time, 100.0, 110.0, 95.0, 102.0))
    rows.append((start + timedelta(hours=20), 102.0, 108.0, 94.0, 96.0))
    candles = build_candles(rows)
    original = candles.copy(deep=True)

    first = detect_turtle_soup(candles, "H1", lookback_bars=20)
    second = detect_turtle_soup(candles, "H1", lookback_bars=20)

    pdt.assert_frame_equal(candles, original)
    assert first.model_dump() == second.model_dump()
    assert first == TurtleSoupResult(
        detected=True,
        direction="BULLISH",
        reference_level=95.0,
        reference_time=datetime(2026, 3, 20, 19, 0, tzinfo=timezone.utc),
        lookback_bars=20,
        sweep_price=94.0,
        close_price=96.0,
        occurred_at=datetime(2026, 3, 20, 20, 0, tzinfo=timezone.utc),
    )


@freeze_time("2026-03-21T00:00:00Z")
def test_turtle_soup_detector_detects_bearish_false_breakout() -> None:
    start = datetime(2026, 3, 20, 0, 0, tzinfo=timezone.utc)
    rows = []
    for index in range(20):
        time = start + timedelta(hours=index)
        rows.append((time, 100.0, 110.0, 95.0, 102.0))
    rows.append((start + timedelta(hours=20), 102.0, 111.0, 100.0, 109.0))
    candles = build_candles(rows)

    result = detect_turtle_soup(candles, "H1", lookback_bars=20)

    assert result == TurtleSoupResult(
        detected=True,
        direction="BEARISH",
        reference_level=110.0,
        reference_time=datetime(2026, 3, 20, 19, 0, tzinfo=timezone.utc),
        lookback_bars=20,
        sweep_price=111.0,
        close_price=109.0,
        occurred_at=datetime(2026, 3, 20, 20, 0, tzinfo=timezone.utc),
    )


@freeze_time("2026-03-21T00:00:00Z")
def test_turtle_soup_detector_does_not_trigger_on_exact_equality() -> None:
    start = datetime(2026, 3, 20, 0, 0, tzinfo=timezone.utc)
    rows = []
    for index in range(20):
        time = start + timedelta(hours=index)
        rows.append((time, 100.0, 110.0, 95.0, 102.0))
    rows.append((start + timedelta(hours=20), 102.0, 108.0, 95.0, 96.0))
    candles = build_candles(rows)

    result = detect_turtle_soup(candles, "H1", lookback_bars=20)

    assert result == TurtleSoupResult()


def test_orb_detector_is_limited_to_m15() -> None:
    candles = build_m15_orb_fixture(breakout=False)

    assert detect_orb(candles, "H1") is None


def test_orb_detector_rejects_non_positive_opening_range_bars() -> None:
    candles = build_m15_orb_fixture(breakout=False)

    with pytest.raises(ValueError, match="opening_range_bars must be positive"):
        detect_orb(candles, "M15", opening_range_bars=0)


@freeze_time("2026-03-20T10:00:00Z")
def test_orb_detector_returns_non_detected_when_no_breakout_occurs(monkeypatch) -> None:
    candles = build_m15_orb_fixture(breakout=False)

    monkeypatch.setattr("smc.orb._load_smc_module", build_fake_session_module)

    result = detect_orb(candles, "M15", opening_range_bars=1)

    assert result == ORBResult()


@freeze_time("2026-03-20T10:00:00Z")
def test_orb_detector_detects_first_close_outside_opening_range(monkeypatch) -> None:
    candles = build_m15_orb_fixture(breakout=True)

    monkeypatch.setattr("smc.orb._load_smc_module", build_fake_session_module)

    result = detect_orb(candles, "M15", opening_range_bars=1)

    assert result == ORBResult(
        detected=True,
        direction="BULLISH",
        session="SYDNEY",
        range_high=102.0,
        range_low=100.0,
        breakout_price=102.5,
        occurred_at=datetime(2026, 3, 20, 8, 30, tzinfo=timezone.utc),
    )


@freeze_time("2026-03-20T10:00:00Z")
def test_orb_detector_returns_the_earliest_close_based_breakout(monkeypatch) -> None:
    candles = build_m15_orb_first_break_fixture()

    monkeypatch.setattr("smc.orb._load_smc_module", build_fake_session_module)

    result = detect_orb(candles, "M15", opening_range_bars=1)

    assert result == ORBResult(
        detected=True,
        direction="BULLISH",
        session="SYDNEY",
        range_high=102.0,
        range_low=100.0,
        breakout_price=102.5,
        occurred_at=datetime(2026, 3, 20, 8, 30, tzinfo=timezone.utc),
    )


@freeze_time("2026-03-20T10:00:00Z")
def test_orb_detector_prefers_the_latest_session_start_when_windows_overlap(monkeypatch) -> None:
    candles = build_m15_overlapping_orb_fixture()

    monkeypatch.setattr("smc.orb._load_smc_module", build_overlapping_session_module)

    result = detect_orb(candles, "M15", opening_range_bars=1)

    assert result == ORBResult(
        detected=True,
        direction="BULLISH",
        session="TOKYO",
        range_high=102.0,
        range_low=100.0,
        breakout_price=102.3,
        occurred_at=datetime(2026, 3, 20, 8, 45, tzinfo=timezone.utc),
    )
