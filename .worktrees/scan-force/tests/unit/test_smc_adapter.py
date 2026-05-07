from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
from types import SimpleNamespace

import pandas as pd
import pytest

from core.models import ActiveZoneSummary, LiquidityPoolSummary, MAX_ACTIVE_ORDER_BLOCKS, StructureEventSummary
from smc.provider import OrderBlockCandidate, SmcAdapter, _load_smc_module


def build_candles(
    closes: list[float],
    *,
    repeated_high_indices: set[int] | None = None,
    start: datetime | None = None,
) -> pd.DataFrame:
    repeated = repeated_high_indices or set()
    start_time = start or datetime(2026, 1, 1, tzinfo=timezone.utc)
    rows = []
    for index, close in enumerate(closes):
        open_price = closes[index - 1] if index else close - 0.5
        high = max(open_price, close) + (0.4 if index in repeated else 0.6)
        low = min(open_price, close) - 0.6
        rows.append(
            {
                "time": start_time + timedelta(hours=index),
                "open": open_price,
                "high": high,
                "low": low,
                "close": close,
                "tick_volume": 100 + index,
            }
        )
    return pd.DataFrame(rows)


def build_stage_06_fixture() -> pd.DataFrame:
    closes = [
        100,
        102,
        104,
        103,
        104,
        103,
        104,
        102,
        100,
        99,
        98,
        99,
        100,
        101,
        100,
        101,
        100,
        102,
        104,
        103,
        104,
        103,
        104,
        102,
        100,
        98,
        97,
        98,
        99,
        100,
    ]
    return build_candles(closes, repeated_high_indices={2, 4, 6, 18, 20, 22})


def build_insufficient_fixture() -> pd.DataFrame:
    closes = [100, 101, 102, 101, 100, 99, 100, 101]
    return build_candles(closes)


def test_smc_adapter_is_deterministic_and_does_not_mutate_input() -> None:
    candles = build_stage_06_fixture()
    original = candles.copy(deep=True)

    adapter = SmcAdapter(swing_length=2)
    first = adapter.analyze("EUR_USD", "H1", candles)
    second = SmcAdapter(swing_length=2).analyze("EUR_USD", "H1", candles)

    pd.testing.assert_frame_equal(candles, original)
    assert first.structure.model_dump() == second.structure.model_dump()
    assert first.zones.model_dump() == second.zones.model_dump()
    assert first.liquidity.model_dump() == second.liquidity.model_dump()
    assert first.smc_context.model_dump() == second.smc_context.model_dump()
    assert first.order_block_candidates == second.order_block_candidates


def test_smc_adapter_maps_stage_06_summaries_and_tracker_candidates() -> None:
    result = SmcAdapter(swing_length=2).analyze("EUR_USD", "H1", build_stage_06_fixture())

    assert result.structure.latest_break is not None
    assert result.structure.latest_break.kind == "BOS"
    assert result.structure.latest_break.direction == "BULLISH"
    assert result.structure.latest_swing_high is not None
    assert result.structure.latest_swing_low is not None

    assert len(result.zones.order_blocks) == 2
    assert result.zones.order_blocks[0].is_mitigated is True
    assert result.zones.order_blocks[1].is_mitigated is False

    assert len(result.liquidity.levels) == 1
    assert result.liquidity.levels[0].side == "BUY_SIDE"

    assert len(result.smc_context.sessions.sessions) == 4
    assert result.smc_context.previous_high_low is not None
    assert result.smc_context.retracement is not None

    assert len(result.order_block_candidates) == 2
    assert result.order_block_candidates[0].status == "MITIGATED"
    assert result.order_block_candidates[0].mitigated_at is not None
    assert result.order_block_candidates[1].status == "ACTIVE"
    assert (
        result.order_block_candidates[0].id
        == SmcAdapter.build_order_block_record_id(
            instrument="EUR_USD",
            timeframe="H1",
            direction=result.order_block_candidates[0].direction,
            created_at=result.order_block_candidates[0].created_at,
            upper_price=result.order_block_candidates[0].upper_price,
            lower_price=result.order_block_candidates[0].lower_price,
        )
    )


def test_smc_adapter_normalizes_negative_retracement_percentages() -> None:
    retracement = SmcAdapter._build_retracement_summary(
        build_stage_06_fixture(),
        pd.DataFrame(
            {
                "Direction": [1],
                "CurrentRetracement%": [-42.0],
                "DeepestRetracement%": [-35.0],
            }
        ),
    )

    assert retracement is not None
    assert retracement.direction == "BULLISH"
    assert retracement.current_retracement_pct == pytest.approx(42.0)
    assert retracement.deepest_retracement_pct == pytest.approx(42.0)


def test_smc_adapter_degrades_gracefully_with_insufficient_history() -> None:
    result = SmcAdapter(swing_length=5).analyze("EUR_USD", "H1", build_insufficient_fixture())

    assert result.structure == StructureEventSummary()
    assert result.zones == ActiveZoneSummary()
    assert result.liquidity == LiquidityPoolSummary()
    assert len(result.smc_context.sessions.sessions) == 4
    assert result.smc_context.previous_high_low is None
    assert result.smc_context.retracement is None
    assert result.order_block_candidates == ()


def test_smc_adapter_zone_summary_caps_order_blocks_at_ten() -> None:
    adapter = SmcAdapter(swing_length=2)
    candidates = tuple(
        OrderBlockCandidate(
            id=f"ob-{index}",
            instrument="EUR_USD",
            timeframe="H1",
            direction="BULLISH" if index % 2 == 0 else "BEARISH",
            upper_price=1.1100 + (index * 0.0010),
            lower_price=1.1090 + (index * 0.0010),
            created_at=pd.Timestamp(datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(hours=index)),
            status="ACTIVE",
            mitigated_at=None,
            last_analyzed_close=1.1000,
        )
        for index in range(MAX_ACTIVE_ORDER_BLOCKS + 2)
    )

    summary = adapter._build_zone_summary(candidates)

    assert len(summary.order_blocks) == MAX_ACTIVE_ORDER_BLOCKS


def test_smc_module_loader_forces_credit_suppression(monkeypatch: pytest.MonkeyPatch) -> None:
    _load_smc_module.cache_clear()

    imported = {}

    def fake_import_module(name: str) -> SimpleNamespace:
        imported["name"] = name
        imported["credit"] = os.environ.get("SMC_CREDIT")
        return SimpleNamespace(smc=object())

    monkeypatch.setattr("smc.provider.importlib.import_module", fake_import_module)
    monkeypatch.setenv("SMC_CREDIT", "1")

    module = _load_smc_module()

    assert imported["name"] == "smartmoneyconcepts"
    assert imported["credit"] == "0"
    assert os.environ["SMC_CREDIT"] == "0"
    assert module is not None

    _load_smc_module.cache_clear()
