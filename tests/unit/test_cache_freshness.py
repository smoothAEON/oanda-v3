from __future__ import annotations

import pandas as pd
from freezegun import freeze_time

from core.candle_policy import calculate_candle_staleness_seconds
from providers.cache import is_cache_fresh


@freeze_time("2026-03-20T10:15:00Z")
def test_cache_is_fresh_when_it_contains_the_latest_completed_h1_candle() -> None:
    assert is_cache_fresh(pd.Timestamp("2026-03-20T09:00:00Z"), "H1")
    assert calculate_candle_staleness_seconds(
        pd.Timestamp("2026-03-20T09:00:00Z"),
        "H1",
    ) == 0.0


@freeze_time("2026-03-20T11:00:00Z")
def test_cache_is_fresh_at_the_exact_close_boundary() -> None:
    assert is_cache_fresh(pd.Timestamp("2026-03-20T10:00:00Z"), "H1")
    assert calculate_candle_staleness_seconds(
        pd.Timestamp("2026-03-20T10:00:00Z"),
        "H1",
    ) == 0.0


@freeze_time("2026-03-20T11:00:00Z")
def test_cache_becomes_stale_once_a_new_boundary_has_completed() -> None:
    assert not is_cache_fresh(pd.Timestamp("2026-03-20T09:00:00Z"), "H1")
    assert calculate_candle_staleness_seconds(
        pd.Timestamp("2026-03-20T09:00:00Z"),
        "H1",
    ) == 3600.0


@freeze_time("2026-03-20T11:00:00Z")
def test_fetched_at_recency_does_not_override_boundary_staleness() -> None:
    last_completed = pd.Timestamp("2026-03-20T09:00:00Z")
    fetched_at = pd.Timestamp("2026-03-20T10:59:59Z")

    assert fetched_at > last_completed
    assert not is_cache_fresh(last_completed, "H1")
