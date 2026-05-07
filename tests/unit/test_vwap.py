from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import numpy as np
import pandas as pd

from indicators.vwap import (
    build_vwap_read_result,
    normalize_vwap_anchor,
    resolve_vwap_candle_count,
)


def build_candles(count: int, *, timeframe: str, end_time: datetime) -> pd.DataFrame:
    delta = pd.Timedelta(timeframe)
    start_time = pd.Timestamp(end_time) - (delta * (count - 1))
    rows = []
    for index in range(count):
        time = start_time + (delta * index)
        close = 100.0 + index
        rows.append(
            {
                "time": time.to_pydatetime(),
                "open": close - 0.25,
                "high": close + 0.50,
                "low": close - 0.50,
                "close": close,
                "tick_volume": 100 + index,
            }
        )
    return pd.DataFrame(rows)


def build_fake_pandasta(calls: dict[str, object]) -> SimpleNamespace:
    def vwap(
        high: pd.Series,
        low: pd.Series,
        close: pd.Series,
        volume: pd.Series,
        anchor: str = "D",
        bands: list[float] | None = None,
        offset=None,
        **kwargs,
    ):
        calls["anchor"] = anchor
        calls["bands"] = list(bands or [])
        base = pd.Series(
            np.linspace(100.0, 100.0 + (len(close) - 1), len(close)),
            index=close.index,
            name=f"VWAP_{anchor}",
        )
        if not bands:
            return base

        frame = pd.DataFrame({base.name: base}, index=close.index)
        for deviation in bands:
            label = str(float(deviation))
            frame[f"{base.name}_L_{label}"] = base - float(deviation)
            frame[f"{base.name}_U_{label}"] = base + float(deviation)
        return frame

    return SimpleNamespace(vwap=vwap)


def test_normalize_vwap_anchor_accepts_public_aliases() -> None:
    assert normalize_vwap_anchor("D") == ("D", "D", "daily")
    assert normalize_vwap_anchor("daily") == ("D", "D", "daily")
    assert normalize_vwap_anchor("W") == ("W", "W-FRI", "weekly")
    assert normalize_vwap_anchor("monthly") == ("M", "M", "monthly")


def test_resolve_vwap_candle_count_monthly_m30_exceeds_default_window() -> None:
    count = resolve_vwap_candle_count(
        "M30",
        "M",
        now_utc=datetime(2026, 3, 31, 23, 30, tzinfo=timezone.utc),
    )

    assert count > 500


def test_build_vwap_read_result_maps_fields_and_bands(monkeypatch) -> None:
    calls: dict[str, object] = {}
    monkeypatch.setattr(
        "indicators.vwap._load_pandasta_module",
        lambda: build_fake_pandasta(calls),
    )
    candles = build_candles(
        5,
        timeframe="1D",
        end_time=datetime(2026, 3, 27, 0, 0, tzinfo=timezone.utc),
    )

    result = build_vwap_read_result(
        candles,
        instrument="SPX500_USD",
        timeframe="D",
        anchor="weekly",
        bands=(2.0, 1.0),
        source="oanda_api",
    )

    assert calls["anchor"] == "W-FRI"
    assert calls["bands"] == [1.0, 2.0]
    assert result.anchor == "W"
    assert result.anchor_name == "weekly"
    assert result.anchor_start == candles["time"].iloc[0]
    assert result.last_completed_candle == candles["time"].iloc[-1]
    assert result.reference_close == 104.0
    assert result.vwap == 104.0
    assert result.price_position == "at"
    assert result.distance_price == 0.0
    assert result.distance_pips == 0.0
    assert [band.deviation for band in result.bands] == [1.0, 2.0]
    assert result.bands[0].lower == 103.0
    assert result.bands[1].upper == 106.0
    assert result.source == "oanda_api"
    assert result.volume_type == "tick_count"
    assert "tick count" in result.caveat


def test_build_vwap_read_result_without_bands(monkeypatch) -> None:
    monkeypatch.setattr(
        "indicators.vwap._load_pandasta_module",
        lambda: build_fake_pandasta({}),
    )
    candles = build_candles(
        4,
        timeframe="1H",
        end_time=datetime(2026, 3, 25, 3, 0, tzinfo=timezone.utc),
    )

    result = build_vwap_read_result(
        candles,
        instrument="SPX500_USD",
        timeframe="H1",
        anchor="D",
        bands=None,
        source="csv",
    )

    assert result.anchor == "D"
    assert result.anchor_name == "daily"
    assert result.bands == ()
    assert result.source == "csv"
