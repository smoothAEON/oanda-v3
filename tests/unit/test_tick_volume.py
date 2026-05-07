from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import numpy as np
import pandas as pd

from indicators.tick_volume import build_tick_volume_metrics


def build_candles(count: int) -> pd.DataFrame:
    start_time = datetime(2026, 3, 20, tzinfo=timezone.utc)
    rows = []
    for index in range(count):
        close = 100.0 + (index * 0.25)
        rows.append(
            {
                "time": start_time + timedelta(hours=index),
                "open": close - 0.2,
                "high": close + 0.4,
                "low": close - 0.5,
                "close": close,
                "tick_volume": 100 + index,
            }
        )
    return pd.DataFrame(rows)


def test_tick_volume_metrics_use_canonical_names_and_otc_caveats(monkeypatch) -> None:
    calls: dict[str, np.ndarray] = {}

    def obv(close: np.ndarray, volume: np.ndarray) -> np.ndarray:
        calls["obv_volume"] = volume
        return np.array([1.0, 2.0, 3.0], dtype=np.float64)

    def mfi(
        high: np.ndarray,
        low: np.ndarray,
        close: np.ndarray,
        volume: np.ndarray,
    ) -> np.ndarray:
        calls["mfi_volume"] = volume
        return np.array([4.0, 5.0, 6.0], dtype=np.float64)

    def adosc(
        high: np.ndarray,
        low: np.ndarray,
        close: np.ndarray,
        volume: np.ndarray,
    ) -> np.ndarray:
        calls["adosc_volume"] = volume
        return np.array([7.0, 8.0, 9.0], dtype=np.float64)

    fake_talib = SimpleNamespace(OBV=obv, MFI=mfi, ADOSC=adosc)
    monkeypatch.setattr("indicators.tick_volume._load_talib_module", lambda: fake_talib)

    candles = build_candles(30)
    metrics = build_tick_volume_metrics(candles)

    assert tuple(metric.name for metric in metrics) == ("tick_obv", "tick_mfi", "tick_adosc")
    assert np.array_equal(calls["obv_volume"], candles["tick_volume"].to_numpy(dtype=np.float64))
    assert np.array_equal(calls["mfi_volume"], candles["tick_volume"].to_numpy(dtype=np.float64))
    assert np.array_equal(calls["adosc_volume"], candles["tick_volume"].to_numpy(dtype=np.float64))
    assert all(metric.volume_type == "tick_count" for metric in metrics)
    assert all(metric.source == "oanda_otc" for metric in metrics)
    assert all("OANDA tick count" in metric.caveat for metric in metrics)
    assert all(not metric.name.startswith("volume") for metric in metrics)


def test_tick_volume_metrics_skip_nonfinite_warmup_values(monkeypatch) -> None:
    fake_talib = SimpleNamespace(
        OBV=lambda close, volume: np.array([1.0, 2.0], dtype=np.float64),
        MFI=lambda high, low, close, volume: np.array([np.nan, np.nan], dtype=np.float64),
        ADOSC=lambda high, low, close, volume: np.array([np.nan, 3.5], dtype=np.float64),
    )
    monkeypatch.setattr("indicators.tick_volume._load_talib_module", lambda: fake_talib)

    metrics = build_tick_volume_metrics(build_candles(5))

    assert tuple(metric.name for metric in metrics) == ("tick_obv", "tick_adosc")
