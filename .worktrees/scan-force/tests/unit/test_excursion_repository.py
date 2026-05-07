from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from data.persistence.trade_store import TradeStore


BASE_TIME = datetime(2026, 3, 21, 8, 0, tzinfo=timezone.utc)


def test_excursion_repository_lists_samples_and_aggregates_mae_mfe(tmp_path: Path) -> None:
    store = TradeStore(db_path=tmp_path / "excursions.json")
    try:
        store.insert_excursion_sample(
            {
                "trade_id": "trade-1",
                "sampled_at": BASE_TIME,
                "bid": 1.1000,
                "ask": 1.1002,
                "adverse_pips": 2.0,
                "favorable_pips": 4.0,
            }
        )
        store.insert_excursion_sample(
            {
                "trade_id": "trade-1",
                "sampled_at": BASE_TIME + timedelta(minutes=5),
                "bid": 1.0995,
                "ask": 1.0997,
                "adverse_pips": 5.0,
                "favorable_pips": 3.0,
            }
        )
        store.insert_excursion_sample(
            {
                "trade_id": "trade-1",
                "sampled_at": BASE_TIME + timedelta(minutes=10),
                "bid": 1.1010,
                "ask": 1.1012,
                "adverse_pips": 1.5,
                "favorable_pips": 7.5,
            }
        )

        samples = store.list_excursion_samples("trade-1")
        summary = store.get_trade_mae_mfe("trade-1")

        assert [sample.sampled_at for sample in samples] == [
            BASE_TIME,
            BASE_TIME + timedelta(minutes=5),
            BASE_TIME + timedelta(minutes=10),
        ]
        assert summary == {
            "trade_id": "trade-1",
            "sample_count": 3,
            "mae_pips": 5.0,
            "mfe_pips": 7.5,
            "last_sampled_at": BASE_TIME + timedelta(minutes=10),
        }
    finally:
        store.close()


def test_excursion_repository_returns_none_without_samples(tmp_path: Path) -> None:
    store = TradeStore(db_path=tmp_path / "empty_excursions.json")
    try:
        assert store.list_excursion_samples("missing") == []
        assert store.get_trade_mae_mfe("missing") is None
    finally:
        store.close()
