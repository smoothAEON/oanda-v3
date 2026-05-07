"""Unit tests for CandleCsvStore."""

from __future__ import annotations

from pathlib import Path
from threading import Barrier
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import pytest

from config.settings import load_settings
from core.candle_policy import CANONICAL_COLUMNS
import data.csv_persistence as csv_persistence_module
from data.csv_persistence import CandleCsvStore


def write_env_file(path: Path, **overrides: str) -> Path:
    values = {
        "OANDA_API_KEY": "k",
        "OANDA_ACCOUNT_ID": "a",
        "OANDA_ENVIRONMENT": "practice",
        "TELEGRAM_BOT_TOKEN": "t",
        "TELEGRAM_CHAT_ID": "1",
        "TELEGRAM_BOT_PASSWORD": "p",
        "TELEGRAM_ADMIN_IDS": "1",
        "TINYDB_PATH": str(path.parent / "bot.json"),
    }
    values.update(overrides)
    path.write_text("\n".join(f"{k}={v}" for k, v in values.items()), encoding="utf-8")
    return path


def make_candles() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "time": ["2026-03-20T08:00:00Z", "2026-03-20T09:00:00Z"],
            "open": [1.1000, 1.1010],
            "high": [1.1005, 1.1015],
            "low": [1.0995, 1.1005],
            "close": [1.1002, 1.1012],
            "tick_volume": [100, 101],
        }
    )


@pytest.fixture()
def csv_store(tmp_path: Path) -> CandleCsvStore:
    return CandleCsvStore(root_dir=tmp_path / "cache")


class TestPathFor:
    def test_returns_expected_path(self, csv_store: CandleCsvStore) -> None:
        path = csv_store.path_for("EUR_USD", "H1")
        assert path.name == "H1.csv"
        assert path.parent.name == "EUR_USD"

    def test_accepts_live_only_instrument_via_catalog_validation(
        self,
        csv_store: CandleCsvStore,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        def fake_validate(instrument: str) -> str:
            if instrument == "US30_USD":
                return instrument
            raise KeyError(instrument)

        monkeypatch.setattr(csv_persistence_module, "validate_live_instrument", fake_validate)
        path = csv_store.path_for("US30_USD", "H1")

        assert path.name == "H1.csv"
        assert path.parent.name == "US30_USD"

    def test_rejects_unknown_instrument(self, csv_store: CandleCsvStore) -> None:
        with pytest.raises(KeyError):
            csv_store.path_for("ZZZ_YYY", "H1")


class TestSaveAndLoad:
    def test_save_then_load_roundtrips_candles(self, csv_store: CandleCsvStore) -> None:
        candles = make_candles()
        csv_store.save_candles("EUR_USD", "H1", candles)
        loaded = csv_store.load_candles("EUR_USD", "H1")

        assert loaded is not None
        assert len(loaded) == 2
        assert list(loaded.columns) == list(CANONICAL_COLUMNS)
        assert loaded["open"].iloc[0] == pytest.approx(1.1000)

    def test_load_returns_none_for_missing_file(self, csv_store: CandleCsvStore) -> None:
        assert csv_store.load_candles("EUR_USD", "M15") is None

    def test_save_creates_parent_directories(self, csv_store: CandleCsvStore) -> None:
        csv_store.save_candles("XAU_USD", "D", make_candles())
        assert csv_store.path_for("XAU_USD", "D").exists()

    def test_save_validates_schema(self, csv_store: CandleCsvStore) -> None:
        bad = make_candles().rename(columns={"tick_volume": "volume"})
        with pytest.raises(ValueError, match="tick_volume"):
            csv_store.save_candles("EUR_USD", "H1", bad)

    def test_load_validates_schema(self, csv_store: CandleCsvStore, tmp_path: Path) -> None:
        # Write a CSV with wrong columns directly
        path = csv_store.path_for("EUR_USD", "H1")
        path.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame({"bad": [1]}).to_csv(path, index=False)

        with pytest.raises(ValueError):
            csv_store.load_candles("EUR_USD", "H1")

    def test_loaded_candles_have_utc_time(self, csv_store: CandleCsvStore) -> None:
        csv_store.save_candles("EUR_USD", "H1", make_candles())
        loaded = csv_store.load_candles("EUR_USD", "H1")

        assert str(loaded["time"].dt.tz) == "UTC"

    def test_save_returns_written_path(self, csv_store: CandleCsvStore) -> None:
        path = csv_store.save_candles("EUR_USD", "H1", make_candles())
        assert path.exists()
        assert path == csv_store.path_for("EUR_USD", "H1")

    def test_overwrite_replaces_previous_data(self, csv_store: CandleCsvStore) -> None:
        csv_store.save_candles("EUR_USD", "H1", make_candles())
        single = pd.DataFrame(
            {
                "time": ["2026-03-20T10:00:00Z"],
                "open": [1.2],
                "high": [1.3],
                "low": [1.1],
                "close": [1.25],
                "tick_volume": [50],
            }
        )
        csv_store.save_candles("EUR_USD", "H1", single)
        loaded = csv_store.load_candles("EUR_USD", "H1")
        assert len(loaded) == 1

    def test_concurrent_saves_to_same_path_do_not_raise(self, csv_store: CandleCsvStore) -> None:
        start_barrier = Barrier(3)

        def writer() -> Path:
            start_barrier.wait(timeout=5)
            return csv_store.save_candles("EUR_USD", "H1", make_candles())

        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = [executor.submit(writer) for _ in range(3)]
            paths = [future.result(timeout=10) for future in futures]

        assert all(path == csv_store.path_for("EUR_USD", "H1") for path in paths)
        loaded = csv_store.load_candles("EUR_USD", "H1")
        assert loaded is not None
        assert len(loaded) == 2

    def test_failed_save_keeps_previous_file_intact(
        self,
        csv_store: CandleCsvStore,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        path = csv_store.save_candles("EUR_USD", "H1", make_candles())
        original_bytes = path.read_bytes()
        original_to_csv = pd.DataFrame.to_csv

        def flaky_to_csv(self, path_or_buf=None, *args, **kwargs):  # type: ignore[no-untyped-def]
            temp_path = Path(path_or_buf)
            temp_path.write_text("partial write", encoding="utf-8")
            raise OSError("disk full")

        monkeypatch.setattr(pd.DataFrame, "to_csv", flaky_to_csv)

        with pytest.raises(OSError, match="disk full"):
            csv_store.save_candles("EUR_USD", "H1", make_candles())

        assert path.read_bytes() == original_bytes
        monkeypatch.setattr(pd.DataFrame, "to_csv", original_to_csv)


class TestSettingsIntegration:
    def test_derives_root_from_settings_tinydb_path(self, tmp_path: Path) -> None:
        env_file = write_env_file(tmp_path / ".env")
        settings = load_settings(env_file=env_file)
        store = CandleCsvStore(settings=settings)

        assert "cache" in str(store.root_dir)
