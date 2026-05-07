from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from data.macro import MacroContextService


BASE_TIME = datetime(2026, 3, 29, 8, 0, tzinfo=timezone.utc)


def build_macro_frame() -> pd.DataFrame:
    index = pd.to_datetime(
        ["2026-03-26T00:00:00Z", "2026-03-27T00:00:00Z"],
        utc=True,
    )
    return pd.DataFrame(
        {
            ("Adj Close", "^VIX"): [27.44, 31.05],
            ("Adj Close", "DX-Y.NYB"): [99.90, 100.15],
            ("Close", "^VIX"): [27.44, 31.05],
            ("Close", "DX-Y.NYB"): [99.90, 100.15],
        },
        index=index,
    )


def test_macro_context_refresh_reads_bounded_symbols() -> None:
    calls: list[tuple[str, ...]] = []

    def fake_download(symbols: tuple[str, ...]) -> pd.DataFrame:
        calls.append(symbols)
        return build_macro_frame()

    service = MacroContextService(
        refresh_interval_hours=1,
        now_fn=lambda: BASE_TIME,
        download_fn=fake_download,
    )

    status = service.refresh(force=True)

    assert calls == [("^VIX", "DX-Y.NYB")]
    assert status.used_cached is False
    assert status.last_error is None
    assert status.vix.value == 31.05
    assert status.dxy.value == 100.15
    assert status.vix.as_of == datetime(2026, 3, 27, 0, 0, tzinfo=timezone.utc)


def test_macro_context_returns_cached_status_within_refresh_window() -> None:
    calls = {"count": 0}

    def fake_download(symbols: tuple[str, ...]) -> pd.DataFrame:
        calls["count"] += 1
        return build_macro_frame()

    service = MacroContextService(
        refresh_interval_hours=4,
        now_fn=lambda: BASE_TIME,
        download_fn=fake_download,
    )

    first = service.refresh(force=True)
    second = service.get_status(force=False)

    assert calls["count"] == 1
    assert first.used_cached is False
    assert second.used_cached is True
    assert second.vix.value == first.vix.value
    assert second.dxy.value == first.dxy.value


def test_macro_context_falls_back_to_last_good_snapshot_on_refresh_failure() -> None:
    should_fail = {"value": False}

    def fake_download(symbols: tuple[str, ...]) -> pd.DataFrame:
        if should_fail["value"]:
            raise RuntimeError("macro backend unavailable")
        return build_macro_frame()

    service = MacroContextService(
        refresh_interval_hours=1,
        now_fn=lambda: BASE_TIME,
        download_fn=fake_download,
    )

    initial = service.refresh(force=True)
    should_fail["value"] = True
    degraded = service.refresh(force=True)

    assert degraded.used_cached is True
    assert degraded.last_error == "macro backend unavailable"
    assert degraded.last_refreshed_at == initial.last_refreshed_at
    assert degraded.vix.value == initial.vix.value
    assert degraded.dxy.value == initial.dxy.value


def test_macro_context_reports_unavailable_when_first_refresh_fails() -> None:
    service = MacroContextService(
        refresh_interval_hours=1,
        now_fn=lambda: BASE_TIME,
        download_fn=lambda symbols: (_ for _ in ()).throw(RuntimeError("no macro data")),
    )

    status = service.refresh(force=True)

    assert status.used_cached is False
    assert status.last_refreshed_at is None
    assert status.last_error == "no macro data"
    assert status.vix.value is None
    assert status.dxy.value is None
